"""Fase 04 — dados importados antes da identidade de origem.

Cobranças gravadas pelo importador antigo (título "Boleto SGR nn - placa",
valor bruto, desconto descartado, ``sgr_payload`` preservado) precisam: (1)
não ser duplicadas pela importação nova; (2) ganhar proveniência pelo
backfill sem inventar vínculo; (3) ter o desconto perdido compensado só por
operação explícita e auditável.
"""
from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.billing import Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.client import Client
from app.models.contract import Contract
from app.models.enums import BillingStatus, ClientStatus, VehicleStatus
from app.models.plan import Plan
from app.models.sgr_migracao import SgrDocumento, SgrDocumentoLinha
from app.models.vehicle import Vehicle
from app.services.sgr_migration.poc import achatar_boletos
from tests.test_fase04_sgr import CPF_A, boleto, cobrancas, importar, no

_SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'sgr_backfill.py'


@pytest.fixture(scope='module')
def backfill():
    spec = importlib.util.spec_from_file_location('sgr_backfill', _SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def legado(db, bol, *, client_id=None):
    """Grava como o importador ANTIGO gravava: só as linhas positivas, com o
    valor bruto (o desconto era descartado)."""
    if client_id is None:
        cliente = db.query(Client).filter_by(cpf_cnpj=CPF_A).first()
        if cliente is None:
            cliente = Client(name='CLIENTE A', cpf_cnpj=CPF_A, type='pf', status=ClientStatus.ACTIVE)
            db.add(cliente)
            db.flush()
        client_id = cliente.id
    ids = []
    for mapped in achatar_boletos([bol], []):
        if mapped['amount_cents'] <= 0:
            continue
        b = Billing(client_id=client_id, title=mapped['title'], billing_type='avulsa',
                    amount=Decimal(mapped['amount_cents']) / 100, due_date=__import__('datetime').date(2026, 10, 15),
                    status=BillingStatus(mapped['status']), receipt_number=mapped['receipt_number'],
                    period_label=mapped['period_label'], sgr_payload=mapped['sgr_payload'],
                    notes='Importado do SGR (Hinova).')
        db.add(b)
        db.flush()
        ids.append(b.id)
    db.commit()
    return ids


DOC_SEM_DESCONTO = boleto(8001, [('60,00', 'ABC1234', 'MENSALIDADE'), ('40,00', 'DEF5678', 'MENSALIDADE')])
DOC_COM_DESCONTO = boleto(8002, [('100,00', 'ABC1234', 'MENSALIDADE'), ('-20,00', 'ABC1234', 'DESCONTO')])


class TestImportacaoSobreLegado:
    def test_documento_legado_e_adotado_sem_duplicar(self, db):
        ids = legado(db, DOC_SEM_DESCONTO)
        stats = importar(db, no(veiculos=[], boletos=[DOC_SEM_DESCONTO]))
        assert [b.id for b in cobrancas(db)] == ids
        assert stats.billings_created == 0 and stats.billings_reused == 2
        assert db.query(SgrDocumento).one().status == 'conciliado'
        assert {linha.billing_id for linha in db.query(SgrDocumentoLinha)} == set(ids)

    def test_legado_com_desconto_descartado_fica_bloqueado_e_nao_duplica(self, db):
        [bid] = legado(db, DOC_COM_DESCONTO)
        stats = importar(db, no(veiculos=[], boletos=[DOC_COM_DESCONTO]))
        assert [b.id for b in cobrancas(db)] == [bid]
        assert db.get(Billing, bid).amount == Decimal('100.00')  # nada muda sem decisão
        assert stats.bloqueios == {'legado_com_desconto_descartado': 1}
        # Rodar de novo continua não duplicando.
        importar(db, no(veiculos=[], boletos=[DOC_COM_DESCONTO]))
        assert [b.id for b in cobrancas(db)] == [bid]


class TestBackfill:
    def test_simulacao_relata_e_nao_grava(self, db, backfill):
        legado(db, DOC_SEM_DESCONTO)
        legado(db, DOC_COM_DESCONTO)
        resultado = backfill.executar(db, aplicar=False, compensar=False, operador=None)
        db.rollback()
        assert (resultado['documentos'], resultado['conciliados']) == (2, 1)
        assert resultado['bloqueados'] == {'legado_com_desconto_descartado': 1}
        assert resultado['descontos_descartados_centavos'] == 2000
        assert db.query(SgrDocumento).count() == 0

    def test_aplicar_grava_proveniencia_e_a_importacao_seguinte_reconhece(self, db, backfill):
        ids = legado(db, DOC_SEM_DESCONTO)
        backfill.executar(db, aplicar=True, compensar=False, operador=None)
        db.commit()
        documento = db.query(SgrDocumento).one()
        assert (documento.status, documento.criado_por) == ('conciliado', 'backfill')
        stats = importar(db, no(veiculos=[], boletos=[DOC_SEM_DESCONTO]))
        assert stats.documentos_inalterados == 1
        assert [b.id for b in cobrancas(db)] == ids

    def test_compensacao_leva_ao_liquido_com_historico(self, db, backfill):
        [bid] = legado(db, DOC_COM_DESCONTO)
        resultado = backfill.executar(db, aplicar=True, compensar=True, operador='Financeiro')
        db.commit()
        assert resultado['compensados'] == 1
        cobranca = db.get(Billing, bid)
        assert cobranca.amount == Decimal('80.00')
        [log] = db.query(BillingChangeLog).filter_by(billing_id=bid, field_name='amount').all()
        assert (log.previous_value, log.new_value) == ('100.00', '80.00')
        assert 'Financeiro' in log.justification and '8002' in log.justification
        assert db.query(SgrDocumento).one().status == 'conciliado'
        assert db.query(SgrDocumentoLinha).filter_by(tipo='desconto').one().valor_origem_centavos == -2000

        stats = importar(db, no(veiculos=[], boletos=[DOC_COM_DESCONTO]))
        assert stats.documentos_inalterados == 1 and stats.manifesto['diferencas'] == []

    def test_compensacao_recusa_cobranca_cancelada(self, db, backfill):
        bol = boleto(8003, [('100,00', 'ABC1234', 'MENSALIDADE'), ('-20,00', 'ABC1234', 'DESCONTO')])
        [bid] = legado(db, bol)
        db.get(Billing, bid).status = BillingStatus.CANCELED
        db.commit()
        resultado = backfill.executar(db, aplicar=True, compensar=True, operador='Financeiro')
        assert resultado['compensacao_recusada'] == [{'cod_boleto': '8003', 'client_id': db.get(Billing, bid).client_id,
                                                      'motivo': 'cobranca_cancelada'}]
        assert db.get(Billing, bid).amount == Decimal('100.00')

    def test_ambiguidade_nao_inventa_vinculo(self, db, backfill):
        ids = legado(db, DOC_SEM_DESCONTO)
        legado(db, DOC_SEM_DESCONTO)  # a mesma linha gravada duas vezes
        resultado = backfill.executar(db, aplicar=True, compensar=False, operador=None)
        assert resultado['bloqueados'] == {'legado_ambiguo': 1}
        assert db.query(SgrDocumentoLinha).count() == 0
        assert ids

    def test_vinculo_cruzado_existente_e_listado(self, db, backfill):
        a = Client(name='A', cpf_cnpj=CPF_A, type='pf', status=ClientStatus.ACTIVE)
        b = Client(name='B', cpf_cnpj='52998224725', type='pf', status=ClientStatus.ACTIVE)
        db.add_all([a, b])
        db.flush()
        carro = Vehicle(client_id=a.id, plate='ABC1234', status=VehicleStatus.ACTIVE)
        plano = Plan(name='P', price=Decimal('10'))
        db.add_all([carro, plano])
        db.flush()
        cruzado = Contract(client_id=b.id, vehicle_id=carro.id, plan_id=plano.id,
                           start_date=__import__('datetime').date(2024, 1, 1), status='ativo')
        db.add(cruzado)
        db.commit()
        resultado = backfill.executar(db, aplicar=False, compensar=False, operador=None)
        assert resultado['vinculos_cruzados']['contratos_ativos'] == {'total': 1, 'ids': [cruzado.id]}
