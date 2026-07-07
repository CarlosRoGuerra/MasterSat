"""Testes do endpoint de integração externa (CobraZap puxa os boletos)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.core.config import settings
from app.models.billing import Billing
from app.models.enums import BillingStatus


@pytest.fixture()
def api_key(monkeypatch):
    monkeypatch.setattr(settings, 'integration_api_key', 'chave-de-teste')
    return 'chave-de-teste'


@pytest.fixture()
def cobranca(db, cliente) -> Billing:
    """Cobrança em aberto com vencimento próximo (boleto local gera sem estourar o fator)."""
    b = Billing(
        client_id=cliente.id,
        amount=Decimal('99.90'),
        due_date=date.today() + timedelta(days=15),
        status=BillingStatus.PENDING,
        billing_type='recorrente',
        title='Mensalidade',
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


@pytest.fixture()
def cobranca2(db, cliente) -> Billing:
    b = Billing(
        client_id=cliente.id,
        amount=Decimal('150.00'),
        due_date=date.today() - timedelta(days=5),
        status=BillingStatus.OVERDUE,
        billing_type='recorrente',
        title='Mensalidade atrasada',
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


# ── Autenticação ──────────────────────────────────────────────────────────────

def test_sem_chave_configurada_retorna_503(http_unauth, monkeypatch):
    monkeypatch.setattr(settings, 'integration_api_key', '')
    resp = http_unauth.get('/api/v1/integrations/cobrancas')
    assert resp.status_code == 503


def test_chave_invalida_retorna_401(http_unauth, api_key):
    resp = http_unauth.get('/api/v1/integrations/cobrancas', headers={'X-API-Key': 'errada'})
    assert resp.status_code == 401


def test_chave_ausente_retorna_401(http_unauth, api_key):
    resp = http_unauth.get('/api/v1/integrations/cobrancas')
    assert resp.status_code == 401


# ── Listagem ────────────────────────────────────────────────────────────────

def test_lista_cobrancas_abertas(http_unauth, api_key, cobranca, cobranca2):
    resp = http_unauth.get('/api/v1/integrations/cobrancas', headers={'X-API-Key': api_key})
    assert resp.status_code == 200
    body = resp.json()
    assert body['total'] == 2
    ids = {c['id'] for c in body['cobrancas']}
    assert {cobranca.id, cobranca2.id} == ids

    item = next(c for c in body['cobrancas'] if c['id'] == cobranca.id)
    # campos que o CobraZap precisa para enviar a cobrança
    assert item['linha_digitavel']
    assert item['codigo_barras']
    assert item['cliente']['nome'] == 'João Silva'
    assert item['cliente']['telefone'] == '11999990000'
    assert item['forma_envio'] == 'email'  # cliente sem delivery_method → default
    assert item['boleto_pdf_url'].endswith(f'/integrations/cobrancas/{cobranca.id}/pdf')


def test_cobranca_paga_nao_aparece(http_unauth, api_key, cobranca, db):
    cobranca.status = BillingStatus.PAID
    db.commit()
    resp = http_unauth.get('/api/v1/integrations/cobrancas', headers={'X-API-Key': api_key})
    assert resp.json()['total'] == 0


def test_boleto_problematico_degrada_sem_quebrar_lote(http_unauth, api_key, cobranca, billing_pendente):
    # billing_pendente vence em 2099 → o gerador local estoura o fator de vencimento.
    # A cobrança ainda deve aparecer, só que com os campos de boleto nulos.
    resp = http_unauth.get('/api/v1/integrations/cobrancas', headers={'X-API-Key': api_key})
    assert resp.status_code == 200
    body = resp.json()
    assert body['total'] == 2
    problematica = next(c for c in body['cobrancas'] if c['id'] == billing_pendente.id)
    assert problematica['linha_digitavel'] is None
    assert problematica['valor'] == 99.90  # metadados continuam presentes


def test_filtro_forma_envio_whatsapp(http_unauth, api_key, db, cobranca, cliente):
    # cliente padrão é 'email' → filtro whatsapp não retorna nada
    resp = http_unauth.get(
        '/api/v1/integrations/cobrancas',
        params={'forma_envio': 'whatsapp'},
        headers={'X-API-Key': api_key},
    )
    assert resp.json()['total'] == 0

    cliente.delivery_method = 'todos'
    db.commit()
    resp = http_unauth.get(
        '/api/v1/integrations/cobrancas',
        params={'forma_envio': 'whatsapp'},
        headers={'X-API-Key': api_key},
    )
    assert resp.json()['total'] == 1


# ── Detalhe + PDF ─────────────────────────────────────────────────────────────

def test_detalhe_e_pdf(http_unauth, api_key, cobranca):
    det = http_unauth.get(
        f'/api/v1/integrations/cobrancas/{cobranca.id}',
        headers={'X-API-Key': api_key},
    )
    assert det.status_code == 200
    assert det.json()['id'] == cobranca.id

    pdf = http_unauth.get(
        f'/api/v1/integrations/cobrancas/{cobranca.id}/pdf',
        headers={'X-API-Key': api_key},
    )
    assert pdf.status_code == 200
    assert pdf.headers['content-type'] == 'application/pdf'
    assert pdf.content[:4] == b'%PDF'


def test_cobranca_inexistente_404(http_unauth, api_key):
    resp = http_unauth.get('/api/v1/integrations/cobrancas/999999', headers={'X-API-Key': api_key})
    assert resp.status_code == 404
