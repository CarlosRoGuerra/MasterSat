"""
Cadastro/consulta de pagadores via API de Cobrança Ailos (v1/pagadores).

Passthrough para a Ailos — sem persistência local própria. ``Client`` não
ganha colunas novas; toda a comunicação é registrada em ``ailos_api_logs``
por ``app.services.ailos_client.request``.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.client import Client
from app.services import ailos_client
from app.services.ailos_boletos import montar_dados_pagador

_PATH_CADASTRAR = '/v1/pagadores/cadastrar'
_PATH_ALTERAR = '/v1/pagadores/alterar'
_PATH_CONSULTAR = '/v1/pagadores/consultar/{numero_inscricao}'
_PATH_LISTAR = '/v1/pagadores/listar'


def montar_payload_pagador(client: Client) -> dict:
    """Monta o payload ``{"pagador": {...}}`` para cadastro/alteração de pagador."""
    dados = montar_dados_pagador(client)
    dados['dda'] = True
    return {'pagador': dados}


def cadastrar_pagador(db: Session, client: Client) -> dict | list | None:
    """Cadastra o cliente como pagador na Ailos."""
    payload = montar_payload_pagador(client)
    resp = ailos_client.request(db, 'POST', _PATH_CADASTRAR, json_body=payload)
    return resp.json


def alterar_pagador(db: Session, client: Client) -> dict | list | None:
    """Atualiza os dados do cliente como pagador na Ailos."""
    payload = montar_payload_pagador(client)
    resp = ailos_client.request(db, 'PUT', _PATH_ALTERAR, json_body=payload)
    return resp.json


def consultar_pagador(db: Session, numero_inscricao: str) -> dict | list | None:
    """Consulta um pagador na Ailos pelo CPF/CNPJ (numeroInscricao)."""
    resp = ailos_client.request(db, 'GET', _PATH_CONSULTAR.format(numero_inscricao=numero_inscricao))
    return resp.json


def listar_pagadores(db: Session) -> dict | list | None:
    """Lista os pagadores cadastrados na Ailos."""
    resp = ailos_client.request(db, 'GET', _PATH_LISTAR)
    return resp.json
