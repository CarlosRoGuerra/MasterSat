"""Fase 03 — corridas reais em PostgreSQL (sessões concorrentes).

Quem perde a corrida recebe 409/400 de domínio — nunca 500, nunca título
ativo sem obrigação, nunca valor alterado com registro em andamento.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier, Event

import pytest
import requests

from app.core.config import settings
from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus, ClientStatus
from app.models.plan import Plan
from app.services import ailos_client
from tests.fase03_apoio import boleto_json, resp
from tests.test_database_concurrency_postgres import postgres_api  # noqa: F401 — fixture

pytestmark = pytest.mark.postgres


def _em_paralelo(*chamadas):
    barreira = Barrier(len(chamadas))

    def rodar(fn):
        barreira.wait()
        return fn()

    with ThreadPoolExecutor(max_workers=len(chamadas)) as pool:
        return [f.result() for f in [pool.submit(rodar, fn) for fn in chamadas]]


def _cobranca(sessions, *, titulo: str | None = None) -> int:
    with sessions() as db:
        cliente = Client(name='Cliente F03', cpf_cnpj='52998224725', type='pf', status=ClientStatus.ACTIVE,
                         zip_code='89201-000', address_line='Rua A', address_number='1',
                         neighborhood='Centro', city='Joinville', state='SC')
        plano = Plan(name='Plano F03', price=Decimal('100.00'))
        db.add_all([cliente, plano])
        db.flush()
        contrato = Contract(client_id=cliente.id, plan_id=plano.id, start_date=date(2025, 1, 10),
                            status='ativo', billing_day=10)
        db.add(contrato)
        db.flush()
        b = Billing(contract_id=contrato.id, client_id=cliente.id, amount=Decimal('100.00'),
                    due_date=date(2099, 11, 30), status=BillingStatus.PENDING, billing_type='avulsa')
        db.add(b)
        db.flush()
        if titulo == 'registrado':
            db.add(AilosBoleto(billing_id=b.id, numero_convenio='102004', nosso_numero=f'NN{b.id}',
                               linha_digitavel=f'LD{b.id}', codigo_barras=f'CB{b.id}', status_ailos='0'))
        elif titulo:
            db.add(AilosBoleto(billing_id=b.id, numero_convenio='102004', status_ailos=titulo))
        db.commit()
        return b.id


def _ailos_local(monkeypatch, responder):
    monkeypatch.setattr(settings, 'ailos_gateway_base_url', 'https://gateway.ailos.test')
    monkeypatch.setattr(ailos_client, 'get_valid_client_token', lambda db: 't')
    monkeypatch.setattr(ailos_client, 'get_valid_cooperado_token', lambda db: 't')
    monkeypatch.setattr(ailos_client.requests, 'request', responder)


def test_delete_put_cancel_em_paralelo_com_titulo_registrado(postgres_api):
    http, sessions, _ = postgres_api
    bid = _cobranca(sessions, titulo='registrado')
    delete, put, cancel = _em_paralelo(
        lambda: http.delete(f'/api/v1/billings/{bid}'),
        lambda: http.put(f'/api/v1/billings/{bid}', json={'amount': 130, 'justification': 'x'}),
        lambda: http.post(f'/api/v1/billings/{bid}/cancel', json={'reason': 'x', 'confirmar_boleto_ailos': True}),
    )
    assert (delete.status_code, put.status_code, cancel.status_code) in {(409, 409, 200), (409, 400, 200)}
    with sessions() as db:
        b = db.get(Billing, bid)
        boleto = db.query(AilosBoleto).filter_by(billing_id=bid).one()
        assert (b.is_deleted, b.status, b.amount) == (False, BillingStatus.CANCELED, Decimal('100.00'))
        assert boleto.baixa_status == 'pendente'


def test_desfecho_desconhecido_resiste_a_todas_as_mutacoes_simultaneas(postgres_api):
    http, sessions, _ = postgres_api
    bid = _cobranca(sessions, titulo='DESFECHO_DESCONHECIDO')
    respostas = _em_paralelo(
        lambda: http.delete(f'/api/v1/billings/{bid}'),
        lambda: http.put(f'/api/v1/billings/{bid}', json={'amount': 130, 'justification': 'x'}),
        lambda: http.post(f'/api/v1/billings/{bid}/cancel', json={'reason': 'x', 'confirmar_boleto_ailos': True}),
        lambda: http.post(f'/api/v1/billings/{bid}/receive', json={'payment_date': '2099-11-01', 'payment_method': 'pix'}),
    )
    assert [r.status_code for r in respostas] == [409, 409, 409, 409]
    with sessions() as db:
        b = db.get(Billing, bid)
        assert (b.is_deleted, b.status, b.amount) == (False, BillingStatus.PENDING, Decimal('100.00'))


def test_timeout_depois_do_envio_com_manutencao_concorrente(postgres_api, monkeypatch):
    """Enquanto o POST está no ar a manutenção vê 'em registro'; depois do
    timeout a cobrança fica em desfecho desconhecido — nunca liberada."""
    http, sessions, _ = postgres_api
    bid = _cobranca(sessions)
    enviado, liberar = Event(), Event()

    def responder(method, url, **_k):
        enviado.set()
        assert liberar.wait(timeout=10)
        raise requests.ReadTimeout('read timed out')

    _ailos_local(monkeypatch, responder)
    with ThreadPoolExecutor(max_workers=2) as pool:
        registro = pool.submit(http.post, '/api/v1/ailos/boletos', json={'billing_id': bid})
        assert enviado.wait(timeout=10)
        durante = http.post('/api/v1/billings/lote/manutencao',
                            json={'billing_ids': [bid], 'amount': 120, 'justification': 'x'})
        liberar.set()
        registro = registro.result(timeout=10)
    depois = http.put(f'/api/v1/billings/{bid}', json={'amount': 120, 'justification': 'x'})

    assert durante.status_code == 409 and durante.json()['detail']['code'] == 'boleto_ailos_em_registro'
    assert registro.status_code == 502 and registro.json()['detail']['code'] == 'desfecho_desconhecido'
    assert depois.status_code == 409 and depois.json()['detail']['code'] == 'boleto_ailos_desfecho_desconhecido'
    with sessions() as db:
        assert db.get(Billing, bid).amount == Decimal('100.00')


def test_conciliacao_nao_quita_cobranca_cancelada_durante_a_consulta(postgres_api, monkeypatch):
    from app.services.ailos_boletos import verificar_pagamento

    http, sessions, _ = postgres_api
    bid = _cobranca(sessions, titulo='registrado')
    consultando, cancelada = Event(), Event()

    def responder(method, url, **_k):
        consultando.set()
        assert cancelada.wait(timeout=10)
        return resp(200, boleto_json(bid, valor=100.0, pago=Decimal('100.00'), situacao=5))

    _ailos_local(monkeypatch, responder)

    def conciliar():
        with sessions() as db:
            return verificar_pagamento(db, db.get(Billing, bid))

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(conciliar)
        assert consultando.wait(timeout=10)
        cancel = http.post(f'/api/v1/billings/{bid}/cancel', json={'reason': 'x', 'confirmar_boleto_ailos': True})
        cancelada.set()
        resultado = fut.result(timeout=10)

    assert cancel.status_code == 200
    assert resultado['quitada'] is False and resultado['pendencia'] == 'pago_em_cobranca_cancelada'
    with sessions() as db:
        assert db.get(Billing, bid).status == BillingStatus.CANCELED


def test_pagar_e_cancelar_conta_a_pagar_em_paralelo(postgres_api):
    from app.models.payable import Payable

    http, sessions, _ = postgres_api
    pid = http.post('/api/v1/payables/', json={'description': 'Chips', 'amount': 80, 'due_date': '2099-10-10'}).json()['id']
    pagar, cancelar = _em_paralelo(
        lambda: http.post(f'/api/v1/payables/{pid}/pay', json={'payment_date': '2099-10-10', 'payment_method': 'pix'}),
        lambda: http.post(f'/api/v1/payables/{pid}/cancel'),
    )
    codigos = sorted([pagar.status_code, cancelar.status_code])
    assert codigos in ([200, 400], [200, 409])
    with sessions() as db:
        conta = db.get(Payable, pid)
        esperado = 'paga' if pagar.status_code == 200 else 'cancelada'
        assert conta.status == esperado
        assert (conta.payment_date is not None) == (esperado == 'paga')


def test_duas_remessas_cnab_para_o_mesmo_titulo(postgres_api, monkeypatch):
    http, sessions, _ = postgres_api
    monkeypatch.setattr(settings, 'cnab_remessa_habilitada', True)
    bid = _cobranca(sessions)
    r240, r400 = _em_paralelo(
        lambda: http.post('/api/v1/boletos/cnab240', json=[bid]),
        lambda: http.post('/api/v1/boletos/cnab400', json=[bid]),
    )
    assert sorted([r240.status_code, r400.status_code]) == [200, 409]
    with sessions() as db:
        from app.models.cnab_remessa import CnabRemessaItem
        assert db.query(CnabRemessaItem).filter_by(billing_id=bid, status='reservado').count() == 1
