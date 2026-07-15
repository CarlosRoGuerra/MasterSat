"""Testes de Contas a Pagar (/payables) e operações em lote de boletos."""
from __future__ import annotations

from datetime import date, timedelta

PREFIX = '/api/v1/payables'
BPREFIX = '/api/v1/billings'


def _nova_conta(http, **kw):
    body = {
        'description': 'Aluguel galpão',
        'supplier': 'Imobiliária X',
        'category': 'aluguel',
        'amount': 1500.0,
        'due_date': str(date.today() + timedelta(days=10)),
    }
    body.update(kw)
    return http.post(f'{PREFIX}/', json=body)


class TestContasAPagar:
    def test_cadastrar_e_listar(self, http):
        r = _nova_conta(http)
        assert r.status_code == 200
        assert r.json()['status'] == 'pendente'

        lista = http.get(f'{PREFIX}/').json()
        assert len(lista) == 1
        assert lista[0]['description'] == 'Aluguel galpão'

    def test_pagar_conta(self, http):
        conta = _nova_conta(http).json()
        r = http.post(f'{PREFIX}/{conta["id"]}/pay', json={
            'payment_date': str(date.today()), 'payment_method': 'pix',
        })
        assert r.status_code == 200
        assert r.json()['status'] == 'paga'
        # pagar de novo → 400
        r2 = http.post(f'{PREFIX}/{conta["id"]}/pay', json={
            'payment_date': str(date.today()), 'payment_method': 'pix',
        })
        assert r2.status_code == 400

    def test_conta_vencida_tem_overdue_days(self, http):
        conta = _nova_conta(http, due_date=str(date.today() - timedelta(days=3))).json()
        lista = http.get(f'{PREFIX}/', params={'status': 'pendente'}).json()
        item = next(c for c in lista if c['id'] == conta['id'])
        assert item['overdue_days'] == 3

    def test_cancelar_e_excluir(self, http):
        conta = _nova_conta(http).json()
        assert http.post(f'{PREFIX}/{conta["id"]}/cancel').json()['status'] == 'cancelada'
        assert http.delete(f'{PREFIX}/{conta["id"]}').status_code == 200
        assert http.get(f'{PREFIX}/').json() == []

    def test_operacional_sem_acesso(self, http_op):
        assert http_op.get(f'{PREFIX}/').status_code == 403


class TestLoteSituacao:
    def test_receber_em_lote(self, http, billing_pendente, billing_vencida):
        r = http.post(f'{BPREFIX}/lote/situacao', json={
            'billing_ids': [billing_pendente.id, billing_vencida.id],
            'action': 'receber',
            'payment_date': str(date.today()),
            'payment_method': 'pix',
        })
        assert r.status_code == 200
        assert sorted(r.json()['processados']) == sorted([billing_pendente.id, billing_vencida.id])
        for bid in (billing_pendente.id, billing_vencida.id):
            assert http.get(f'{BPREFIX}/{bid}').json()['status'] == 'paga'

    def test_cancelar_em_lote_ignora_paga(self, http, db, billing_pendente, billing_vencida):
        from app.models.enums import BillingStatus
        billing_pendente.status = BillingStatus.PAID
        db.commit()
        r = http.post(f'{BPREFIX}/lote/situacao', json={
            'billing_ids': [billing_pendente.id, billing_vencida.id],
            'action': 'cancelar',
            'reason': 'Negociação',
        })
        body = r.json()
        assert body['processados'] == [billing_vencida.id]
        assert body['ignorados'] == [billing_pendente.id]

    def test_receber_sem_dados_400(self, http, billing_pendente):
        r = http.post(f'{BPREFIX}/lote/situacao', json={
            'billing_ids': [billing_pendente.id], 'action': 'receber',
        })
        assert r.status_code == 400


class TestLoteManutencao:
    def test_altera_vencimento_em_lote_com_historico(self, http, billing_pendente, billing_vencida):
        novo = str(date.today() + timedelta(days=30))
        r = http.post(f'{BPREFIX}/lote/manutencao', json={
            'billing_ids': [billing_pendente.id, billing_vencida.id],
            'due_date': novo,
            'justification': 'Prorrogação negociada',
        })
        assert r.status_code == 200
        assert len(r.json()['processados']) == 2
        for bid in (billing_pendente.id, billing_vencida.id):
            assert http.get(f'{BPREFIX}/{bid}').json()['due_date'] == novo
            changes = http.get(f'{BPREFIX}/{bid}/changes').json()
            assert any('[lote] Prorrogação negociada' in c['justification'] for c in changes)

    def test_sem_campos_400(self, http, billing_pendente):
        r = http.post(f'{BPREFIX}/lote/manutencao', json={
            'billing_ids': [billing_pendente.id], 'justification': 'x',
        })
        assert r.status_code == 400
