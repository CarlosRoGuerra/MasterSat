"""Correção de vencimento de cobrança com boleto já registrado na Ailos.

Caso real (10/2026): boletos únicos do fechamento saíram com o maior vencimento
do grupo (ex.: 28/10 para cliente que vence dia 10), já registrados e enviados.
A correção substitui a cobrança por uma nova, sem boleto, na data certa; o
boleto antigo fica com baixa pendente (a API da Ailos não dá baixa).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.enums import BillingStatus
from app.services import titulo_bancario
from app.services.billing_closure import _vencimento_do_boleto_unico
from tests.fase03_apoio import cobranca, registrar_titulo

URL = '/api/v1/billings/{id}/corrigir-vencimento'
NOVA_DATA = '2099-12-10'


@pytest.fixture()
def unico(db, contrato):
    """Boleto único registrado que agrupa 2 mensalidades + 1 taxa."""
    titulo = cobranca(db, contrato, billing_type='boleto_unico', amount=Decimal('279.98'),
                      due_date=date(2099, 12, 28), period_label='11/2099', title='Fechamento 11/2099')
    componentes = [
        cobranca(db, contrato, billing_type='avulsa', amount=Decimal('64.99'), due_date=date(2099, 12, 10),
                 status=BillingStatus.CANCELED, substituted_by_id=titulo.id),
        cobranca(db, contrato, billing_type='avulsa', amount=Decimal('64.99'), due_date=date(2099, 12, 10),
                 status=BillingStatus.CANCELED, substituted_by_id=titulo.id),
        cobranca(db, contrato, billing_type='avulsa', amount=Decimal('150.00'), due_date=date(2099, 12, 28),
                 status=BillingStatus.CANCELED, substituted_by_id=titulo.id),
    ]
    registrar_titulo(db, titulo)
    return titulo, componentes


def _corrigir(http, billing_id, **extra):
    return http.post(URL.format(id=billing_id), json={
        'due_date': NOVA_DATA, 'reason': 'vencimento errado no fechamento', **extra,
    })


def test_exige_confirmar_que_o_boleto_antigo_segue_ativo(http, db, unico):
    titulo, _ = unico
    r = _corrigir(http, titulo.id)
    assert r.status_code == 409
    assert r.json()['detail']['code'] == 'boleto_ailos_registrado'
    db.refresh(titulo)
    assert titulo.status == BillingStatus.PENDING


def test_boleto_unico_vira_cobranca_nova_sem_boleto_na_data_certa(http, db, unico):
    titulo, componentes = unico
    r = _corrigir(http, titulo.id, confirmar_boleto_ailos=True)
    assert r.status_code == 200, r.text
    nova = db.get(Billing, r.json()['id'])
    db.refresh(titulo)

    assert nova.due_date == date(2099, 12, 10)
    assert nova.amount == titulo.amount and nova.billing_type == 'boleto_unico'
    assert nova.period_label == '11/2099'
    # A antiga sai sem substituto: reverter a nova não reabre o título do banco.
    assert titulo.status == BillingStatus.CANCELED and titulo.substituted_by_id is None
    for c in componentes:
        db.refresh(c)
        assert c.substituted_by_id == nova.id and c.status == BillingStatus.CANCELED
    boleto_antigo = db.query(AilosBoleto).filter_by(billing_id=titulo.id).one()
    assert boleto_antigo.baixa_status == 'pendente'
    # A nova não tem boleto: a emissão normal fica liberada.
    assert db.query(AilosBoleto).filter_by(billing_id=nova.id).count() == 0
    assert titulo_bancario.exigir(db, titulo_bancario.EMITIR_AILOS, [nova.id])[nova.id].estado == 'sem_titulo'
    log = db.query(BillingChangeLog).filter_by(billing_id=titulo.id, field_name='due_date').one()
    assert log.previous_value == '2099-12-28' and f'#{nova.id}' in log.new_value


def test_reverter_a_nova_reabre_as_agrupadas_e_nao_o_titulo_do_banco(http, db, unico):
    titulo, componentes = unico
    nova_id = _corrigir(http, titulo.id, confirmar_boleto_ailos=True).json()['id']
    r = http.post(f'/api/v1/billings/{nova_id}/cancel', json={
        'reason': 'teste', 'reverter_substituicao': True,
    })
    assert r.status_code == 200, r.text
    db.refresh(titulo)
    assert titulo.status == BillingStatus.CANCELED
    for c in componentes:
        db.refresh(c)
        assert c.status == BillingStatus.PENDING and c.substituted_by_id is None


def test_mensalidade_simples_vira_titulo_de_pagamento_e_o_mes_continua_ocupado(http, db, contrato):
    mensal = cobranca(db, contrato, billing_type='recorrente', amount=Decimal('64.99'),
                      due_date=date(2099, 12, 28), period_label='12/2099')
    registrar_titulo(db, mensal)
    r = _corrigir(http, mensal.id, confirmar_boleto_ailos=True)
    assert r.status_code == 200, r.text
    nova = db.get(Billing, r.json()['id'])
    db.refresh(mensal)
    assert nova.billing_type == 'boleto_unico' and nova.contract_id == contrato.id
    assert nova.period_label == '12/2099'  # sem lote de fechamento: mantém o rótulo
    assert mensal.substituted_by_id == nova.id and mensal.status == BillingStatus.CANCELED
    # Outra mensalidade no mesmo mês/contrato continua barrada pelo índice.
    from sqlalchemy.exc import IntegrityError
    db.add(Billing(contract_id=contrato.id, client_id=contrato.client_id, amount=Decimal('64.99'),
                   due_date=date(2099, 12, 10), status=BillingStatus.PENDING,
                   billing_type='recorrente', period_label='12/2099', title='Duplicada'))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_recusa_paga_cancelada_e_data_igual(http, db, contrato):
    paga = cobranca(db, contrato, status=BillingStatus.PAID)
    assert _corrigir(http, paga.id, confirmar_boleto_ailos=True).status_code == 400
    mesma = cobranca(db, contrato, due_date=date(2099, 12, 10))
    assert _corrigir(http, mesma.id).status_code == 400


def test_registro_em_andamento_bloqueia(http, db, contrato):
    from tests.fase03_apoio import reserva
    b = cobranca(db, contrato)
    reserva(db, b, 'REGISTRANDO')
    assert _corrigir(http, b.id, confirmar_boleto_ailos=True).status_code == 409


def test_operacional_nao_corrige(http_op, db, unico):
    titulo, _ = unico
    assert _corrigir(http_op, titulo.id, confirmar_boleto_ailos=True).status_code == 403


def test_nfse_emitida_acompanha_a_cobranca_nova(http, db, unico):
    from app.models.nfse_nota import NfseNota
    titulo, _ = unico
    db.add(NfseNota(billing_id=titulo.id, status='emitida', numero_nfse='103'))
    db.commit()
    nova_id = _corrigir(http, titulo.id, confirmar_boleto_ailos=True).json()['id']
    nota = db.query(NfseNota).filter_by(numero_nfse='103').one()
    assert nota.billing_id == nova_id  # sem segunda nota para o mesmo serviço
    assert db.query(NfseNota).filter_by(billing_id=titulo.id).count() == 0


@pytest.mark.parametrize('status', ['processing', 'desconhecido'])
def test_nfse_em_emissao_bloqueia_a_correcao(http, db, unico, status):
    from app.models.nfse_nota import NfseNota
    titulo, _ = unico
    db.add(NfseNota(billing_id=titulo.id, status=status))
    db.commit()
    r = _corrigir(http, titulo.id, confirmar_boleto_ailos=True)
    assert r.status_code == 400 and 'NFS-e' in r.json()['detail']
    db.refresh(titulo)
    assert titulo.status == BillingStatus.PENDING


def _lote(db, ids, mes='2099-11'):
    from app.models.closure_job import ClosureJob
    job = ClosureJob(reference_month=mes, filter_type='all', status='completed',
                     result={'payment_billing_ids': ids})
    db.add(job)
    db.commit()
    return job


def test_mensalidade_do_fechamento_ganha_o_mes_do_servico_e_fica_no_mesmo_lote(http, db, contrato):
    # Caso GILCIONEI/JONAS: mensalidade de 1 placa, rótulo técnico 12/2099
    # (mês do vencimento); o serviço do lote é 11/2099.
    mensal = cobranca(db, contrato, billing_type='recorrente', amount=Decimal('64.99'),
                      due_date=date(2099, 12, 28), period_label='12/2099')
    outra = cobranca(db, contrato, billing_type='boleto_unico', amount=Decimal('10'))
    registrar_titulo(db, mensal)
    job = _lote(db, [outra.id, mensal.id])
    nova_id = _corrigir(http, mensal.id, confirmar_boleto_ailos=True).json()['id']
    nova = db.get(Billing, nova_id)
    assert nova.period_label == '11/2099'
    db.refresh(job)
    assert job.result['payment_billing_ids'] == [outra.id, nova_id]


def test_boleto_unico_do_fechamento_troca_de_lugar_no_lote(http, db, unico):
    from app.services.closure_delivery import recuperar_lotes_anteriores
    titulo, _ = unico
    job = _lote(db, [titulo.id])
    nova_id = _corrigir(http, titulo.id, confirmar_boleto_ailos=True).json()['id']
    db.refresh(job)
    assert job.result['payment_billing_ids'] == [nova_id]
    # Já registrada no lote original: a recuperação não cria lote avulso.
    from app.models.closure_job import ClosureJob
    antes = db.query(ClosureJob).count()
    recuperar_lotes_anteriores(db)
    assert db.query(ClosureJob).count() == antes


# --- Vencimento do boleto único no fechamento ------------------------------

def _b(tipo, dia):
    return Billing(billing_type=tipo, due_date=date(2026, 10, dia), amount=Decimal('1'))


def test_boleto_unico_vence_no_dia_das_mensalidades_e_nao_da_taxa():
    # Caso SERGIO: mensalidades dia 10 (e algumas 15), taxas até 28 → antes 28.
    grupo = [_b('recorrente', 10)] * 25 + [_b('recorrente', 15)] * 4 + [_b('item', 28), _b('item', 7)]
    assert _vencimento_do_boleto_unico(grupo) == date(2026, 10, 10)


def test_empate_fica_com_o_dia_mais_cedo_e_sem_mensalidade_mantem_o_maior():
    assert _vencimento_do_boleto_unico([_b('recorrente', 15), _b('recorrente', 10)]) == date(2026, 10, 10)
    # Pró-rata final de contrato encerrado (dia 10) não decide o dia do ativo (15).
    assert _vencimento_do_boleto_unico([_b('recorrente', 15), _b('prorata', 10)]) == date(2026, 10, 15)
    assert _vencimento_do_boleto_unico([_b('prorata', 12), _b('item', 28)]) == date(2026, 10, 12)
    assert _vencimento_do_boleto_unico([_b('item', 11), _b('taxa_instalacao', 28)]) == date(2026, 10, 28)
