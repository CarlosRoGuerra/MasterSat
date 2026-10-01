"""Fase 03 — FIN-05 (carteira inteira conciliada) e a baixa automática sem
quitação silenciosa (FIN-06, lado banco)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.enums import BillingStatus
from app.services import ailos_boletos
from tests.fase03_apoio import ailos_falsa  # noqa: F401 — fixture
from tests.fase03_apoio import boleto_json, cobranca, registrar_titulo, resp


def _numero(url: str) -> int:
    return int(url.rstrip('/').rsplit('/', 1)[-1])


def _carteira(db, contrato, n: int) -> list[Billing]:
    billings = [
        Billing(contract_id=contrato.id, client_id=contrato.client_id, amount=Decimal('99.90'),
                due_date=date(2099, 11, 30), status=BillingStatus.PENDING, billing_type='avulsa')
        for _ in range(n)
    ]
    db.add_all(billings)
    db.commit()
    db.add_all([
        AilosBoleto(billing_id=b.id, numero_convenio='102004', numero_documento=str(b.id),
                    nosso_numero=f'NN{b.id}', linha_digitavel=f'LD{b.id}', codigo_barras=f'CB{b.id}',
                    status_ailos='0')
        for b in billings
    ])
    db.commit()
    return billings


class TestRodizio:
    def test_1001_titulos_com_os_primeiros_300_nunca_pagos(self, db, contrato, ailos_falsa):
        billings = _carteira(db, contrato, 1001)
        nunca_pagos = {b.id for b in billings[:300]}
        consultados: list[int] = []

        def responder(method, url, **_k):
            numero = _numero(url)
            consultados.append(numero)
            pago = None if numero in nunca_pagos else Decimal('99.90')
            return resp(200, boleto_json(numero, pago=pago))

        ailos_falsa(responder)
        execucoes = [ailos_boletos.conciliar_boletos_abertos(db, limit=300) for _ in range(4)]

        assert set(consultados) == {b.id for b in billings}  # ninguém ficou de fora
        assert len(consultados) == 300 * 3 + (1001 - 900) + 199  # 4ª rodada volta aos 300 abertos
        assert sum(e['baixados'] for e in execucoes) == 701
        assert db.query(Billing).filter(Billing.status == BillingStatus.PAID).count() == 701
        assert execucoes[0]['carteira_monitorada'] == 1001 - execucoes[0]['baixados']
        assert execucoes[0]['janela_estimada_horas'] >= 1

    def test_ordem_prioriza_quem_esta_ha_mais_tempo_sem_consulta(self, db, contrato, ailos_falsa):
        a, b, c = _carteira(db, contrato, 3)
        agora = datetime.now(timezone.utc)
        for billing, horas in ((a, 1), (b, 30), (c, None)):
            boleto = db.query(AilosBoleto).filter_by(billing_id=billing.id).one()
            boleto.ultima_consulta_em = agora - timedelta(hours=horas) if horas else None
        db.commit()
        consultados = []
        ailos_falsa(lambda m, u, **k: consultados.append(_numero(u)) or resp(200, boleto_json(_numero(u))))
        ailos_boletos.conciliar_boletos_abertos(db, limit=2)
        assert consultados == [c.id, b.id]

    def test_erro_individual_nao_para_a_carteira_e_vai_para_o_fim_da_fila(self, db, contrato, ailos_falsa):
        a, b, c = _carteira(db, contrato, 3)

        def responder(method, url, **_k):
            if _numero(url) == a.id:
                return resp(500, {'mensagem': 'falha pontual'})
            return resp(200, boleto_json(_numero(url)))

        ailos_falsa(responder)
        r1 = ailos_boletos.conciliar_boletos_abertos(db, limit=3)
        assert r1['erros'] == 1 and r1['consultados'] == 2
        boleto_a = db.query(AilosBoleto).filter_by(billing_id=a.id).one()
        assert boleto_a.falhas_consulta == 1 and boleto_a.ultima_consulta_erro
        assert boleto_a.ultima_consulta_em is not None

        consultados = []
        ailos_falsa(lambda m, u, **k: consultados.append(_numero(u)) or resp(200, boleto_json(_numero(u))))
        ailos_boletos.conciliar_boletos_abertos(db, limit=2)
        assert consultados == [a.id, b.id]  # a consultada há mais tempo; não prende a fila
        db.refresh(boleto_a)
        assert boleto_a.falhas_consulta == 0 and boleto_a.ultima_consulta_erro is None

    def test_consulta_repetida_de_titulo_pago_da_uma_baixa_so(self, db, contrato, ailos_falsa):
        (b,) = _carteira(db, contrato, 1)
        ailos_falsa(lambda m, u, **k: resp(200, boleto_json(b.id, pago=Decimal('99.90'))))
        primeira = ailos_boletos.verificar_pagamento(db, b)
        segunda = ailos_boletos.verificar_pagamento(db, db.get(Billing, b.id))
        assert primeira['quitada'] is True and segunda['quitada'] is False
        db.refresh(b)
        assert b.status == BillingStatus.PAID and b.paid_amount == Decimal('99.90')
        assert b.notes.count('Baixa automática via Ailos.') == 1
        assert ailos_boletos.conciliar_boletos_abertos(db)['processados'] == 0

    def test_metricas_e_endpoint(self, http, db, contrato):
        _carteira(db, contrato, 2)
        m = http.get('/api/v1/ailos/conciliacao').json()
        assert m['carteira_monitorada'] == 2 and m['nunca_consultados'] == 2
        assert m['orcamento_por_execucao'] == 300 and m['janela_estimada_horas'] == 1


class TestSemQuitacaoSilenciosa:
    def test_valor_divergente_vira_pendencia_e_nao_quita(self, http, db, contrato, ailos_falsa):
        (b,) = _carteira(db, contrato, 1)
        ailos_falsa(lambda m, u, **k: resp(200, boleto_json(b.id, pago=Decimal('1.00'))))
        r = ailos_boletos.verificar_pagamento(db, b)
        assert r['quitada'] is False and r['pendencia'] == 'pagamento_divergente'
        db.refresh(b)
        assert b.status == BillingStatus.PENDING
        pend = http.get('/api/v1/ailos/pendencias').json()
        assert [p['billing_id'] for p in pend] == [b.id]
        assert pend[0]['pendencia_detalhe']['valor_pago'] == '1.00'

        # O financeiro registra o recebimento classificando a diferença: a
        # pendência é resolvida e o título (liquidado no banco) tem baixa.
        rec = http.post(f'/api/v1/billings/{b.id}/receive', json={
            'paid_amount': 1, 'payment_date': '2026-09-10', 'payment_method': 'boleto',
            'tratamento_diferenca': 'desconto', 'justificativa_diferenca': 'pago a menor no banco'})
        assert rec.status_code == 200
        boleto = db.query(AilosBoleto).filter_by(billing_id=b.id).one()
        db.refresh(boleto)
        assert boleto.pendencia is None and boleto.baixa_status == 'confirmada'

    def test_pago_no_banco_depois_de_cancelada_vira_pendencia(self, db, contrato, ailos_falsa):
        b = cobranca(db, contrato, status=BillingStatus.CANCELED)
        boleto = registrar_titulo(db, b, baixa_status='pendente')
        ailos_falsa(lambda m, u, **k: resp(200, boleto_json(b.id, pago=Decimal('100.00'), situacao=5)))
        ailos_boletos.conciliar_boletos_abertos(db, limit=5)
        db.refresh(b)
        db.refresh(boleto)
        assert b.status == BillingStatus.CANCELED
        assert boleto.pendencia == 'pago_em_cobranca_cancelada'
        assert boleto.baixa_status == 'confirmada'  # liquidado: não é mais pagável

    def test_situacao_baixado_confirma_baixa_de_cancelada(self, db, contrato, ailos_falsa):
        b = cobranca(db, contrato, status=BillingStatus.CANCELED)
        boleto = registrar_titulo(db, b, baixa_status='pendente')
        ailos_falsa(lambda m, u, **k: resp(200, boleto_json(b.id, situacao=3)))
        ailos_boletos.conciliar_boletos_abertos(db, limit=5)
        db.refresh(boleto)
        assert boleto.baixa_status == 'confirmada' and boleto.pendencia is None
        assert 'situação 3' in boleto.baixa_observacao

    def test_baixado_no_banco_com_cobranca_aberta_vira_pendencia(self, db, contrato, ailos_falsa):
        (b,) = _carteira(db, contrato, 1)
        ailos_falsa(lambda m, u, **k: resp(200, boleto_json(b.id, situacao=3)))
        r = ailos_boletos.verificar_pagamento(db, b)
        assert r['pendencia'] == 'baixado_com_cobranca_aberta'
        db.refresh(b)
        assert b.status == BillingStatus.PENDING

    def test_cancelada_durante_a_consulta_nao_e_quitada(self, db, contrato, ailos_falsa):
        (b,) = _carteira(db, contrato, 1)

        def responder(method, url, **_k):
            # Cancelamento confirmado por outra sessão enquanto a consulta roda.
            db.query(Billing).filter_by(id=b.id).update({Billing.status: BillingStatus.CANCELED})
            db.commit()
            return resp(200, boleto_json(b.id, pago=Decimal('99.90')))

        ailos_falsa(responder)
        r = ailos_boletos.verificar_pagamento(db, b)
        assert r['quitada'] is False and r['pendencia'] == 'pago_em_cobranca_cancelada'
        db.refresh(b)
        assert b.status == BillingStatus.CANCELED

    def test_removida_com_titulo_continua_monitorada(self, db, contrato, ailos_falsa):
        b = cobranca(db, contrato, is_deleted=True)
        registrar_titulo(db, b, baixa_status='pendente')
        ailos_falsa(lambda m, u, **k: resp(200, boleto_json(b.id, pago=Decimal('100.00'))))
        r = ailos_boletos.conciliar_boletos_abertos(db, limit=5)
        assert r['processados'] == 1 and r['pendencias_novas'] == 1
