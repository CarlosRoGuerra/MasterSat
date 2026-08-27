from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_token(subject: str, expires_delta: timedelta, token_type: str, extra: dict[str, Any] | None = None) -> str:
    agora = datetime.now(timezone.utc)
    expire = agora + expires_delta
    # 'iat' e o que permite revogar sessoes: comparado com User.tokens_valid_from
    # em token_revogado(). Sem ele nao ha como distinguir um token emitido antes
    # da troca de senha de um emitido depois.
    payload: dict[str, Any] = {'sub': subject, 'exp': expire, 'iat': agora, 'type': token_type}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def token_revogado(payload: dict[str, Any], tokens_valid_from: datetime | None) -> bool:
    """True se o token foi emitido antes do corte de revogacao do usuario.

    Chamado na validacao de todo access token e no /refresh.

    O corte e truncado ao segundo porque 'iat' e um timestamp INTEIRO. Sem
    truncar, o token que o usuario recebe ao logar com a senha nova seria
    recusado (iat 10 < corte 10.5) e ele nao conseguiria entrar. O preco e
    uma janela de ate 1 segundo em que um token antigo emitido no mesmo
    segundo do reset ainda passa — irrelevante diante de quebrar o login.
    """
    if tokens_valid_from is None:
        return False

    corte = tokens_valid_from
    if corte.tzinfo is None:
        corte = corte.replace(tzinfo=timezone.utc)
    corte = corte.replace(microsecond=0)

    iat = payload.get('iat')
    if iat is None:
        # Token anterior a introducao do 'iat'. Havendo um corte gravado, a
        # intencao era derrubar as sessoes antigas — entao recusa.
        return True
    try:
        emitido_em = datetime.fromtimestamp(float(iat), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return True
    return emitido_em < corte


def create_access_token(subject: str, name: str | None = None, role: str | None = None) -> str:
    extra: dict[str, str] = {}
    if name:
        extra['name'] = name
    if role:
        extra['role'] = role
    return create_token(subject, timedelta(minutes=settings.access_token_expire_minutes), 'access', extra or None)


def create_refresh_token(subject: str, family: str | None = None) -> tuple[str, str, str]:
    """Emite um refresh token com 'jti' (identidade única desta emissão) e
    'family' (uuid estável em toda a cadeia de rotações de um mesmo login).

    family=None inicia uma família nova (login). Passar a family existente
    é o que caracteriza uma ROTAÇÃO (ver /auth/refresh) — permite detectar
    reuso: se um jti já substituído voltar a ser apresentado, a família
    inteira é revogada (endpoints/auth.py).

    Retorna (token, jti, family) — o chamador persiste jti/family em
    RefreshToken (models/refresh_token.py) para poder validar/rotacionar/
    revogar depois; o token em si não fica salvo, só seu jti.
    """
    jti = uuid4().hex
    family = family or uuid4().hex
    token = create_token(
        subject,
        timedelta(days=settings.refresh_token_expire_days),
        'refresh',
        {'jti': jti, 'family': family},
    )
    return token, jti, family


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
