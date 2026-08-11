"""
O PDF do boleto só sai para título REGISTRADO na Ailos.

Antes o endpoint gerava o PDF para qualquer cobrança, usando nosso número e
código de barras calculados localmente. O papel tinha cara de boleto pronto,
mas o banco não tinha registro dele: não era pagável, não conciliava, e nada
impedia que fosse enviado ao cliente.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.enums import BillingStatus


@pytest.fixture()
def cobranca(db, cliente) -> Billing:
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


def _registrar(db, billing_id: int, **kw) -> AilosBoleto:
    campos = dict(
        billing_id=billing_id,
        numero_convenio='102004',
        nosso_numero='000000301',
        linha_digitavel='08591.02006 40045.470206 00000.003012 5 14890000009990',
        codigo_barras='08595148900000099901020040045470200000000301',
    )
    campos.update(kw)
    ab = AilosBoleto(**campos)
    db.add(ab)
    db.commit()
    return ab


# ---------------------------------------------------------------------------
# Download do PDF
# ---------------------------------------------------------------------------

def test_sem_boleto_na_ailos_o_pdf_e_recusado(http, cobranca):
    resp = http.get(f'/api/v1/boletos/{cobranca.id}/pdf')
    assert resp.status_code == 409
    assert 'Ailos' in resp.json()['detail']


def test_com_boleto_registrado_o_pdf_sai(http, db, cobranca):
    _registrar(db, cobranca.id)
    resp = http.get(f'/api/v1/boletos/{cobranca.id}/pdf')
    assert resp.status_code == 200
    assert resp.content[:4] == b'%PDF'


def test_registro_pela_metade_nao_vale(http, db, cobranca):
    """Linha digitável sem código de barras (ou vice-versa) é registro
    incompleto — o PDF sairia com o código calculado localmente."""
    _registrar(db, cobranca.id, codigo_barras=None)
    assert http.get(f'/api/v1/boletos/{cobranca.id}/pdf').status_code == 409


def test_o_boleto_usa_o_codigo_oficial_da_ailos_e_nao_o_calculado(http, db, cobranca):
    """O código local existe e é plausível — por isso o PDF antigo enganava.
    Com registro, o que vale é o que a Ailos devolveu."""
    oficial = '08595148900000099901020040045470200000000301'
    local = http.get(f'/api/v1/boletos/{cobranca.id}').json()
    assert local['boleto_registrado'] is False
    assert local['codigo_barras'] != oficial

    _registrar(db, cobranca.id)
    com_registro = http.get(f'/api/v1/boletos/{cobranca.id}').json()
    assert com_registro['boleto_registrado'] is True
    assert com_registro['codigo_barras'] == oficial


# ---------------------------------------------------------------------------
# Flag na listagem de cobranças (a tela usa para mostrar ou não o botão)
# ---------------------------------------------------------------------------

def test_listagem_marca_quem_tem_boleto_na_ailos(http, db, cobranca):
    def _flag():
        itens = http.get(f'/api/v1/billings/?client_id={cobranca.client_id}').json()
        return next(i['boleto_ailos'] for i in itens if i['id'] == cobranca.id)

    assert _flag() is False
    _registrar(db, cobranca.id)
    assert _flag() is True


# ---------------------------------------------------------------------------
# Recibo de pagamento — só existe depois de pago
# ---------------------------------------------------------------------------

def test_recibo_recusado_enquanto_a_cobranca_nao_esta_paga(http, cobranca):
    resp = http.get(f'/api/v1/billings/{cobranca.id}/receipt')
    assert resp.status_code == 400
    assert 'pagas' in resp.json()['detail']


def test_recibo_recusado_para_cobranca_vencida(http, db, cobranca):
    cobranca.status = BillingStatus.OVERDUE
    db.commit()
    assert http.get(f'/api/v1/billings/{cobranca.id}/receipt').status_code == 400


def test_recibo_recusado_para_cobranca_cancelada(http, db, cobranca):
    """Cancelada depois de paga não vale recibo — o dinheiro não ficou."""
    cobranca.status = BillingStatus.CANCELED
    db.commit()
    assert http.get(f'/api/v1/billings/{cobranca.id}/receipt').status_code == 400


def test_recibo_sai_depois_de_paga(http, db, cobranca):
    cobranca.status = BillingStatus.PAID
    cobranca.payment_date = date.today()
    cobranca.paid_amount = cobranca.amount
    db.commit()
    resp = http.get(f'/api/v1/billings/{cobranca.id}/receipt')
    assert resp.status_code == 200
    assert resp.content[:4] == b'%PDF'


def test_recibo_sai_mesmo_sem_numero_de_recibo_gravado(http, db, cobranca):
    """Cobranças antigas foram pagas antes de existir receipt_number; o recibo
    não pode depender dele (o nome do arquivo cai no id)."""
    cobranca.status = BillingStatus.PAID
    cobranca.payment_date = date.today()
    cobranca.receipt_number = None
    db.commit()
    assert http.get(f'/api/v1/billings/{cobranca.id}/receipt').status_code == 200


# ---------------------------------------------------------------------------
# Carnê — PDF de várias parcelas registradas (backlog 1.2)
# ---------------------------------------------------------------------------

def _lote_carne(db, billing_ids: list[int]):
    from app.models.ailos_lote import AilosLote
    lote = AilosLote(tipo='carne', ticket=f'tkt-{billing_ids[0]}',
                     numero_convenio='102004', billing_ids=billing_ids, status='concluido')
    db.add(lote)
    db.commit()
    return lote


def _cobranca(db, cliente, valor='189.90'):
    from datetime import date, timedelta
    from decimal import Decimal
    b = Billing(client_id=cliente.id, amount=Decimal(valor),
                due_date=date.today() + timedelta(days=30), status=BillingStatus.PENDING,
                billing_type='recorrente', title='Mensalidade')
    db.add(b)
    db.commit()
    return b


def test_carne_gera_pdf_com_as_parcelas_registradas(http, db, cliente):
    ids = []
    for _ in range(3):
        b = _cobranca(db, cliente)
        _registrar(db, b.id)
        ids.append(b.id)
    lote = _lote_carne(db, ids)

    r = http.get(f'/api/v1/boletos/carne/{lote.id}/pdf')
    assert r.status_code == 200
    assert r.content[:4] == b'%PDF'


def test_carne_ignora_parcela_nao_registrada(http, db, cliente):
    """Parcela sem registro na Ailos não é pagável — fica de fora, mas o carnê
    sai com as que estão prontas."""
    b1 = _cobranca(db, cliente); _registrar(db, b1.id)
    b2 = _cobranca(db, cliente)  # sem registro
    lote = _lote_carne(db, [b1.id, b2.id])
    r = http.get(f'/api/v1/boletos/carne/{lote.id}/pdf')
    assert r.status_code == 200
    assert r.content[:4] == b'%PDF'


def test_carne_sem_nenhuma_parcela_registrada_recusa(http, db, cliente):
    b = _cobranca(db, cliente)  # sem registro
    lote = _lote_carne(db, [b.id])
    r = http.get(f'/api/v1/boletos/carne/{lote.id}/pdf')
    assert r.status_code == 409


def test_carne_inexistente_404(http, db):
    assert http.get('/api/v1/boletos/carne/999999/pdf').status_code == 404


def test_lote_de_boleto_nao_e_carne(http, db, cliente):
    """Um lote comum (tipo 'boleto') não é servido pela rota de carnê."""
    from app.models.ailos_lote import AilosLote
    b = _cobranca(db, cliente); _registrar(db, b.id)
    lote = AilosLote(tipo='boleto', ticket='tkt-boleto', numero_convenio='102004',
                     billing_ids=[b.id], status='concluido')
    db.add(lote); db.commit()
    assert http.get(f'/api/v1/boletos/carne/{lote.id}/pdf').status_code == 404
