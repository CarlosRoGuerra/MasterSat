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

def test_detalhe_e_pdf(http_unauth, api_key, cobranca, boleto_registrado):
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


# ── Filtros e confirmação financeira solicitados pela integradora ───────────

def _consultar(http_unauth, api_key, **params):
    return http_unauth.get(
        '/api/v1/integrations/cobrancas', params=params, headers={'X-API-Key': api_key},
    )


def _criar_cobranca(db, cliente, *, status=BillingStatus.PENDING,
                    vencimento=date(2026, 10, 10), **kwargs):
    billing = Billing(
        client_id=cliente.id, amount=Decimal('100.00'), due_date=vencimento,
        status=status, billing_type='avulsa', **kwargs,
    )
    db.add(billing)
    db.commit()
    return billing


@pytest.mark.parametrize('filtro', ['pendente', 'vencida', 'paga', 'cancelada', 'todos', None])
def test_filtro_status_inclui_pagamento_confirmado(http_unauth, api_key, db, cliente, filtro):
    registros = {status.value: _criar_cobranca(db, cliente, status=status) for status in BillingStatus}
    params = {'status': filtro} if filtro else {}
    resp = _consultar(http_unauth, api_key, **params)
    assert resp.status_code == 200
    esperado = ({'pendente', 'vencida'} if filtro is None else
                set(registros) if filtro == 'todos' else {filtro})
    assert {item['status'] for item in resp.json()['cobrancas']} == esperado
    assert resp.json()['total_registros'] == len(esperado)


def test_pagamento_confirmado_traz_mesmos_dados_no_detalhe_e_lista(http_unauth, api_key, db, cliente):
    paga = _criar_cobranca(
        db, cliente, status=BillingStatus.PAID, payment_date=date(2026, 10, 7),
        paid_amount=Decimal('103.00'), payment_method='pix',
    )
    item = _consultar(http_unauth, api_key, status='paga').json()['cobrancas'][0]
    assert item['id'] == paga.id
    assert item['pagamento_confirmado'] is True
    assert item['data_pagamento'] == '2026-10-07'
    assert item['valor_pago'] == 103.00
    assert item['forma_pagamento'] == 'pix'
    assert item['valor'] == 100.00
    assert item['boleto_disponivel'] is False
    assert item['motivo_boleto_indisponivel'] == 'cobranca_paga'
    assert item['boleto_link_cliente'] is None
    detalhe = http_unauth.get(
        f'/api/v1/integrations/cobrancas/{paga.id}', headers={'X-API-Key': api_key},
    )
    assert detalhe.status_code == 200
    assert detalhe.json() == item


def test_pagamento_legado_nao_inventa_data_ou_valor(http_unauth, api_key, db, cliente):
    _criar_cobranca(db, cliente, status=BillingStatus.PAID)
    item = _consultar(http_unauth, api_key, status='paga').json()['cobrancas'][0]
    assert item['pagamento_confirmado'] is True
    assert item['data_pagamento'] is None
    assert item['valor_pago'] is None
    assert item['nosso_numero'] is None


@pytest.mark.parametrize('status_ailos,baixa_status', [
    ('0', None), ('5', None), ('3', 'confirmada'), ('5', 'confirmada'),
])
def test_pagamento_recente_preserva_referencia_oficial_do_boleto_na_lista_e_detalhe(
    http_unauth, api_key, db, cobranca, boleto_registrado, status_ailos, baixa_status,
):
    cobranca.status = BillingStatus.PAID
    cobranca.payment_date = date(2026, 10, 6)
    cobranca.paid_amount = cobranca.amount
    cobranca.payment_method = 'boleto'
    boleto_registrado.nosso_numero = '00000123000456789'
    boleto_registrado.status_ailos = status_ailos
    boleto_registrado.baixa_status = baixa_status
    db.commit()

    resposta = _consultar(
        http_unauth, api_key, status='paga', pagamento_de='2026-10-01', pagamento_ate='2026-10-07',
    )
    assert resposta.status_code == 200
    item = resposta.json()['cobrancas'][0]
    assert item['id'] == cobranca.id
    assert item['pagamento_confirmado'] is True
    assert item['data_pagamento'] == '2026-10-06'
    assert item['forma_pagamento'] == 'boleto'
    assert item['nosso_numero'] == '00000123000456789'
    assert item['boleto_disponivel'] is False
    assert item['motivo_boleto_indisponivel'] == 'cobranca_paga'
    for campo in ('linha_digitavel', 'codigo_barras', 'pix_copia_cola', 'boleto_pdf_url', 'boleto_link_cliente'):
        assert item[campo] is None

    detalhe = http_unauth.get(
        f'/api/v1/integrations/cobrancas/{cobranca.id}', headers={'X-API-Key': api_key},
    )
    assert detalhe.status_code == 200
    assert detalhe.json() == item
    pdf = http_unauth.get(
        f'/api/v1/integrations/cobrancas/{cobranca.id}/pdf', headers={'X-API-Key': api_key},
    )
    assert pdf.status_code == 409
    assert pdf.json()['detail']['code'] == 'cobranca_paga'


def test_pagamento_com_registro_sem_nosso_numero_nao_inventa_referencia(
    http_unauth, api_key, db, cobranca, boleto_registrado,
):
    cobranca.status = BillingStatus.PAID
    db.commit()
    item = _consultar(http_unauth, api_key, status='paga').json()['cobrancas'][0]
    assert item['boleto_registrado'] is True
    assert item['nosso_numero'] is None


@pytest.mark.parametrize('params,esperados', [
    ({'vencimento': '2026-10-10'}, {10}),
    ({'vencimento_de': '2026-10-10', 'vencimento_ate': '2026-10-11'}, {10, 11}),
    ({'vencimento_de': '2026-10-11'}, {11, 12}),
    ({'vencimento_ate': '2026-10-10'}, {9, 10}),
    ({'vencimento': '2026-10-10', 'vencimento_de': '2026-10-11'}, set()),
])
def test_filtros_vencimento_inclusivos(http_unauth, api_key, db, cliente, params, esperados):
    ids = {dia: _criar_cobranca(db, cliente, vencimento=date(2026, 10, dia)).id for dia in (9, 10, 11, 12)}
    resp = _consultar(http_unauth, api_key, **params)
    assert resp.status_code == 200
    assert {item['id'] for item in resp.json()['cobrancas']} == {ids[dia] for dia in esperados}
    assert resp.json()['total_registros'] == len(esperados)


def test_status_canal_vencimento_e_pagamento_combinam(http_unauth, api_key, db, cliente, outro_cliente):
    cliente.delivery_method = 'whatsapp'
    outro_cliente.delivery_method = 'email'
    db.commit()
    alvo = _criar_cobranca(db, cliente, status=BillingStatus.PAID, payment_date=date(2026, 10, 7))
    _criar_cobranca(db, cliente, status=BillingStatus.PAID, payment_date=date(2026, 10, 6))
    _criar_cobranca(db, cliente, status=BillingStatus.PAID,
                    vencimento=date(2026, 10, 11), payment_date=date(2026, 10, 7))
    _criar_cobranca(db, outro_cliente, status=BillingStatus.PAID, payment_date=date(2026, 10, 7))
    _criar_cobranca(db, cliente)
    item = _consultar(
        http_unauth, api_key, status='paga', forma_envio='whatsapp', vencimento='2026-10-10',
        pagamento_de='2026-10-07', pagamento_ate='2026-10-07',
    ).json()
    assert item['total_registros'] == 1
    assert item['cobrancas'][0]['id'] == alvo.id


@pytest.mark.parametrize('params', [
    {'status': 'desconhecido'}, {'forma_envio': 'sms'},
    {'vencimento': '07/10/2026'}, {'vencimento': '2026-02-30'},
    {'vencimento_de': 'invalida'}, {'vencimento_ate': '2026-02-30'},
    {'pagamento_de': 'invalida'}, {'pagamento_ate': '2026-02-30'},
    {'vencimento_de': '2026-10-11', 'vencimento_ate': '2026-10-10'},
    {'pagamento_de': '2026-10-08', 'pagamento_ate': '2026-10-07'},
    {'offset': -1}, {'offset': 'abc'}, {'limit': 0}, {'limit': 2001},
])
def test_parametros_invalidos_retorna_422(http_unauth, api_key, params):
    assert _consultar(http_unauth, api_key, **params).status_code == 422


def test_paginacao_ordenacao_estavel_e_total_sem_limite(http_unauth, api_key, db, cliente):
    # Vencimentos empatados precisam do ID como desempate; ambos os filtros
    # e o total global devem ser aplicados ANTES do offset/limit.
    ids = [_criar_cobranca(db, cliente).id for _ in range(5)]
    _criar_cobranca(db, cliente, status=BillingStatus.PAID)
    vistos = []
    for offset, restantes in [(0, True), (2, True), (4, False)]:
        pagina = _consultar(http_unauth, api_key, limit=2, offset=offset).json()
        assert pagina['total_registros'] == 5
        assert pagina['total'] == len(pagina['cobrancas'])
        assert pagina['limit'] == 2
        assert pagina['offset'] == offset
        assert pagina['has_more'] is restantes
        assert pagina['next_offset'] == (offset + 2 if restantes else None)
        vistos.extend(item['id'] for item in pagina['cobrancas'])
    assert vistos == ids
    fim = _consultar(http_unauth, api_key, limit=2, offset=100).json()
    assert fim['total'] == 0
    assert fim['total_registros'] == 5
    assert fim['has_more'] is False
    assert fim['next_offset'] is None


def test_paginacao_permite_consultar_mais_de_2000_registros(http_unauth, api_key, db, cliente):
    db.add_all([
        Billing(client_id=cliente.id, amount=Decimal('1.00'), due_date=date(2026, 10, 10),
                status=BillingStatus.PENDING, billing_type='avulsa') for _ in range(2003)
    ])
    db.commit()
    primeira = _consultar(http_unauth, api_key, limit=2000).json()
    segunda = _consultar(http_unauth, api_key, limit=2000, offset=primeira['next_offset']).json()
    assert primeira['total'] == 2000
    assert primeira['total_registros'] == segunda['total_registros'] == 2003
    assert segunda['total'] == 3
    assert primeira['has_more'] is True
    assert segunda['has_more'] is False
    ids = [item['id'] for item in primeira['cobrancas'] + segunda['cobrancas']]
    assert len(set(ids)) == 2003


def test_leitura_nao_reclassifica_ou_confirma_pagamento(http_unauth, api_key, db, cliente):
    aberta = _criar_cobranca(db, cliente, vencimento=date(2020, 1, 1))
    item = _consultar(http_unauth, api_key, status='pendente').json()['cobrancas'][0]
    assert item['pagamento_confirmado'] is False
    assert item['data_pagamento'] is None
    db.refresh(aberta)
    assert aberta.status == BillingStatus.PENDING
    assert aberta.paid_amount is None


# ── Coerência entre filtro de canal, pagador e dados bancários oficiais ──────

@pytest.mark.parametrize('snapshot', [False, True])
@pytest.mark.parametrize('canal_origem,canal_pagador,esperado', [
    ('email', 'whatsapp', True), ('whatsapp', 'email', False),
])
def test_filtro_canal_usa_pagador_resolvido(
    http_unauth, api_key, db, cliente, outro_cliente, contrato, snapshot,
    canal_origem, canal_pagador, esperado,
):
    cliente.delivery_method = canal_origem
    outro_cliente.delivery_method = canal_pagador
    if not snapshot:
        contrato.interveniente_client_id = outro_cliente.id
    db.commit()
    billing = _criar_cobranca(
        db, cliente, contract_id=contrato.id,
        payer_client_id=outro_cliente.id if snapshot else None,
    )
    result = _consultar(http_unauth, api_key, forma_envio='whatsapp').json()
    assert result['total_registros'] == int(esperado)
    if esperado:
        assert result['cobrancas'][0]['id'] == billing.id
        assert result['cobrancas'][0]['cliente']['id'] == outro_cliente.id
        assert result['cobrancas'][0]['forma_envio'] == 'whatsapp'


def test_snapshot_pagador_tem_precedencia_sobre_interveniente(http_unauth, api_key, db, cliente, outro_cliente, contrato):
    cliente.delivery_method = 'email'
    outro_cliente.delivery_method = 'whatsapp'
    contrato.interveniente_client_id = outro_cliente.id
    db.commit()
    _criar_cobranca(db, cliente, contract_id=contrato.id, payer_client_id=cliente.id)
    assert _consultar(http_unauth, api_key, forma_envio='whatsapp').json()['total'] == 0


def test_interveniente_removido_usa_origem(http_unauth, api_key, db, cliente, outro_cliente, contrato):
    cliente.send_boleto_whatsapp = True
    outro_cliente.is_deleted = True
    contrato.interveniente_client_id = outro_cliente.id
    db.commit()
    _criar_cobranca(db, cliente, contract_id=contrato.id)
    item = _consultar(http_unauth, api_key, forma_envio='whatsapp').json()['cobrancas'][0]
    assert item['cliente']['id'] == cliente.id


def test_pagador_snapshot_removido_nao_causa_500(http_unauth, api_key, db, cliente, outro_cliente):
    outro_cliente.is_deleted = True
    db.commit()
    billing = _criar_cobranca(db, cliente, payer_client_id=outro_cliente.id)
    assert _consultar(http_unauth, api_key, status='todos').json()['total'] == 0
    resp = http_unauth.get(
        f'/api/v1/integrations/cobrancas/{billing.id}', headers={'X-API-Key': api_key},
    )
    assert resp.status_code == 404


def test_lista_dados_oficiais_sem_gerar_boleto_local(http_unauth, api_key, db, cobranca, boleto_registrado, monkeypatch):
    from app.api.v1.endpoints import integrations

    cobranca.due_date = date(2099, 12, 31)
    boleto_registrado.nosso_numero = '00000123'
    boleto_registrado.pix_emv = None
    db.commit()
    monkeypatch.setattr(integrations, '_dados_boleto', lambda *args: pytest.fail('Lista não deve calcular boleto'))
    item = _consultar(http_unauth, api_key).json()['cobrancas'][0]
    assert item['boleto_registrado'] is True
    assert item['boleto_disponivel'] is True
    assert item['nosso_numero'] == '00000123'
    assert item['linha_digitavel'] == boleto_registrado.linha_digitavel
    assert item['codigo_barras'] == boleto_registrado.codigo_barras
    assert item['pix_copia_cola'] is None


@pytest.mark.parametrize('situacao,motivo', [
    ('sem_codigo', 'sem_registro_bancario'), ('sem_linha', 'sem_registro_bancario'),
    ('baixa_pendente', 'boleto_baixado'), ('baixa_confirmada', 'boleto_baixado'),
    ('liquidado', 'boleto_baixado'), ('paga', 'cobranca_paga'),
    ('cancelada', 'cobranca_cancelada'), ('somente_sistema', 'somente_sistema'),
])
def test_boleto_indisponivel_nao_oferece_codigos_ou_pdf(
    http_unauth, api_key, db, cobranca, boleto_registrado, situacao, motivo,
):
    boleto_registrado.nosso_numero = '00000123000456789'
    if situacao == 'sem_codigo':
        boleto_registrado.codigo_barras = None
    elif situacao == 'sem_linha':
        boleto_registrado.linha_digitavel = None
    elif situacao in ('baixa_pendente', 'baixa_confirmada'):
        boleto_registrado.baixa_status = situacao.removeprefix('baixa_')
    elif situacao == 'liquidado':
        boleto_registrado.status_ailos = '5'
    elif situacao == 'somente_sistema':
        cobranca.somente_sistema = True
    else:
        cobranca.status = BillingStatus(situacao)
    db.commit()
    item = _consultar(http_unauth, api_key, status='todos').json()['cobrancas'][0]
    assert item['boleto_registrado'] is (situacao not in ('sem_codigo', 'sem_linha'))
    assert item['boleto_disponivel'] is False
    assert item['motivo_boleto_indisponivel'] == motivo
    assert item['nosso_numero'] == '00000123000456789'
    assert item['boleto_link_cliente'] is None
    assert item['boleto_pdf_url'] is None
    assert item['linha_digitavel'] is None
    assert item['codigo_barras'] is None
    assert item['pix_copia_cola'] is None
    pdf = http_unauth.get(
        f'/api/v1/integrations/cobrancas/{cobranca.id}/pdf', headers={'X-API-Key': api_key},
    )
    assert pdf.status_code == 409
    assert pdf.json()['detail']['code'] == motivo


def test_pdf_sem_registro_nao_gera_titulo_local(http_unauth, api_key, cobranca):
    resp = http_unauth.get(
        f'/api/v1/integrations/cobrancas/{cobranca.id}/pdf', headers={'X-API-Key': api_key},
    )
    assert resp.status_code == 409
    assert resp.json()['detail']['code'] == 'sem_registro_bancario'


def test_soft_delete_excluido_antes_da_contagem(http_unauth, api_key, db, cliente, outro_cliente):
    _criar_cobranca(db, cliente)
    _criar_cobranca(db, cliente, is_deleted=True)
    _criar_cobranca(db, outro_cliente)
    outro_cliente.is_deleted = True
    db.commit()
    result = _consultar(http_unauth, api_key, status='todos').json()
    assert result['total_registros'] == result['total'] == 1


def test_recebimento_financeiro_aparece_como_pagamento_confirmado(http, api_key, db, cliente):
    billing = _criar_cobranca(db, cliente)
    assert _consultar(http, api_key, status='paga').json()['total'] == 0
    recebimento = http.post(
        f'/api/v1/billings/{billing.id}/receive',
        json={'payment_date': '2026-10-07', 'payment_method': 'pix', 'paid_amount': 100.00},
    )
    assert recebimento.status_code == 200
    assert _consultar(http, api_key).json()['total'] == 0
    item = _consultar(http, api_key, status='paga').json()['cobrancas'][0]
    assert item['id'] == billing.id
    assert item['pagamento_confirmado'] is True
    assert item['data_pagamento'] == '2026-10-07'
    assert item['valor_pago'] == 100.00
    assert item['forma_pagamento'] == 'pix'


@pytest.mark.parametrize('chave', [None, 'errada'])
@pytest.mark.parametrize('rota', ['lista', 'detalhe', 'pdf'])
def test_novas_consultas_continuam_exigindo_chave(http_unauth, api_key, cobranca, chave, rota):
    caminho = ('?status=paga&offset=100' if rota == 'lista' else
               f'/{cobranca.id}' + ('/pdf' if rota == 'pdf' else ''))
    headers = {'X-API-Key': chave} if chave else {}
    resp = http_unauth.get('/api/v1/integrations/cobrancas' + caminho, headers=headers)
    assert resp.status_code == 401
