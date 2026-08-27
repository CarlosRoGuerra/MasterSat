"""
Fila durável (outbox) das sincronizações com o Multiportal.

Antes o fluxo inteiro rodava dentro da requisição HTTP: se o provedor caísse
no meio, parte dos dados existia lá fora e parte não, e o reprocessamento
dependia de alguém notar o status vermelho e clicar em "reprocessar".

Cobertos: enfileiramento idempotente e transacional, backoff exponencial,
falha terminal após esgotar tentativas, devolução de itens órfãos,
reconciliação de pendentes fora da fila e o ciclo do worker.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from app.models.enums import TrackerStatus
from app.models.multiportal_outbox import MultiportalOutbox
from app.models.tracker import Tracker
from app.services import multiportal_outbox as outbox


@pytest.fixture()
def rastreador_vinculado(db, cliente, veiculo) -> Tracker:
    t = Tracker(
        imei='555554444433333',
        serial_number='555554444433333',
        brand='Teltonika',
        model='FMB920',
        status=TrackerStatus.INSTALLED,
        client_id=cliente.id,
        vehicle_id=veiculo.id,
        install_date=date(2025, 1, 10),
        external_manufacturer_id=10,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _resultado(operation: str, *, success: bool = True):
    from app.services.multiportal import CallResult
    return CallResult(
        operation=operation,
        transaction_id='1234567890123456789',
        status_code='200' if success else '99',
        status_description='OK' if success else 'Provedor recusou',
        success=success,
        response_payload={},
    )


def _fluxo_ok(**kwargs):
    return [
        _resultado('sincronizaCliente'),
        _resultado('sincronizaVeiculo'),
        _resultado('sincronizaEquipamento'),
        _resultado('vinculoVeiculoCliente'),
        _resultado('vinculoEquipamentoVeiculo'),
    ]


class TestEnfileiramento:
    def test_enfileira_uma_vez(self, db, rastreador_vinculado):
        outbox.enqueue_full_sync(db, rastreador_vinculado.id, reason='teste')
        db.commit()
        assert db.query(MultiportalOutbox).count() == 1

    def test_e_idempotente_por_rastreador(self, db, rastreador_vinculado):
        """Dez edições seguidas do mesmo cliente devem gerar UMA sincronização,
        não dez chamadas repetidas ao provedor."""
        for _ in range(10):
            outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        db.commit()
        assert db.query(MultiportalOutbox).count() == 1

    def test_nova_alteracao_antecipa_a_proxima_tentativa(self, db, rastreador_vinculado):
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        item.attempts = 3
        item.next_attempt_at = outbox._now() + timedelta(minutes=30)
        db.commit()

        outbox.enqueue_full_sync(db, rastreador_vinculado.id, reason='cliente alterado')
        db.commit()
        db.refresh(item)
        # O dado mudou de novo: esperar o backoff antigo sincronizaria um valor
        # que já ficou obsoleto.
        assert outbox._as_aware(item.next_attempt_at) <= outbox._now() + timedelta(seconds=5)

    def test_item_concluido_nao_bloqueia_novo_enfileiramento(self, db, rastreador_vinculado):
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        item.status = 'done'
        db.commit()

        outbox.enqueue_full_sync(db, rastreador_vinculado.id, reason='nova alteração')
        db.commit()
        assert db.query(MultiportalOutbox).count() == 2

    def test_edicao_durante_processamento_cria_novo_item(self, db, rastreador_vinculado):
        atual = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        atual.status = 'processing'
        db.commit()

        novo = outbox.enqueue_full_sync(db, rastreador_vinculado.id, reason='cliente alterado')
        db.commit()

        assert novo.id != atual.id
        assert atual.status == 'processing'
        assert novo.status == 'pending'


class TestBackoff:
    def test_cresce_exponencialmente_e_tem_teto(self):
        assert outbox.backoff_for(1) == timedelta(minutes=1)
        assert outbox.backoff_for(2) == timedelta(minutes=2)
        assert outbox.backoff_for(3) == timedelta(minutes=4)
        assert outbox.backoff_for(50) == outbox.MAX_BACKOFF

    def test_falha_reagenda_para_o_futuro(self, db, rastreador_vinculado):
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        db.commit()

        outbox.mark_failed_attempt(db, item, 'provedor fora do ar')
        db.refresh(item)
        assert item.status == 'pending'
        assert item.attempts == 1
        assert outbox._as_aware(item.next_attempt_at) > outbox._now()
        assert 'provedor fora do ar' in item.last_error

    def test_esgotar_tentativas_vira_falha_terminal(self, db, rastreador_vinculado):
        """Erro que sobrevive a todas as tentativas costuma ser dado inválido.
        Insistir para sempre esconderia o problema em vez de expô-lo."""
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        db.commit()

        for _ in range(outbox.MAX_ATTEMPTS):
            outbox.mark_failed_attempt(db, item, 'CPF inválido')
        db.refresh(item)
        assert item.status == 'failed'
        assert item.completed_at is not None

    def test_item_vencido_nao_e_pego_antes_da_hora(self, db, rastreador_vinculado):
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        db.commit()
        outbox.mark_failed_attempt(db, item, 'erro')

        assert outbox.claim_due_items(db) == []


class TestItensOrfaos:
    def test_processing_antigo_volta_para_a_fila(self, db, rastreador_vinculado):
        """Restart no meio de uma tentativa deixava o item presoem 'processing'
        para sempre — a sincronização nunca mais aconteceria."""
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        item.status = 'processing'
        item.updated_at = outbox._now() - timedelta(hours=1)
        db.commit()

        assert outbox.requeue_stale_processing(db) == 1
        db.refresh(item)
        assert item.status == 'pending'

    def test_processing_recente_e_respeitado(self, db, rastreador_vinculado):
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        item.status = 'processing'
        db.commit()

        assert outbox.requeue_stale_processing(db) == 0
        db.refresh(item)
        assert item.status == 'processing'


class TestReconciliacao:
    def test_enfileira_pendente_que_ficou_fora_da_fila(self, db, rastreador_vinculado):
        rastreador_vinculado.integration_status = 'pendente'
        db.commit()

        assert outbox.reconcile_pending_trackers(db) == 1
        assert db.query(MultiportalOutbox).count() == 1

    def test_nao_duplica_quem_ja_esta_na_fila(self, db, rastreador_vinculado):
        rastreador_vinculado.integration_status = 'pendente'
        outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        db.commit()

        assert outbox.reconcile_pending_trackers(db) == 0
        assert db.query(MultiportalOutbox).count() == 1

    def test_nao_ressuscita_falha_terminal(self, db, rastreador_vinculado):
        """'failed' pede intervenção humana; reenfileirar sozinho repetiria o
        mesmo erro de cadastro indefinidamente."""
        rastreador_vinculado.integration_status = 'erro'
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        item.status = 'failed'
        db.commit()

        assert outbox.reconcile_pending_trackers(db) == 0
        db.refresh(item)
        assert item.status == 'failed'

    def test_novo_dado_apos_falha_terminal_pode_ser_reconciliado(self, db, rastreador_vinculado):
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        item.status = 'failed'
        rastreador_vinculado.integration_status = 'pendente'
        db.commit()

        assert outbox.reconcile_pending_trackers(db) == 1
        assert db.query(MultiportalOutbox).filter_by(status='pending').count() == 1

    def test_ignora_rastreador_sincronizado(self, db, rastreador_vinculado):
        rastreador_vinculado.integration_status = 'sincronizado'
        db.commit()
        assert outbox.reconcile_pending_trackers(db) == 0


class TestProcessamento:
    def test_fluxo_completo_marca_done_e_sincronizado(self, db, rastreador_vinculado):
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        db.commit()

        with patch('app.services.multiportal.multiportal_service.full_sync_for_tracker', side_effect=_fluxo_ok):
            assert outbox.process_item(db, item) is True

        db.refresh(item)
        db.refresh(rastreador_vinculado)
        assert item.status == 'done'
        assert rastreador_vinculado.integration_status == 'sincronizado'

    def test_etapa_recusada_gera_retry_e_status_erro(self, db, rastreador_vinculado):
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        db.commit()

        def _fluxo_ruim(**kwargs):
            return [_resultado('sincronizaCliente'), _resultado('sincronizaVeiculo', success=False)]

        with patch('app.services.multiportal.multiportal_service.full_sync_for_tracker', side_effect=_fluxo_ruim):
            assert outbox.process_item(db, item) is False

        db.refresh(item)
        db.refresh(rastreador_vinculado)
        assert item.status == 'pending'
        assert item.attempts == 1
        assert 'sincronizaVeiculo' in item.last_error
        assert rastreador_vinculado.integration_status == 'erro'

    def test_provedor_fora_do_ar_nao_derruba_o_worker(self, db, rastreador_vinculado):
        """Indisponibilidade tem que virar nova tentativa, não exceção que mata
        o loop e para a fila inteira."""
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        db.commit()

        with patch(
            'app.services.multiportal.multiportal_service.full_sync_for_tracker',
            side_effect=ConnectionError('conexão recusada'),
        ):
            assert outbox.process_item(db, item) is False

        db.refresh(item)
        assert item.status == 'pending'
        assert 'conexão recusada' in item.last_error

    def test_rastreador_desvinculado_encerra_o_item(self, db, rastreador_vinculado):
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        rastreador_vinculado.vehicle_id = None
        db.commit()

        assert outbox.process_item(db, item) is True
        db.refresh(item)
        assert item.status == 'done'

    def test_run_once_drena_a_fila(self, db, rastreador_vinculado):
        outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        db.commit()

        with patch('app.services.multiportal.multiportal_service.full_sync_for_tracker', side_effect=_fluxo_ok):
            res = outbox.run_once(db)

        assert res['processados'] == 1
        assert res['sucesso'] == 1
        assert res['falhas'] == 0

    def test_resultado_antigo_nao_apaga_pendencia_mais_nova(self, db, rastreador_vinculado):
        atual = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        atual.status = 'processing'
        db.commit()
        novo = outbox.enqueue_full_sync(db, rastreador_vinculado.id, reason='cliente alterado')
        db.commit()

        with patch('app.services.multiportal.multiportal_service.full_sync_for_tracker', side_effect=_fluxo_ok):
            assert outbox.process_item(db, atual) is True

        db.refresh(atual)
        db.refresh(novo)
        db.refresh(rastreador_vinculado)
        assert atual.status == 'done'
        assert 'Substituído' in atual.last_error
        assert novo.status == 'pending'
        assert rastreador_vinculado.integration_status == 'pendente'


class TestQueueStats:
    def test_conta_por_status(self, db, rastreador_vinculado):
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        db.commit()
        stats = outbox.queue_stats(db)
        assert stats['pending'] == 1
        assert stats['failed'] == 0

        item.status = 'failed'
        db.commit()
        assert outbox.queue_stats(db)['failed'] == 1


class TestEndpointsDaFila:
    PREFIX = '/api/v1/integrations/multiportal'

    def test_queue_lista_e_conta(self, http, db, rastreador_vinculado):
        outbox.enqueue_full_sync(db, rastreador_vinculado.id, reason='teste')
        db.commit()

        r = http.get(f'{self.PREFIX}/queue')
        assert r.status_code == 200
        body = r.json()
        assert body['stats']['pending'] == 1
        assert body['items'][0]['tracker_id'] == rastreador_vinculado.id
        assert body['items'][0]['reason'] == 'teste'

    def test_queue_filtra_por_status(self, http, db, rastreador_vinculado):
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        item.status = 'failed'
        db.commit()

        assert http.get(f'{self.PREFIX}/queue', params={'status': 'pending'}).json()['items'] == []
        assert len(http.get(f'{self.PREFIX}/queue', params={'status': 'failed'}).json()['items']) == 1

    def test_retry_devolve_item_terminal_para_a_fila(self, http, db, rastreador_vinculado):
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        item.status = 'failed'
        item.attempts = outbox.MAX_ATTEMPTS
        db.commit()

        r = http.post(f'{self.PREFIX}/queue/{item.id}/retry')
        assert r.status_code == 200
        db.refresh(item)
        assert item.status == 'pending'
        assert item.attempts == 0

    def test_retry_de_item_inexistente_404(self, http):
        assert http.post(f'{self.PREFIX}/queue/999999/retry').status_code == 404

    def test_retry_nao_toma_item_em_processamento_do_worker(self, http, db, rastreador_vinculado):
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        item.status = 'processing'
        db.commit()
        assert http.post(f'{self.PREFIX}/queue/{item.id}/retry').status_code == 409

    def test_retry_nao_duplica_item_ativo(self, http, db, rastreador_vinculado):
        falho = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        falho.status = 'failed'
        db.commit()
        outbox.enqueue_full_sync(db, rastreador_vinculado.id, reason='nova edição')
        db.commit()

        assert http.post(f'{self.PREFIX}/queue/{falho.id}/retry').status_code == 409

    def test_queue_negada_para_cliente(self, http_cliente):
        assert http_cliente.get(f'{self.PREFIX}/queue').status_code == 403

    def test_retry_negado_para_financeiro(self, http_fin, db, rastreador_vinculado):
        item = outbox.enqueue_full_sync(db, rastreador_vinculado.id)
        db.commit()
        assert http_fin.post(f'{self.PREFIX}/queue/{item.id}/retry').status_code == 403


class TestIntegracaoTransacional:
    """O enfileiramento tem que acontecer na mesma transação da alteração do
    dado: se a alteração for desfeita, a intenção de sincronizar some junto."""

    def test_alterar_veiculo_enfileira(self, http, db, veiculo, rastreador_vinculado):
        r = http.put(f'/api/v1/vehicles/{veiculo.id}', json={'model': 'Onix'})
        assert r.status_code == 200

        item = db.query(MultiportalOutbox).filter_by(tracker_id=rastreador_vinculado.id).first()
        assert item is not None
        assert item.status == 'pending'
        assert item.reason == 'veículo alterado'

    def test_campo_so_local_nao_enfileira(self, http, db, veiculo, rastreador_vinculado):
        # 'notes' não vai para o provedor: não pode criar falsa pendência.
        r = http.put(f'/api/v1/vehicles/{veiculo.id}', json={'notes': 'observação interna'})
        assert r.status_code == 200
        assert db.query(MultiportalOutbox).count() == 0

    def test_contrato_e_pagamento_nao_disparam_multiportal(
        self, http, db, cliente, veiculo, plan, rastreador_vinculado,
    ):
        r = http.post('/api/v1/contracts/', json={
            'client_id': cliente.id,
            'plan_id': plan.id,
            'vehicle_id': veiculo.id,
            'tracker_id': rastreador_vinculado.id,
            'start_date': '2026-01-01',
            'status': 'ativo',
            'billing_day': 10,
        })
        assert r.status_code == 200
        contract_id = r.json()['id']
        assert db.query(MultiportalOutbox).count() == 0

        r = http.put(f'/api/v1/contracts/{contract_id}', json={
            'billing_day': 15,
            'payment_method': 'pix',
        })
        assert r.status_code == 200
        assert db.query(MultiportalOutbox).count() == 0

        r = http.delete(f'/api/v1/contracts/{contract_id}')
        assert r.status_code == 200
        assert db.query(MultiportalOutbox).count() == 0
