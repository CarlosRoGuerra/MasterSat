"""
Envio de e-mail via SMTP configurado no painel.

A configuração fica em system_settings (chave/valor); a senha é guardada
criptografada com Fernet (mesma chave dos demais segredos — ver core/crypto).
Nada de senha em texto puro no banco nem exposta pela API.

Segurança da conexão (``smtp_security``):
  none → porta simples, sem criptografia (evitar)
  tls  → STARTTLS (587, o mais comum)
  ssl  → SSL/TLS direto (465)
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import getaddresses

from sqlalchemy.orm import Session

from app.core.crypto import CryptoError, decrypt_token, encrypt_token
from app.models.system_setting import SystemSetting
from app.models.client import Client


def emails_cadastrados(cliente: Client) -> list[str]:
    """E-mail principal e adicionais do mesmo responsável, sem duplicatas."""
    return list(dict.fromkeys(
        email.strip().lower()
        for email in [cliente.email, *(cliente.extra_emails or [])]
        if email and email.strip()
    ))

# Chaves usadas em system_settings.
KEY_HOST = 'smtp_host'
KEY_PORT = 'smtp_port'
KEY_USERNAME = 'smtp_username'
KEY_PASSWORD = 'smtp_password_enc'
KEY_FROM_EMAIL = 'smtp_from_email'
KEY_FROM_NAME = 'smtp_from_name'
KEY_SECURITY = 'smtp_security'
KEY_ENABLED = 'smtp_enabled'

_ALL_KEYS = (
    KEY_HOST, KEY_PORT, KEY_USERNAME, KEY_PASSWORD,
    KEY_FROM_EMAIL, KEY_FROM_NAME, KEY_SECURITY, KEY_ENABLED,
)


class EmailConfigError(Exception):
    """SMTP mal configurado (faltando host/remetente) ou senha ilegível."""


class EmailRateLimitError(EmailConfigError):
    """Nenhuma mensagem aceita: aguardar antes de tentar novamente."""

    def __init__(self, retry_at):
        self.retry_at = retry_at
        super().__init__('Envio pausado pelo limite da conta de e-mail. Tente novamente após '
                         + retry_at.strftime('%d/%m/%Y %H:%M UTC') + '.')


def _quota_exceeded(value) -> bool:
    return 'quota' in str(value).lower() and 'exceed' in str(value).lower()


def verificar_intervalo(db: Session, config: dict, destinatario: str, *, reserve=False):
    from app.services.smtp_rate_limit import check_budget, RateLimited
    recipients = {address.lower() for _, address in getaddresses([destinatario]) if address}
    try:
        check_budget(db, config, len(recipients), reserve=reserve)
    except RateLimited as exc:
        raise EmailRateLimitError(exc.retry_at) from exc
    except ValueError as exc:
        raise EmailConfigError(str(exc)) from exc


def load_config(db: Session) -> dict:
    """Config atual (com a senha JÁ descriptografada em ``password``)."""
    rows = {
        s.key: s.value
        for s in db.query(SystemSetting).filter(SystemSetting.key.in_(_ALL_KEYS)).all()
    }
    senha = ''
    if rows.get(KEY_PASSWORD):
        try:
            senha = decrypt_token(rows[KEY_PASSWORD])
        except CryptoError:
            senha = ''
    try:
        porta = int(rows.get(KEY_PORT) or 587)
    except (TypeError, ValueError):
        porta = 587
    return {
        'host': (rows.get(KEY_HOST) or '').strip(),
        'port': porta,
        'username': (rows.get(KEY_USERNAME) or '').strip(),
        'password': senha,
        'password_set': bool(rows.get(KEY_PASSWORD)),
        'from_email': (rows.get(KEY_FROM_EMAIL) or '').strip(),
        'from_name': (rows.get(KEY_FROM_NAME) or '').strip(),
        'security': (rows.get(KEY_SECURITY) or 'tls').strip().lower(),
        'enabled': (rows.get(KEY_ENABLED) or '').strip().lower() == 'true',
    }


def _set(db: Session, key: str, value: str) -> None:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=key, value=value))


def save_config(db: Session, data: dict) -> dict:
    """Persiste a config. A senha só é trocada quando ``password`` vem preenchida."""
    _set(db, KEY_HOST, (data.get('host') or '').strip())
    _set(db, KEY_PORT, str(data.get('port') or 587))
    _set(db, KEY_USERNAME, (data.get('username') or '').strip())
    _set(db, KEY_FROM_EMAIL, (data.get('from_email') or '').strip())
    _set(db, KEY_FROM_NAME, (data.get('from_name') or '').strip())
    _set(db, KEY_SECURITY, (data.get('security') or 'tls').strip().lower())
    _set(db, KEY_ENABLED, 'true' if data.get('enabled') else 'false')
    senha = data.get('password')
    if senha:  # só sobrescreve quando o operador digitou uma nova senha
        _set(db, KEY_PASSWORD, encrypt_token(senha))
    db.commit()
    return load_config(db)


def _abrir_conexao(cfg: dict):
    ctx = ssl.create_default_context()
    if cfg['security'] == 'ssl':
        srv = smtplib.SMTP_SSL(cfg['host'], cfg['port'], context=ctx, timeout=20)
    else:
        srv = smtplib.SMTP(cfg['host'], cfg['port'], timeout=20)
        srv.ehlo()
        if cfg['security'] == 'tls':
            srv.starttls(context=ctx)
            srv.ehlo()
    if cfg['username']:
        srv.login(cfg['username'], cfg['password'])
    return srv


def enviar_email(
    db: Session,
    destinatario: str,
    assunto: str,
    corpo: str,
    html: str | None = None,
    anexo: tuple[str, bytes, str] | None = None,
    config: dict | None = None,
    anexos: list[tuple[str, bytes, str]] | None = None,
) -> None:
    """Envia um e-mail. Levanta EmailConfigError/smtplib.* em caso de falha.

    ``anexo`` é mantido para as chamadas existentes; ``anexos`` permite
    mandar boleto e NFS-e juntos no mesmo e-mail.
    """
    cfg = config or load_config(db)
    if not cfg['host'] or not cfg['from_email']:
        raise EmailConfigError('SMTP não configurado: informe ao menos o servidor e o e-mail remetente.')

    msg = EmailMessage()
    msg['From'] = f"{cfg['from_name']} <{cfg['from_email']}>" if cfg['from_name'] else cfg['from_email']
    # Envelope e quota usam a mesma lista, sem RCPT duplicado.
    destinatario = ', '.join(dict.fromkeys(address.lower() for _, address in getaddresses([destinatario]) if address))
    msg['To'] = destinatario
    msg['Subject'] = assunto
    msg.set_content(corpo)
    if html:
        msg.add_alternative(html, subtype='html')
    for nome, conteudo, content_type in ([anexo] if anexo else []) + (anexos or []):
        maintype, _, subtype = content_type.partition('/')
        msg.add_attachment(conteudo, maintype=maintype or 'application', subtype=subtype or 'octet-stream', filename=nome)

    verificar_intervalo(db, cfg, destinatario, reserve=True)
    srv = None
    try:
        srv = _abrir_conexao(cfg)
        recusados = srv.send_message(msg)
        if recusados:
            if _quota_exceeded(recusados):
                from app.services.smtp_rate_limit import pause_after_quota
                pause_after_quota(db, cfg)
            # SMTP pode aceitar alguns destinatários e recusar outros. Não
            # registrar sucesso integral nem repetir o envio automaticamente.
            raise smtplib.SMTPException(
                'Envio parcial; destinatários recusados: ' + ', '.join(recusados)
                + '. Confira os destinatários antes de reenviar.'
            )
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError) as exc:
        # Recusa explícita de TODOS os destinatários ou do DATA: nenhuma
        # mensagem aceita. Só esta falha de quota pode voltar à fila.
        if _quota_exceeded(exc):
            from app.services.smtp_rate_limit import pause_after_quota
            raise EmailRateLimitError(pause_after_quota(db, cfg)) from exc
        raise
    finally:
        try:
            if srv is not None:
                srv.quit()
        except Exception:  # pragma: no cover - fechamento best-effort
            pass
