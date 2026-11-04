"""Cliente HTTP da API de Cobrança Sicredi (v1).

Autenticação (manual "Autenticação em detalhe"): OAuth2 ``password`` em
``/auth/openapi/token`` com os headers ``x-api-key`` e ``context: COBRANCA``;
``username`` = código do beneficiário (5) + cooperativa (4) e ``password`` =
Código de Acesso gerado no Internet Banking. O ``access_token`` vale poucos
minutos (300 s no manual) e é renovado com o ``refresh_token``; quando este
também vence, autentica de novo. Os tempos vêm da resposta, nunca fixos.

Ambientes: ``sandbox`` usa as credenciais fixas de teste do Sicredi (só a
x-api-key de sandbox vem da configuração); ``producao`` exige
``SICREDI_PRODUCAO_CONFIRMADA=true`` — verificado ANTES de qualquer chamada.

Os tokens ficam só em memória, por processo: duram minutos e um novo login
não tem efeito colateral (ao contrário do cooperado Ailos).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import requests
# Vinculado no import: testes substituem ``requests`` inteiro neste módulo e
# a classificação de falha precisa das classes reais.
from requests import exceptions as _req_exc

from app.core.config import settings
from app.core.crypto import mask_token
from app.db import session as db_session
from app.models.sicredi_api_log import SicrediApiLog

logger = logging.getLogger(__name__)

BASE_URLS = {
    'sandbox': 'https://api-parceiro.sicredi.com.br/sb',
    'producao': 'https://api-parceiro.sicredi.com.br',
}
PATH_TOKEN = '/auth/openapi/token'

# Dados fixos de teste publicados pelo Sicredi para o sandbox.
SANDBOX = {
    'cooperativa': '6789',
    'posto': '03',
    'codigo_beneficiario': '12345',
    'password': 'teste123',
}

# Margem antes do vencimento do token para não usá-lo no limite.
_MARGEM_TOKEN_S = 20


class SicrediError(Exception):
    """Configuração/estado da integração — nenhum pedido de boleto chegou ao Sicredi."""


class SicrediDesfechoDesconhecido(SicrediError):
    """O pedido pode ter chegado ao Sicredi, mas a resposta não chegou aqui
    (timeout de leitura, conexão caída no meio). Num cadastro de boleto isto
    não é falha: o título pode existir no banco."""


class SicrediApiError(Exception):
    """Resposta HTTP >= 400 da API."""

    def __init__(self, status_code: int | None, mensagem: str | None, codigo: str | None, friendly_message: str):
        self.status_code = status_code
        self.mensagem = mensagem
        self.codigo = codigo
        self.friendly_message = friendly_message
        super().__init__(friendly_message)


@dataclass(frozen=True)
class Credenciais:
    ambiente: str
    base_url: str
    api_key: str
    cooperativa: str
    posto: str
    codigo_beneficiario: str
    password: str

    @property
    def username(self) -> str:
        return f'{self.codigo_beneficiario}{self.cooperativa}'


@dataclass
class SicrediResponse:
    status_code: int
    json: dict | list | None
    content: bytes
    headers: dict


def ambiente_configurado() -> str:
    return (settings.sicredi_ambiente or '').strip().lower()


def habilitado() -> bool:
    return bool((settings.sicredi_api_key or '').strip())


def _exigir_digitos(valor: str, tamanho: int, nome: str) -> str:
    texto = (valor or '').strip()
    if not (texto.isdigit() and len(texto) == tamanho):
        raise SicrediError(f'{nome} do Sicredi inválido na configuração: esperado {tamanho} dígitos.')
    return texto


def credenciais() -> Credenciais:
    """Credenciais do ambiente configurado; levanta antes de qualquer rede."""
    api_key = (settings.sicredi_api_key or '').strip()
    if not api_key:
        raise SicrediError('Integração Sicredi não configurada (SICREDI_API_KEY vazio).')
    ambiente = ambiente_configurado()
    if ambiente not in BASE_URLS:
        raise SicrediError(f'SICREDI_AMBIENTE inválido: "{settings.sicredi_ambiente}" (use sandbox ou producao).')
    if ambiente == 'sandbox':
        return Credenciais(
            ambiente=ambiente, base_url=BASE_URLS[ambiente], api_key=api_key,
            cooperativa=SANDBOX['cooperativa'], posto=SANDBOX['posto'],
            codigo_beneficiario=SANDBOX['codigo_beneficiario'], password=SANDBOX['password'],
        )
    if not settings.sicredi_producao_confirmada:
        raise SicrediError(
            'Sicredi em produção bloqueado: defina SICREDI_PRODUCAO_CONFIRMADA=true para '
            'registrar boletos reais (trava contra acidente).'
        )
    codigo_acesso = (settings.sicredi_codigo_acesso or '').strip()
    if not codigo_acesso:
        raise SicrediError('SICREDI_CODIGO_ACESSO vazio: gere o código de acesso no Internet Banking.')
    return Credenciais(
        ambiente=ambiente, base_url=BASE_URLS[ambiente], api_key=api_key,
        cooperativa=_exigir_digitos(settings.sicredi_cooperativa, 4, 'Cooperativa'),
        posto=_exigir_digitos(settings.sicredi_posto, 2, 'Posto'),
        codigo_beneficiario=_exigir_digitos(settings.sicredi_codigo_beneficiario, 5, 'Código do beneficiário'),
        password=codigo_acesso,
    )


# ---------------------------------------------------------------------------
# Máscara e log
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS = ('password', 'token', 'secret', 'authorization', 'api-key', 'api_key', 'apikey')
_DOCUMENT_KEYS = {'documento', 'cpfcnpj', 'numerocpfcnpj', 'cpfcnpjbeneficiariofinal'}
_NAME_KEYS = {'nome', 'nomedestinatario'}
_PII_KEYS = {'endereco', 'logradouro', 'numeroendereco', 'cep', 'telefone', 'email'}


def _mask_document(value: str) -> str:
    digits = ''.join(c for c in value if c.isdigit())
    if len(digits) <= 2:
        return '*' * len(digits)
    return '*' * (len(digits) - 2) + digits[-2:]


def _mask_name(value: str) -> str:
    first, _, rest = value.strip().partition(' ')
    return f'{first} ***' if rest else value


def mask_payload(value):
    """Mascara segredos e dados pessoais (mesma política dos logs da Ailos)."""
    if isinstance(value, dict):
        masked = {}
        for k, v in value.items():
            key = str(k).lower()
            if isinstance(v, str) and any(s in key for s in _SENSITIVE_KEYS):
                masked[k] = mask_token(v)
            elif isinstance(v, str) and key in _DOCUMENT_KEYS:
                masked[k] = _mask_document(v)
            elif isinstance(v, str) and key in _NAME_KEYS:
                masked[k] = _mask_name(v)
            elif isinstance(v, str) and key in _PII_KEYS:
                masked[k] = '***' if v else v
            else:
                masked[k] = mask_payload(v)
        return masked
    if isinstance(value, list):
        return [mask_payload(v) for v in value]
    return value


def _log_call(ambiente, method, path, request_payload, response_payload, status_code,
              success, error_message, billing_id) -> None:
    """Grava em ``sicredi_api_logs`` numa sessão própria (não comita a do chamador)."""
    log_db = db_session.SessionLocal()
    try:
        log_db.add(SicrediApiLog(
            ambiente=ambiente,
            endpoint=path[:255],
            method=method.upper(),
            request_payload=mask_payload(request_payload) if request_payload is not None else None,
            response_payload=mask_payload(response_payload) if response_payload is not None else None,
            status_code=status_code,
            success=success,
            error_message=error_message,
            billing_id=billing_id,
        ))
        log_db.commit()
    finally:
        log_db.close()


def _registrar_log(*args) -> None:
    """O log nunca muda o desfecho bancário: falha dele só vai para o log da aplicação."""
    try:
        _log_call(*args)
    except Exception:  # noqa: BLE001
        logger.exception('Falha ao gravar sicredi_api_logs para %s %s; a resposta do Sicredi foi mantida.',
                         args[1], args[2])


# ---------------------------------------------------------------------------
# Classificação de falha de rede (mesma regra do cliente Ailos)
# ---------------------------------------------------------------------------

def requisicao_pode_ter_chegado(exc: BaseException) -> bool:
    """Só erro na fase de conexão garante que nada foi enviado ao banco."""
    if isinstance(exc, _req_exc.ConnectTimeout):
        return False
    if isinstance(exc, _req_exc.ConnectionError):
        texto = repr(exc)
        if any(marca in texto for marca in (
            'NewConnectionError', 'NameResolutionError', 'Failed to establish a new connection',
            'Name or service not known', 'nodename nor servname', 'getaddrinfo failed',
        )):
            return False
    if isinstance(exc, (_req_exc.InvalidURL, _req_exc.MissingSchema,
                        _req_exc.InvalidSchema, _req_exc.InvalidHeader)):
        return False
    return True


def _extract_message(body) -> str | None:
    if body is None:
        return None
    if isinstance(body, list):
        body = body[0] if body else None
    if isinstance(body, str):
        return body[:500] or None
    if not isinstance(body, dict):
        return None
    for key in ('mensagem', 'message', 'error_description', 'descricao', 'detail', 'error'):
        value = body.get(key)
        if value:
            return str(value)
    for key in ('errors', 'erros', 'details', 'detalhes'):
        nested = body.get(key)
        if isinstance(nested, list) and nested:
            return _extract_message(nested[0]) if isinstance(nested[0], (dict, str)) else str(nested[0])
    return None


def _extract_code(body) -> str | None:
    if isinstance(body, list):
        body = body[0] if body else None
    if not isinstance(body, dict):
        return None
    for key in ('codigo', 'code', 'errorCode'):
        if body.get(key):
            return str(body[key])
    return None


def _json_ou_none(resp) -> dict | list | None:
    try:
        return resp.json()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

@dataclass
class _Token:
    access_token: str
    expira_em: float
    refresh_token: str | None
    refresh_expira_em: float


_TOKENS: dict[tuple[str, str, str], _Token] = {}
_TOKEN_LOCK = threading.Lock()


def _chave(cred: Credenciais) -> tuple[str, str, str]:
    return (cred.ambiente, cred.username, cred.api_key)


def limpar_tokens() -> None:
    """Esquece os tokens em memória (testes e troca de configuração)."""
    with _TOKEN_LOCK:
        _TOKENS.clear()


def _post_token(cred: Credenciais, data: dict) -> _Token:
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'x-api-key': cred.api_key,
        'context': 'COBRANCA',
    }
    try:
        resp = requests.post(
            f'{cred.base_url}{PATH_TOKEN}', data=data, headers=headers,
            timeout=settings.sicredi_timeout_seconds,
        )
    except requests.RequestException as exc:
        _registrar_log(cred.ambiente, 'POST', PATH_TOKEN, data, None, None, False, str(exc), None)
        raise SicrediError(f'Falha de conexão na autenticação Sicredi: {exc}') from exc
    body = _json_ou_none(resp)
    ok = resp.status_code == 200 and isinstance(body, dict) and body.get('access_token')
    erro = None if ok else (_extract_message(body) or (resp.text or '')[:500])
    _registrar_log(cred.ambiente, 'POST', PATH_TOKEN, data, body, resp.status_code, bool(ok), erro, None)
    if not ok:
        raise SicrediApiError(
            resp.status_code, erro, _extract_code(body),
            f'Autenticação no Sicredi recusada (HTTP {resp.status_code}). Confira a x-api-key do '
            f'ambiente {cred.ambiente}, o código de acesso e o código do beneficiário.',
        )
    agora = time.monotonic()
    return _Token(
        access_token=str(body['access_token']),
        expira_em=agora + max(int(body.get('expires_in') or 300) - _MARGEM_TOKEN_S, 1),
        refresh_token=body.get('refresh_token'),
        refresh_expira_em=agora + max(int(body.get('refresh_expires_in') or 0) - _MARGEM_TOKEN_S, 0),
    )


def obter_token(cred: Credenciais, *, forcar: bool = False) -> str:
    """Access token válido: reaproveita, renova pelo refresh ou autentica de novo."""
    with _TOKEN_LOCK:
        agora = time.monotonic()
        atual = _TOKENS.get(_chave(cred))
        if atual and not forcar and atual.expira_em > agora:
            return atual.access_token
        novo = None
        if atual and atual.refresh_token and atual.refresh_expira_em > agora:
            try:
                novo = _post_token(cred, {'grant_type': 'refresh_token', 'refresh_token': atual.refresh_token})
            except (SicrediApiError, SicrediError):
                novo = None  # refresh recusado: autentica do zero
        if novo is None:
            novo = _post_token(cred, {
                'grant_type': 'password',
                'username': cred.username,
                'password': cred.password,
                'scope': 'cobranca',
            })
        _TOKENS[_chave(cred)] = novo
        return novo.access_token


def _descartar_token(cred: Credenciais) -> None:
    with _TOKEN_LOCK:
        _TOKENS.pop(_chave(cred), None)


# ---------------------------------------------------------------------------
# Chamada autenticada
# ---------------------------------------------------------------------------

def request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
    headers_extra: dict | None = None,
    billing_id: int | None = None,
    binario: bool = False,
    cred: Credenciais | None = None,
) -> SicrediResponse:
    """Chamada autenticada. 401 descarta o token e repete uma vez com outro.

    Falha de rede depois do envio levanta ``SicrediDesfechoDesconhecido``;
    HTTP >= 400 levanta ``SicrediApiError``.
    """
    cred = cred or credenciais()
    token = obter_token(cred)
    resp = None
    for tentativa in range(2):
        headers = {
            'Authorization': f'Bearer {token}',
            'x-api-key': cred.api_key,
            'cooperativa': cred.cooperativa,
            'posto': cred.posto,
            'Content-Type': 'application/json',
            'Accept': '*/*' if binario else 'application/json',
        }
        headers.update(headers_extra or {})
        try:
            resp = requests.request(
                method, f'{cred.base_url}{path}', params=params, json=json_body, headers=headers,
                timeout=settings.sicredi_timeout_seconds,
            )
        except requests.RequestException as exc:
            _registrar_log(cred.ambiente, method, path, json_body or params, None, None, False, str(exc), billing_id)
            if requisicao_pode_ter_chegado(exc):
                raise SicrediDesfechoDesconhecido(
                    f'Sem resposta do Sicredi ({path}): {exc}. O pedido pode ter sido processado.'
                ) from exc
            raise SicrediError(f'Falha de conexão com o Sicredi ({path}): {exc}') from exc
        if resp.status_code == 401 and tentativa == 0:
            _descartar_token(cred)
            token = obter_token(cred, forcar=True)
            continue
        break

    assert resp is not None
    sucesso = resp.status_code < 400
    corpo = None
    if not (binario and sucesso):
        corpo = _json_ou_none(resp)
    erro = None if sucesso else (_extract_message(corpo) or (resp.text or '')[:500])
    _registrar_log(
        cred.ambiente, method, path, json_body or params,
        corpo if not binario else {'bytes': len(resp.content or b'')},
        resp.status_code, sucesso, erro, billing_id,
    )
    if not sucesso:
        raise SicrediApiError(
            resp.status_code, erro, _extract_code(corpo),
            f'Erro na API Sicredi ({path}): HTTP {resp.status_code}',
        )
    return SicrediResponse(
        status_code=resp.status_code, json=corpo, content=resp.content or b'', headers=dict(resp.headers),
    )


def testar_conexao() -> dict:
    """Autentica (sem tocar em boleto) e diz em que ambiente/convênio."""
    cred = credenciais()
    obter_token(cred, forcar=True)
    return {
        'ambiente': cred.ambiente,
        'cooperativa': cred.cooperativa,
        'posto': cred.posto,
        'codigo_beneficiario': cred.codigo_beneficiario,
    }
