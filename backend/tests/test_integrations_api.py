"""Testes do endpoint de integração externa (CobraZap puxa os boletos)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.core.config import settings
from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus, ClientStatus
from app.models.plan import Plan


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


@pytest.fixture()
def boleto_registrado(db, cobranca) -> AilosBoleto:
    """Boleto oficial Ailos vinculado à cobrança (título registrado/pagável)."""
    ab = AilosBoleto(
        billing_id=cobranca.id,
        numero_convenio='102004',
        numero_documento=str(cobranca.id),
        linha_digitavel='08591.02006 40045.470206 00000.003012 5 14890000009990',
        codigo_barras='08595148900000099901020040045470200000000301',
        pix_emv='000201teste',
    )
    db.add(ab)
    db.commit()
    db.refresh(ab)
    return ab


def test_cobranca_usa_interveniente_como_pagador(http_unauth, api_key, db, cliente):
    """Com interveniente no contrato, o CobraZap recebe a cobrança no nome do
    interveniente (quem paga e recebe a mensagem)."""
    interv = Client(name='FINANCEIRA XPTO', cpf_cnpj='11144477735', type='pj',
                    status=ClientStatus.ACTIVE, issue_invoice='sim')
    db.add(interv)
    db.commit()
    db.refresh(interv)
    plan = Plan(name='PLANO CZ', price=Decimal('100.00'))
    db.add(plan)
    db.commit()
    db.refresh(plan)
    contrato = Contract(client_id=cliente.id, plan_id=plan.id,
                        interveniente_client_id=interv.id,
                        start_date=date(2024, 1, 1), status='ativo', billing_day=10)
    db.add(contrato)
    db.commit()
    db.refresh(contrato)
    b = Billing(client_id=cliente.id, contract_id=contrato.id, amount=Decimal('99.90'),
                due_date=date.today() + timedelta(days=15), status=BillingStatus.PENDING,
                billing_type='recorrente', title='Mensalidade')
    db.add(b)
    db.commit()
    db.refresh(b)

    resp = http_unauth.get('/api/v1/integrations/cobrancas', headers={'X-API-Key': api_key})
    assert resp.status_code == 200
    item = next(c for c in resp.json()['cobrancas'] if c['id'] == b.id)
    assert item['cliente']['nome'] == 'FINANCEIRA XPTO'
    assert item['cliente']['cpf_cnpj'] == '11144477735'


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

def test_lista_cobrancas_abertas(http_unauth, api_key, cobranca, cobranca2, boleto_registrado):
    resp = http_unauth.get('/api/v1/integrations/cobrancas', headers={'X-API-Key': api_key})
    assert resp.status_code == 200
    body = resp.json()
    assert body['total'] == 2
    ids = {c['id'] for c in body['cobrancas']}
    assert {cobranca.id, cobranca2.id} == ids

    # REGISTRADA na Ailos → entrega os dados de pagamento
    item = next(c for c in body['cobrancas'] if c['id'] == cobranca.id)
    assert item['boleto_registrado'] is True
    assert item['linha_digitavel']
    assert item['codigo_barras']
    assert item['pix_copia_cola'] == '000201teste'
    assert item['cliente']['nome'] == 'João Silva'
    assert item['cliente']['telefone'] == '11999990000'
    assert item['forma_envio'] == 'email'  # cliente sem delivery_method → default
    assert item['boleto_pdf_url'].endswith(f'/integrations/cobrancas/{cobranca.id}/pdf')

    # SEM registro → não é pagável no banco → campos de pagamento nulos
    sem_registro = next(c for c in body['cobrancas'] if c['id'] == cobranca2.id)
    assert sem_registro['boleto_registrado'] is False
    assert sem_registro['linha_digitavel'] is None
    assert sem_registro['codigo_barras'] is None
    assert sem_registro['boleto_link_cliente'] is None


def test_vencida_traz_valor_com_juros(http_unauth, api_key, cobranca2):
    resp = http_unauth.get('/api/v1/integrations/cobrancas', headers={'X-API-Key': api_key})
    item = next(c for c in resp.json()['cobrancas'] if c['id'] == cobranca2.id)
    # 5 dias de atraso → multa 2% + 1 mês de juros 1% = 150 × 1.03
    assert item['valor_com_juros'] == 154.50


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


# ── Link público do boleto (token HMAC, sem login) ───────────────────────────

def test_boleto_link_publico(http_unauth, cobranca, boleto_registrado):
    from app.api.v1.endpoints.boletos import _public_token

    ok = http_unauth.get(f'/api/v1/public/boleto/{cobranca.id}/{_public_token(cobranca.id)}')
    assert ok.status_code == 200
    assert ok.headers['content-type'] == 'application/pdf'
    assert ok.content[:4] == b'%PDF'

    errado = http_unauth.get(f'/api/v1/public/boleto/{cobranca.id}/token-invalido')
    assert errado.status_code == 404


def test_link_publico_sem_registro_na_ailos_nao_entrega_pdf(http_unauth, cobranca):
    """Sem registro na Ailos o título não existe no banco: não é pagável e não
    concilia. Entregar o PDF ao cliente por esse link seria mandar um papel
    impagável — 404, e não o boleto calculado localmente."""
    from app.api.v1.endpoints.boletos import _public_token

    resp = http_unauth.get(f'/api/v1/public/boleto/{cobranca.id}/{_public_token(cobranca.id)}')
    assert resp.status_code == 404


def test_listagem_inclui_link_publico(http_unauth, api_key, cobranca, boleto_registrado):
    resp = http_unauth.get('/api/v1/integrations/cobrancas', headers={'X-API-Key': api_key})
    item = resp.json()['cobrancas'][0]
    assert '/public/boleto/' in item['boleto_link_cliente']
