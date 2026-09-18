"""
Cliente HTTP isolado e READ-ONLY para a API do SGR (Hinova).

Único ponto do código que fala com o SGR — nenhuma chamada HTTP ao SGR deve
existir fora deste módulo (ETAPA 4 da POC de migração).

Autenticação (conforme doc oficial, grupo "autenticacao" /headers_authorization):
  1. POST /headers_authorization com {"cliente": codMobile, "nome": usuario,
     "senha": senha} no corpo. A doc não declara explicitamente o content-type
     do corpo — assumimos JSON (padrão do restante da API, que só aceita
     "Accept: application/json"); ver nota em `authenticate()`.
  2. O "Exemplo Retorno" da doc mostra ``X-Auth-Token``/``Authorization``
     DENTRO do corpo JSON, num objeto ``"Headers": {...}`` — não como headers
     HTTP de verdade (confirmado batendo contra a API real: eles não vêm em
     ``resp.headers``). A doc grafa "Autorization" — aceitamos ambas as
     grafias. ``authenticate()`` checa o corpo primeiro e os headers HTTP como
     reserva. Esses valores devem ser reenviados em toda chamada seguinte.
     A doc também instrui "guarde os cookies retornados" — por isso usamos
     ``requests.Session()``, que persiste cookies automaticamente.
  3. Toda chamada além da autenticação usa a "chave API" (gerada ao cadastrar
     o Fornecedor de Serviços no SGR — distinta do codMobile) como segmento
     de PATH: ``/buscar_cliente/{chave_api}``.
  4. **NÃO DOCUMENTADO**: o codMobile precisa ser reenviado em TODA chamada,
     na query string como ``cliente=<codMobile>``. Sem isso a API responde
     401 "Código de cliente inválido" em todos os endpoints — inclusive nas
     tabelas de domínio —, o que NÃO é falta de permissão (essa devolve outra
     mensagem, explícita: "Você não tem permissão para a função X"). Só a
     query funciona: um header ``codMobile`` é ignorado (verificado em sessão
     isolada). A apidoc não cita esse parâmetro em endpoint nenhum; foi
     descoberto testando contra a API real.

Apenas métodos GET (consulta) são implementados — de propósito. Esta POC é
somente leitura; não existem wrappers para os endpoints de inserir/editar do
SGR.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

_PATH_AUTH = '/headers_authorization'


# ---------------------------------------------------------------------------
# Exceções — cada uma corresponde a uma causa raiz distinta (ETAPA 15)
# ---------------------------------------------------------------------------

class SGRError(Exception):
    """Erro de configuração — nenhuma chamada de rede foi feita."""


class SGRApiError(Exception):
    """Erro retornado pela API do SGR (ou falha ao interpretar a resposta)."""

    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class SGRAuthenticationError(SGRApiError):
    """401/403 — credenciais ou chave de API inválidas/expiradas."""


class SGRNotFoundError(SGRApiError):
    """404 — recurso inexistente."""


class SGRRateLimitError(SGRApiError):
    """429 — limite de requisições atingido."""


class SGRServerError(SGRApiError):
    """5xx — erro no servidor do SGR (não é responsabilidade do MasterSat)."""


class SGRConnectionError(SGRApiError):
    """Timeout ou falha de conexão — nenhuma resposta HTTP foi recebida."""


class SGRInvalidResponseError(SGRApiError):
    """A resposta não é JSON, não tem o formato esperado, ou Error=true."""


def _ci_get(payload: dict, *keys: str):
    """Busca uma chave em um dict de forma case-insensitive (a doc do SGR mistura
    PascalCase e snake_case entre endpoints e até dentro do mesmo endpoint)."""
    lowered = {str(k).lower(): v for k, v in payload.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value is not None:
            return value
    return None


@dataclass
class RequestLogEntry:
    method: str
    path: str
    status_code: int | None


@dataclass
class SGRClient:
    """Cliente HTTP para a API do SGR. Uma instância = uma sessão autenticada."""

    session: requests.Session = field(default_factory=requests.Session)
    request_count: int = field(default=0, init=False)
    request_log: list[RequestLogEntry] = field(default_factory=list, init=False)
    _auth_headers: dict[str, str] = field(default_factory=dict, init=False)

    # -- configuração ------------------------------------------------------

    @staticmethod
    def _require_config() -> None:
        missing = [
            name for name, value in (
                ('SGR_BASE_URL', settings.sgr_base_url),
                ('SGR_COD_MOBILE', settings.sgr_cod_mobile),
                ('SGR_USERNAME', settings.sgr_username),
                ('SGR_PASSWORD', settings.sgr_password),
                ('SGR_API_KEY', settings.sgr_api_key),
            ) if not value
        ]
        if missing:
            raise SGRError(
                'Credenciais do SGR ausentes: ' + ', '.join(missing)
                + '. Configure-as via variáveis de ambiente (.env) — nunca no código.'
            )

    @property
    def is_authenticated(self) -> bool:
        return bool(self._auth_headers)

    # -- infraestrutura HTTP -------------------------------------------------

    def _track(self, method: str, path: str, status_code: int | None) -> None:
        self.request_count += 1
        # Nunca registra query params/corpo aqui — podem conter CPF/placa/etc.
        self.request_log.append(RequestLogEntry(method=method, path=path, status_code=status_code))

    def _send(self, method: str, url: str, *, context: str, **kwargs) -> requests.Response:
        try:
            resp = self.session.request(method, url, timeout=settings.sgr_timeout_seconds, **kwargs)
        except requests.Timeout as exc:
            self._track(method, context, None)
            raise SGRConnectionError(f'Timeout ao consultar o SGR ({context})') from exc
        except requests.RequestException as exc:
            self._track(method, context, None)
            raise SGRConnectionError(f'Falha de conexão com o SGR ({context})') from exc
        self._track(method, context, resp.status_code)
        return resp

    @staticmethod
    def _raise_for_status(resp: requests.Response, context: str) -> None:
        if resp.status_code in (401, 403):
            raise SGRAuthenticationError(
                f'SGR recusou a credencial/chave de API ({context}): HTTP {resp.status_code}',
                status_code=resp.status_code,
            )
        if resp.status_code == 404:
            raise SGRNotFoundError(f'Recurso não encontrado no SGR ({context})', status_code=404)
        if resp.status_code == 429:
            raise SGRRateLimitError(f'Limite de requisições do SGR atingido ({context})', status_code=429)
        if resp.status_code >= 500:
            raise SGRServerError(
                f'Erro no servidor do SGR ({context}): HTTP {resp.status_code}', status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise SGRApiError(
                f'Erro ao consultar o SGR ({context}): HTTP {resp.status_code}', status_code=resp.status_code,
            )

    @staticmethod
    def _parse_json(resp: requests.Response, context: str) -> dict:
        try:
            body = resp.json()
        except ValueError as exc:
            raise SGRInvalidResponseError(f'Resposta do SGR não é JSON válido ({context})') from exc
        if not isinstance(body, dict):
            raise SGRInvalidResponseError(f'Formato de resposta inesperado do SGR ({context})')
        error_flag = _ci_get(body, 'error')
        if isinstance(error_flag, str) and error_flag.strip().lower() == 'true':
            raise SGRInvalidResponseError(f'SGR retornou Error=true ({context})')
        return body

    # -- autenticação --------------------------------------------------------

    def authenticate(self) -> None:
        """POST /headers_authorization — obtém X-Auth-Token/Authorization."""
        self._require_config()
        url = f'{settings.sgr_base_url.rstrip("/")}{_PATH_AUTH}'
        resp = self._send(
            'POST', url, context='autenticação',
            headers={'Accept': 'application/json'},
            json={
                'cliente': settings.sgr_cod_mobile,
                'nome': settings.sgr_username,
                'senha': settings.sgr_password,
            },
        )
        self._raise_for_status(resp, context='autenticação')
        body = self._parse_json(resp, context='autenticação')

        # O "Exemplo Retorno" da doc mostra X-Auth-Token/Autorization DENTRO do
        # corpo JSON, num objeto "Headers" — não como headers HTTP de verdade.
        # Checamos os dois lugares (corpo primeiro, headers HTTP como reserva)
        # porque a doc está longe de confiável (ver mapping.py) e o comportamento
        # real só se confirma rodando contra a API viva.
        headers_obj = _ci_get(body, 'headers')
        token = _ci_get(headers_obj, 'x-auth-token') if isinstance(headers_obj, dict) else None
        authorization = _ci_get(headers_obj, 'authorization', 'autorization') if isinstance(headers_obj, dict) else None
        token = token or resp.headers.get('X-Auth-Token')
        authorization = authorization or resp.headers.get('Authorization') or resp.headers.get('Autorization')
        if not token or not authorization:
            raise SGRInvalidResponseError(
                'Resposta de autenticação do SGR sem X-Auth-Token/Authorization (nem no corpo, nem nos headers HTTP)'
            )
        self._auth_headers = {'X-Auth-Token': token, 'Authorization': authorization}
        logger.info('SGR: autenticação OK')

    def _ensure_authenticated(self) -> None:
        if not self.is_authenticated:
            self.authenticate()

    # -- consultas genéricas ---------------------------------------------------

    def get(self, path: str, params: dict | None = None, *, retry_auth: bool = True) -> dict:
        """GET {base}{path}/{chave_api} com os headers de autenticação."""
        self._ensure_authenticated()
        self._require_config()
        url = f'{settings.sgr_base_url.rstrip("/")}{path}/{settings.sgr_api_key}'
        # 'cliente' (codMobile) em TODA chamada — ver item 4 do docstring do módulo.
        clean_params = {'cliente': settings.sgr_cod_mobile}
        clean_params.update({k: v for k, v in (params or {}).items() if v not in (None, '')})
        headers = {'Accept': 'application/json', **self._auth_headers}

        resp = self._send('GET', url, context=path, headers=headers, params=clean_params)

        if resp.status_code in (401, 403) and retry_auth:
            # Token pode ter expirado no meio da execução — reautentica 1x.
            self._auth_headers = {}
            self.authenticate()
            return self.get(path, params, retry_auth=False)

        self._raise_for_status(resp, context=path)
        return self._parse_json(resp, context=path)

    @staticmethod
    def extract_data(body: dict) -> list[dict]:
        """Normaliza o envelope {Error, Data} / {error, data} em uma lista de registros."""
        data = _ci_get(body, 'data')
        if data is None:
            return []
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return data
        raise SGRInvalidResponseError('Campo Data da resposta do SGR em formato inesperado')

    # -- endpoints usados pela POC (só leitura) ---------------------------------

    def buscar_clientes(self, total: int, indice: int = 0) -> list[dict]:
        body = self.get('/buscar_cliente', {'total': total, 'indice': indice})
        return self.extract_data(body)

    def buscar_veiculos_por_cliente(self, cod_cliente, total: int = 200, indice: int = 0) -> list[dict]:
        body = self.get('/buscar_veiculo', {'cod_cliente': cod_cliente, 'total': total, 'indice': indice})
        return self.extract_data(body)

    def buscar_vinculos_por_placa(self, placa: str, total: int = 200, indice: int = 0) -> list[dict]:
        body = self.get('/buscar_vinculo', {'placa_vinculo': placa, 'total': total, 'indice': indice})
        return self.extract_data(body)

    def buscar_equipamento(self, cod_equipamento) -> list[dict]:
        body = self.get('/buscar_equipamento', {'cod_equipamento': cod_equipamento})
        return self.extract_data(body)

    def buscar_rastreadores(self, total: int = 200, indice: int = 0) -> list[dict]:
        """Rastreadores em lote. Este endpoint é o único que traz IMEI do
        equipamento, situação e PLACA no mesmo registro — por isso a POC
        indexa o resultado por placa em vez de fazer 1 chamada por veículo."""
        body = self.get('/buscar_rastreador', {'total': total, 'indice': indice})
        return self.extract_data(body)
