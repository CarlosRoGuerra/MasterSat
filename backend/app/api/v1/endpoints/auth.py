from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token, get_password_hash, verify_password
from app.db.session import get_db
from app.models.client import Client
from app.models.enums import ClientStatus, UserRole
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    RefreshRequest,
    RegisterClientRequest,
    RegisterClientResponse,
    ResetPasswordRequest,
    TokenResponse,
    UserOut,
)

router = APIRouter()


def build_full_address(payload: RegisterClientRequest) -> str:
    parts = [
        payload.address_line,
        f'nº {payload.address_number}',
        payload.address_complement or None,
        payload.neighborhood,
        f'{payload.city}/{payload.state}',
        f'CEP {payload.zip_code}',
    ]
    return ', '.join(part for part in parts if part)


@router.post('/login', response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    normalized_email = payload.email.strip().lower()
    user = db.scalar(select(User).where(User.email == normalized_email, User.is_deleted.is_(False)))
    if not user or not user.active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Credenciais inválidas')
    if user.role == UserRole.CLIENT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='O acesso de clientes foi desativado. Utilize o painel administrativo.')
    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
    )


@router.post('/register-client', response_model=RegisterClientResponse)
def register_client(payload: RegisterClientRequest, db: Session = Depends(get_db)):
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail='O cadastro de clientes é realizado exclusivamente pela equipe administrativa.',
    )



@router.post('/refresh', response_model=TokenResponse)
def refresh(payload: RefreshRequest):
    try:
        decoded = jwt.decode(payload.refresh_token, settings.secret_key, algorithms=[settings.algorithm])
        if decoded.get('type') != 'refresh':
            raise HTTPException(status_code=401, detail='Token inválido')
        user_id = decoded.get('sub')
    except JWTError as exc:
        raise HTTPException(status_code=401, detail='Refresh token inválido') from exc

    return TokenResponse(
        access_token=create_access_token(str(user_id)),
        refresh_token=create_refresh_token(str(user_id)),
    )


@router.post('/forgot-password', response_model=ForgotPasswordResponse)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email, User.is_deleted.is_(False)))
    if not user:
        return ForgotPasswordResponse(message='Se o e-mail existir, você receberá as instruções de redefinição.')

    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).update({'used_at': datetime.now(timezone.utc)})

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.password_reset_expire_minutes)
    reset_token = uuid4().hex
    token_row = PasswordResetToken(user_id=user.id, token=reset_token, expires_at=expires_at)
    db.add(token_row)
    db.commit()

    return ForgotPasswordResponse(
        message='Solicitação recebida. Use o token abaixo para redefinir sua senha.',
        reset_token=reset_token if settings.debug_return_reset_token else None,
        expires_at=expires_at if settings.debug_return_reset_token else None,
    )


@router.post('/reset-password')
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    reset_row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token == payload.token))
    if not reset_row or reset_row.used_at is not None:
        raise HTTPException(status_code=400, detail='Token de redefinição inválido')

    if reset_row.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail='Token de redefinição expirado')

    user = db.get(User, reset_row.user_id)
    if not user or user.is_deleted:
        raise HTTPException(status_code=404, detail='Usuário não encontrado')

    user.password_hash = get_password_hash(payload.new_password)
    reset_row.used_at = datetime.now(timezone.utc)
    db.commit()

    return {'message': 'Senha redefinida com sucesso.'}


@router.get('/me', response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user
