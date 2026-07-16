"""
Configurações editáveis pelo painel (templates das mensagens ao cliente).

GET /settings/mensagens → templates atuais (salvos ou padrão)
PUT /settings/mensagens → salva os templates (somente ADMIN)

Variáveis disponíveis nos templates: {NOME}, {VALOR}, {VENCIMENTO},
{REFERENTE}, {CODIGO_BARRAS}, {LINK_BOLETO}.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.system_setting import SystemSetting

router = APIRouter()

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
    db.commit()
    return _load(db)
