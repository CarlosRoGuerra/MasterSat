"""
Configurações editáveis pelo painel (templates das mensagens ao cliente).

GET /settings/mensagens → templates atuais (salvos ou padrão)
PUT /settings/mensagens → salva os templates (somente ADMIN)

Variáveis disponíveis nos templates: {NOME}, {VALOR}, {VENCIMENTO},
{REFERENTE}, {CODIGO_BARRAS}, {LINK_BOLETO}.
"""
from __future__ import annotations

import re
import smtplib

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, EmailStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.uploads import read_limited
from app.core.integrity import raise_integrity_conflict
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.system_setting import SystemSetting
from app.services.contract_signature import (
    delete_contract_signature,
    get_contract_signature,
    save_contract_signature,
)

router = APIRouter()


@router.get('/contract-signature')
def contract_signature_status(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN)),
):
    return {'configured': get_contract_signature(db) is not None}


@router.get('/contract-signature/image')
def contract_signature_image(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN)),
):
    content = get_contract_signature(db)
    if content is None:
        raise HTTPException(status_code=404, detail='Assinatura não configurada.')
    return Response(content, media_type='image/png', headers={'Cache-Control': 'private, no-store'})


@router.post('/contract-signature')
async def upload_contract_signature(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN)),
):
    if (file.content_type or '').split(';')[0].lower() not in ('image/png', 'image/jpeg'):
        raise HTTPException(status_code=415, detail='Envie a assinatura em PNG ou JPG.')
    content = await read_limited(file)
    try:
        save_contract_signature(db, content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {'configured': True}


@router.delete('/contract-signature')
def remove_contract_signature(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN)),
):
    delete_contract_signature(db)
    return {'configured': False}

# O upsert abaixo (SELECT, depois INSERT se não achar) não é atômico: duas
# gravações concorrentes da mesma chave podem ambas cair no INSERT e disputar
# o UNIQUE de `system_settings.key`. Sem valor de negócio em expor isso ao
# operador — a última escrita é o que importa aqui —, então a resposta cai
# pro genérico de volta e o operador só tenta salvar de novo.
_SETTING_INTEGRITY_MESSAGES = {
    'ix_system_settings_key': 'Configuração alterada por outra sessão ao mesmo tempo. Tente salvar novamente.',
}
_SETTING_SQLITE_CONSTRAINTS = {
    'UNIQUE constraint failed: system_settings.key': 'ix_system_settings_key',
}

# Templates padrão (usados enquanto nada foi salvo no painel)
MENSAGENS_PADRAO = {
    'msg_boleto': (
        'Olá, {NOME} tudo bem? Estamos enviando o código de barras do seu boleto, '
        'basta copiar a linha digitável e realizar o pagamento junto ao banco.\n'
        '\n'
        'Atenciosamente,\n'
        'MASTERSAT COMERCIO E SERVIÇOS DE RASTREAMENTO LTDA\n'
        '\n'
        'Referente:{REFERENTE} Valor: {VALOR} Vencimento:{VENCIMENTO}\n'
        '\n'
        'Código de Barras:\n'
        '{CODIGO_BARRAS}\n'
        '\n'
        'Clique no link abaixo para visualizar seu boleto:\n'
        '{LINK_BOLETO}'
    ),
    'msg_boleto_assunto': 'Boleto MasterSat — vencimento {VENCIMENTO}',
}


class MensagensPayload(BaseModel):
    msg_boleto: str | None = None
    msg_boleto_assunto: str | None = None


def _load(db: Session) -> dict[str, str]:
    saved = {
        s.key: s.value
        for s in db.query(SystemSetting).filter(SystemSetting.key.in_(MENSAGENS_PADRAO)).all()
    }
    return {key: saved.get(key) or padrao for key, padrao in MENSAGENS_PADRAO.items()}


# Reusado pelo envio de boleto por e-mail (boletos.py) — mesmos templates e
# variáveis {NOME}/{VALOR}/... que o operador edita nesta tela.
carregar_mensagens = _load


def render_template(tpl: str, variaveis: dict[str, str]) -> str:
    return re.sub(r'\{(\w+)\}', lambda m: variaveis.get(m.group(1), ''), tpl)


@router.get('/mensagens')
def get_mensagens(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.FINANCIAL)),
):
    return _load(db)


@router.put('/mensagens')
def put_mensagens(
    payload: MensagensPayload,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN)),
):
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key not in MENSAGENS_PADRAO or value is None:
            continue
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if row:
            row.value = value
        else:
            db.add(SystemSetting(key=key, value=value))
    try:
        db.commit()
    except IntegrityError as exc:
        raise_integrity_conflict(
            db, exc, _SETTING_INTEGRITY_MESSAGES, sqlite_columns=_SETTING_SQLITE_CONSTRAINTS,
        )
    return _load(db)


# ── E-mail (SMTP) ────────────────────────────────────────────────────────────
class EmailConfigPayload(BaseModel):
    host: str = ''
    port: int = 587
    username: str = ''
    # None/'' = mantém a senha atual; string preenchida = troca a senha.
    password: str | None = None
    from_email: str = ''
    from_name: str = ''
    security: str = 'tls'  # none | tls | ssl
    enabled: bool = False


class EmailTestPayload(BaseModel):
    to: EmailStr


def _email_out(cfg: dict) -> dict:
    """Config para a tela — nunca devolve a senha, só se existe uma salva."""
    return {
        'host': cfg['host'],
        'port': cfg['port'],
        'username': cfg['username'],
        'from_email': cfg['from_email'],
        'from_name': cfg['from_name'],
        'security': cfg['security'],
        'enabled': cfg['enabled'],
        'password_set': cfg['password_set'],
    }


@router.get('/email')
def get_email_config(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN)),
):
    from app.services.email_smtp import load_config
    return _email_out(load_config(db))


@router.put('/email')
def put_email_config(
    payload: EmailConfigPayload,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN)),
):
    if payload.security not in ('none', 'tls', 'ssl'):
        raise HTTPException(status_code=422, detail='Segurança deve ser none, tls ou ssl.')
    from app.services.email_smtp import save_config
    return _email_out(save_config(db, payload.model_dump()))


@router.post('/email/test')
def test_email_config(
    payload: EmailTestPayload,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN)),
):
    """Envia um e-mail de teste com a configuração SALVA para conferir o SMTP."""
    from app.services.email_smtp import EmailConfigError, enviar_email
    try:
        enviar_email(
            db,
            destinatario=str(payload.to),
            assunto='Teste de e-mail — MasterSat',
            corpo=(
                'Este é um e-mail de teste enviado pelo painel MasterSat.\n\n'
                'Se você recebeu esta mensagem, a configuração de SMTP está funcionando.'
            ),
        )
    except EmailConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except smtplib.SMTPAuthenticationError as exc:
        raise HTTPException(status_code=400, detail=f'Falha de autenticação no servidor de e-mail: {exc.smtp_error.decode(errors="ignore") if hasattr(exc, "smtp_error") else exc}') from exc
    except smtplib.SMTPException as exc:
        raise HTTPException(status_code=400, detail=f'Erro do servidor de e-mail: {exc}') from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f'Não foi possível conectar ao servidor de e-mail: {exc}') from exc
    return {'message': f'E-mail de teste enviado para {payload.to}.'}
