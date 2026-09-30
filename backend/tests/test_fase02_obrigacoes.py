"""Fase 02 — obrigação financeira, competência canônica, substituição e parcelas.

Regressões dos achados FIN-01, FIN-04, FIN-11, FIN-12 (parte serial) e DB-02
pelo seam HTTP e pelos serviços. As corridas reais (duas sessões) estão em
test_fase02_postgres.py; migrations em test_fase02_migrations_postgres.py.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.models.billing import Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.services.billing_closure import execute_closure, simulate_closure
from app.services.competencia import competencia_do_rotulo, rotulo_canonico
from app.services.financial import (
    InstallmentSplitError,
    generate_item_billings,
    split_amount_in_installments,
)

PREFIX = '/api/v1/billings'


def _mensalidade(db, contrato, rotulo, *, status=BillingStatus.PENDING, due=date(2099, 9, 15), **extra):
    b = Billing(
        contract_id=contrato.id,
        client_id=contrato.client_id,
        amount=Decimal('99.90'),
        due_date=due,
        status=status,
        billing_type=extra.pop('billing_type', 'recorrente'),
        period_label=rotulo,
        title='Plano Teste',
        **extra,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _payload(contrato, **extra):
    base = {
        'client_id': contrato.client_id,
        'contract_id': contrato.id,
        'amount': 99.90,
        'due_date': '2099-09-15',
        'billing_type': 'recorrente',
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Competência canônica (FIN-04)
# ---------------------------------------------------------------------------

class TestCompetenciaCanonica:
    @pytest.mark.parametrize('rotulo,esperado', [
        ('09/2026', date(2026, 9, 1)),
        ('9/2026', date(2026, 9, 1)),
        (' 9 / 2026 ', date(2026, 9, 1)),
        ('2026-09', date(2026, 9, 1)),
        ('2026 • T3', date(2026, 7, 1)),
        ('2026 • S2', date(2026, 7, 1)),
        ('2026', date(2026, 1, 1)),
        ('13/2026', None),
        ('Setembro/2026', None),
        (None, None),
    ])
    def test_rotulos_equivalentes_viram_a_mesma_data(self, rotulo, esperado):
        assert competencia_do_rotulo(rotulo) == esperado

    def test_rotulo_canonico_preserva_a_granularidade(self):
        assert rotulo_canonico('9/2026') == '09/2026'
        assert rotulo_canonico('2026•t3') == '2026 • T3'
        assert rotulo_canonico('2026 • s2') == '2026 • S2'
        assert rotulo_canonico('2026') == '2026'
        assert rotulo_canonico('lixo') is None

    def test_orm_preenche_competencia_em_qualquer_escritor(self, db, contrato):
        b = _mensalidade(db, contrato, '9/2099')
        assert b.competencia == date(2099, 9, 1)
        b.period_label = '10/2099'
        db.commit()
        db.refresh(b)
        assert b.competencia == date(2099, 10, 1)

    def test_rotulos_equivalentes_nao_passam_um_ao_lado_do_outro(self, http, db, contrato):
        """Reprodução da auditoria: '09/2026' e '9/2026' eram persistidos juntos."""
        _mensalidade(db, contrato, '09/2099')
        r = http.post(PREFIX + '/', json=_payload(contrato, period_label='9/2099'))
        assert r.status_code == 409
        assert r.json()['detail']['code'] == 'competencia_ocupada'
        assert db.query(Billing).filter(Billing.contract_id == contrato.id).count() == 1

    def test_barreira_do_banco_vira_409_de_dominio(self, http, db, contrato, monkeypatch):
        """Se a checagem da aplicação deixar passar (caminho novo, corrida), o
        índice decide e a resposta é conflito de domínio, não 500."""
        import app.api.v1.endpoints.billings as endpoint

        _mensalidade(db, contrato, '09/2099')
        monkeypatch.setattr(endpoint, 'existing_recurring_periods', lambda *a, **k: set())
        r = http.post(PREFIX + '/', json=_payload(contrato, period_label='9/2099'))
        assert r.status_code == 409
        assert 'já tem mensalidade lançada para esta competência' in r.json()['detail']

    def test_criacao_grava_rotulo_canonico(self, http, db, contrato):
        r = http.post(PREFIX + '/', json=_payload(contrato, period_label=' 9/2099'))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['period_label'] == '09/2099'
        assert body['competencia'] == '2099-09-01'
        assert body['competencia_liberada'] is False
        assert body['substituted_by_id'] is None

    def test_mensalidade_com_rotulo_sem_periodo_e_recusada(self, http, contrato):
        r = http.post(PREFIX + '/', json=_payload(contrato, period_label='Setembro'))
        assert r.status_code == 422

    def test_avulsa_continua_aceitando_rotulo_livre(self, http, contrato):
        r = http.post(PREFIX + '/', json=_payload(contrato, billing_type='avulsa', period_label='Acordo'))
        assert r.status_code == 200, r.text
        assert r.json()['competencia'] is None

    def test_tipo_desconhecido_e_recusado(self, http, contrato):
        r = http.post(PREFIX + '/', json=_payload(contrato, billing_type='mensalidadee'))
        assert r.status_code == 422

    def test_avulsa_no_mes_da_mensalidade_continua_permitida(self, http, db, contrato):
        _mensalidade(db, contrato, '09/2099')
        r = http.post(PREFIX + '/', json=_payload(contrato, billing_type='avulsa'))
        assert r.status_code == 200, r.text

    def test_veiculo_de_outro_cliente_e_recusado(self, http, contrato, veiculo_outro_cliente):
        r = http.post(PREFIX + '/', json=_payload(
            contrato, billing_type='avulsa', vehicle_id=veiculo_outro_cliente.id,
        ))
        assert r.status_code == 400

    def test_carne_enxerga_rotulo_equivalente(self, http, db, contrato):
        _mensalidade(db, contrato, '9/2099')
        r = http.post(PREFIX + '/parcelar', json={
            'contract_id': contrato.id, 'num_parcelas': 2, 'primeiro_vencimento': '2099-09-15',
        })
        assert r.status_code == 409
        assert '09/2099' in r.json()['detail']

    def test_remover_libera_o_mes_e_permite_reuso(self, http, db, contrato):
        b = _mensalidade(db, contrato, '09/2099')
        assert http.delete(f'{PREFIX}/{b.id}').status_code == 200
        r = http.post(PREFIX + '/', json=_payload(contrato, period_label='09/2099'))
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Cancelamento simples x liberação de competência (FIN-01)
# ---------------------------------------------------------------------------

class TestCancelamentoELiberacao:
    def test_cancelada_continua_ocupando_o_mes(self, http, db, contrato):
        b = _mensalidade(db, contrato, '09/2099')
        assert http.post(f'{PREFIX}/{b.id}/cancel', json={'reason': 'dispensa'}).status_code == 200
        r = http.post(PREFIX + '/parcelar', json={
            'contract_id': contrato.id, 'num_parcelas': 2, 'primeiro_vencimento': '2099-09-15',
        })
        assert r.status_code == 409
        # A mensagem antiga mandava "cancelar" — o que não liberava nada.
        assert 'liberando a competência' in r.json()['detail']

    def test_liberar_competencia_permite_novo_carne(self, http, db, contrato):
        b = _mensalidade(db, contrato, '09/2099')
        http.post(f'{PREFIX}/{b.id}/cancel', json={'reason': 'valor errado'})
        r = http.post(f'{PREFIX}/{b.id}/liberar-competencia', json={'justificativa': 'reemitir no carnê'})
        assert r.status_code == 200, r.text
        assert r.json()['competencia_liberada'] is True
        log = db.query(BillingChangeLog).filter_by(billing_id=b.id, field_name='competencia_liberada').one()
        assert log.justification == 'reemitir no carnê'

        r = http.post(PREFIX + '/parcelar', json={
            'contract_id': contrato.id, 'num_parcelas': 2, 'primeiro_vencimento': '2099-09-15',
        })
        assert r.status_code == 200, r.text
        assert [p['period_label'] for p in r.json()] == ['09/2099', '10/2099']

    def test_cancelar_liberando_numa_so_chamada(self, http, db, contrato):
        b = _mensalidade(db, contrato, '09/2099')
        r = http.post(f'{PREFIX}/{b.id}/cancel', json={'reason': 'erro', 'liberar_competencia': True})
        assert r.status_code == 200, r.text
        assert r.json()['competencia_liberada'] is True
        assert http.post(PREFIX + '/', json=_payload(contrato, period_label='09/2099')).status_code == 200

    def test_nao_libera_cobranca_em_aberto(self, http, db, contrato):
        b = _mensalidade(db, contrato, '09/2099')
        r = http.post(f'{PREFIX}/{b.id}/liberar-competencia', json={'justificativa': 'x' * 5})
        assert r.status_code == 409
        assert r.json()['detail']['code'] == 'cobranca_nao_cancelada'

    def test_nao_libera_original_com_substituto_valido(self, http, db, contrato):
        a = _mensalidade(db, contrato, '09/2099')
        b = _mensalidade(db, contrato, '10/2099', due=date(2099, 10, 15))
        nova = http.post(PREFIX + '/unificar', json={
            'billing_ids': [a.id, b.id], 'due_date': '2099-11-15',
        }).json()
        r = http.post(f'{PREFIX}/{a.id}/liberar-competencia', json={'justificativa': 'tentar'})
        assert r.status_code == 409
        assert r.json()['detail'] == {
            'code': 'titulo_substituido',
            'billing_ids': [nova['id']],
            'message': r.json()['detail']['message'],
        }

    def test_fechamento_nao_recobra_cancelada_mas_recobra_liberada(self, db, cliente, plan):
        contrato = Contract(client_id=cliente.id, plan_id=plan.id, start_date=date(2025, 1, 15),
                            status='ativo', billing_day=15)
        db.add(contrato)
        db.commit()
        ref = date(2025, 5, 1)
        primeira = execute_closure(db, ref)['billing_ids']
        billing = db.get(Billing, primeira[0])
        billing.status = BillingStatus.CANCELED
        db.commit()
        assert execute_closure(db, ref)['generated'] == 0

        billing.competencia_liberada = True
        db.commit()
        refeito = execute_closure(db, ref)
        assert refeito['generated'] == 1
        assert refeito['billing_ids'] != primeira


# ---------------------------------------------------------------------------
# Substituição: negociação e boleto único (FIN-01)
# ---------------------------------------------------------------------------

class TestSubstituicao:
    def _negociar(self, http, db, contrato):
        a = _mensalidade(db, contrato, '09/2099')
        b = _mensalidade(db, contrato, '10/2099', due=date(2099, 10, 15))
        r = http.post(PREFIX + '/unificar', json={'billing_ids': [a.id, b.id], 'due_date': '2099-11-15'})
        assert r.status_code == 200, r.text
        return a, b, r.json()['id']

    def test_unificacao_grava_vinculo_estrutural(self, http, db, contrato):
        a, b, nova = self._negociar(http, db, contrato)
        db.expire_all()
        assert db.get(Billing, a.id).substituted_by_id == nova
        assert db.get(Billing, b.id).substituted_by_id == nova
        assert http.get(f'{PREFIX}/{a.id}').json()['substituted_by_id'] == nova

    def test_cancelar_substituto_sem_reverter_e_recusado(self, http, db, contrato):
        a, b, nova = self._negociar(http, db, contrato)
        r = http.post(f'{PREFIX}/{nova}/cancel', json={'reason': 'desistiu'})
        assert r.status_code == 409
        assert r.json()['detail']['code'] == 'titulo_substituto'
        assert r.json()['detail']['billing_ids'] == [a.id, b.id]
        db.expire_all()
        assert db.get(Billing, nova).status == BillingStatus.PENDING

    def test_cancelar_substituto_revertendo_reabre_as_originais(self, http, db, contrato):
        a, b, nova = self._negociar(http, db, contrato)
        r = http.post(f'{PREFIX}/{nova}/cancel', json={'reason': 'desistiu', 'reverter_substituicao': True})
        assert r.status_code == 200, r.text
        db.expire_all()
        for original in (a, b):
            linha = db.get(Billing, original.id)
            assert linha.status == BillingStatus.PENDING
            assert linha.substituted_by_id is None
            assert f'#{nova}' in linha.notes
        assert db.get(Billing, nova).status == BillingStatus.CANCELED
        logs = db.query(BillingChangeLog).filter(BillingChangeLog.billing_id.in_([a.id, b.id])).all()
        assert {log.new_value for log in logs} == {BillingStatus.PENDING.value}

    def test_excluir_substituto_exige_reversao_e_reabre(self, http, db, contrato):
        a, b, nova = self._negociar(http, db, contrato)
        r = http.delete(f'{PREFIX}/{nova}')
        assert r.status_code == 409
        assert r.json()['detail']['code'] == 'titulo_substituto'
        r = http.delete(f'{PREFIX}/{nova}', params={'reverter_substituicao': True})
        assert r.status_code == 200
        assert r.json()['reabertas'] == [a.id, b.id]
        abertas = db.query(Billing).filter(
            Billing.contract_id == contrato.id,
            Billing.is_deleted.is_(False),
            Billing.status == BillingStatus.PENDING,
        ).count()
        assert abertas == 2

    def test_excluir_original_substituida_e_recusado(self, http, db, contrato):
        a, _, nova = self._negociar(http, db, contrato)
        r = http.delete(f'{PREFIX}/{a.id}')
        assert r.status_code == 409
        assert r.json()['detail']['code'] == 'titulo_substituido'

    def test_cancelar_substituto_em_lote_e_recusado(self, http, db, contrato):
        _, _, nova = self._negociar(http, db, contrato)
        r = http.post(PREFIX + '/lote/situacao', json={
            'billing_ids': [nova], 'action': 'cancelar', 'reason': 'lote',
        })
        assert r.status_code == 409
        assert r.json()['detail']['billing_ids'] == [nova]

    def test_boleto_unico_removido_nao_deixa_meses_sem_cobranca(self, http, db, cliente, plan):
        """Reprodução da auditoria (delete_aggregate): excluir o consolidado
        deixava as originais canceladas ocupando os meses com zero em aberto."""
        cliente.boleto_format = 'unico'
        for _ in range(2):
            db.add(Contract(client_id=cliente.id, plan_id=plan.id, start_date=date(2025, 1, 15),
                            status='ativo', billing_day=15))
        db.commit()
        ref = date(2025, 5, 1)
        resultado = execute_closure(db, ref)
        unico = resultado['billing_ids'][0]
        originais = db.query(Billing).filter(Billing.substituted_by_id == unico).all()
        assert len(originais) == 2 and all(o.status == BillingStatus.CANCELED for o in originais)

        assert http.delete(f'{PREFIX}/{unico}').status_code == 409
        assert http.delete(f'{PREFIX}/{unico}', params={'reverter_substituicao': True}).status_code == 200

        simulacao = simulate_closure(db, ref)
        assert [i['already_generated'] for i in simulacao['items']] == [True, True]
        abertas = db.query(Billing).filter(
            Billing.client_id == cliente.id,
            Billing.is_deleted.is_(False),
            Billing.status.in_([BillingStatus.PENDING, BillingStatus.OVERDUE]),
        ).count()
        assert abertas == 2  # antes: 0

    def test_parcela_de_servico_negociada_nao_e_regerada(self, http, db, cliente):
        item = ClientChargeItem(client_id=cliente.id, title='Instalação', quantity=1,
                                unit_price=Decimal('90'), total_amount=Decimal('90'),
                                installment_count=3, start_date=date(2099, 1, 10))
        db.add(item)
        db.commit()
        parcelas = generate_item_billings(db, item)
        r = http.post(PREFIX + '/unificar', json={
            'billing_ids': [parcelas[0].id, parcelas[1].id], 'due_date': '2099-05-10',
        })
        assert r.status_code == 200, r.text
        # Mesmo forçando a volta do item à fila, as parcelas 1 e 2 estão
        # cobertas pela negociação (canceladas por substituição).
        item.active = True
        db.commit()
        assert generate_item_billings(db, item) == []


# ---------------------------------------------------------------------------
# Parcelamento em centavos (FIN-11)
# ---------------------------------------------------------------------------

def _regra_antiga(total: Decimal, n: int) -> list[Decimal]:
    base = (total / n).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return [base] * (n - 1) + [total - base * (n - 1)]


class TestParcelamento:
    @pytest.mark.parametrize('total,n,esperado', [
        ('100.00', 3, ['33.33', '33.33', '33.34']),
        ('100.00', 6, ['16.67', '16.67', '16.67', '16.67', '16.67', '16.65']),
        ('0.05', 4, ['0.01', '0.01', '0.01', '0.02']),
        ('0.07', 4, ['0.02', '0.02', '0.02', '0.01']),
        ('0.01', 1, ['0.01']),
        ('0.03', 3, ['0.01', '0.01', '0.01']),
    ])
    def test_casos_conhecidos(self, total, n, esperado):
        assert split_amount_in_installments(Decimal(total), n) == [Decimal(v) for v in esperado]

    @pytest.mark.parametrize('total,n', [('0.02', 4), ('0.01', 3), ('0.00', 1), ('0.59', 60)])
    def test_recusa_quando_nao_cabe_um_centavo_por_parcela(self, total, n):
        with pytest.raises(InstallmentSplitError):
            split_amount_in_installments(Decimal(total), n)

    def test_residuo_que_zerava_ou_negativava_a_ultima(self):
        # 1,00 em 60: a regra antiga dava 0,02 × 59 e -0,18 na última.
        parcelas = split_amount_in_installments(Decimal('1.00'), 60)
        assert sum(parcelas) == Decimal('1.00')
        assert min(parcelas) > 0
        # 0,06 em 4: a antiga dava 0,02 × 3 e 0,00.
        assert split_amount_in_installments(Decimal('0.06'), 4) == [Decimal('0.01')] * 3 + [Decimal('0.03')]

    def test_varredura_soma_exata_positiva_e_compativel(self):
        for cents in range(1, 701):
            total = Decimal(cents) / 100
            for n in range(1, min(cents, 60) + 1):
                parcelas = split_amount_in_installments(total, n)
                assert sum(parcelas) == total
                assert all(p >= Decimal('0.01') for p in parcelas)
                antiga = _regra_antiga(total, n)
                if min(antiga) > 0:
                    # Nada muda onde a regra antiga já era válida.
                    assert parcelas == antiga, (total, n)

    def test_entrada_com_mais_de_duas_casas_e_quantizada(self):
        assert sum(split_amount_in_installments('10.005', 2)) == Decimal('10.01')

    def test_api_recusa_item_que_geraria_parcela_negativa(self, http, cliente):
        """Reprodução da auditoria (negative_installment): 0,02 em 4."""
        r = http.post('/api/v1/client-charge-items/', json={
            'client_id': cliente.id, 'title': 'Taxa', 'unit_price': 0.02, 'quantity': 1,
            'installment_count': 4, 'start_date': '2099-01-10',
        })
        assert r.status_code == 422
        assert 'não comporta 4 parcelas' in r.json()['detail']

    def test_api_recusa_preco_com_tres_casas(self, http, cliente):
        r = http.post('/api/v1/client-charge-items/', json={
            'client_id': cliente.id, 'title': 'Taxa', 'unit_price': 0.015, 'quantity': 3,
            'installment_count': 1, 'start_date': '2099-01-10',
        })
        assert r.status_code == 422

    def test_api_total_em_decimal(self, http, cliente):
        r = http.post('/api/v1/client-charge-items/', json={
            'client_id': cliente.id, 'title': 'Taxa', 'unit_price': 0.1, 'quantity': 3,
            'installment_count': 1, 'start_date': '2099-01-10',
        })
        assert r.status_code == 200, r.text
        assert r.json()['total_amount'] == pytest.approx(0.30)

    def test_edicao_que_quebraria_o_parcelamento_e_recusada(self, http, cliente):
        criado = http.post('/api/v1/client-charge-items/', json={
            'client_id': cliente.id, 'title': 'Taxa', 'unit_price': 0.05, 'quantity': 1,
            'installment_count': 1, 'start_date': '2099-01-10',
        }).json()
        r = http.put(f"/api/v1/client-charge-items/{criado['id']}", json={'installment_count': 6})
        assert r.status_code == 422

    def test_fechamento_com_item_legado_invalido_falha_fechado(self, db, cliente):
        # Legado criado antes da validação: não pode virar parcela ≤ 0.
        item = ClientChargeItem(client_id=cliente.id, title='Legado', quantity=1,
                                unit_price=Decimal('0.02'), total_amount=Decimal('0.02'),
                                installment_count=4, start_date=date(2025, 5, 10))
        db.add(item)
        db.commit()
        with pytest.raises(ValueError, match='não comporta 4 parcelas'):
            simulate_closure(db, date(2025, 5, 1))
        assert db.query(Billing).count() == 0

    def test_previa_do_fechamento_soma_o_que_sera_gravado(self, db, cliente):
        item = ClientChargeItem(client_id=cliente.id, title='Serviço', quantity=1,
                                unit_price=Decimal('100'), total_amount=Decimal('100'),
                                installment_count=6, start_date=date(2025, 5, 10))
        db.add(item)
        db.commit()
        previa = simulate_closure(db, date(2025, 5, 1))['charge_items'][0]
        assert previa['total_remaining'] == pytest.approx(100.00)  # antes: 16,67 × 6 = 100,02
        resultado = execute_closure(db, date(2025, 5, 1))
        assert resultado['total_services_amount'] == pytest.approx(100.00)


# ---------------------------------------------------------------------------
# Parcela única por serviço (FIN-12, parte serial) e soft delete
# ---------------------------------------------------------------------------

class TestParcelaUnica:
    def _item(self, db, cliente, **extra):
        valores = dict(client_id=cliente.id, title='Serviço', quantity=1, unit_price=Decimal('100'),
                       total_amount=Decimal('100'), installment_count=1, start_date=date(2025, 9, 10))
        valores.update(extra)
        item = ClientChargeItem(**valores)
        db.add(item)
        db.commit()
        return item

    def test_item_faturado_por_outro_fechamento_nao_gera_de_novo(self, db, cliente):
        item = self._item(db, cliente)
        assert len(generate_item_billings(db, item)) == 1
        # Segundo fechamento que tinha selecionado o mesmo item antes.
        assert generate_item_billings(db, item) == []
        assert db.query(Billing).filter(Billing.item_id == item.id).count() == 1

    def test_fechamento_seguinte_informa_item_ja_faturado(self, db, cliente):
        item = self._item(db, cliente)
        set_ = execute_closure(db, date(2025, 9, 1), charge_item_ids=[item.id])
        assert set_['services_generated'] == 1
        out = execute_closure(db, date(2025, 10, 1), charge_item_ids=[item.id])
        assert out['services_generated'] == 0
        assert db.query(Billing).filter(Billing.item_id == item.id).count() == 1

    def test_parcela_cancelada_volta_para_a_fila_e_regera(self, http, db, cliente):
        item = self._item(db, cliente)
        [parcela] = generate_item_billings(db, item)
        assert http.post(f'{PREFIX}/{parcela.id}/cancel', json={'reason': 'erro'}).status_code == 200
        db.refresh(item)
        assert item.active is True
        [nova] = generate_item_billings(db, item)
        assert nova.installment_number == 1 and nova.id != parcela.id

    def test_indice_barra_parcela_duplicada_de_escritor_sem_trava(self, db, cliente):
        from sqlalchemy.exc import IntegrityError

        item = self._item(db, cliente)
        generate_item_billings(db, item)
        db.add(Billing(client_id=cliente.id, item_id=item.id, installment_number=1, installment_total=1,
                       amount=Decimal('100'), due_date=date(2025, 9, 10), billing_type='item',
                       status=BillingStatus.PENDING))
        with pytest.raises(IntegrityError):
            db.commit()


# ---------------------------------------------------------------------------
# CHECKs do banco (DB-02)
# ---------------------------------------------------------------------------

class TestChecks:
    @pytest.mark.parametrize('campos', [
        {'amount': Decimal('0')},
        {'amount': Decimal('-0.01')},
        {'paid_amount': Decimal('0')},
        {'installment_number': 0},
        {'installment_number': 3, 'installment_total': 2},
        {'competencia_liberada': True},  # só cancelada pode liberar
    ])
    def test_cobranca_incoerente_nao_e_gravada(self, db, contrato, campos):
        from sqlalchemy.exc import IntegrityError

        valores = dict(contract_id=contrato.id, client_id=contrato.client_id, amount=Decimal('10'),
                       due_date=date(2099, 1, 1), billing_type='avulsa', status=BillingStatus.PENDING)
        valores.update(campos)
        db.add(Billing(**valores))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_removida_com_valor_legado_nao_bloqueia(self, db, contrato):
        # Exceção nominal: removida é histórico; remover é o saneamento.
        db.add(Billing(contract_id=contrato.id, client_id=contrato.client_id, amount=Decimal('-0.01'),
                       due_date=date(2099, 1, 1), billing_type='item', status=BillingStatus.PENDING,
                       is_deleted=True))
        db.commit()

    def test_substituida_precisa_estar_cancelada(self, db, contrato, billing_pendente):
        from sqlalchemy.exc import IntegrityError

        db.add(Billing(contract_id=contrato.id, client_id=contrato.client_id, amount=Decimal('10'),
                       due_date=date(2099, 1, 1), billing_type='avulsa', status=BillingStatus.PENDING,
                       substituted_by_id=billing_pendente.id))
        with pytest.raises(IntegrityError):
            db.commit()
