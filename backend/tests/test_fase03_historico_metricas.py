"""Fase 03 — FIN-10 (histórico de relações removidas), PROD-01 (bases das
métricas, canceladas fora) e PROD-02 (dashboard)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models.billing import Billing
from app.models.billing_adjustment import BillingAdjustment
from app.models.client import Client
from app.models.enums import BillingStatus, ClientStatus, TrackerStatus
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from tests.fase03_apoio import cobranca

B = '/api/v1/billings'


def _paga(db, contrato, **kw) -> Billing:
    dados = dict(status=BillingStatus.PAID, payment_date=date(2026, 9, 3), paid_amount=Decimal('100.00'),
                 receipt_number='RCB-TESTE', due_date=date(2026, 8, 15))
    dados.update(kw)
    return cobranca(db, contrato, **dados)


class TestHistoricoDeRelacoesRemovidas:
    def test_contrato_removido_nao_esconde_cobranca_paga(self, http, db, contrato):
        paga = _paga(db, contrato)
        assert http.delete(f'/api/v1/contracts/{contrato.id}').status_code == 200

        lista = http.get(f'{B}/?client_id={contrato.client_id}').json()
        assert [b['id'] for b in lista] == [paga.id]
        assert lista[0]['relacoes_removidas'] == ['contrato']
        assert http.get(f'{B}/{paga.id}').status_code == 200
        assert http.get(f'{B}/{paga.id}/receipt').status_code == 200
        assert str(paga.id) in http.get(f'{B}/exports/csv').text

    def test_veiculo_plano_e_pagador_removidos_continuam_rotulados(self, http, db, contrato, veiculo, plan, outro_cliente):
        paga = _paga(db, contrato, vehicle_id=veiculo.id, payer_client_id=outro_cliente.id)
        veiculo.is_deleted = True
        plan.is_deleted = True
        outro_cliente.is_deleted = True
        db.commit()
        item = http.get(f'{B}/{paga.id}').json()
        assert set(item['relacoes_removidas']) == {'veiculo', 'plano', 'responsavel_financeiro'}
        assert item['vehicle_plate'] == veiculo.plate
        # Recibo sai no nome do pagador histórico mesmo removido.
        assert http.get(f'{B}/{paga.id}/receipt').status_code == 200

    def test_nova_cobranca_nao_referencia_contrato_removido(self, http, db, contrato):
        contrato.is_deleted = True
        db.commit()
        r = http.post(f'{B}/', json={'client_id': contrato.client_id, 'contract_id': contrato.id,
                                     'billing_type': 'avulsa', 'amount': 10, 'due_date': '2099-01-10'})
        assert r.status_code == 404

    def test_extrato_de_cliente_removido(self, http, db, contrato, cliente):
        _paga(db, contrato)
        cliente.is_deleted = True
        db.commit()
        r = http.get(f'/api/v1/reports/client-statement/{cliente.id}')
        assert r.status_code == 200 and r.json()['cliente']['removido'] is True


class TestBasesDasMetricas:
    def _cenario(self, http, db, contrato):
        """Duas de R$100 (vencimento ago/2031) negociadas numa de R$200, e uma
        de R$100 com vencimento em agosto paga em setembro."""
        a = cobranca(db, contrato, due_date=date(2031, 8, 10))
        b = cobranca(db, contrato, due_date=date(2031, 8, 12))
        r = http.post(f'{B}/unificar', json={'billing_ids': [a.id, b.id], 'due_date': '2031-08-20'})
        assert r.status_code == 200
        _paga(db, contrato, due_date=date(2031, 8, 15), payment_date=date(2031, 9, 3))

    def test_rota_antiga_sem_canceladas_e_bases_nomeadas(self, http, db, contrato):
        self._cenario(http, db, contrato)
        rev = {i['label']: i for i in http.get(f'{B}/reports/revenue?period=monthly').json()}
        assert rev['08/2031']['total_billed'] == 300.0          # antes 400: canceladas somavam
        assert rev['08/2031']['total_outstanding'] == 200.0
        assert rev['08/2031']['total_received_by_due'] == 100.0
        assert rev['08/2031']['total_received'] == 0.0          # caixa: pago em setembro
        assert rev['09/2031']['total_received'] == 100.0
        assert rev['09/2031']['total_billed'] == 0.0

    def test_rotas_reconciliam_na_mesma_base(self, http, db, contrato):
        self._cenario(http, db, contrato)
        antiga = {i['label']: i for i in http.get(f'{B}/reports/revenue?period=monthly').json()}
        nova = http.get('/api/v1/reports/revenue?date_from=2031-08-01&date_to=2031-09-30').json()
        mes = {m['label']: m for m in nova['meses']}
        assert nova['base'] == 'vencimento' and 'total_recebido_caixa' in nova['definicoes']
        for label in ('08/2031', '09/2031'):
            assert mes[label]['total_emitido'] == antiga[label]['total_billed']
            assert mes[label]['total_aberto'] == antiga[label]['total_outstanding']
            assert mes[label]['total_recebido'] == antiga[label]['total_received_by_due']
            assert mes[label]['total_recebido_caixa'] == antiga[label]['total_received']
        assert nova['totais']['emitido_avulso'] == 300.0 and nova['totais']['emitido_recorrente'] == 0.0

    def test_ordem_das_series_atravessa_a_virada_do_ano(self, http, db, contrato):
        cobranca(db, contrato, due_date=date(2030, 12, 10))
        cobranca(db, contrato, due_date=date(2031, 1, 10))
        labels = [i['label'] for i in http.get(f'{B}/reports/revenue?period=monthly').json()]
        assert labels.index('12/2030') < labels.index('01/2031')

    def test_serie_trimestral_e_anual_preserva_caixa_e_vencimento(self, http, db, contrato):
        _paga(db, contrato, due_date=date(2030, 12, 10), payment_date=date(2031, 1, 2))
        cobranca(db, contrato, amount=Decimal('50.00'), due_date=date(2031, 2, 10))
        cobranca(db, contrato, status=BillingStatus.CANCELED, due_date=date(2031, 2, 12))

        trimestral = {item['label']: item for item in http.get(f'{B}/reports/revenue?period=quarterly').json()}
        anual = {item['label']: item for item in http.get(f'{B}/reports/revenue?period=annual').json()}
        assert trimestral['2030 • T4']['total_billed'] == 100.0
        assert trimestral['2030 • T4']['total_received_by_due'] == 100.0
        assert trimestral['2031 • T1']['total_billed'] == 50.0
        assert trimestral['2031 • T1']['total_outstanding'] == 50.0
        assert trimestral['2031 • T1']['total_received'] == 100.0
        assert anual['2030']['total_billed'] == 100.0
        assert anual['2031']['total_billed'] == 50.0
        assert anual['2031']['total_received'] == 100.0

    def test_base_competencia(self, http, db, contrato):
        # Mensalidade de competência 07/2031 que vence em 08/2031.
        cobranca(db, contrato, billing_type='recorrente', period_label='07/2031', due_date=date(2031, 8, 5))
        venc = http.get('/api/v1/reports/revenue?date_from=2031-07-01&date_to=2031-08-31').json()
        comp = http.get('/api/v1/reports/revenue?date_from=2031-07-01&date_to=2031-08-31&base=competencia').json()
        assert [m['label'] for m in venc['meses']] == ['08/2031']
        assert [m['label'] for m in comp['meses']] == ['07/2031']
        assert comp['meses'][0]['emitido_recorrente'] == 100.0

    def test_caixa_separa_principal_e_encargos(self, http, db, contrato):
        paga = _paga(db, contrato, due_date=date(2031, 3, 10), payment_date=date(2031, 4, 2),
                     paid_amount=Decimal('103.00'))
        db.add(BillingAdjustment(billing_id=paga.id, kind='encargos', amount=Decimal('3.00'), justification='multa'))
        db.commit()
        rel = http.get('/api/v1/reports/revenue?date_from=2031-04-01&date_to=2031-04-30').json()
        abril = rel['meses'][0]
        assert (abril['total_recebido_caixa'], abril['encargos_caixa'], abril['recebido_principal_caixa']) == (103.0, 3.0, 100.0)

    def test_extrato_separa_atendido_e_pagador_e_ignora_canceladas(self, http, db, contrato, cliente, outro_cliente):
        cobranca(db, contrato)
        cobranca(db, contrato, status=BillingStatus.CANCELED)
        cobranca(db, contrato, payer_client_id=outro_cliente.id, amount=Decimal('40.00'))
        dono = http.get(f'/api/v1/reports/client-statement/{cliente.id}').json()
        # atendido: 100 próprio + 40 pago pelo interveniente (cancelada fora)
        assert dono['resumo']['total_cobrado'] == 140.0 and dono['resumo']['qtd_canceladas'] == 1
        # responsável financeiro: só a própria de 100 (a de 40 é do outro)
        assert dono['resumo_responsavel_financeiro']['total_cobrado'] == 100.0
        pagador = http.get(f'/api/v1/reports/client-statement/{outro_cliente.id}').json()
        assert [c['papel'] for c in pagador['cobrancas']] == ['responsavel_financeiro']
        assert pagador['resumo_responsavel_financeiro']['total_aberto'] == 40.0


class TestDashboard:
    def test_suspensos_no_denominador(self, http, db):
        for i in range(2):
            db.add(Client(name=f'S{i}', cpf_cnpj=f'0000000000{i}', type='pf', status=ClientStatus.SUSPENDED))
            db.add(Client(name=f'A{i}', cpf_cnpj=f'1000000000{i}', type='pf', status=ClientStatus.ACTIVE))
        db.commit()
        c = http.get('/api/v1/dashboard/').json()['clients']
        assert (c['active'], c['suspended'], c['total']) == (2, 2, 4)

    def test_veiculos_com_rastreador_sao_distintos(self, http, db, cliente, veiculo, rastreador_instalado):
        db.add(Tracker(imei='111', brand='x', model='y', status=TrackerStatus.INSTALLED,
                       client_id=cliente.id, vehicle_id=veiculo.id))
        db.add(Vehicle(client_id=cliente.id, plate='SEM0R00', type='passeio'))
        db.commit()
        v = http.get('/api/v1/dashboard/').json()['vehicles']
        assert (v['total'], v['with_tracker'], v['without_tracker']) == (2, 1, 1)

    def test_proximos_vencimentos_nao_sao_escondidos_por_atrasos(self, http, db, contrato):
        hoje = date.today()
        for d in range(6):
            cobranca(db, contrato, status=BillingStatus.OVERDUE, due_date=hoje - timedelta(days=100 + d))
        proxima = cobranca(db, contrato, due_date=hoje + timedelta(days=3))
        longe = cobranca(db, contrato, due_date=hoje + timedelta(days=30))
        d = http.get('/api/v1/dashboard/').json()
        assert [b['id'] for b in d['upcoming_billings']] == [proxima.id]
        assert longe.id not in [b['id'] for b in d['upcoming_billings']]
        assert len(d['overdue_billings']) == 5
        assert all(b['days_until'] < 0 for b in d['overdue_billings'])
