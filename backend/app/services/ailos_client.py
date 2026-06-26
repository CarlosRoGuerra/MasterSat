"""
Cliente HTTP + ciclo de vida de tokens da API de Cobrança Bancária Ailos.

Modelo de autenticação (2 níveis):
  1. Token de aplicação ("client token"): OAuth2 client_credentials no
     APIM/WSO2 (``AILOS_APIM_BASE_URL``). Enviado no header padrão do WSO2
     ``Authorization: Bearer <client_token>`` (confirmado contra o sandbox
     real — ``Authentication`` retorna 401 "Missing Credentials" do gateway).
  2. Token de cooperado: obtido via fluxo de autorização (``/connect`` →
     navegador → ``/callback``), enviado no header ``x-ailos-authentication``.

Integração singleton: existe no máximo 1 linha em ``ailos_integrations``
(o cooperado MasterSat) e 1 linha em ``ailos_client_tokens`` por ambiente
(``AILOS_ENV``).

Fluxo de autorização do cooperado (conforme "Implementação de aplicativos
clients com acesso às APIs Ailos"):
  1. ``POST {APIM}/ailos/identity/api/v1/autenticacao/login/obter/id`` com
     header ``Authorization: Bearer <client_token>`` e corpo JSON
     ``{"ailosApiKeyDeveloper", "state", "urlCallback"}`` → retorna o ``id``
     do cooperado (texto puro).
  2. URL de login = ``GET {APIM}/ailos/identity/api/v1/login/index?id=<id>``
     — aberta manualmente pelo admin no navegador.
  3. A Ailos chama ``POST {AILOS_CALLBACK_URL}`` com ``{"state", "code"}``;
     o "code" é tratado como o próprio token de cooperado (usado em
     ``x-ailos-authentication``).
  4. ``refresh_cooperado_token`` troca esse valor por um novo via
     ``GET {APIM}/ailos/identity/api/v1/autenticacao/token/refresh?code=``.

Ponto de ajuste documentado (assunção feita a partir da documentação):
  - ``Authorization: Basic base64(client_id:client_secret)`` em
    ``fetch_client_token`` — padrão OAuth2/WSO2 client_credentials. Se o
    sandbox exigir outro formato, ajustar apenas a montagem de ``basic`` ali.
"""
from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt_token, encrypt_token, mask_token
from app.models.ailos_api_log import AilosApiLog
from app.models.ailos_client_token import AilosClientToken
from app.models.ailos_integration import AilosIntegration

# ---------------------------------------------------------------------------
# Caminhos (relativos às bases configuradas)
# ---------------------------------------------------------------------------
_PATH_TOKEN = '/token'                                                          # base apim — client_credentials
_PATH_COOPERADO_OBTER_ID = '/ailos/identity/api/v1/autenticacao/login/obter/id'  # base apim — POST, JSON body, retorna id (text/plain)
_PATH_COOPERADO_AUTORIZAR = '/ailos/identity/api/v1/login/index'                # base apim — GET ?id={id}
_PATH_COOPERADO_TOKEN_REFRESH = '/ailos/identity/api/v1/autenticacao/token/refresh'  # base apim — GET ?code=

_TOKEN_REFRESH_MARGIN = timedelta(seconds=60)
# O JWT do cooperado vive 30 min (Cartilha jan/26, p.26) e NÃO pode ser
# renovado após expirar — então renovamos proativamente com folga.
_COOPERADO_TOKEN_LIFETIME = timedelta(minutes=30)
# Renova com folga: qualquer chamada faltando < 10 min para expirar já renova
# (o keepalive em background renova a cada 20 min, então normalmente nem chega
# perto). Evita o token morrer entre uma operação e outra.
_COOPERADO_REFRESH_MARGIN = timedelta(minutes=10)
_MAX_ATTEMPTS = 3

# Código de falha do WSO2 para access token expirado/inválido (Cartilha p.18).
_WSO2_INVALID_CREDENTIALS_CODE = '900901'

_SENSITIVE_KEYS = ('token', 'code', 'senha', 'password', 'secret', 'authorization', 'authentication')


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AilosError(Exception):
    """Erro de configuração/estado da integração — nenhuma chamada à API foi feita."""


class AilosApiError(Exception):
    """Erro retornado pela API Ailos (HTTP >= 400, exceto o caso "processando")."""

    def __init__(self, status_code: int, ailos_message: str | None, ailos_code: str | None, friendly_message: str):
        self.status_code = status_code
        self.ailos_message = ailos_message
        self.ailos_code = ailos_code
        self.friendly_message = friendly_message
        super().__init__(friendly_message)


@dataclass
class AilosResponse:
    status_code: int
    json: dict | list | None
    content: bytes
    headers: dict
    processing: bool = False


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _is_json_response(resp: requests.Response) -> bool:
    return 'application/json' in (resp.headers.get('Content-Type') or '')


def _extract_message(body: dict | list | None) -> str | None:
    if body is None:
        return None
    if isinstance(body, list):
        body = body[0] if body else None
    if not isinstance(body, dict):
        return None
    for key in ('mensagem', 'message', 'descricao', 'description', 'detail', 'error_description'):
        value = body.get(key)
        if value:
            return str(value)
    nested = body.get('errors') or body.get('erros')
    if isinstance(nested, list) and nested:
        return _extract_message(nested[0]) if isinstance(nested[0], dict) else str(nested[0])
    return None


def _extract_code(body: dict | list | None) -> str | None:
    if body is None:
        return None
    if isinstance(body, list):
        body = body[0] if body else None
    if not isinstance(body, dict):
        return None
    for key in ('codigo', 'code', 'errorCode'):
        value = body.get(key)
        if value:
            return str(value)
    nested = body.get('errors') or body.get('erros')
    if isinstance(nested, list) and nested and isinstance(nested[0], dict):
        return _extract_code(nested[0])
    return None


def _mask_payload(value):
    """Mascara recursivamente valores de chaves sensíveis (token/code/senha/etc.)."""
    if isinstance(value, dict):
        masked = {}
        for k, v in value.items():
            if isinstance(v, str) and any(s in k.lower() for s in _SENSITIVE_KEYS):
                masked[k] = mask_token(v)
            else:
                masked[k] = _mask_payload(v)
        return masked
    if isinstance(value, list):
        return [_mask_payload(v) for v in value]
    return value


def _log_call(
    db: Session,
    method: str,
    path: str,
    request_payload,
    response_payload,
    status_code: int | None,
    success: bool,
    error_message: str | None,
    correlation_id: str | None,
    billing_id: int | None,
) -> None:
    db.add(AilosApiLog(
        endpoint=path,
        method=method.upper(),
        request_payload=_mask_payload(request_payload) if request_payload is not None else None,
        response_payload=_mask_payload(response_payload) if response_payload is not None else None,
        status_code=status_code,
        success=success,
        error_message=error_message,
        correlation_id=correlation_id,
        billing_id=billing_id,
    ))
    db.commit()


def _get_or_create_integration(db: Session) -> AilosIntegration:
    integration = db.query(AilosIntegration).order_by(AilosIntegration.id.asc()).first()
    if integration is None:
        integration = AilosIntegration(
            numero_convenio=settings.ailos_numero_convenio,
            codigo_carteira=settings.ailos_default_carteira,
            status='pending',
        )
        db.add(integration)
        db.flush()
    return integration


# ---------------------------------------------------------------------------
# Token de aplicação (client_credentials)
# ---------------------------------------------------------------------------

def fetch_client_token(db: Session) -> AilosClientToken:
    """Obtém um novo token de aplicação junto ao APIM/WSO2 e persiste criptografado."""
    if not settings.ailos_client_id or not settings.ailos_client_secret:
        raise AilosError('AILOS_CLIENT_ID/AILOS_CLIENT_SECRET não configurados')
    if not settings.ailos_apim_base_url:
        raise AilosError('AILOS_APIM_BASE_URL não configurado')

    basic = base64.b64encode(
        f'{settings.ailos_client_id}:{settings.ailos_client_secret}'.encode('utf-8')
    ).decode('ascii')

    try:
        resp = requests.post(
            f'{settings.ailos_apim_base_url}{_PATH_TOKEN}',
            headers={
                'Authorization': f'Basic {basic}',
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
            },
            data={'grant_type': 'client_credentials'},
            timeout=settings.ailos_timeout_seconds,
        )
    except requests.RequestException as exc:
        raise AilosError(f'Falha de conexão ao obter token de aplicação Ailos: {exc}') from exc

    if resp.status_code >= 400:
        raise AilosApiError(
            status_code=resp.status_code,
            ailos_message=resp.text[:500],
            ailos_code=None,
            friendly_message='Falha ao autenticar aplicação na Ailos (client_credentials)',
        )

    try:
        body = resp.json()
    except ValueError as exc:
        raise AilosApiError(
            status_code=resp.status_code,
            ailos_message=resp.text[:500],
            ailos_code=None,
            friendly_message='Resposta de token Ailos não é JSON válido',
        ) from exc

    access_token = body.get('access_token')
    if not access_token:
        raise AilosApiError(
            status_code=resp.status_code,
            ailos_message=str(body)[:500],
            ailos_code=None,
            friendly_message='Resposta de token Ailos sem access_token',
        )

    expires_in = int(body.get('expires_in') or 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    token_row = db.query(AilosClientToken).filter_by(environment=settings.ailos_env).first()
    if token_row is None:
        token_row = AilosClientToken(environment=settings.ailos_env)
        db.add(token_row)

    token_row.access_token_encrypted = encrypt_token(access_token)
    token_row.token_type = body.get('token_type')
    token_row.scope = body.get('scope')
    token_row.expires_at = expires_at
    db.commit()
    db.refresh(token_row)
    return token_row


def refresh_client_token(db: Session) -> str:
    """Força a renovação do token de aplicação (ignora cache)."""
    token_row = fetch_client_token(db)
    return decrypt_token(token_row.access_token_encrypted)


def get_valid_client_token(db: Session) -> str:
    """Retorna um token de aplicação válido, buscando/renovando se necessário."""
    token_row = db.query(AilosClientToken).filter_by(environment=settings.ailos_env).first()
    if token_row is None:
        token_row = fetch_client_token(db)
    else:
        expires_at = token_row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at - datetime.now(timezone.utc) < _TOKEN_REFRESH_MARGIN:
            token_row = fetch_client_token(db)
    return decrypt_token(token_row.access_token_encrypted)


# ---------------------------------------------------------------------------
# Token de cooperado (autorização)
# ---------------------------------------------------------------------------

def _obter_id_cooperado(db: Session, state: str) -> str:
    """
    Chama o endpoint ``obter/id`` da Ailos com o client token e retorna o id do
    cooperado (texto puro). Renova o client token uma vez e tenta de novo se o
    WSO2 responder 900901 (token de aplicação inválido/expirado) — evita o erro
    quando o token em cache foi invalidado pelo gateway.
    """
    client_token = get_valid_client_token(db)
    refreshed = False

    while True:
        try:
            resp = requests.post(
                f'{settings.ailos_apim_base_url}{_PATH_COOPERADO_OBTER_ID}',
                headers={
                    'Accept': '*/*',
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {client_token}',
                },
                json={
                    'ailosApiKeyDeveloper': settings.ailos_developer_key,
                    'state': state,
                    'urlCallback': settings.ailos_callback_url,
                },
                timeout=settings.ailos_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise AilosError(f'Falha de conexão ao obter id do cooperado Ailos: {exc}') from exc

        token_invalido = resp.status_code in (401, 403) or (
            resp.status_code >= 400 and _WSO2_INVALID_CREDENTIALS_CODE in (resp.text or '')
        )
        if token_invalido and not refreshed:
            client_token = refresh_client_token(db)
            refreshed = True
            continue

        if resp.status_code >= 400:
            raise AilosApiError(
                status_code=resp.status_code,
                ailos_message=resp.text[:500],
                ailos_code=None,
                friendly_message='Falha ao obter identificador do cooperado na Ailos',
            )

        return resp.text.strip().strip('"')


def build_cooperado_login_url(db: Session) -> tuple[str, str]:
    """
    Inicia o fluxo de autorização do cooperado: obtém o id do cooperado na
    Ailos, gera um ``state`` aleatório, salva em ``ailos_integrations`` e
    retorna a URL de login para o admin abrir manualmente no navegador.
    """
    if not settings.ailos_callback_url:
        raise AilosError('AILOS_CALLBACK_URL não configurado')
    if not settings.ailos_apim_base_url:
        raise AilosError('AILOS_APIM_BASE_URL não configurado')

    state = secrets.token_urlsafe(32)
    cooperado_id = _obter_id_cooperado(db, state)
    login_url = f'{settings.ailos_apim_base_url}{_PATH_COOPERADO_AUTORIZAR}?id={quote(cooperado_id, safe="")}'

    integration = _get_or_create_integration(db)
    integration.state = state
    integration.status = 'pending'
    integration.last_error = None
    db.commit()

    return login_url, state


def autorizar_cooperado_directo(
    db: Session,
    codigo_cooperativa: str | int,
    codigo_conta: str | int,
    senha: str,
) -> dict:
    """
    Autoriza o cooperado de forma headless (sem navegador), submetendo o login
    diretamente à Ailos com o CÓDIGO da cooperativa (não o nome do dropdown).

    Faz ``obter/id`` (armazenando o ``state``) e depois um POST multipart em
    ``/login/index`` com ``Login.CodigoCooperativa``/``CodigoConta``/``Senha``
    (equivalente ao ``--form`` do cURL da Cartilha p.6). A Ailos então chama o
    ``AILOS_CALLBACK_URL`` (que persiste o JWT via ``handle_cooperado_callback``).

    Retorna o status/corpo da resposta do ``login/index`` para diagnóstico
    (ex.: erros LG006 de cooperativa/conta incorretas).
    """
    if not settings.ailos_callback_url:
        raise AilosError('AILOS_CALLBACK_URL não configurado')
    if not settings.ailos_apim_base_url:
        raise AilosError('AILOS_APIM_BASE_URL não configurado')

    state = secrets.token_urlsafe(32)
    cooperado_id = _obter_id_cooperado(db, state)

    integration = _get_or_create_integration(db)
    integration.state = state
    integration.status = 'pending'
    integration.last_error = None
    db.commit()

    client_token = get_valid_client_token(db)
    try:
        resp = requests.post(
            f'{settings.ailos_apim_base_url}{_PATH_COOPERADO_AUTORIZAR}',
            params={'id': cooperado_id},
            headers={'Authorization': f'Bearer {client_token}'},
            files={
                'Login.CodigoCooperativa': (None, str(codigo_cooperativa)),
                'Login.CodigoConta': (None, str(codigo_conta)),
                'Login.Senha': (None, senha),
            },
            timeout=settings.ailos_timeout_seconds,
        )
    except requests.RequestException as exc:
        raise AilosError(f'Falha de conexão ao autenticar cooperado Ailos: {exc}') from exc

    body: dict | list | None = None
    if _is_json_response(resp):
        try:
            body = resp.json()
        except ValueError:
            body = None

    return {'status_code': resp.status_code, 'json': body, 'text': resp.text[:1000]}


def handle_cooperado_callback(db: Session, state: str, code: str) -> AilosIntegration:
    """Valida o ``state`` recebido no callback e persiste o token do cooperado."""
    integration = db.query(AilosIntegration).filter(
        AilosIntegration.state == state,
        AilosIntegration.state.isnot(None),
    ).first()
    if integration is None:
        raise AilosError('state inválido ou expirado')

    integration.cooperado_token_encrypted = encrypt_token(code)
    integration.cooperado_token_expires_at = datetime.now(timezone.utc) + _COOPERADO_TOKEN_LIFETIME
    integration.status = 'authorized'
    integration.authorized_at = datetime.now(timezone.utc)
    integration.last_refresh_at = datetime.now(timezone.utc)
    integration.last_error = None
    integration.state = None
    integration.auto_relogin_failures = 0  # login OK → libera o re-login automático
    db.commit()
    db.refresh(integration)
    return integration


def get_valid_cooperado_token(db: Session) -> str:
    """
    Retorna o token de cooperado atual, renovando proativamente quando perto
    de expirar (o JWT vive 30 min e não pode ser renovado após expirar).
    AilosError se ainda não autorizado.
    """
    integration = db.query(AilosIntegration).order_by(AilosIntegration.id.asc()).first()
    if integration is None or integration.status != 'authorized' or not integration.cooperado_token_encrypted:
        raise AilosError(
            'Cooperado Ailos ainda não autorizado. '
            'Use POST /api/v1/ailos/connect para iniciar a autorização.'
        )

    expires_at = integration.cooperado_token_expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        # Já expirou: a Ailos NÃO permite renovar token expirado — exige nova
        # autorização. Devolve erro tratado (400), nunca deixa estourar 500.
        if expires_at <= now:
            integration.status = 'expired'
            integration.last_error = 'Token do cooperado expirado'
            db.commit()
            raise AilosError(
                'Sessão do cooperado Ailos expirada. Reautorize via '
                'POST /api/v1/ailos/connect.'
            )

        # Perto de expirar: renova proativamente. Qualquer falha inesperada
        # vira AilosError (erro tratado) em vez de 500.
        if expires_at - now < _COOPERADO_REFRESH_MARGIN:
            try:
                return refresh_cooperado_token(db)
            except (AilosError, AilosApiError):
                raise
            except Exception as exc:  # noqa: BLE001
                raise AilosError(
                    f'Falha ao renovar o token do cooperado Ailos ({exc}). '
                    'Reautorize via POST /api/v1/ailos/connect.'
                ) from exc

    return decrypt_token(integration.cooperado_token_encrypted)


def refresh_cooperado_token(db: Session) -> str:
    """Renova o token de cooperado via GET .../cooperado/token/refresh?code=<atual>."""
    integration = db.query(AilosIntegration).order_by(AilosIntegration.id.asc()).first()
    if integration is None or not integration.cooperado_token_encrypted:
        raise AilosError('Cooperado Ailos ainda não autorizado')

    client_token = get_valid_client_token(db)
    current_token = decrypt_token(integration.cooperado_token_encrypted)

    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {client_token}',
    }
    if settings.ailos_developer_key:
        headers['ailosApiKeyDeveloper'] = settings.ailos_developer_key

    try:
        resp = requests.get(
            f'{settings.ailos_apim_base_url}{_PATH_COOPERADO_TOKEN_REFRESH}',
            params={'code': current_token},
            headers=headers,
            timeout=settings.ailos_timeout_seconds,
        )
    except requests.RequestException as exc:
        integration.last_error = str(exc)[:500]
        integration.status = 'error'
        db.commit()
        raise AilosError(f'Falha de conexão ao renovar token do cooperado Ailos: {exc}') from exc

    if resp.status_code >= 400:
        integration.last_error = resp.text[:500]
        integration.status = 'expired'
        db.commit()
        raise AilosApiError(
            status_code=resp.status_code,
            ailos_message=resp.text[:500],
            ailos_code=None,
            friendly_message='Falha ao renovar token do cooperado na Ailos. Reautorize via /connect.',
        )

    try:
        body = resp.json() if _is_json_response(resp) else {}
    except ValueError:
        body = {}
    new_token = body.get('access_token') or body.get('code') or resp.text.strip()
    if not new_token:
        integration.last_error = 'Renovação sem token na resposta'
        integration.status = 'expired'
        db.commit()
        raise AilosError('Renovação do token do cooperado não retornou token. Reautorize via /connect.')

    try:
        expires_seconds = int(body.get('expires_in'))
    except (TypeError, ValueError):
        expires_seconds = None

    integration.cooperado_token_encrypted = encrypt_token(new_token)
    integration.cooperado_token_expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_seconds)
        if expires_seconds else datetime.now(timezone.utc) + _COOPERADO_TOKEN_LIFETIME
    )
    integration.last_refresh_at = datetime.now(timezone.utc)
    integration.last_error = None
    integration.status = 'authorized'
    db.commit()
    return new_token


# Teto de tentativas consecutivas de re-login automático. Mantém abaixo das 3
# senhas erradas que BLOQUEIAM a conta na Ailos (Cartilha p.22).
_MAX_RELOGIN_FAILURES = 2


def manter_sessao_cooperado(db: Session) -> str:
    """Mantém a sessão do cooperado viva (usado pelo keepalive em background).

    1) Sessão sadia (autorizada e token válido) → renova via refresh.
    2) Token morto OU refresh falhou → se ``AILOS_AUTO_RELOGIN`` ligado e as
       credenciais configuradas, refaz o login headless (``autorizar_cooperado_directo``).

    Trava de segurança: re-login só é tentado enquanto a sessão NÃO volta a
    ficar sadia; ao chegar em ``_MAX_RELOGIN_FAILURES`` re-logins seguidos sem
    sucesso, para de tentar (evita o bloqueio por 3 senhas erradas). O contador
    zera assim que a sessão fica sadia de novo. Nunca levanta exceção.
    """
    integration = db.query(AilosIntegration).order_by(AilosIntegration.id.asc()).first()
    if integration is None:
        return 'sem_integracao'

    token_saudavel = False
    if integration.status == 'authorized' and integration.cooperado_token_encrypted:
        expires_at = integration.cooperado_token_expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at is None or expires_at > datetime.now(timezone.utc):
            token_saudavel = True

    if token_saudavel:
        # sessão sadia → zera o contador de falhas de re-login e renova
        if integration.auto_relogin_failures:
            integration.auto_relogin_failures = 0
            db.commit()
        try:
            refresh_cooperado_token(db)
            return 'renovado'
        except (AilosError, AilosApiError):
            pass  # refresh falhou → re-login proativo abaixo (token ainda vivo)

    # token morto OU refresh falhou → re-login headless
    if not (settings.ailos_auto_relogin
            and settings.ailos_cooperado_conta
            and settings.ailos_cooperado_senha):
        return 'expirado_sem_relogin'

    if (integration.auto_relogin_failures or 0) >= _MAX_RELOGIN_FAILURES:
        return 'relogin_travado'  # trava de segurança — credencial provavelmente errada

    # incrementa ANTES (pessimista): garante o teto mesmo se o processo cair.
    # Zera só quando a sessão volta a ficar sadia (no topo do próximo ciclo).
    integration.auto_relogin_failures = (integration.auto_relogin_failures or 0) + 1
    db.commit()
    try:
        autorizar_cooperado_directo(
            db,
            settings.ailos_cooperado_cooperativa,
            settings.ailos_cooperado_conta,
            settings.ailos_cooperado_senha,
        )
        return 'relogin_disparado'
    except (AilosError, AilosApiError):
        # falha de conexão / obter-id (não foi rejeição de senha) → não conta strike
        integration.auto_relogin_failures = max(0, (integration.auto_relogin_failures or 1) - 1)
        db.commit()
        return 'relogin_erro_conexao'


# ---------------------------------------------------------------------------
# Request genérico (gateway/apim) com retry de tokens
# ---------------------------------------------------------------------------

def request(
    db: Session,
    method: str,
    path: str,
    json_body: dict | list | None = None,
    base: str = 'gateway',
    accept_zip: bool = False,
    billing_id: int | None = None,
) -> AilosResponse:
    """
    Executa uma chamada autenticada à API Ailos (client token + cooperado
    token), com retry automático de tokens expirados e log em
    ``ailos_api_logs``.

    - 401 com ``WWW-Authenticate`` indicando token de aplicação inválido
      (WSO2) → renova o client token e tenta novamente (1x).
    - 401 com corpo vazio (token de cooperado expirado) → renova o token de
      cooperado e tenta novamente (1x).
    - 400 com mensagem "[99] ... processamento/processando" → não é erro;
      retorna ``AilosResponse(processing=True)``.
    - Demais erros (>= 400) → ``AilosApiError``.
    """
    base_url = settings.ailos_apim_base_url if base == 'apim' else settings.ailos_gateway_base_url
    if not base_url:
        raise AilosError(f'URL base "{base}" da Ailos não configurada')

    accept = 'application/zip, application/json' if accept_zip else 'application/json'

    client_token = get_valid_client_token(db)
    cooperado_token = get_valid_cooperado_token(db)

    refreshed_client = False
    refreshed_cooperado = False
    resp: requests.Response | None = None

    for _attempt in range(_MAX_ATTEMPTS):
        headers = {
            'Accept': accept,
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {client_token}',
            'x-ailos-authentication': f'Bearer {cooperado_token}',
        }
        if settings.ailos_developer_key:
            headers['ailosApiKeyDeveloper'] = settings.ailos_developer_key

        try:
            resp = requests.request(
                method,
                f'{base_url}{path}',
                headers=headers,
                json=json_body,
                timeout=settings.ailos_timeout_seconds,
            )
        except requests.RequestException as exc:
            _log_call(db, method, path, json_body, None, None, False, str(exc), None, billing_id)
            raise AilosError(f'Falha de conexão com a API Ailos ({path}): {exc}') from exc

        if resp.status_code == 401:
            www_auth = resp.headers.get('WWW-Authenticate', '')
            # Access token (client) expirado: WSO2 sinaliza via WWW-Authenticate
            # ou via corpo { "fault": { "code": 900901 } } (Cartilha p.18).
            fault_code = None
            if _is_json_response(resp):
                try:
                    fault = (resp.json() or {}).get('fault') or {}
                    fault_code = str(fault.get('code')) if fault.get('code') is not None else None
                except ValueError:
                    fault_code = None
            client_token_expired = (
                'WSO2' in www_auth
                or 'invalid token' in www_auth.lower()
                or fault_code == _WSO2_INVALID_CREDENTIALS_CODE
            )
            if not refreshed_client and client_token_expired:
                client_token = refresh_client_token(db)
                refreshed_client = True
                continue
            if not refreshed_cooperado and not (resp.content or b'').strip():
                cooperado_token = refresh_cooperado_token(db)
                refreshed_cooperado = True
                continue
        break

    assert resp is not None

    body_json: dict | list | None = None
    if _is_json_response(resp):
        try:
            body_json = resp.json()
        except ValueError:
            body_json = None

    processing = False
    success = resp.status_code < 400

    if resp.status_code == 400 and body_json:
        message = _extract_message(body_json) or ''
        if '[99]' in message and ('processament' in message.lower() or 'processando' in message.lower()):
            processing = True
            success = True

    correlation_id = resp.headers.get('X-Correlation-Id') or resp.headers.get('correlationId')
    error_message = None if success else (_extract_message(body_json) or resp.text[:500])

    _log_call(
        db, method, path, json_body, body_json, resp.status_code, success,
        error_message, correlation_id, billing_id,
    )

    if not success:
        raise AilosApiError(
            status_code=resp.status_code,
            ailos_message=error_message,
            ailos_code=_extract_code(body_json),
            friendly_message=f'Erro na API Ailos ({path}): HTTP {resp.status_code}',
        )

    return AilosResponse(
        status_code=resp.status_code,
        json=body_json,
        content=resp.content,
        headers=dict(resp.headers),
        processing=processing,
    )
