"""Fase 03 — FIN-08: contas a pagar com máquina de estados, trava e histórico."""
from __future__ import annotations

from app.models.payable import Payable
from app.models.payable_change_log import PayableChangeLog

P = '/api/v1/payables'


def _nova(http, **kw) -> int:
    corpo = {'description': 'Aluguel', 'amount': 500, 'due_date': '2099-10-10', **kw}
    return http.post(f'{P}/', json=corpo).json()['id']


def _pagar(http, pid):
    return http.post(f'{P}/{pid}/pay', json={'payment_date': '2099-10-10', 'payment_method': 'pix'})


class TestTransicoes:
    def test_cancelada_nao_vira_paga(self, http, db):
        pid = _nova(http)
        assert http.post(f'{P}/{pid}/cancel').status_code == 200
        r = _pagar(http, pid)
        assert r.status_code == 409 and r.json()['detail']['code'] == 'conta_cancelada'
        assert db.get(Payable, pid).status == 'cancelada'

    def test_paga_nao_muda_valor_nem_e_removida(self, http, db):
        pid = _nova(http)
        assert _pagar(http, pid).status_code == 200
        editar = http.put(f'{P}/{pid}', json={'amount': 1})
        assert editar.status_code == 409 and editar.json()['detail']['code'] == 'conta_em_estado_terminal'
        assert http.put(f'{P}/{pid}', json={'due_date': '2099-01-01'}).status_code == 409
        remover = http.delete(f'{P}/{pid}')
        assert remover.status_code == 409 and remover.json()['detail']['code'] == 'conta_paga'
        conta = db.get(Payable, pid)
        db.refresh(conta)
        assert (float(conta.amount), conta.is_deleted) == (500.0, False)

    def test_paga_aceita_correcao_descritiva(self, http, db):
        pid = _nova(http)
        _pagar(http, pid)
        r = http.put(f'{P}/{pid}', json={'category': 'Imóvel', 'justification': 'reclassificação'})
        assert r.status_code == 200 and r.json()['category'] == 'Imóvel'

    def test_cancelada_nao_muda_valor(self, http):
        pid = _nova(http)
        http.post(f'{P}/{pid}/cancel')
        assert http.put(f'{P}/{pid}', json={'amount': 10}).status_code == 409

    def test_estorno_volta_a_pendente_e_permite_corrigir(self, http, db):
        pid = _nova(http)
        _pagar(http, pid)
        assert http.post(f'{P}/{pid}/estornar', json={'justificativa': 'pago em duplicidade'}).status_code == 200
        conta = db.get(Payable, pid)
        db.refresh(conta)
        assert (conta.status, conta.payment_date) == ('pendente', None)
        assert http.put(f'{P}/{pid}', json={'amount': 450, 'justification': 'valor correto'}).status_code == 200
        assert http.post(f'{P}/{pid}/estornar', json={'justificativa': 'x' * 3}).status_code == 409

    def test_pendente_e_cancelada_podem_ser_removidas(self, http):
        assert http.delete(f'{P}/{_nova(http)}').status_code == 200
        pid = _nova(http)
        http.post(f'{P}/{pid}/cancel', json={'reason': 'duplicada'})
        assert http.delete(f'{P}/{pid}').status_code == 200


class TestHistorico:
    def test_toda_mudanca_tem_antes_depois_e_responsavel(self, http, db):
        pid = _nova(http)
        http.put(f'{P}/{pid}', json={'amount': 450, 'justification': 'reajuste'})
        _pagar(http, pid)
        http.post(f'{P}/{pid}/estornar', json={'justificativa': 'erro'})
        hist = http.get(f'{P}/{pid}/historico').json()
        acoes = [h['action'] for h in reversed(hist)]
        assert acoes == ['criacao', 'edicao', 'pagamento', 'estorno']
        edicao = next(h for h in hist if h['action'] == 'edicao')
        assert (edicao['field_name'], edicao['previous_value'], edicao['new_value']) == ('amount', '500.00', '450.00')
        assert edicao['justification'] == 'reajuste'
        assert all(h['changed_by_user_id'] == 1 for h in hist)
        assert db.query(PayableChangeLog).filter_by(payable_id=pid).count() == 4
