from datetime import datetime, timedelta, timezone
from typing import Any
from jose import jwt
from passlib.context import CryptContext
from app.core.config import settings
pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')
def verify_password(plain_password: str, hashed_password: str) -> bool: return pwd_context.verify(plain_password, hashed_password)
def get_password_hash(password: str) -> str: return pwd_context.hash(password)
def create_token(subject: str, expires_delta: timedelta, token_type: str) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    payload: dict[str, Any] = {'sub': subject, 'exp': expire, 'type': token_type}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
def create_access_token(subject: str) -> str: return create_token(subject, timedelta(minutes=settings.access_token_expire_minutes), 'access')
def create_refresh_token(subject: str) -> str: return create_token(subject, timedelta(days=settings.refresh_token_expire_days), 'refresh')
