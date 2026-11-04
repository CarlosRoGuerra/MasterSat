"""Fakes do HTTP do Sicredi para os testes (o cliente usa ``requests``)."""
from __future__ import annotations

import json as _json

import pytest
import requests

from app.core.config import settings
from app.services import sicredi_client


class Resp:
    def __init__(self, status: int, body=None, *, content: bytes | None = None, headers: dict | None = None):
        self.status_code = status
        self._body = body
        if content is None:
            content = _json.dumps(body).encode() if body is not None else b''
        self.content = content
        self.headers = headers or {'Content-Type': 'application/json'}
        self.text = content.decode('latin-1', errors='replace')

    def json(self):
        if self._body is None:
            raise ValueError('sem JSON')
        return self._body


class SicrediFalso:
    """Substitui ``requests`` dentro de ``sicredi_client``.

    ``quando(metodo, caminho, *respostas)`` enfileira respostas (a última se
    repete); uma resposta pode ser ``Resp``, exceção ou função(params, json, headers).
    """

    RequestException = requests.RequestException

    def __init__(self):
        self.chamadas: list[tuple] = []
        self.tokens: list[dict] = []
        self.rotas: dict[tuple[str, str], list] = {}
        self.resposta_token = None

    def post(self, url, data=None, headers=None, timeout=None):
        self.tokens.append({'url': url, 'data': dict(data or {}), 'headers': dict(headers or {})})
        if self.resposta_token is not None:
            return self.resposta_token
        n = len(self.tokens)
        return Resp(200, {'access_token': f'tok{n}', 'refresh_token': f'ref{n}', 'token_type': 'Bearer',
                          'expires_in': 300, 'refresh_expires_in': 1800, 'scope': 'cobranca'})

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        caminho = url.split('api-parceiro.sicredi.com.br', 1)[1]
        if caminho.startswith('/sb/'):
            caminho = caminho[3:]
        self.chamadas.append((method, caminho, {'params': params, 'json': json, 'headers': dict(headers or {})}))
        fila = self.rotas.get((method, caminho))
        if not fila:
            raise AssertionError(f'Chamada inesperada ao Sicredi: {method} {caminho}')
        resposta = fila.pop(0) if len(fila) > 1 else fila[0]
        if isinstance(resposta, BaseException):
            raise resposta
        if callable(resposta):
            return resposta(params=params, json=json, headers=headers)
        return resposta

    def quando(self, metodo: str, caminho: str, *respostas) -> None:
        self.rotas[(metodo, caminho)] = list(respostas)

    def chamadas_de(self, metodo: str, caminho: str) -> list[dict]:
        return [c[2] for c in self.chamadas if c[0] == metodo and c[1] == caminho]


@pytest.fixture()
def sicredi(monkeypatch):
    falso = SicrediFalso()
    monkeypatch.setattr(sicredi_client, 'requests', falso)
    monkeypatch.setattr(settings, 'sicredi_api_key', '11111111-2222-3333-4444-555555555555')
    monkeypatch.setattr(settings, 'sicredi_ambiente', 'sandbox')
    monkeypatch.setattr(settings, 'sicredi_producao_confirmada', False)
    monkeypatch.setattr(settings, 'sicredi_boleto_hibrido', False)
    sicredi_client.limpar_tokens()
    yield falso
    sicredi_client.limpar_tokens()


def resposta_cadastro(nosso_numero: str, *, qr: str | None = None) -> Resp:
    return Resp(201, {
        'txid': 'tx1' if qr else None,
        'qrCode': qr,
        'linhaDigitavel': '74891125110061420512803153351030188640000009990',
        'codigoBarras': '74891886400000099901125100614205120315335103',
        'cooperativa': '6789',
        'posto': '03',
        'nossoNumero': nosso_numero,
    })


def resposta_consulta(nosso_numero: str, seu_numero: str, situacao: str = 'EM CARTEIRA', *,
                      liquidacao: dict | None = None) -> Resp:
    corpo = {
        'linhaDigitavel': '74891125110061420512803153351030188640000009990',
        'codigoBarras': '74891886400000099901125100614205120315335103',
        'nossoNumero': nosso_numero,
        'seuNumero': seu_numero,
        'situacao': situacao,
        'valorNominal': 99.9,
    }
    if liquidacao is not None:
        corpo['dadosLiquidacao'] = liquidacao
    return Resp(200, corpo)
