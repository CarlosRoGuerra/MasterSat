"""Carnê do plano com vários veículos: um boleto por mês com todas as placas."""
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from sqlalchemy import select

from app.models.billing import Billing
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.models.vehicle import Vehicle
from app.services.financial import existing_recurring_periods
from tests.sicredi_apoio import resposta_cadastro, sicredi  # noqa: F401

B = '/api/v1/billings'
MESES = [f'{m:02d}/2099' for m in range(1, 13)]


def _veiculo(db, cliente, plate, plan, payer=None):
    v = Vehicle(client_id=cliente.id, plate=plate, type='passeio')
    db.add(v)
    db.flush()
    c = Contract(client_id=cliente.id, plan_id=plan.id, vehicle_id=v.id, start_date=date(2026, 1, 1),
                 status='ativo', billing_day=10, interveniente_client_id=payer.id if payer else None)
    db.add(c)
    db.commit()
    return c


@pytest.fixture
def dois_carros(db, cliente, plan):
    plan.price = Decimal('64.99')
    db.commit()
    return _veiculo(db, cliente, 'RMO0H18', plan), _veiculo(db, cliente, 'RDU2G08', plan)


def _parcelar(http, contratos, **extra):
    return http.post(f'{B}/parcelar', json={
        'contract_ids': [c.id for c in contratos], 'num_parcelas': 12, 'banco': 'sicredi',
        'primeiro_vencimento': '2099-01-10', **extra})


def test_um_boleto_por_mes_com_as_duas_placas(http, db, dois_carros):
    r = _parcelar(http, dois_carros)
    assert r.status_code == 200, r.text
    parcelas = r.json()
    assert len(parcelas) == 12
    assert [p['installment_number'] for p in parcelas] == list(range(1, 13))
    assert {p['amount'] for p in parcelas} == {129.98}
    assert [p['due_date'] for p in parcelas] == [f'2099-{m:02d}-10' for m in range(1, 13)]
    assert 'RMO0H18 + RDU2G08' in parcelas[0]['title']
    # As mensalidades de cada veículo ficam registradas e ocupam o mês de cada contrato.
    for contrato in dois_carros:
        assert existing_recurring_periods(db, contrato.id, MESES) == set(MESES)
    componentes = db.scalars(select(Billing).where(Billing.billing_type == 'carne')).all()
    assert len(componentes) == 24
    assert all(b.status == BillingStatus.CANCELED and b.substituted_by_id for b in componentes)
    assert {b.substituted_by_id for b in componentes} == {p['id'] for p in parcelas}


def test_carne_sicredi_das_duas_placas_imprime_cada_placa_na_parcela(http, db, dois_carros, sicredi):
    from pypdf import PdfReader
    ids = [p['id'] for p in _parcelar(http, dois_carros).json()]
    sicredi.quando('POST', '/cobranca/boleto/v1/boletos', lambda **kw: resposta_cadastro(kw['json']['nossoNumero']))
    lote = http.post('/api/v1/sicredi/carne/lote', json={'billing_ids': ids})
    assert lote.status_code == 200, lote.text
    assert len(sicredi.chamadas_de('POST', '/cobranca/boleto/v1/boletos')) == 12
    pdf = http.get(f"/api/v1/boletos/carne/{lote.json()['id']}/pdf?banco=sicredi")
    assert pdf.status_code == 200, pdf.text
    primeira = PdfReader(BytesIO(pdf.content)).pages[0].extract_text()
    assert 'RMO0H18, RDU2G08' in primeira and 'Placas: RMO0H18, RDU2G08' in primeira and '129,98' in primeira


def test_um_contrato_continua_como_antes(http, db, dois_carros):
    r = _parcelar(http, dois_carros[:1])
    assert r.status_code == 200, r.text
    assert {p['amount'] for p in r.json()} == {64.99}
    assert {p['billing_type'] for p in r.json()} == {'carne'}
    assert db.query(Billing).count() == 12


def test_responsaveis_diferentes_nao_formam_um_carne(http, db, cliente, plan, dois_carros):
    outro = Client(name='Outro pagador', type='pf', cpf_cnpj='11144477735')
    db.add(outro)
    db.commit()
    terceiro = _veiculo(db, cliente, 'XYZ9Z99', plan, payer=outro)
    r = _parcelar(http, [dois_carros[0], terceiro])
    assert r.status_code == 422 and 'único responsável' in r.json()['detail']
    assert db.query(Billing).count() == 0


def test_valor_informado_so_com_um_veiculo(http, db, dois_carros):
    r = _parcelar(http, dois_carros, valor_parcela=100)
    assert r.status_code == 422
    assert db.query(Billing).count() == 0


def test_mes_ja_cobrado_em_um_carro_recusa_o_carne_inteiro(http, db, cliente, dois_carros):
    db.add(Billing(contract_id=dois_carros[1].id, client_id=cliente.id, billing_type='recorrente',
                   amount=Decimal('64.99'), due_date=date(2099, 3, 10), period_label='03/2099',
                   status=BillingStatus.PENDING))
    db.commit()
    r = _parcelar(http, dois_carros)
    assert r.status_code == 409
    assert 'RDU2G08: Este contrato já tem cobrança de mensalidade lançada para: 03/2099' in r.json()['detail']
    assert db.query(Billing).count() == 1
