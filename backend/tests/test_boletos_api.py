"""
O PDF do boleto só sai para título REGISTRADO na Ailos.

Antes o endpoint gerava o PDF para qualquer cobrança, usando nosso número e
código de barras calculados localmente. O papel tinha cara de boleto pronto,
mas o banco não tinha registro dele: não era pagável, não conciliava, e nada
impedia que fosse enviado ao cliente.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.api.v1.endpoints.boletos import _public_token
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
# Link público (sem login) — não pode servir boleto de cobrança cancelada
# ---------------------------------------------------------------------------

def test_link_publico_serve_boleto_ativo(http, db, cobranca):
    _registrar(db, cobranca.id)
    resp = http.get(f'/api/v1/public/boleto/{cobranca.id}/{_public_token(cobranca.id)}')
    assert resp.status_code == 200
    assert resp.content[:4] == b'%PDF'


def test_link_publico_recusa_boleto_de_cobranca_cancelada(http, db, cobranca):
    # Mesmo com o título ainda registrado na Ailos, cobrança cancelada não pode
    # continuar pagável pelo link já enviado ao cliente.
    _registrar(db, cobranca.id)
    cobranca.status = BillingStatus.CANCELED
    db.commit()
    resp = http.get(f'/api/v1/public/boleto/{cobranca.id}/{_public_token(cobranca.id)}')
    assert resp.status_code == 404


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


def test_carne_com_parcela_faltando_recusa_enquanto_recente(http, db, cliente):
    """Carnê recém-criado com 1 de 2 parcelas registradas: recusa o download em
    vez de servir um PDF incompleto sem avisar (era assim que o carnê "saía com
    1 boleto só" — a parcela que faltava era simplesmente pulada em silêncio)."""
    b1 = _cobranca(db, cliente); _registrar(db, b1.id)
    b2 = _cobranca(db, cliente)  # sem registro ainda
    lote = _lote_carne(db, [b1.id, b2.id])
    r = http.get(f'/api/v1/boletos/carne/{lote.id}/pdf')
    assert r.status_code == 409
    assert '1 de 2' in r.json()['detail']


def test_carne_com_parcela_faltando_serve_parcial_apos_prazo(http, db, cliente):
    """Se uma parcela nunca resolve, o download não pode ficar bloqueado para
    sempre — depois do prazo de espera, serve o que conseguiu."""
    b1 = _cobranca(db, cliente); _registrar(db, b1.id)
    b2 = _cobranca(db, cliente)  # nunca chega a registrar
    lote = _lote_carne(db, [b1.id, b2.id])
    lote.created_at = datetime.now(timezone.utc) - timedelta(minutes=11)
    db.commit()

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


# ---------------------------------------------------------------------------
# Carnê — listar por cliente + auto-recuperação no download
# ---------------------------------------------------------------------------

def test_lista_carnes_do_cliente(http, db, cliente):
    b1 = _cobranca(db, cliente); _registrar(db, b1.id)
    b2 = _cobranca(db, cliente); _registrar(db, b2.id)
    lote = _lote_carne(db, [b1.id, b2.id])
    # vincula os boletos ao lote (é assim que a lista encontra o carnê do cliente)
    for bid in (b1.id, b2.id):
        ab = db.query(AilosBoleto).filter_by(billing_id=bid).first()
        ab.lote_id = lote.id
    db.commit()

    r = http.get(f'/api/v1/boletos/carne?client_id={cliente.id}')
    assert r.status_code == 200
    carnes = r.json()
    assert len(carnes) == 1
    assert carnes[0]['lote_id'] == lote.id
    assert carnes[0]['parcelas'] == 2
    assert carnes[0]['parcelas_registradas'] == 2


def test_lista_carne_nao_vaza_de_outro_cliente(http, db, cliente, outro_cliente):
    b = _cobranca(db, cliente); _registrar(db, b.id)
    lote = _lote_carne(db, [b.id])
    ab = db.query(AilosBoleto).filter_by(billing_id=b.id).first()
    ab.lote_id = lote.id; db.commit()

    assert http.get(f'/api/v1/boletos/carne?client_id={outro_cliente.id}').json() == []


def test_download_recupera_parcela_sem_linha_digitavel(http, db, cliente, monkeypatch):
    """Carnê registrado mas sem linha digitável salva (a consulta falhou na
    geração): o download recupera via Ailos antes de montar o PDF."""
    from app.services import ailos_boletos

    b1 = _cobranca(db, cliente)
    b2 = _cobranca(db, cliente)
    # parcelas SEM linha digitável (como após uma geração cuja consulta falhou)
    for bid in (b1.id, b2.id):
        db.add(AilosBoleto(billing_id=bid, numero_convenio='102004'))
    db.commit()
    lote = _lote_carne(db, [b1.id, b2.id])

    # a "recuperação" preenche a linha digitável das parcelas
    def _fake_consultar(db_, lt):
        for bid in lt.billing_ids:
            ab = db_.query(AilosBoleto).filter_by(billing_id=bid).first()
            ab.linha_digitavel = '08591.02006 40045.470206 00000.003012 5 1489'
            ab.codigo_barras = '0859514890000009990102004004547020000000030'
        db_.commit()
        return {'status': 'completed', 'lote': lt}

    monkeypatch.setattr(ailos_boletos, 'consultar_lote', _fake_consultar)
    r = http.get(f'/api/v1/boletos/carne/{lote.id}/pdf')
    assert r.status_code == 200
    assert r.content[:4] == b'%PDF'
