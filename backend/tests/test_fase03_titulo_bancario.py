"""Fase 03 — FIN-02: política bancária única em todos os escritores.

Nenhuma operação pode deixar um título ativo no banco sem obrigação local,
nem liberar o mês para nova cobrança enquanto o boleto antigo é pagável.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.billing_change_log import BillingChangeLog
from app.models.enums import BillingStatus
from app.services import titulo_bancario
from tests.fase03_apoio import cobranca, registrar_titulo, reserva

B = '/api/v1/billings'


def _detail(resp):
    return resp.json()['detail']


class TestEstadoDoTitulo:
    def test_estados_derivados(self, db, contrato):
        sem = cobranca(db, contrato)
        reg = cobranca(db, contrato)
        registrar_titulo(db, reg)
        fresca = cobranca(db, contrato)
        reserva(db, fresca, 'REGISTRANDO')
        orfa = cobranca(db, contrato)
        reserva(db, orfa, 'PROCESSANDO', minutos_atras=31)
        incerta = cobranca(db, contrato)
        reserva(db, incerta, 'DESFECHO_DESCONHECIDO')
        recusada = cobranca(db, contrato)
        reserva(db, recusada, 'ERRO_REGISTRO')

        estados = {bid: t.estado for bid, t in titulo_bancario.titulos_bancarios(
            db, [sem.id, reg.id, fresca.id, orfa.id, incerta.id, recusada.id]).items()}

        assert estados == {
            sem.id: 'sem_titulo', reg.id: 'registrado', fresca.id: 'em_registro',
            orfa.id: 'desfecho_desconhecido', incerta.id: 'desfecho_desconhecido',
            recusada.id: 'sem_titulo',
        }

    def test_serializacao_expoe_titulo_bancario(self, http, db, contrato):
        b = cobranca(db, contrato)
        registrar_titulo(db, b)
        corpo = http.get(f'{B}/{b.id}').json()
        assert corpo['titulo_bancario']['estado'] == 'registrado'
        assert corpo['titulo_bancario']['nosso_numero'] == f'NN{b.id:08d}'
        assert http.get(f'{B}/{cobranca(db, contrato).id}').json()['titulo_bancario'] is None


class TestExclusao:
    def test_registrada_nao_e_removida_e_mes_continua_ocupado(self, http, db, contrato):
        b = cobranca(db, contrato, billing_type='recorrente', period_label='10/2099',
                     due_date=date(2099, 10, 15))
        registrar_titulo(db, b)

        r = http.delete(f'{B}/{b.id}')
        assert r.status_code == 409
        assert _detail(r)['code'] == 'boleto_ailos_registrado'
        db.refresh(b)
        assert b.is_deleted is False

        nova = http.post(f'{B}/', json={
            'client_id': contrato.client_id, 'contract_id': contrato.id, 'billing_type': 'recorrente',
            'amount': 100, 'due_date': '2099-10-15', 'period_label': '10/2099'})
        assert nova.status_code == 409  # competência continua ocupada

    def test_desfecho_desconhecido_e_em_registro_nao_sao_removidos(self, http, db, contrato):
        incerta = cobranca(db, contrato)
        reserva(db, incerta, 'DESFECHO_DESCONHECIDO')
        fresca = cobranca(db, contrato)
        reserva(db, fresca, 'REGISTRANDO')
        assert _detail(http.delete(f'{B}/{incerta.id}'))['code'] == 'boleto_ailos_desfecho_desconhecido'
        assert _detail(http.delete(f'{B}/{fresca.id}'))['code'] == 'boleto_ailos_em_registro'

    def test_sem_titulo_ou_recusada_pelo_banco_continua_removivel(self, http, db, contrato):
        livre = cobranca(db, contrato)
        recusada = cobranca(db, contrato)
        reserva(db, recusada, 'ERRO_REGISTRO')
        assert http.delete(f'{B}/{livre.id}').status_code == 200
        assert http.delete(f'{B}/{recusada.id}').status_code == 200


class TestEdicao:
    def test_desfecho_desconhecido_bloqueia_valor_e_vencimento(self, http, db, contrato):
        b = cobranca(db, contrato)
        reserva(db, b, 'DESFECHO_DESCONHECIDO')
        r = http.put(f'{B}/{b.id}', json={'amount': 130, 'justification': 'x'})
        assert r.status_code == 409
        assert _detail(r)['code'] == 'boleto_ailos_desfecho_desconhecido'
        db.refresh(b)
        assert b.amount == Decimal('100.00')

    def test_reserva_orfa_vira_desfecho_desconhecido(self, http, db, contrato):
        b = cobranca(db, contrato)
        reserva(db, b, 'REGISTRANDO', minutos_atras=45)
        r = http.put(f'{B}/{b.id}', json={'due_date': '2099-12-20', 'justification': 'x'})
        assert _detail(r)['code'] == 'boleto_ailos_desfecho_desconhecido'

    def test_manutencao_em_lote_e_unificacao_respeitam_a_politica(self, http, db, contrato):
        a = cobranca(db, contrato)
        b = cobranca(db, contrato)
        reserva(db, b, 'DESFECHO_DESCONHECIDO')
        lote = http.post(f'{B}/lote/manutencao', json={
            'billing_ids': [a.id, b.id], 'amount': 50, 'justification': 'x'})
        assert lote.status_code == 409
        assert _detail(lote)['billing_ids'] == [b.id]
        db.refresh(a)
        assert a.amount == Decimal('100.00')  # nada foi aplicado
        unif = http.post(f'{B}/unificar', json={'billing_ids': [a.id, b.id], 'due_date': '2099-12-01'})
        assert unif.status_code == 409


class TestCancelamento:
    def test_registrada_exige_confirmacao_e_fica_com_baixa_pendente(self, http, db, contrato):
        b = cobranca(db, contrato)
        boleto = registrar_titulo(db, b)
        sem = http.post(f'{B}/{b.id}/cancel', json={'reason': 'x'})
        assert sem.status_code == 409 and _detail(sem)['code'] == 'boleto_ailos_registrado'

        com = http.post(f'{B}/{b.id}/cancel', json={'reason': 'x', 'confirmar_boleto_ailos': True})
        assert com.status_code == 200
        db.refresh(boleto)
        assert boleto.baixa_status == 'pendente'
        assert com.json()['titulo_bancario']['baixa_status'] == 'pendente'

    def test_cancelar_liberando_mes_com_boleto_ativo_e_recusado_sem_efeito(self, http, db, contrato):
        b = cobranca(db, contrato, billing_type='recorrente', period_label='09/2099',
                     due_date=date(2099, 9, 15))
        registrar_titulo(db, b)
        r = http.post(f'{B}/{b.id}/cancel', json={
            'reason': 'x', 'confirmar_boleto_ailos': True, 'liberar_competencia': True})
        assert r.status_code == 409 and _detail(r)['code'] == 'baixa_bancaria_pendente'
        db.refresh(b)
        assert b.status == BillingStatus.PENDING and b.competencia_liberada is False

    def test_liberar_mes_so_depois_da_baixa_confirmada(self, http, db, contrato):
        b = cobranca(db, contrato, billing_type='recorrente', period_label='08/2099',
                     due_date=date(2099, 8, 15))
        boleto = registrar_titulo(db, b)
        assert http.post(f'{B}/{b.id}/cancel', json={'reason': 'x', 'confirmar_boleto_ailos': True}).status_code == 200

        antes = http.post(f'{B}/{b.id}/liberar-competencia', json={'justificativa': 'recobrar'})
        assert antes.status_code == 409 and _detail(antes)['code'] == 'baixa_bancaria_pendente'

        conf = http.post(f'/api/v1/ailos/boletos/{b.id}/confirmar-baixa',
                         json={'justificativa': 'baixado no internet banking em 30/09'})
        assert conf.status_code == 200
        db.refresh(boleto)
        assert boleto.baixa_status == 'confirmada' and boleto.baixa_confirmada_por_user_id == 1

        depois = http.post(f'{B}/{b.id}/liberar-competencia', json={'justificativa': 'recobrar'})
        assert depois.status_code == 200
        assert db.query(BillingChangeLog).filter_by(billing_id=b.id, field_name='baixa_bancaria').count() == 1

    def test_confirmar_baixa_nao_e_do_financeiro(self, http_fin, db, contrato):
        b = cobranca(db, contrato, status=BillingStatus.CANCELED)
        registrar_titulo(db, b, baixa_status='pendente')
        assert http_fin.post(f'/api/v1/ailos/boletos/{b.id}/confirmar-baixa',
                             json={'justificativa': 'baixei'}).status_code == 403

    def test_confirmar_baixa_de_cobranca_aberta_e_recusada(self, http, db, contrato):
        b = cobranca(db, contrato)
        registrar_titulo(db, b)
        r = http.post(f'/api/v1/ailos/boletos/{b.id}/confirmar-baixa', json={'justificativa': 'baixei'})
        assert r.status_code == 409 and _detail(r)['code'] == 'cobranca_em_aberto'

    def test_desfecho_desconhecido_bloqueia_cancelamento(self, http, db, contrato):
        b = cobranca(db, contrato)
        reserva(db, b, 'DESFECHO_DESCONHECIDO')
        r = http.post(f'{B}/{b.id}/cancel', json={'reason': 'x', 'confirmar_boleto_ailos': True})
        assert _detail(r)['code'] == 'boleto_ailos_desfecho_desconhecido'

    def test_lote_cancelar_marca_baixa_pendente_e_recusa_incerta(self, http, db, contrato):
        a = cobranca(db, contrato)
        boleto = registrar_titulo(db, a)
        ok = http.post(f'{B}/lote/situacao', json={'billing_ids': [a.id], 'action': 'cancelar', 'reason': 'x'})
        assert ok.status_code == 200
        assert ok.json()['boletos_ativos'] == [{'billing_id': a.id, 'nosso_numero': boleto.nosso_numero}]
        db.refresh(boleto)
        assert boleto.baixa_status == 'pendente'

        b = cobranca(db, contrato)
        reserva(db, b, 'DESFECHO_DESCONHECIDO')
        r = http.post(f'{B}/lote/situacao', json={'billing_ids': [b.id], 'action': 'cancelar', 'reason': 'x'})
        assert r.status_code == 409


class TestRecebimentoManualComTitulo:
    def test_recebida_por_fora_fica_com_baixa_pendente_e_link_publico_some(self, http, db, contrato):
        from app.api.v1.endpoints.boletos import _public_token
        b = cobranca(db, contrato)
        boleto = registrar_titulo(db, b)
        r = http.post(f'{B}/{b.id}/receive', json={'payment_date': '2099-11-01', 'payment_method': 'pix'})
        assert r.status_code == 200
        db.refresh(boleto)
        assert boleto.baixa_status == 'pendente'
        publico = http.get(f'/api/v1/public/boleto/{b.id}/{_public_token(b.id)}')
        assert publico.status_code == 404

    def test_desfecho_desconhecido_bloqueia_recebimento(self, http, db, contrato):
        b = cobranca(db, contrato)
        reserva(db, b, 'DESFECHO_DESCONHECIDO')
        r = http.post(f'{B}/{b.id}/receive', json={'payment_date': '2099-11-01', 'payment_method': 'pix'})
        assert r.status_code == 409


class TestTituloBaixadoNaoVoltaAoCliente:
    def test_gerar_boleto_nao_devolve_titulo_baixado(self, http, db, contrato):
        b = cobranca(db, contrato)
        registrar_titulo(db, b, baixa_status='confirmada')
        r = http.post('/api/v1/ailos/boletos', json={'billing_id': b.id})
        assert r.status_code == 409 and _detail(r)['code'] == 'boleto_ailos_baixado'

    def test_pdf_e_email_recusados_com_baixa_pendente(self, http, db, contrato):
        b = cobranca(db, contrato, status=BillingStatus.PAID)
        registrar_titulo(db, b, baixa_status='pendente')
        assert _detail(http.get(f'/api/v1/boletos/{b.id}/pdf'))['code'] == 'boleto_ailos_baixado'
        assert _detail(http.post(f'/api/v1/boletos/{b.id}/enviar-email'))['code'] == 'boleto_ailos_baixado'

    def test_carne_nao_inclui_parcela_cancelada(self, http, db, contrato):
        from app.models.ailos_lote import AilosLote

        # Vencimento realista: o cálculo local do código de barras (usado
        # na montagem do PDF) não aceita fator de vencimento de 2099.
        ativa = cobranca(db, contrato, due_date=date(2026, 12, 10))
        cancelada = cobranca(db, contrato, status=BillingStatus.CANCELED, due_date=date(2027, 1, 10))
        registrar_titulo(db, ativa)
        registrar_titulo(db, cancelada, baixa_status='pendente')
        lote = AilosLote(tipo='carne', ticket='T-C', numero_convenio='102004',
                         billing_ids=[ativa.id, cancelada.id], status='completed')
        db.add(lote)
        db.commit()
        from unittest.mock import patch
        with patch('app.api.v1.endpoints.boletos.gerar_carne_pdf', return_value=b'%PDF') as gerar:
            r = http.get(f'/api/v1/boletos/carne/{lote.id}/pdf')
        assert r.status_code == 200
        assert [p.billing_id for p in gerar.call_args.args[0]] == [ativa.id]


class TestExclusaoDeContrato:
    def test_contrato_com_desfecho_desconhecido_nao_e_removido(self, http, db, contrato):
        b = cobranca(db, contrato)
        reserva(db, b, 'DESFECHO_DESCONHECIDO')
        r = http.delete(f'/api/v1/contracts/{contrato.id}')
        assert r.status_code == 409
        db.refresh(b)
        assert b.status == BillingStatus.PENDING

    def test_contrato_removido_deixa_titulo_com_baixa_pendente(self, http, db, contrato):
        b = cobranca(db, contrato)
        boleto = registrar_titulo(db, b)
        r = http.delete(f'/api/v1/contracts/{contrato.id}')
        assert r.status_code == 200
        assert r.json()['boletos_ativos'] == [{'billing_id': b.id, 'nosso_numero': boleto.nosso_numero}]
        db.refresh(boleto)
        assert boleto.baixa_status == 'pendente'


class TestRegravacaoForcada:
    def test_force_nao_regrava_cobranca_com_titulo(self, db, contrato, plan):
        from app.services.financial import generate_monthly_billings

        criadas = generate_monthly_billings(db, contrato, cycles=1, start_cycle=0)
        db.commit()
        alvo = criadas[0]
        registrar_titulo(db, alvo)
        valor = alvo.amount
        plan.price = Decimal('555.00')
        db.commit()
        generate_monthly_billings(db, contrato, cycles=1, start_cycle=0, force=True)
        db.commit()
        db.refresh(alvo)
        assert alvo.amount == valor


class TestDesinstalacaoNaoMudaValorDeTitulo:
    def _mensalidade_do_ciclo(self, db, contrato, plan) -> Billing:
        from app.services.financial import add_months, period_label_for_date

        inicio = add_months(date.today().replace(day=1), 1)
        mensalidade = Billing(
            contract_id=contrato.id, client_id=contrato.client_id, amount=Decimal('99.90'),
            due_date=date.today(), status=BillingStatus.PENDING, billing_type='recorrente',
            period_label=period_label_for_date(inicio, 1),
        )
        db.add(mensalidade)
        db.commit()
        db.refresh(mensalidade)
        return mensalidade

    def test_pro_rata_nao_muda_valor_de_mensalidade_com_boleto(self, http, db, contrato, plan, veiculo):
        """A desinstalação é um escritor de valor: com título no banco ela não
        aplica o pró-rata (o boleto ficaria com o valor antigo) e devolve o
        ajuste pendente para o financeiro."""
        mensalidade = self._mensalidade_do_ciclo(db, contrato, plan)
        registrar_titulo(db, mensalidade)

        r = http.post(f'/api/v1/vehicles/{veiculo.id}/uninstall',
                      params={'uninstall_date': date.today().isoformat()})
        assert r.status_code == 200, r.text
        pendentes = r.json()['ajustes_pro_rata_pendentes']
        assert [p['billing_id'] for p in pendentes] == [mensalidade.id]
        assert pendentes[0]['estado_bancario'] == 'registrado'
        db.refresh(mensalidade)
        assert mensalidade.amount == Decimal('99.90')
        assert '[AJUSTE PENDENTE]' in (mensalidade.notes or '')

    def test_pro_rata_aplica_quando_nao_ha_titulo(self, http, db, contrato, plan, veiculo):
        mensalidade = self._mensalidade_do_ciclo(db, contrato, plan)
        r = http.post(f'/api/v1/vehicles/{veiculo.id}/uninstall',
                      params={'uninstall_date': date.today().isoformat()})
        assert r.status_code == 200, r.text
        assert r.json()['ajustes_pro_rata_pendentes'] == []
        db.refresh(mensalidade)
        assert mensalidade.amount == Decimal(str(r.json()['source_prorated_amount']))
