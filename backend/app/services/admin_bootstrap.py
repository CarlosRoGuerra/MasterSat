"""Provisionamento do admin inicial e recuperação controlada (SEC-03).

Bootstrap (automático, no startup):
- só roda com o banco SEM NENHUM usuário (nem excluído). Excluir o admin
  inicial é uma decisão que sobrevive ao restart — antes ele era reativado
  a cada boot, com senha nova escrita no log e sem derrubar tokens antigos;
- a senha vem de INITIAL_ADMIN_PASSWORD e passa pela política de senha.
  Sem ela, nada é criado: o log diz como provisionar. Nenhuma senha é
  gerada nem escrita em log.

Recuperação (manual, scripts/reset_admin_senha.py):
- redefine a senha de um ADMIN existente, pela política;
- conta excluída/inativa só volta com --reativar explícito;
- toda redefinição encerra as sessões do usuário (corte + refresh
  revogados) e grava uma linha em audit_logs (sem a senha).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.password_policy import password_problems
from app.core.security import get_password_hash
from app.models.audit_log import AuditLog
from app.models.enums import UserRole
from app.models.refresh_token import RefreshToken
from app.models.user import User

logger = logging.getLogger('uvicorn.error')

CRIADO = 'criado'
JA_PROVISIONADO = 'ja_provisionado'
SEM_SENHA = 'sem_senha'
SENHA_FORA_DA_POLITICA = 'senha_fora_da_politica'


def seed_initial_admin(db: Session) -> str:
    """Cria o admin inicial numa instalação nova. Devolve o desfecho."""
    if db.query(User.id).first() is not None:
        return JA_PROVISIONADO

    email = settings.initial_admin_email.strip().lower()
    senha = settings.initial_admin_password
    if not senha:
        logger.warning(
            'Nenhum usuário cadastrado e INITIAL_ADMIN_PASSWORD vazia: admin inicial NÃO criado. '
            'Defina INITIAL_ADMIN_PASSWORD (política de senha) e reinicie, ou rode '
            '"python scripts/reset_admin_senha.py --criar --email %s".',
            email,
        )
        return SEM_SENHA
    problemas = password_problems(senha)
    if problemas:
        logger.error(
            'INITIAL_ADMIN_PASSWORD não atende à política de senha (%s): admin inicial NÃO criado.',
            '; '.join(problemas),
        )
        return SENHA_FORA_DA_POLITICA

    db.add(User(
        name='Administrador',
        email=email,
        password_hash=get_password_hash(senha),
        role=UserRole.ADMIN,
        active=True,
    ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # outro worker criou primeiro
        return JA_PROVISIONADO
    logger.warning(
        'ADMIN INICIAL criado: %s (senha = INITIAL_ADMIN_PASSWORD). Troque-a no primeiro acesso '
        'e remova INITIAL_ADMIN_PASSWORD do ambiente.',
        email,
    )
    return CRIADO


class RecuperacaoRecusada(Exception):
    """Pedido de recuperação que o script não deve executar (mensagem ao operador)."""


@dataclass
class ResultadoRecuperacao:
    user_id: int
    email: str
    criado: bool
    reativado: bool
    sessoes_revogadas: int


def _encerrar_sessoes(db: Session, user: User, agora: datetime) -> int:
    user.tokens_valid_from = agora
    return db.query(RefreshToken).filter(
        RefreshToken.user_id == user.id,
        RefreshToken.revoked_at.is_(None),
    ).update({'revoked_at': agora}, synchronize_session=False)


def recuperar_admin(
    db: Session,
    email: str,
    senha: str,
    *,
    reativar: bool = False,
    criar: bool = False,
    operador: str = 'cli',
) -> ResultadoRecuperacao:
    """Redefine (ou, com criar=True em banco vazio, cria) um admin.

    Levanta RecuperacaoRecusada quando o pedido fere a política; nunca
    registra a senha.
    """
    email = email.strip().lower()
    problemas = password_problems(senha)
    if problemas:
        raise RecuperacaoRecusada('Senha fora da política: ' + '; '.join(problemas))

    agora = datetime.now(timezone.utc)
    user = db.query(User).filter(User.email == email).first()

    if user is None:
        if not criar:
            raise RecuperacaoRecusada(f'Usuário {email} não encontrado. Use --criar só em instalação nova.')
        if db.query(User.id).first() is not None:
            raise RecuperacaoRecusada(
                'O banco já tem usuários: --criar só provisiona instalação nova. '
                'Recupere um admin existente pelo e-mail dele.'
            )
        user = User(name='Administrador', email=email, role=UserRole.ADMIN, active=True,
                    password_hash=get_password_hash(senha))
        db.add(user)
        db.flush()
        acao, criado, reativado, revogadas = 'criado (instalação nova)', True, False, 0
    else:
        if user.role != UserRole.ADMIN:
            raise RecuperacaoRecusada(
                f'{email} não é administrador ({user.role.value}). Use a tela de usuários ou o reset por e-mail.'
            )
        precisa_reativar = user.is_deleted or not user.active
        if precisa_reativar and not reativar:
            estado = 'excluído' if user.is_deleted else 'inativo'
            raise RecuperacaoRecusada(
                f'{email} está {estado}. Reativar é uma decisão explícita: repita com --reativar.'
            )
        user.password_hash = get_password_hash(senha)
        reativado = precisa_reativar
        if reativado:
            user.is_deleted = False
            user.active = True
        revogadas = _encerrar_sessoes(db, user, agora)
        acao = 'senha redefinida' + (' e conta reativada' if reativado else '')
        criado = False

    db.add(AuditLog(
        user_id=user.id,
        user_name=f'recuperacao:{operador}'[:120],
        user_role='admin',
        method='CLI',
        path='scripts/reset_admin_senha.py',
        entity_type='user',
        entity_id=user.id,
        status_code=200,
        description=f'Recuperação de admin {email}: {acao}; sessões encerradas: {revogadas}.',
    ))
    db.commit()
    return ResultadoRecuperacao(
        user_id=user.id, email=email, criado=criado, reativado=reativado, sessoes_revogadas=revogadas,
    )
