"""
Criptografia simétrica (Fernet) para tokens/códigos sensíveis da integração
Ailos (cooperado token, authorization code, client app token).

A chave é configurada via AILOS_TOKEN_ENCRYPTION_KEY. Para gerar uma nova:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class CryptoError(Exception):
    """Erro de configuração ou operação de criptografia de token."""


@lru_cache
def _fernet() -> Fernet:
    key = settings.ailos_token_encryption_key
    if not key:
        raise CryptoError('AILOS_TOKEN_ENCRYPTION_KEY não configurada')
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise CryptoError(f'AILOS_TOKEN_ENCRYPTION_KEY inválida: {exc}') from exc


def encrypt_token(value: str) -> str:
    """Criptografa uma string (token/code) e retorna o resultado como texto."""
    return _fernet().encrypt(value.encode('utf-8')).decode('ascii')


def decrypt_token(value: str) -> str:
    """Descriptografa uma string previamente gerada por encrypt_token."""
    try:
        return _fernet().decrypt(value.encode('ascii')).decode('utf-8')
    except InvalidToken as exc:
        raise CryptoError('Token criptografado inválido ou corrompido') from exc


def mask_token(value: str | None) -> str:
    """Mascara um valor sensível para uso em logs (mantém só os 4 últimos chars)."""
    if not value:
        return ''
    if len(value) <= 4:
        return '*' * len(value)
    return '*' * (len(value) - 4) + value[-4:]
