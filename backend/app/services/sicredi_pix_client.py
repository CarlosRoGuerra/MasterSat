"""Cliente mTLS/OAuth da API Pix Sicredi, sem reutilizar a API dos boletos."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import monotonic
import re

import certifi
import requests

from app.core.config import settings

HOSTS = {
    'homologacao': 'https://api-pix-h.sicredi.com.br',
    'producao': 'https://api-pix.sicredi.com.br',
}
READ_SCOPES = 'rec.read payloadlocationrec.read cobr.read pix.read webhookrec.read webhookcobr.read webhook.read'
WRITE_SCOPES = READ_SCOPES + ' rec.write payloadlocationrec.write cobr.write webhookrec.write webhookcobr.write webhook.write'
_tokens: dict[str, tuple[str, float]] = {}
_token_lock = Lock()


class PixError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class PixResultadoIncerto(PixError):
    """Pedido pode ter sido aceito; consultar antes de reenviar."""


def ambiente() -> str:
    value = settings.sicredi_pix_ambiente.strip().lower()
    if value not in HOSTS:
        raise PixError('Ambiente da API Pix inválido: use homologacao ou producao.')
    return value


def validar_configuracao() -> None:
    if not settings.sicredi_pix_enabled:
        raise PixError('API Pix Sicredi ainda não habilitada neste ambiente.')
    ambiente()
    if not settings.sicredi_pix_client_id or not settings.sicredi_pix_client_secret:
        raise PixError('Credenciais da API Pix não configuradas.')
    for field in ('sicredi_pix_cert_path', 'sicredi_pix_key_path', 'sicredi_pix_ca_path'):
        if not getattr(settings, field) or not Path(getattr(settings, field)).is_file():
            raise PixError('Certificado, chave privada ou cadeia da API Pix não configurados.')


def exigir_escrita() -> None:
    validar_configuracao()
    if not settings.sicredi_pix_escrita_habilitada:
        raise PixError('Criação de autorizações e agendamentos Pix desativada neste ambiente.')
    if ambiente() == 'producao' and not settings.sicredi_pix_producao_confirmada:
        raise PixError('Operações Pix de produção ainda não liberadas.')


def erro_seguro(message: str) -> str:
    value = str(message)
    for secret in (settings.sicredi_pix_client_secret, settings.sicredi_pix_client_id,
                   settings.sicredi_pix_webhook_token):
        if secret:
            value = value.replace(secret, '[redigido]')
    value = re.sub(r'\beyJ[A-Za-z0-9_.-]+', '[token]', value)
    value = re.sub(r'\b\d{11,14}\b', '[documento]', value)
    return value[:900]


class PixClient:
    def __init__(self, *, escrita: bool = False):
        validar_configuracao()
        if escrita:
            exigir_escrita()
        self.escrita = escrita
        self.host = HOSTS[ambiente()]
        self.scopes = WRITE_SCOPES if escrita else READ_SCOPES
        self._temp = TemporaryDirectory(prefix='mastersat-api-pix-')
        temp = Path(self._temp.name)
        chain = Path(settings.sicredi_pix_ca_path).read_bytes()
        cert_path = temp / 'cliente.pem'
        cert_path.write_bytes(Path(settings.sicredi_pix_cert_path).read_bytes().rstrip() + b'\n' + chain)
        trust_path = temp / 'trust.pem'
        trust_path.write_bytes(Path(certifi.where()).read_bytes().rstrip() + b'\n' + chain)
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.cert = (str(cert_path), settings.sicredi_pix_key_path)
        self.session.verify = str(trust_path)
        self.session.headers['Accept'] = 'application/json'
        fingerprint = '|'.join((self.host, settings.sicredi_pix_client_id, settings.sicredi_pix_client_secret,
                                settings.sicredi_pix_cert_path, sha256(cert_path.read_bytes()).hexdigest(), self.scopes))
        self.cache_key = sha256(fingerprint.encode()).hexdigest()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.session.close()
        self._temp.cleanup()

    def _call(self, method: str, path: str, **kwargs):
        try:
            return self.session.request(method, self.host + path,
                                        timeout=(10, settings.sicredi_pix_timeout_seconds),
                                        allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            error = PixResultadoIncerto if method != 'GET' and path != '/oauth/token' else PixError
            raise error('Falha de comunicação com a API Pix Sicredi; consulte o resultado antes de reenviar.') from exc

    @staticmethod
    def _body(response, *, mutacao: bool = False) -> dict:
        if not 200 <= response.status_code < 300:
            try:
                body = response.json()
                message = body.get('detail') or body.get('title') or 'Pedido recusado pela API Pix.'
            except (ValueError, AttributeError):
                message = 'Resposta inválida da API Pix Sicredi.'
            error = PixResultadoIncerto if mutacao and response.status_code not in (400, 401, 403, 404, 422) else PixError
            raise error(erro_seguro(message), status_code=response.status_code)
        if response.status_code == 204:
            return {}
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError('objeto esperado')
            return body
        except ValueError as exc:
            error = PixResultadoIncerto if mutacao else PixError
            raise error('A API Pix devolveu resposta incompleta; consulte o resultado.') from exc

    def token(self) -> str:
        with _token_lock:
            cached = _tokens.get(self.cache_key)
            if cached and cached[1] > monotonic():
                return cached[0]
            body = self._body(self._call('POST', '/oauth/token',
                              auth=(settings.sicredi_pix_client_id, settings.sicredi_pix_client_secret),
                              data={'grant_type': 'client_credentials', 'scope': self.scopes}))
            token = body.get('access_token')
            if not token:
                raise PixError('API Pix não retornou token de acesso.')
            granted = set(str(body.get('scope', '')).replace('+', ' ').split())
            if not set(self.scopes.split()).issubset(granted):
                raise PixError('Credenciais não possuem todos os escopos necessários para esta operação Pix.')
            _tokens[self.cache_key] = (token, monotonic() + max(0, int(body.get('expires_in', 300)) - 30))
            return token

    def request(self, method: str, path: str, *, params=None, json=None) -> dict:
        if not path.startswith(('/api/v1/', '/api/v2/')) or '?' in path or '..' in path:
            raise PixError('Caminho da API Pix inválido.')
        if method != 'GET':
            if not self.escrita:
                raise PixError('Esta conexão Pix permite somente consultas.')
            exigir_escrita()
        response = self._call(method, path, params=params, json=json,
                              headers={'Authorization': 'Bearer ' + self.token()})
        if response.status_code == 401 and method == 'GET':
            with _token_lock:
                _tokens.pop(self.cache_key, None)
            response = self._call(method, path, params=params,
                                  headers={'Authorization': 'Bearer ' + self.token()})
        return self._body(response, mutacao=method != 'GET')
