"""Recuperação das avulsas já criadas pelo formulário antigo do rastreador."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.client import Client
from app.models.client_charge_item import ClientChargeItem
from app.models.closure_job import ClosureJob
from app.models.cnab_remessa import CnabRemessa, CnabRemessaItem
from app.models.enums import BillingStatus
from app.models.nfse_nota import NfseNota
from app.services.titulo_bancario import PoliticaBancariaError
from app.services.tracker_charge_recovery import (
    RECOVERY_FIELD, find_tracker_charge_candidates, recover_tracker_charges,
)

MONTH = date(2026, 10, 1)


@pytest.fixture()
def legacy_charge(http, db, contrato):
    # Mesmo POST usado pelo formulário antes da correção, com vencimento em
    # novembro mas serviço que o operador precisa fechar em outubro.
    response = http.post('/api/v1/billings/', json={
        'client_id': contrato.client_id, 'contract_id': contrato.id,
        'vehicle_id': contrato.vehicle_id, 'tracker_id': contrato.tracker_id,
        'billing_type': 'avulsa', 'title': 'Manutenção do rastreador',
        'amount': 120.50, 'due_date': '2026-11-10', 'notes': 'Acertado com o cliente',
    })
    assert response.status_code == 200, response.text
    billing = db.get(Billing, response.json()['id'])
    billing.created_at = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    db.commit()
    return billing


def recover(db, billing, **kwargs):
    return recover_tracker_charges(
        db, [billing.id], MONTH, expected_client_id=billing.client_id, **kwargs,
    )


def assert_not_converted(db, billing):
    db.refresh(billing)
    assert billing.item_id is None
    assert billing.status != BillingStatus.CANCELED
    assert db.query(ClientChargeItem).count() == 0
    assert db.query(BillingChangeLog).filter(BillingChangeLog.field_name == RECOVERY_FIELD).count() == 0


def test_dry_run_does_not_create_item_or_change_original(db, legacy_charge):
    [result] = recover(db, legacy_charge)
    assert result['result'] == 'pronta_para_recuperar'
    assert result['service_month'] == '2026-10'
    assert result['original_due_date'] == '2026-11-10'
    db.commit()
    assert_not_converted(db, legacy_charge)
    assert legacy_charge.notes == 'Acertado com o cliente'


def test_legacy_charge_enters_october_closure_once_with_its_links(http, db, legacy_charge, contrato):
    assert http.get('/api/v1/billing-closure/simulate', params={'service_month': '2026-10'}).json()['charge_items'] == []
    [result] = recover(db, legacy_charge, apply=True)
    db.commit()
    item = db.get(ClientChargeItem, result['item_id'])
    assert item.start_date == date(2026, 11, 10)
    assert item.date_basis == 'due_date'
    assert item.total_amount == Decimal('120.50')
    assert (item.client_id, item.contract_id, item.vehicle_id, item.tracker_id) == (
        legacy_charge.client_id, legacy_charge.contract_id, legacy_charge.vehicle_id, legacy_charge.tracker_id,
    )
    assert 'Acertado com o cliente' in item.description
    assert legacy_charge.status == BillingStatus.CANCELED
    assert legacy_charge.amount == Decimal('120.50')
    assert legacy_charge.due_date == date(2026, 11, 10)
    assert legacy_charge.item_id == item.id
    assert db.query(ClosureJob).count() == 0

    params = {'service_month': '2026-10'}
    preview = http.get('/api/v1/billing-closure/simulate', params=params)
    assert preview.status_code == 200, preview.text
    assert preview.json()['reference_month'] == '10/2026'
    assert [charge['item_id'] for charge in preview.json()['charge_items']] == [item.id]
    assert preview.json()['grand_total'] == 220.40
    generated = http.post('/api/v1/billing-closure/generate', params={
        **params, 'contract_ids': contrato.id, 'charge_item_ids': item.id,
    })
    assert generated.status_code == 200, generated.text
    data = generated.json()
    assert data['reference_month'] == '2026-10'
    assert db.get(ClosureJob, data['closure_batch_id']).reference_month == '2026-10'
    [title_id] = data['payment_billing_ids']
    assert db.get(Billing, title_id).amount == Decimal('220.40')
    assert legacy_charge.id not in data['payment_billing_ids']
    for month in ('2026-10', '2026-11'):
        again = http.get('/api/v1/billing-closure/simulate', params={'service_month': month})
        assert again.status_code == 200, again.text
        assert again.json()['charge_items'] == []
    [repeated] = recover(db, legacy_charge, apply=True)
    db.commit()
    assert repeated['result'] == 'ja_recuperada'
    assert repeated['item_id'] == item.id
    assert db.query(ClientChargeItem).count() == 1
    assert db.query(BillingChangeLog).count() == 2


def test_recovery_keeps_payer_and_appears_under_interveniente(http, db, legacy_charge, contrato, outro_cliente):
    contrato.interveniente_client_id = outro_cliente.id
    legacy_charge.payer_client_id = outro_cliente.id
    db.commit()
    [result] = recover(db, legacy_charge, apply=True)
    db.commit()
    preview = http.get('/api/v1/billing-closure/simulate', params={
        'service_month': '2026-10', 'filter_type': 'client', 'client_id': outro_cliente.id,
    })
    assert preview.status_code == 200, preview.text
    [item] = preview.json()['charge_items']
    assert item['item_id'] == result['item_id']
    assert item['payer_client_id'] == outro_cliente.id


def test_recovery_without_contract_still_enters_october(http, db, legacy_charge):
    legacy_charge.contract_id = None
    db.commit()
    [result] = recover(db, legacy_charge, apply=True)
    db.commit()
    preview = http.get('/api/v1/billing-closure/simulate', params={'service_month': '2026-10'})
    assert preview.status_code == 200, preview.text
    assert [item['item_id'] for item in preview.json()['charge_items']] == [result['item_id']]


@pytest.mark.parametrize('change', [
    {'status': BillingStatus.PAID, 'payment_date': date(2026, 10, 29), 'paid_amount': Decimal('120.50')},
    {'paid_amount': Decimal('10.00')},
    {'receipt_number': 'recibo-1'},
    {'billing_type': 'recorrente'},
    {'tracker_id': None},
    {'vehicle_id': None},
    {'sgr_payload': {'id': 123}},
    {'sgr_payload': {}},
    {'somente_sistema': True},
    {'installment_total': 2, 'installment_number': 1},
])
def test_ineligible_sources_are_not_converted(db, legacy_charge, change):
    for key, value in change.items():
        setattr(legacy_charge, key, value)
    db.commit()
    with pytest.raises(ValueError):
        recover(db, legacy_charge, apply=True)
    assert_not_converted(db, legacy_charge)


@pytest.mark.parametrize('bank_state', ['registered', 'REGISTRANDO', 'DESFECHO_DESCONHECIDO'])
def test_bank_title_prevents_conversion(db, legacy_charge, bank_state):
    boleto = AilosBoleto(billing_id=legacy_charge.id, numero_convenio='102004', status_ailos=bank_state)
    if bank_state == 'registered':
        boleto.linha_digitavel = '1' * 47
        boleto.codigo_barras = '1' * 44
    db.add(boleto)
    db.commit()
    with pytest.raises(PoliticaBancariaError):
        recover(db, legacy_charge, apply=True)
    assert_not_converted(db, legacy_charge)


def test_reserved_cnab_title_prevents_conversion(db, legacy_charge):
    remessa = CnabRemessa(layout='240', sequencial=1, arquivo_sha256='0' * 64,
        arquivo=b'test', total_titulos=1, valor_total=legacy_charge.amount)
    db.add(remessa)
    db.flush()
    db.add(CnabRemessaItem(remessa_id=remessa.id, billing_id=legacy_charge.id,
        valor=legacy_charge.amount, status='reservado'))
    db.commit()
    with pytest.raises(PoliticaBancariaError):
        recover(db, legacy_charge, apply=True)
    assert_not_converted(db, legacy_charge)


def test_any_fiscal_attempt_prevents_conversion(db, legacy_charge):
    db.add(NfseNota(billing_id=legacy_charge.id, status='processing'))
    db.commit()
    with pytest.raises(ValueError, match='NFS-e'):
        recover(db, legacy_charge, apply=True)
    assert_not_converted(db, legacy_charge)


def test_all_sources_are_validated_before_first_write(db, legacy_charge):
    other = Billing(
        client_id=legacy_charge.client_id, contract_id=legacy_charge.contract_id,
        vehicle_id=legacy_charge.vehicle_id, tracker_id=legacy_charge.tracker_id,
        billing_type='avulsa', amount=Decimal('10.00'), title='Outro adicional',
        due_date=date(2026, 11, 15), status=BillingStatus.PENDING,
    )
    db.add(other)
    db.flush()
    db.add(NfseNota(billing_id=other.id, status='emitida'))
    db.commit()
    with pytest.raises(ValueError, match='NFS-e'):
        recover_tracker_charges(db, [legacy_charge.id, other.id], MONTH,
            apply=True, expected_client_id=legacy_charge.client_id)
    db.commit()
    assert_not_converted(db, legacy_charge)


def test_unexpected_client_and_changed_payer_prevent_conversion(db, legacy_charge, contrato, outro_cliente):
    with pytest.raises(ValueError, match='cliente esperado'):
        recover_tracker_charges(db, [legacy_charge.id], MONTH,
            apply=True, expected_client_id=outro_cliente.id)
    contrato.interveniente_client_id = outro_cliente.id
    db.commit()
    with pytest.raises(ValueError, match='responsável financeiro mudou'):
        recover(db, legacy_charge, apply=True)
    assert_not_converted(db, legacy_charge)


def test_repeated_conversion_cannot_silently_change_month(db, legacy_charge):
    recover(db, legacy_charge, apply=True)
    db.commit()
    with pytest.raises(ValueError, match='outro mês'):
        recover_tracker_charges(db, [legacy_charge.id], date(2026, 11, 1),
            apply=True, expected_client_id=legacy_charge.client_id)
    assert db.query(ClientChargeItem).one().start_date == date(2026, 11, 10)


def test_candidate_month_uses_brasilia_creation_date_not_due_date(db, legacy_charge, outro_cliente):
    legacy_charge.created_at = datetime(2026, 11, 1, 1, tzinfo=timezone.utc)  # 31/10 às 22h em Brasília
    db.commit()
    assert find_tracker_charge_candidates(db, legacy_charge.client_id, MONTH) == [legacy_charge.id]
    assert find_tracker_charge_candidates(db, outro_cliente.id, MONTH) == []
    assert find_tracker_charge_candidates(db, legacy_charge.client_id, date(2026, 11, 1)) == []
    [result] = recover(db, legacy_charge, apply=True)
    db.commit()
    assert db.get(ClientChargeItem, result['item_id']).start_date == date(2026, 11, 10)


def test_explicit_month_and_long_title_keep_original_information(db, legacy_charge):
    legacy_charge.title = 'Manutenção ' + 'x' * 140
    legacy_charge.created_at = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
    db.commit()
    [result] = recover(db, legacy_charge, apply=True)
    db.commit()
    item = db.get(ClientChargeItem, result['item_id'])
    assert item.start_date == date(2026, 11, 10)
    assert len(item.title) == 140
    assert legacy_charge.title in item.description


def test_cli_simulates_applies_and_can_be_repeated(db, legacy_charge, monkeypatch, capsys):
    from scripts import recuperar_cobrancas_rastreador as command
    monkeypatch.setattr(command, 'SessionLocal', sessionmaker(bind=db.get_bind(), autoflush=False))
    client = db.get(Client, legacy_charge.client_id)
    args = ['--cliente', client.name, '--mes-servico', '2026-10']
    assert command.main(args) == 0
    assert 'SIMULACAO_SEM_GRAVACAO' in capsys.readouterr().out
    assert_not_converted(db, legacy_charge)
    assert command.main([*args, '--aplicar']) == 0
    assert 'recuperada' in capsys.readouterr().out
    db.expire_all()
    assert legacy_charge.status == BillingStatus.CANCELED
    assert db.query(ClientChargeItem).count() == 1
    assert command.main([*args, '--aplicar']) == 0
    assert 'ja_recuperada' in capsys.readouterr().out
    assert db.query(ClientChargeItem).count() == 1
