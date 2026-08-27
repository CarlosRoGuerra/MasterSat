from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
    token_revogado,
    verify_password,
)
from app.db.session import get_db
from app.models.client import Client
from app.models.enums import ClientStatus, UserRole
from app.models.password_reset_token import PasswordResetToken
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    RegisterClientRequest,
    RegisterClientResponse,
    ResetPasswordRequest,
    TokenResponse,
    UserOut,
)

router = APIRouter()

# Refresh token: cookie httpOnly (JS não consegue ler/exfiltrar via XSS) em vez
# de corpo JSON/localStorage. Escopo de path restrito a /auth — o cookie não é
# reenviado em chamadas comuns da API (/clients, /vehicles, etc.), só onde é
# de fato lido (refresh e logout).
REFRESH_COOKIE_NAME = 'refresh_token'
REFRESH_COOKIE_PATH = f'{settings.api_v1_prefix}/auth'


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,
        # Secure exige HTTPS — desligado só em dev (http://localhost), senão o
        # navegador nunca grava o cookie e ninguém consegue logar localmente.
        secure=settings.is_production,
        samesite='strict',
        path=REFRESH_COOKIE_PATH,
        max_age=settings.refresh_token_expire_days * 86400,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)


def _issue_refresh_token(db: Session, user_id: int, family: str | None = None) -> tuple[str, str]:
    """Emite (e persiste o estado de) um refresh token novo. Retorna (token, jti).

    family=None → login novo, começa uma família. family=<existente> → é uma
    ROTAÇÃO dentro do /refresh (ver _rotate_refresh_token).
    """
    token, jti, family = create_refresh_token(str(user_id), family=family)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    db.add(RefreshToken(user_id=user_id, jti=jti, family=family, expires_at=expires_at))
    return token, jti


def _revoke_family(db: Session, family: str) -> None:
    db.query(RefreshToken).filter(
        RefreshToken.family == family,
        RefreshToken.revoked_at.is_(None),
    ).update({'revoked_at': datetime.now(timezone.utc)}, synchronize_session=False)


def _rotate_refresh_token(db: Session, token: str) -> tuple[User, str]:
    """Valida um refresh token, detecta reuso e rotaciona. Retorna (user, novo_token).

    Reuso = apresentar um jti que já tem replaced_by_jti (foi rotacionado) ou
    já está revoked_at (família comprometida/logout). É o sinal de que um
    refresh token vazado está sendo usado em paralelo ao legítimo — a família
    inteira é revogada, forçando novo login em todos os dispositivos daquela
    sessão. Sem isto, rotacionar sozinho não detecta token roubado, só atrasa
    o problema.
    """
    credenciais_invalidas = HTTPException(status_code=401, detail='Sessão expirada. Faça login novamente.')
    try:
        decoded = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        if decoded.get('type') != 'refresh':
            raise HTTPException(status_code=401, detail='Token inválido')
        user_id = decoded.get('sub')
        jti = decoded.get('jti')
        family = decoded.get('family')
        if not user_id or not jti or not family:
            raise HTTPException(status_code=401, detail='Token inválido')
    except JWTError as exc:
        raise HTTPException(status_code=401, detail='Refresh token inválido') from exc

    user = db.scalar(select(User).where(User.id == int(user_id), User.is_deleted.is_(False)))
    if not user or not user.active:
        raise credenciais_invalidas
    if token_revogado(decoded, user.tokens_valid_from):
        raise credenciais_invalidas

    row = db.scalar(select(RefreshToken).where(RefreshToken.jti == jti))
    if not row or row.user_id != user.id:
        raise credenciais_invalidas

    if row.revoked_at is not None or row.replaced_by_jti is not None:
        _revoke_family(db, row.family)
        db.commit()
        raise credenciais_invalidas

    expira_em = row.expires_at
    if expira_em.tzinfo is None:
        expira_em = expira_em.replace(tzinfo=timezone.utc)
    if expira_em < datetime.now(timezone.utc):
        raise credenciais_invalidas

    new_token, new_jti = _issue_refresh_token(db, user.id, family=family)
    row.replaced_by_jti = new_jti
    db.commit()
    return user, new_token


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
@limiter.limit('5/minute')
def login(request: Request, response: Response, payload: LoginRequest, db: Session = Depends(get_db)):
    normalized_email = payload.email.strip().lower()
    user = db.scalar(select(User).where(User.email == normalized_email, User.is_deleted.is_(False)))
    if not user or not user.active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Credenciais inválidas')
    if user.role == UserRole.CLIENT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='O acesso de clientes foi desativado. Utilize o painel administrativo.')
    role_value = user.role.value if hasattr(user.role, 'value') else str(user.role)

    refresh_token, _jti = _issue_refresh_token(db, user.id)
    db.commit()
    _set_refresh_cookie(response, refresh_token)

    return TokenResponse(access_token=create_access_token(str(user.id), name=user.name, role=role_value))


@router.post('/register-client', response_model=RegisterClientResponse)
def register_client(payload: RegisterClientRequest, db: Session = Depends(get_db)):
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail='O cadastro de clientes é realizado exclusivamente pela equipe administrativa.',
    )



@router.post('/refresh', response_model=TokenResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail='Sessão expirada. Faça login novamente.')

    user, new_refresh_token = _rotate_refresh_token(db, token)
    _set_refresh_cookie(response, new_refresh_token)

    role_value = user.role.value if hasattr(user.role, 'value') else str(user.role)
    return TokenResponse(access_token=create_access_token(str(user.id), name=user.name, role=role_value))


@router.post('/logout')
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Revoga a família do refresh token apresentado (server-side) e limpa o
    cookie. Sem isto, 'logout' era só um efeito visual do front — um cookie
    vazado antes do clique continuava válido normalmente até expirar sozinho."""
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    if token:
        try:
            decoded = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
            family = decoded.get('family')
            if family:
                _revoke_family(db, family)
                db.commit()
        except JWTError:
            pass  # cookie inválido/expirado — nada pra revogar, só limpa
    _clear_refresh_cookie(response)
    return {'message': 'Sessão encerrada.'}


@router.post('/forgot-password', response_model=ForgotPasswordResponse)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    # Resposta IDÊNTICA exista ou não o e-mail — não revela quais e-mails têm
    # conta (evita enumeração de usuários).
    generic_message = 'Se o e-mail existir, você receberá as instruções de redefinição.'

    user = db.scalar(select(User).where(User.email == payload.email, User.is_deleted.is_(False)))
    if not user:
        return ForgotPasswordResponse(message=generic_message)

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
        message=generic_message,
        # Só em modo debug (desligado em produção) o token volta no response.
        reset_token=reset_token if settings.debug_return_reset_token else None,
        expires_at=expires_at if settings.debug_return_reset_token else None,
    )


@router.post('/reset-password')
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    reset_row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token == payload.token))
    if not reset_row or reset_row.used_at is not None:
        raise HTTPException(status_code=400, detail='Token de redefinição inválido')

    # expires_at pode voltar SEM tzinfo (o driver nem sempre preserva o fuso);
    # comparar naive com aware estoura TypeError e virava 500 no lugar do 400.
    expira_em = reset_row.expires_at
    if expira_em.tzinfo is None:
        expira_em = expira_em.replace(tzinfo=timezone.utc)
    if expira_em < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail='Token de redefinição expirado')

    user = db.get(User, reset_row.user_id)
    if not user or user.is_deleted:
        raise HTTPException(status_code=404, detail='Usuário não encontrado')

    user.password_hash = get_password_hash(payload.new_password)
    # Derruba TODA sessao anterior: sem isto, um refresh token roubado seguia
    # valido por ate 7 dias depois da troca de senha — a senha nova nao
    # expulsava o invasor. tokens_valid_from cobre os access tokens já
    # emitidos (via 'iat'); a revogação abaixo cobre os refresh tokens
    # rastreados nesta tabela, reforçando o mesmo corte.
    user.tokens_valid_from = datetime.now(timezone.utc)
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user.id,
        RefreshToken.revoked_at.is_(None),
    ).update({'revoked_at': datetime.now(timezone.utc)}, synchronize_session=False)
    reset_row.used_at = datetime.now(timezone.utc)
    db.commit()

    return {'message': 'Senha redefinida com sucesso.'}


@router.get('/me', response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user
