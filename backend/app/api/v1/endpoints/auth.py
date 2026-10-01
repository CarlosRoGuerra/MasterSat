from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.core.security import (
    JWTError,
    decode_token,
    create_access_token,
    create_refresh_token,
    get_password_hash,
    token_revogado,
    verify_password,
)
from app.db.session import get_db
from app.models.client import Client
from app.models.enums import ClientStatus, UserRole
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.password_reset import (
    MENSAGEM_GENERICA,
    consumir_token,
    entregar_email_reset,
    solicitar_reset,
)
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


def _issue_refresh_token(db: Session, user_id: int, family: str | None = None) -> tuple[str, str, str]:
    """Emite (e persiste o estado de) um refresh token novo. Retorna (token, jti, family).

    family=None → login novo, começa uma família. family=<existente> → é uma
    ROTAÇÃO dentro do /refresh (ver _rotate_refresh_token).
    """
    token, jti, family = create_refresh_token(str(user_id), family=family)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    db.add(RefreshToken(user_id=user_id, jti=jti, family=family, expires_at=expires_at))
    return token, jti, family


def _revoke_family(db: Session, family: str) -> None:
    db.query(RefreshToken).filter(
        RefreshToken.family == family,
        RefreshToken.revoked_at.is_(None),
    ).update({'revoked_at': datetime.now(timezone.utc)}, synchronize_session=False)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _refresh_ja_usado(db: Session, row_id: int, family: str) -> HTTPException:
    """Decide o que fazer com um refresh que já foi trocado por um sucessor.

    - rotacionado há menos de REFRESH_REUSE_GRACE_SECONDS: duas abas do mesmo
      navegador renovaram juntas com o mesmo cookie. Uma venceu; esta perde
      com 409 — nada é emitido e nada é revogado. O navegador já recebeu o
      cookie do vencedor, então a aba tenta de novo e segue logada.
    - depois disso (ou revogado): reuso de token antigo = sinal de vazamento.
      A família inteira é revogada e todos daquela sessão fazem novo login.
    """
    row = db.get(RefreshToken, row_id)
    db.refresh(row)
    rotated_at = _aware(row.rotated_at)
    if (
        row.revoked_at is None
        and rotated_at is not None
        and datetime.now(timezone.utc) - rotated_at <= timedelta(seconds=settings.refresh_reuse_grace_seconds)
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Sessão renovada em outra aba. Tente novamente.',
        )
    _revoke_family(db, family)
    db.commit()
    return HTTPException(status_code=401, detail='Sessão expirada. Faça login novamente.')


def _rotate_refresh_token(db: Session, token: str) -> tuple[User, str, str]:
    """Valida um refresh token, detecta reuso e rotaciona. Retorna (user, novo_token, family).

    Reuso = apresentar um jti que já tem replaced_by_jti (foi rotacionado) ou
    já está revoked_at (família comprometida/logout). É o sinal de que um
    refresh token vazado está sendo usado em paralelo ao legítimo — a família
    inteira é revogada, forçando novo login em todos os dispositivos daquela
    sessão. Sem isto, rotacionar sozinho não detecta token roubado, só atrasa
    o problema.

    Consumo único (SEC-02): o pai é marcado com um UPDATE condicional
    (compare-and-set em replaced_by_jti IS NULL). No PostgreSQL, duas
    rotações simultâneas do mesmo token se serializam nessa linha: a segunda
    espera o commit da primeira, reavalia o WHERE e altera 0 linhas — só uma
    ganha sucessor. Antes, as duas liam "não usado" e ambas emitiam filhos
    válidos (bifurcação da sessão).
    """
    credenciais_invalidas = HTTPException(status_code=401, detail='Sessão expirada. Faça login novamente.')
    try:
        decoded = decode_token(token)
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
        raise _refresh_ja_usado(db, row.id, row.family)

    if _aware(row.expires_at) < datetime.now(timezone.utc):
        raise credenciais_invalidas

    new_token, new_jti, family = create_refresh_token(str(user.id), family=row.family)
    agora = datetime.now(timezone.utc)
    consumido = db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.id == row.id,
            RefreshToken.replaced_by_jti.is_(None),
            RefreshToken.revoked_at.is_(None),
        )
        .values(replaced_by_jti=new_jti, rotated_at=agora)
        .execution_options(synchronize_session=False)
    ).rowcount
    if consumido != 1:
        # Outra requisição consumiu este token entre o SELECT e o UPDATE.
        db.rollback()
        raise _refresh_ja_usado(db, row.id, row.family)

    db.add(RefreshToken(
        user_id=user.id,
        jti=new_jti,
        family=family,
        expires_at=agora + timedelta(days=settings.refresh_token_expire_days),
    ))
    db.commit()
    return user, new_token, family


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

    refresh_token, _jti, family = _issue_refresh_token(db, user.id)
    db.commit()
    _set_refresh_cookie(response, refresh_token)

    return TokenResponse(access_token=create_access_token(
        str(user.id), name=user.name, role=role_value, session_id=family,
    ))


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

    user, new_refresh_token, family = _rotate_refresh_token(db, token)
    _set_refresh_cookie(response, new_refresh_token)

    role_value = user.role.value if hasattr(user.role, 'value') else str(user.role)
    return TokenResponse(access_token=create_access_token(
        str(user.id), name=user.name, role=role_value, session_id=family,
    ))


def _sessao_do_token(token: str | None, tipo: str) -> str | None:
    """Família (sessão) de um refresh ('family') ou access ('sid') assinado.
    Token inválido/expirado → None: não há o que revogar."""
    if not token:
        return None
    try:
        decoded = decode_token(token)
    except JWTError:
        return None
    if decoded.get('type') != tipo:
        return None
    return decoded.get('family' if tipo == 'refresh' else 'sid')


@router.post('/logout')
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Encerra ESTA sessão (SEC-06): revoga a família do refresh token e,
    com ela, todo access token emitido para a mesma sessão ('sid') — que
    passa a levar 401 na hora, não só quando expirar. Outros dispositivos
    do mesmo usuário continuam logados; para derrubar todos, troque a senha.

    A sessão vem do cookie de refresh e, na falta dele, do Bearer enviado.
    Sempre 200: logout não revela se o token existia."""
    families = {
        _sessao_do_token(request.cookies.get(REFRESH_COOKIE_NAME), 'refresh'),
    }
    auth_header = request.headers.get('authorization') or ''
    if auth_header.lower().startswith('bearer '):
        families.add(_sessao_do_token(auth_header[7:].strip(), 'access'))
    families.discard(None)
    for family in families:
        _revoke_family(db, family)
    if families:
        db.commit()
    _clear_refresh_cookie(response)
    return {'message': 'Sessão encerrada.'}


@router.post('/forgot-password', response_model=ForgotPasswordResponse)
@limiter.limit(settings.rate_limit_forgot_password)
def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Envia o link de redefinição por e-mail (SEC-05).

    Resposta IDÊNTICA para e-mail existente, inexistente, inativo ou acima
    do limite por conta — não revela quais e-mails têm conta. O envio roda
    depois da resposta (services/password_reset.py), então o SMTP também
    não altera o tempo de resposta."""
    emitido = solicitar_reset(db, payload.email)
    if emitido is None:
        return ForgotPasswordResponse(message=MENSAGEM_GENERICA)

    row, _user, token = emitido
    background_tasks.add_task(entregar_email_reset, row.id, token)
    return ForgotPasswordResponse(
        message=MENSAGEM_GENERICA,
        # Só em modo debug (desligado em produção) o token volta no response.
        reset_token=token if settings.debug_return_reset_token else None,
        expires_at=row.expires_at if settings.debug_return_reset_token else None,
    )


@router.post('/reset-password')
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Troca a senha com o token do e-mail. Uso único: o consumo é um UPDATE
    condicional, então dois envios simultâneos do mesmo token não trocam a
    senha duas vezes. Conta excluída/inativa não é reativada por aqui."""
    try:
        _row, user = consumir_token(db, payload.token)
    except ValueError as exc:
        if str(exc) == 'expirado':
            raise HTTPException(status_code=400, detail='Token de redefinição expirado') from exc
        raise HTTPException(status_code=400, detail='Token de redefinição inválido') from exc

    user.password_hash = get_password_hash(payload.new_password)
    # Derruba TODA sessao anterior: sem isto, um refresh token roubado seguia
    # valido por ate 7 dias depois da troca de senha — a senha nova nao
    # expulsava o invasor. tokens_valid_from cobre os access tokens já
    # emitidos (via 'iat'); a revogação abaixo cobre os refresh tokens
    # rastreados nesta tabela e, pelo 'sid', os access das mesmas sessões.
    user.tokens_valid_from = datetime.now(timezone.utc)
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user.id,
        RefreshToken.revoked_at.is_(None),
    ).update({'revoked_at': datetime.now(timezone.utc)}, synchronize_session=False)
    db.commit()

    return {'message': 'Senha redefinida com sucesso.'}


@router.get('/me', response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user
