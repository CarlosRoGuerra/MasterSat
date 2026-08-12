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

from sqlalchemy.orm import Session

from app.core.crypto import CryptoError, decrypt_token, encrypt_token
from app.models.system_setting import SystemSetting

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
    config: dict | None = None,
) -> None:
    """Envia um e-mail. Levanta EmailConfigError/smtplib.* em caso de falha."""
    cfg = config or load_config(db)
    if not cfg['host'] or not cfg['from_email']:
        raise EmailConfigError('SMTP não configurado: informe ao menos o servidor e o e-mail remetente.')

    msg = EmailMessage()
    msg['From'] = f"{cfg['from_name']} <{cfg['from_email']}>" if cfg['from_name'] else cfg['from_email']
    msg['To'] = destinatario
    msg['Subject'] = assunto
    msg.set_content(corpo)
    if html:
        msg.add_alternative(html, subtype='html')

    srv = _abrir_conexao(cfg)
    try:
        srv.send_message(msg)
    finally:
        try:
            srv.quit()
        except Exception:  # pragma: no cover - fechamento best-effort
            pass
