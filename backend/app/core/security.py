from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_token(subject: str, expires_delta: timedelta, token_type: str, extra: dict[str, Any] | None = None) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    payload: dict[str, Any] = {'sub': subject, 'exp': expire, 'type': token_type}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_access_token(subject: str) -> str:
    return create_token(subject, timedelta(minutes=settings.access_token_expire_minutes), 'access')


def create_refresh_token(subject: str) -> str:
    return create_token(subject, timedelta(days=settings.refresh_token_expire_days), 'refresh')


def create_file_access_token(document_id: int, expires_hours: int = 2) -> str:
    return create_token(str(document_id), timedelta(hours=expires_hours), 'file_access')


def decode_file_access_token(token: str) -> int:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        if payload.get('type') != 'file_access':
            raise ValueError('invalid token type')
        subject = payload.get('sub')
        if subject is None:
            raise ValueError('missing subject')
        return int(subject)
    except (JWTError, ValueError, TypeError) as exc:
        raise ValueError('invalid file access token') from exc
