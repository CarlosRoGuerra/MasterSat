"""Apoio dos testes da Fase 03: títulos bancários sintéticos e Ailos falsa.

Nenhum teste chama a Ailos: o transporte HTTP do cliente é substituído e os
tokens são fixos. Os dados bancários são fixtures independentes (não saem do
próprio código que está sendo testado).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.enums import BillingStatus
from app.services import ailos_client


@pytest.fixture()
def ailos_falsa(monkeypatch):
    """Configura a Ailos de teste; devolve a função que instala o responder."""
    monkeypatch.setattr(settings, 'ailos_gateway_base_url', 'https://gateway.ailos.test')
    monkeypatch.setattr(settings, 'ailos_timeout_seconds', 5)
    monkeypatch.setattr(settings, 'ailos_numero_convenio', '102004')
    monkeypatch.setattr(ailos_client, 'get_valid_client_token', lambda db: 'client-token')
    monkeypatch.setattr(ailos_client, 'get_valid_cooperado_token', lambda db: 'coop-token')
    chamadas: list[tuple[str, str]] = []

    def instalar(responder):
        def request(method, url, **kwargs):
            chamadas.append((method, url))
            resultado = responder(method, url, **kwargs)
            if isinstance(resultado, BaseException):
                raise resultado
            return resultado
        monkeypatch.setattr(ailos_client.requests, 'request', request)
        return chamadas

    return instalar


def resp(status_code=200, json_data=None):
    r = MagicMock()
    r.status_code = status_code
    r.headers = {'Content-Type': 'application/json'}
    r.text = '' if json_data is None else str(json_data)
    r.content = r.text.encode()
    if json_data is not None:
        r.json.return_value = json_data
    else:
        r.json.side_effect = ValueError('sem json')
    return r


def boleto_json(billing_id: int, *, valor=99.9, pago: Decimal | float | None = None,
                situacao=0, data_pagamento: date | None = None, linha=True) -> dict:
    corpo = {
        'documento': {'numeroDocumento': billing_id, 'nossoNumero': f'NN{billing_id:08d}'},
        'indicadorSituacaoBoleto': situacao,
        'valorBoleto': {'valorNominal': valor, 'valorPago': float(pago) if pago is not None else 0},
        'pagamento': {'dataPagamento': (data_pagamento or date(2026, 9, 10)).isoformat()
                      if pago is not None else '0001-01-01T00:00:00'},
        'vencimento': {'dataVencimento': '2099-12-31'},
    }
    if linha:
        corpo['codigoBarras'] = {'linhaDigitavel': f'LD{billing_id}', 'codigoBarras': f'CB{billing_id}'}
    return {'boleto': corpo}


def registrar_titulo(db, billing: Billing, **extra) -> AilosBoleto:
    """Título já registrado na Ailos (linha digitável + código de barras)."""
    boleto = AilosBoleto(
        billing_id=billing.id, numero_convenio='102004', numero_documento=str(billing.id),
        nosso_numero=f'NN{billing.id:08d}', linha_digitavel=f'LD{billing.id}',
        codigo_barras=f'CB{billing.id}', status_ailos='0', valor_nominal=billing.amount,
        data_vencimento=billing.due_date, **extra,
    )
    db.add(boleto)
    db.commit()
    db.refresh(boleto)
    return boleto


def reserva(db, billing: Billing, status: str, *, minutos_atras: int = 0) -> AilosBoleto:
    """Reserva de registro sem título (REGISTRANDO/PROCESSANDO/DESFECHO/ERRO)."""
    boleto = AilosBoleto(
        billing_id=billing.id, numero_convenio='102004', status_ailos=status,
        payload_request={'documento': {'numeroDocumento': billing.id}},
        registro_iniciado_em=datetime.now(timezone.utc) - timedelta(minutes=minutos_atras),
    )
    db.add(boleto)
    db.commit()
    db.refresh(boleto)
    return boleto


def cobranca(db, contrato, **kw) -> Billing:
    dados = dict(
        contract_id=contrato.id, client_id=contrato.client_id, amount=Decimal('100.00'),
        due_date=date(2099, 11, 30), status=BillingStatus.PENDING, billing_type='avulsa',
        title='Cobrança teste',
    )
    dados.update(kw)
    b = Billing(**dados)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def preencher_endereco(db, cliente) -> None:
    cliente.zip_code = '89201-000'
    cliente.address_line = 'Rua A'
    cliente.address_number = '1'
    cliente.neighborhood = 'Centro'
    cliente.city = 'Joinville'
    cliente.state = 'SC'
    db.commit()
