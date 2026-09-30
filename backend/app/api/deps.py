import secrets
from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import token_revogado
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.refresh_token import RefreshToken
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f'{settings.api_v1_prefix}/auth/login')


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Não foi possível validar as credenciais',
        headers={'WWW-Authenticate': 'Bearer'},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        user_id = payload.get('sub')
        token_type = payload.get('type')
        session_id = payload.get('sid')
        # Sem 'sid' = access emitido antes da Fase 01 (ou forjado sem sessão).
        # O frontend responde ao 401 renovando pelo cookie de refresh, que
        # continua válido, e recebe um access com 'sid' — transparente.
        if user_id is None or token_type != 'access' or not session_id:
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc

    user = db.get(User, int(user_id))
    if not user or not user.active or user.is_deleted:
        raise credentials_exception
    # Sessao revogada (troca de senha) — o token e valido criptograficamente,
    # mas foi emitido antes do corte.
    if token_revogado(payload, user.tokens_valid_from):
        raise credentials_exception
    # Sessão encerrada (logout desta sessão, reuso detectado, troca de senha):
    # a família não tem mais nenhum refresh não revogado deste usuário.
    sessao_ativa = db.scalar(
        select(RefreshToken.id)
        .where(
            RefreshToken.family == session_id,
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
        )
        .limit(1)
    )
    if sessao_ativa is None:
        raise credentials_exception
    return user


def require_roles(*roles: UserRole) -> Callable[[User], User]:
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Acesso não autorizado para este perfil')
        return current_user

    return dependency


def require_api_key(x_api_key: str | None = Header(default=None, alias='X-API-Key')) -> None:
    """Autenticação máquina-a-máquina para integrações externas (ex.: CobraZap).

    Espera o header ``X-API-Key`` igual a ``settings.integration_api_key``.
    Sem chave configurada no servidor → 503 (integração desativada).
    """
    expected = settings.integration_api_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Integração externa não configurada (INTEGRATION_API_KEY ausente).',
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='API key inválida ou ausente.',
            headers={'WWW-Authenticate': 'ApiKey'},
        )
