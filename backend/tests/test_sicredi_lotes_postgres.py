"""Concorrência real da fila Sicredi; rede bancária substituída por fake."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier, Event

import pytest

from app.models.billing import Billing
from app.models.client import Client
from app.models.enums import BillingStatus
from app.models.sicredi_boleto import SicrediBoleto
from app.models.sicredi_lote import SicrediLote
from app.services import sicredi_lotes
from tests.sicredi_apoio import resposta_cadastro, sicredi  # noqa: F401
from tests.test_database_concurrency_postgres import postgres_api  # noqa: F401

pytestmark = pytest.mark.postgres
BOLETOS = '/cobranca/boleto/v1/boletos'


def _parcelas(sessions):
    with sessions() as db:
        c = Client(name='Cliente concorrencia Sicredi', cpf_cnpj='52998224725', type='pf')
        db.add(c)
        db.flush()
        itens = [Billing(client_id=c.id, amount=Decimal('10.00'), due_date=date(2099, m, 15),
                         status=BillingStatus.PENDING, billing_type='carne') for m in (1, 2)]
        db.add_all(itens)
        db.commit()
        return [b.id for b in itens]


def test_criacao_concorrente_reutiliza_lote_e_reservas(postgres_api, sicredi):
    _, sessions, _ = postgres_api
    ids = _parcelas(sessions)
    barreira = Barrier(2)

    def criar():
        barreira.wait(timeout=10)
        with sessions() as db:
            return sicredi_lotes.criar_lote(db, ids).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futuros = [pool.submit(criar) for _ in range(2)]
        assert len({f.result(timeout=15) for f in futuros}) == 1
    with sessions() as db:
        assert db.query(SicrediLote).count() == 1
        assert db.query(SicrediBoleto).count() == 2
    assert sicredi.chamadas == []


def test_dois_processadores_nao_enviam_a_mesma_parcela(postgres_api, sicredi):
    http, sessions, _ = postgres_api
    ids = _parcelas(sessions)
    with sessions() as db:
        lote_id = sicredi_lotes.criar_lote(db, ids).id
    # As reservas já impedem manutenção antes de iniciar o POST.
    assert http.put(f'/api/v1/billings/{ids[1]}', json={'amount': 20, 'justification': 'teste'}).status_code == 409
    enviado, liberar = Event(), Event()

    def responder(**kwargs):
        enviado.set()
        assert liberar.wait(timeout=15)
        return resposta_cadastro(kwargs['json']['nossoNumero'])

    sicredi.quando('POST', BOLETOS, responder)

    def processar():
        with sessions() as db:
            return sicredi_lotes.processar_lote(db, lote_id, retentar=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        primeiro = pool.submit(processar)
        try:
            assert enviado.wait(timeout=10)
            segundo = pool.submit(processar).result(timeout=5)
            assert segundo['prontas'] == 0
            assert len(sicredi.chamadas_de('POST', BOLETOS)) == 1
        finally:
            liberar.set()
        assert primeiro.result(timeout=15)['prontas'] == 2
    assert len(sicredi.chamadas_de('POST', BOLETOS)) == 2
