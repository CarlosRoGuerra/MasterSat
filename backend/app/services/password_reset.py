"""Redefinição de senha por e-mail (SEC-05).

Antes: /forgot-password gravava o token e respondia "você receberá as
instruções", mas nenhum e-mail saía — e o token ficava em texto puro no
banco. Agora:

- o token (32 bytes aleatórios) só existe no e-mail; o banco guarda o SHA-256;
- a entrega usa o SMTP configurado no painel (services/email_smtp.py), em
  segundo plano (depois da resposta), com até PASSWORD_RESET_EMAIL_ATTEMPTS
  tentativas. O desfecho fica em sent_at/delivery_attempts/delivery_error;
- a resposta HTTP é a mesma para e-mail existente, inexistente, inativo ou
  limitado — e o tempo de resposta não inclui o SMTP;
- limites: por origem (slowapi, RATE_LIMIT_FORGOT_PASSWORD) e por conta
  (PASSWORD_RESET_MAX_PER_WINDOW pedidos em PASSWORD_RESET_WINDOW_MINUTES);
  acima disso nada é emitido nem invalidado, evitando que terceiros fiquem
  anulando o link legítimo;
- log nunca contém token, link nem senha — só o id do usuário.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import UserRole
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User

logger = logging.getLogger('uvicorn.error')

MENSAGEM_GENERICA = 'Se o e-mail existir, você receberá as instruções de redefinição.'
_BACKOFF_SEGUNDOS = 2


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _elegivel(user: User | None) -> bool:
    return bool(user and user.active and not user.is_deleted and user.role != UserRole.CLIENT)


def _limite_da_conta_atingido(db: Session, user_id: int, agora: datetime) -> bool:
    limite = settings.password_reset_max_per_window
    recentes = db.scalars(
        select(PasswordResetToken.created_at)
        .where(PasswordResetToken.user_id == user_id)
        .order_by(PasswordResetToken.id.desc())
        .limit(limite)
    ).all()
    janela = agora - timedelta(minutes=settings.password_reset_window_minutes)
    return len(recentes) >= limite and all(
        (_aware(c) or agora) >= janela for c in recentes
    )


def solicitar_reset(db: Session, email: str) -> tuple[PasswordResetToken, User, str] | None:
    """Emite um pedido de reset se a conta for elegível e estiver dentro do
    limite. Devolve (linha, usuário, token em texto puro) ou None.

    O texto puro sai daqui só para o e-mail (e, em DEBUG_RETURN_RESET_TOKEN,
    para a resposta de desenvolvimento)."""
    user = db.scalar(select(User).where(User.email == email, User.is_deleted.is_(False)))
    if not _elegivel(user):
        return None

    agora = datetime.now(timezone.utc)
    if _limite_da_conta_atingido(db, user.id, agora):
        logger.warning('Reset de senha: limite por conta atingido (usuário %s); pedido ignorado.', user.id)
        return None

    # Um link por vez: pedir de novo anula o anterior ainda não usado.
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=agora)
        .execution_options(synchronize_session=False)
    )
    token = secrets.token_urlsafe(32)
    row = PasswordResetToken(
        user_id=user.id,
        token_hash=hash_reset_token(token),
        expires_at=agora + timedelta(minutes=settings.password_reset_expire_minutes),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, user, token


def _mensagem(nome: str, link: str) -> tuple[str, str, str]:
    app_name = settings.app_name
    minutos = settings.password_reset_expire_minutes
    assunto = f'Redefinição de senha — {app_name}'
    texto = (
        f'Olá, {nome}.\n\n'
        f'Recebemos um pedido para redefinir a senha do seu acesso ao {app_name}.\n'
        f'Para criar uma nova senha, abra o link abaixo. Ele vale por {minutos} minutos '
        f'e só pode ser usado uma vez:\n\n{link}\n\n'
        'Se você não pediu a redefinição, ignore este e-mail: sua senha atual continua valendo.\n'
    )
    html = (
        f'<p>Olá, {nome}.</p>'
        f'<p>Recebemos um pedido para redefinir a senha do seu acesso ao {app_name}.</p>'
        f'<p><a href="{link}">Criar nova senha</a> — o link vale por {minutos} minutos '
        'e só pode ser usado uma vez.</p>'
        '<p>Se você não pediu a redefinição, ignore este e-mail: sua senha atual continua valendo.</p>'
    )
    return assunto, texto, html


def entregar_email_reset(token_id: int, token: str) -> None:
    """Tarefa em segundo plano: envia o link e registra o desfecho.

    Sessão própria (a da requisição já foi fechada). Nunca levanta exceção
    e nunca registra token/link em log ou no banco."""
    from html import escape

    from app.db import session as db_session
    from app.services.email_smtp import EmailConfigError, enviar_email

    db = db_session.SessionLocal()
    try:
        row = db.get(PasswordResetToken, token_id)
        user = db.get(User, row.user_id) if row else None
        if row is None or user is None:
            return
        link = f"{settings.frontend_url.rstrip('/')}/resetar-senha?token={quote(token, safe='')}"
        assunto, texto, html = _mensagem(escape(user.name or ''), link)
        tentativas = max(1, settings.password_reset_email_attempts)
        for tentativa in range(1, tentativas + 1):
            row.delivery_attempts = tentativa
            try:
                enviar_email(db, user.email, assunto, texto, html=html)
            except EmailConfigError:
                row.delivery_error = 'smtp_nao_configurado'
                db.commit()
                logger.error(
                    'Reset de senha pedido (usuário %s), mas o SMTP não está configurado: '
                    'e-mail NÃO enviado. Configure em Configurações > E-mail.',
                    user.id,
                )
                return
            except Exception as exc:  # noqa: BLE001 — registra a categoria e tenta de novo
                row.delivery_error = f'{type(exc).__name__}'[:255]
                db.commit()
                if tentativa < tentativas:
                    logger.warning(
                        'Reset de senha: falha ao enviar e-mail (usuário %s, tentativa %s/%s: %s).',
                        user.id, tentativa, tentativas, type(exc).__name__,
                    )
                    time.sleep(_BACKOFF_SEGUNDOS * tentativa)
                    continue
                logger.error(
                    'Reset de senha: e-mail NÃO enviado após %s tentativas (usuário %s, último erro: %s).',
                    tentativas, user.id, type(exc).__name__,
                )
                return
            row.sent_at = datetime.now(timezone.utc)
            row.delivery_error = None
            db.commit()
            logger.info('Reset de senha: e-mail enviado (usuário %s).', user.id)
            return
    except Exception:  # noqa: BLE001 — tarefa de fundo não pode derrubar o worker
        logger.exception('Reset de senha: erro inesperado na entrega (pedido %s).', token_id)
    finally:
        db.close()


def consumir_token(db: Session, token: str) -> tuple[PasswordResetToken, User]:
    """Valida e consome (uso único, compare-and-set) um token de reset.
    Levanta ValueError('invalido'|'expirado'). Não faz commit."""
    row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_reset_token(token)))
    if row is None or row.used_at is not None:
        raise ValueError('invalido')
    if _aware(row.expires_at) < datetime.now(timezone.utc):
        raise ValueError('expirado')
    user = db.get(User, row.user_id)
    if not _elegivel(user):
        raise ValueError('invalido')
    consumido = db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.id == row.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    ).rowcount
    if consumido != 1:
        db.rollback()
        raise ValueError('invalido')
    return row, user
