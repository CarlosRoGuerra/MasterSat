"""
Testes de integração para /api/v1/vehicles.

Cobertos:
- GET /               → listar, filtros, busca, paginação
- POST /              → criar com/sem cliente, campos obrigatórios, XSS safe
- GET /{id}           → sucesso, 404, deletado
- PUT /{id}           → atualizar placa/modelo, 404
- DELETE /{id}        → soft-delete, 404
- POST /{id}/uninstall→ cria UninstallEvent (não billing direto), tracker vai para estoque,
                        veículo fica com status REMOVED, contrato cancelado,
                        sem taxa não cria evento, data obrigatória
- Autorização         → CLIENT → 403, sem auth → 401
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.enums import TrackerStatus, VehicleStatus
from app.models.uninstall_event import UninstallEvent

PREFIX = "/api/v1/vehicles"


def _multiportal_result(operation: str, *, success: bool = True):
    from app.services.multiportal import CallResult
    return CallResult(
        operation=operation,
        transaction_id='1234567890123456789',
        status_code='200' if success else '99',
        status_description='OK' if success else 'Falha simulada',
        success=success,
        response_payload={},
    )


def _enable_multiportal(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'multiportal_enabled', True)
    monkeypatch.setattr(settings, 'multiportal_id', 'test-id')
    monkeypatch.setattr(settings, 'multiportal_password', 'test-password')
    monkeypatch.setattr(settings, 'multiportal_wsdl_url', 'https://multiportal.invalid/wsdl')


def _payload(client_id: int, plate: str = "DEF2G34") -> dict:
    return {
        "client_id": client_id,
        "plate": plate,
        "type": "passeio",
        "brand": "Honda",
        "model": "Civic",
        "year": 2023,
        "chassis": "9HGFB2F55DA014877",
    }


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestListVehicles:
    def test_empty_list(self, http):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert r.json() == {"items": [], "total": 0}

    def test_returns_existing_vehicle(self, http, veiculo):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert any(x["id"] == veiculo.id for x in body["items"])

    def test_filter_by_client_id(self, http, veiculo, cliente):
        r = http.get(PREFIX + "/", params={"client_id": cliente.id})
        assert r.status_code == 200
        assert all(x["client_id"] == cliente.id for x in r.json()["items"])

    def test_search_by_plate(self, http, veiculo):
        r = http.get(PREFIX + "/", params={"search": veiculo.plate[:4]})
        assert r.status_code == 200
        assert len(r.json()["items"]) >= 1

    def test_excludes_soft_deleted(self, http, db, veiculo):
        veiculo.is_deleted = True
        db.commit()
        r = http.get(PREFIX + "/")
        body = r.json()
        assert body["total"] == 0
        assert all(x["id"] != veiculo.id for x in body["items"])

    def test_client_role_cannot_list(self, http_cliente):
        r = http_cliente.get(PREFIX + "/")
        assert r.status_code == 403

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.get(PREFIX + "/")
        assert r.status_code == 401

    def test_sql_injection_safe(self, http, veiculo):
        r = http.get(PREFIX + "/", params={"search": "'; DROP TABLE vehicles; --"})
        assert r.status_code == 200

    def test_total_reflects_full_count_beyond_limit(self, http, db, veiculo, cliente):
        # total precisa contar TODOS os veículos que casam o filtro, não só
        # os que couberam na página — é o motivo de existir o campo.
        from app.models.vehicle import Vehicle

        for i in range(3):
            db.add(Vehicle(
                client_id=cliente.id,
                plate=f"EXT{i}234",
                type="carro",
                status="ativo",
            ))
        db.commit()

        r = http.get(PREFIX + "/", params={"limit": 2})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 4  # veiculo (fixture) + 3 extras
        assert len(body["items"]) == 2


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------

class TestCreateVehicle:
    def test_create_success(self, http, cliente):
        r = http.post(PREFIX + "/", json=_payload(cliente.id))
        assert r.status_code == 200
        data = r.json()
        assert data["client_id"] == cliente.id
        assert data["plate"] == "DEF2G34"

    def test_missing_client_id_returns_422(self, http):
        r = http.post(PREFIX + "/", json={"plate": "TST1A11", "type": "passeio"})
        assert r.status_code == 422

    def test_missing_plate_returns_422(self, http, cliente):
        r = http.post(PREFIX + "/", json={"client_id": cliente.id, "type": "passeio"})
        assert r.status_code == 422

    def test_nonexistent_client_returns_404(self, http):
        r = http.post(PREFIX + "/", json=_payload(99999))
        assert r.status_code == 404

    def test_operational_can_create(self, http_op, cliente):
        r = http_op.post(PREFIX + "/", json=_payload(cliente.id, "GHI3H45"))
        assert r.status_code == 200

    def test_client_role_cannot_create(self, http_cliente, cliente):
        r = http_cliente.post(PREFIX + "/", json=_payload(cliente.id))
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /{id}
# ---------------------------------------------------------------------------

class TestGetVehicle:
    def test_get_existing(self, http, veiculo):
        r = http.get(f"{PREFIX}/{veiculo.id}")
        assert r.status_code == 200
        assert r.json()["id"] == veiculo.id

    def test_get_nonexistent_returns_404(self, http):
        r = http.get(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_get_deleted_returns_404(self, http, db, veiculo):
        veiculo.is_deleted = True
        db.commit()
        r = http.get(f"{PREFIX}/{veiculo.id}")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# PUT /{id}
# ---------------------------------------------------------------------------

class TestUpdateVehicle:
    def test_update_model(self, http, veiculo):
        r = http.put(f"{PREFIX}/{veiculo.id}", json={"model": "Fit"})
        assert r.status_code == 200
        assert r.json()["model"] == "Fit"

    def test_update_color(self, http, veiculo):
        r = http.put(f"{PREFIX}/{veiculo.id}", json={"color": "Preto"})
        assert r.status_code == 200

    def test_update_nonexistent_returns_404(self, http):
        r = http.put(f"{PREFIX}/99999", json={"model": "X"})
        assert r.status_code == 404

    def test_multiportal_change_marks_all_vehicle_trackers_pending(
        self, http, db, cliente, veiculo, rastreador, rastreador_instalado,
    ):
        rastreador.vehicle_id = veiculo.id
        rastreador.client_id = cliente.id
        rastreador.status = TrackerStatus.INSTALLED
        rastreador.integration_status = 'sincronizado'
        rastreador_instalado.integration_status = 'sincronizado'
        db.commit()

        r = http.put(f"{PREFIX}/{veiculo.id}", json={"model": "Modelo atualizado"})

        assert r.status_code == 200
        db.refresh(rastreador)
        db.refresh(rastreador_instalado)
        assert rastreador.integration_status == 'pendente'
        assert rastreador_instalado.integration_status == 'pendente'

    def test_local_only_change_keeps_tracker_synced(
        self, http, db, veiculo, rastreador_instalado,
    ):
        rastreador_instalado.integration_status = 'sincronizado'
        db.commit()

        r = http.put(f"{PREFIX}/{veiculo.id}", json={"sales_point": "Loja Centro"})

        assert r.status_code == 200
        db.refresh(rastreador_instalado)
        assert rastreador_instalado.integration_status == 'sincronizado'


# ---------------------------------------------------------------------------
# DELETE /{id}
# ---------------------------------------------------------------------------

class TestDeleteVehicle:
    def test_soft_delete(self, http, db, veiculo):
        r = http.delete(f"{PREFIX}/{veiculo.id}")
        assert r.status_code == 200
        db.refresh(veiculo)
        assert veiculo.is_deleted is True

    def test_delete_nonexistent_returns_404(self, http):
        r = http.delete(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_financial_cannot_delete(self, http_fin, veiculo):
        r = http_fin.delete(f"{PREFIX}/{veiculo.id}")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /{id}/uninstall
# ---------------------------------------------------------------------------

class TestUninstallVehicle:
    @pytest.fixture(autouse=True)
    def _multiportal_disabled_by_default(self, monkeypatch):
        # Sem isso, o MULTIPORTAL_ENABLED real do .env do ambiente (o
        # setdefault do conftest não sobrescreve uma env var que o container
        # já definiu) vaza pros testes: rastreador_instalado tem
        # external_manufacturer_id/serial_number preenchidos, então
        # _requires_external_cleanup vira True e a desinstalação é bloqueada
        # com 503 mesmo nos testes que não têm nada a ver com Multiportal.
        # Quem quiser testar o caminho com Multiportal habilitado chama
        # _enable_multiportal(monkeypatch) explicitamente, que sobrescreve.
        from app.core.config import settings
        monkeypatch.setattr(settings, 'multiportal_enabled', False)

    def _uninstall(self, client, vehicle_id: int, **kwargs):
        params = {"uninstall_date": "2025-05-15", **kwargs}
        return client.post(f"{PREFIX}/{vehicle_id}/uninstall", params=params)

    def test_uninstall_creates_uninstall_event_not_billing(self, http, db, veiculo, rastreador_instalado, contrato, produto_desinstalacao):
        """Quando há taxa, deve criar UninstallEvent pendente, NÃO billing direto."""
        from app.models.billing import Billing
        billings_before = db.query(Billing).count()

        r = self._uninstall(
            http, veiculo.id,
            uninstall_service_product_id=produto_desinstalacao.id,
        )
        assert r.status_code == 200

        # Nenhum billing novo de taxa criado diretamente
        billings_after = db.query(Billing).count()
        assert billings_after == billings_before

        # UninstallEvent criado com status pending
        event = db.query(UninstallEvent).filter(UninstallEvent.vehicle_id == veiculo.id).first()
        assert event is not None
        assert event.status == "pending"
        assert event.service_product_id == produto_desinstalacao.id

    def test_uninstall_tracker_goes_to_stock(self, http, db, veiculo, rastreador_instalado, contrato):
        r = self._uninstall(http, veiculo.id)
        assert r.status_code == 200
        db.refresh(rastreador_instalado)
        assert rastreador_instalado.status == TrackerStatus.STOCK
        assert rastreador_instalado.vehicle_id is None

    def test_uninstall_vehicle_becomes_removed(self, http, db, veiculo, rastreador_instalado, contrato):
        r = self._uninstall(http, veiculo.id)
        assert r.status_code == 200
        db.refresh(veiculo)
        assert veiculo.status == VehicleStatus.REMOVED
        assert veiculo.uninstalled_at == date(2025, 5, 15)

    def test_uninstall_contract_gets_cancelled(self, http, db, veiculo, rastreador_instalado, contrato):
        r = self._uninstall(http, veiculo.id)
        assert r.status_code == 200
        db.refresh(contrato)
        assert contrato.status == "cancelado"
        assert contrato.end_date == date(2025, 5, 15)

    def test_uninstall_without_fee_no_event(self, http, db, veiculo, rastreador_instalado, contrato):
        """Sem taxa nem produto, não deve criar UninstallEvent."""
        r = self._uninstall(http, veiculo.id)
        assert r.status_code == 200
        event = db.query(UninstallEvent).filter(UninstallEvent.vehicle_id == veiculo.id).first()
        assert event is None

    def test_uninstall_with_direct_fee_creates_event(self, http, db, veiculo, rastreador_instalado, contrato):
        r = self._uninstall(http, veiculo.id, uninstall_fee=100.0)
        assert r.status_code == 200
        event = db.query(UninstallEvent).filter(UninstallEvent.vehicle_id == veiculo.id).first()
        assert event is not None
        assert float(event.fee_amount) == 100.0

    def test_uninstall_fee_snapshots_contract_intervenient(
        self, http, db, veiculo, rastreador_instalado, contrato, outro_cliente,
    ):
        contrato.interveniente_client_id = outro_cliente.id
        db.commit()

        r = self._uninstall(http, veiculo.id, uninstall_fee=100.0)

        assert r.status_code == 200
        event = db.query(UninstallEvent).filter_by(vehicle_id=veiculo.id).one()
        assert event.client_id == veiculo.client_id
        assert event.payer_client_id == outro_cliente.id

    def test_uninstall_nao_cancela_contrato_de_outro_veiculo(self, http, db, cliente, veiculo, rastreador_instalado, contrato, plan):
        """Frota: desinstalar o veículo A não pode cancelar o contrato do veículo B."""
        from app.models.contract import Contract
        from app.models.vehicle import Vehicle
        outro_veiculo = Vehicle(client_id=cliente.id, plate='XYZ9Z99', type='passeio',
                                chassis='9BWZZZ377VT099999')
        db.add(outro_veiculo)
        db.commit()
        db.refresh(outro_veiculo)
        contrato_b = Contract(client_id=cliente.id, plan_id=plan.id, vehicle_id=outro_veiculo.id,
                              start_date=date(2024, 3, 1), status='ativo', billing_day=10)
        db.add(contrato_b)
        db.commit()
        db.refresh(contrato_b)

        r = self._uninstall(http, veiculo.id)
        assert r.status_code == 200
        db.refresh(contrato)
        db.refresh(contrato_b)
        assert contrato.status == 'cancelado'      # o do veículo desinstalado
        assert contrato_b.status == 'ativo'        # o do outro veículo, intacto

    def test_uninstall_repetido_bloqueado(self, http, db, veiculo, rastreador_instalado, contrato):
        """Re-desinstalar um veículo já retirado é recusado (evita taxa dupla)."""
        assert self._uninstall(http, veiculo.id, uninstall_fee=100.0).status_code == 200
        r2 = self._uninstall(http, veiculo.id, uninstall_fee=100.0)
        assert r2.status_code == 400
        # Só um UninstallEvent — a taxa não foi duplicada.
        assert db.query(UninstallEvent).filter(UninstallEvent.vehicle_id == veiculo.id).count() == 1

    def test_uninstall_nonexistent_vehicle_returns_404(self, http):
        r = self._uninstall(http, 99999)
        assert r.status_code == 404

    def test_uninstall_missing_date_returns_422(self, http, veiculo, rastreador_instalado):
        r = http.post(f"{PREFIX}/{veiculo.id}/uninstall")
        assert r.status_code == 422

    def test_financial_cannot_uninstall(self, http_fin, veiculo):
        r = self._uninstall(http_fin, veiculo.id)
        assert r.status_code == 403

    def test_synced_uninstall_unlinks_multiportal_before_local_change(
        self, http, db, veiculo, rastreador_instalado, contrato, monkeypatch,
    ):
        from app.services.multiportal import multiportal_service

        _enable_multiportal(monkeypatch)
        rastreador_instalado.integration_status = 'sincronizado'
        db.commit()
        calls = []

        def unlink_equipment(tracker, vehicle, when=None):
            calls.append(('equipment', tracker.id, vehicle.id, tracker.vehicle_id))
            return _multiportal_result('vinculoEquipamentoVeiculo')

        def unlink_client(vehicle, client, when=None):
            calls.append(('client', vehicle.id, client.id))
            return _multiportal_result('vinculoVeiculoCliente')

        monkeypatch.setattr(multiportal_service, 'unlink_equipment_vehicle', unlink_equipment)
        monkeypatch.setattr(multiportal_service, 'unlink_vehicle_client', unlink_client)

        r = self._uninstall(http, veiculo.id)
        assert r.status_code == 200
        assert r.json()['multiportal_unlinked'] is True
        assert calls[0][0] == 'equipment'
        assert calls[0][3] == veiculo.id  # o vínculo local ainda existia durante a chamada externa
        assert calls[1][0] == 'client'
        db.refresh(rastreador_instalado)
        assert rastreador_instalado.vehicle_id is None
        assert rastreador_instalado.integration_status == 'desvinculado'

    def test_multiportal_failure_keeps_local_assignment_and_contract(
        self, http, db, veiculo, rastreador_instalado, contrato, monkeypatch,
    ):
        from app.services.multiportal import multiportal_service

        _enable_multiportal(monkeypatch)
        rastreador_instalado.integration_status = 'sincronizado'
        db.commit()
        calls = []

        def unlink_equipment(tracker, vehicle, when=None):
            calls.append(('unlink_equipment', tracker.id, vehicle.id))
            return _multiportal_result('vinculoEquipamentoVeiculo', success=False)

        def relink_equipment(tracker, vehicle, when=None):
            calls.append(('relink_equipment', tracker.id, vehicle.id))
            return _multiportal_result('vinculoEquipamentoVeiculo')

        def relink_client(vehicle, client, when=None):
            calls.append(('relink_client', vehicle.id, client.id))
            return _multiportal_result('vinculoVeiculoCliente')

        monkeypatch.setattr(multiportal_service, 'unlink_equipment_vehicle', unlink_equipment)
        monkeypatch.setattr(multiportal_service, 'link_equipment_vehicle', relink_equipment)
        monkeypatch.setattr(multiportal_service, 'link_vehicle_client', relink_client)

        r = self._uninstall(http, veiculo.id)
        assert r.status_code == 502
        assert r.json()['detail']['code'] == 'multiportal_unlink_failed'
        assert r.json()['detail']['reconciliation_required'] is False
        assert [call[0] for call in calls] == [
            'unlink_equipment', 'relink_client', 'relink_equipment',
        ]
        db.refresh(rastreador_instalado)
        db.refresh(contrato)
        db.refresh(veiculo)
        assert rastreador_instalado.vehicle_id == veiculo.id
        assert contrato.status == 'ativo'
        assert veiculo.status != VehicleStatus.REMOVED

    def test_synced_uninstall_is_blocked_when_integration_is_disabled(
        self, http, db, veiculo, rastreador_instalado, contrato,
    ):
        rastreador_instalado.integration_status = 'sincronizado'
        db.commit()

        r = self._uninstall(http, veiculo.id)
        assert r.status_code == 503
        db.refresh(rastreador_instalado)
        db.refresh(contrato)
        assert rastreador_instalado.vehicle_id == veiculo.id
        assert contrato.status == 'ativo'

    def test_synced_uninstall_blocks_insecure_multiportal_transport(
        self, http, db, veiculo, rastreador_instalado, contrato, monkeypatch,
    ):
        from app.core.config import settings

        _enable_multiportal(monkeypatch)
        monkeypatch.setattr(settings, 'multiportal_wsdl_url', 'http://multiportal.invalid/wsdl')
        monkeypatch.setattr(settings, 'multiportal_allow_insecure_http', False)
        rastreador_instalado.integration_status = 'sincronizado'
        db.commit()

        r = self._uninstall(http, veiculo.id)
        assert r.status_code == 503
        assert 'sem TLS' in r.json()['detail']['message']
        db.refresh(rastreador_instalado)
        db.refresh(contrato)
        assert rastreador_instalado.vehicle_id == veiculo.id
        assert contrato.status == 'ativo'

    def test_operacional_nao_pode_cobrar_abaixo_da_tabela(
        self, http_op, db, veiculo, rastreador_instalado, contrato, produto_desinstalacao,
    ):
        """Exploit fechado: com o valor mandando sobre o catalogo, um perfil
        OPERACIONAL poderia registrar o servico e informar R$ 4,99 — abaixo do
        minimo faturavel — fazendo a taxa ser descartada no fechamento. Veiculo
        retirado, receita perdida, sem rastro de quem autorizou."""
        r = self._uninstall(
            http_op, veiculo.id,
            uninstall_service_product_id=produto_desinstalacao.id,
            uninstall_fee=4.99,
        )
        assert r.status_code == 403
        assert db.query(UninstallEvent).filter_by(vehicle_id=veiculo.id).count() == 0

    def test_operacional_pode_desinstalar_pelo_preco_de_tabela(
        self, http_op, db, veiculo, rastreador_instalado, contrato, produto_desinstalacao,
    ):
        r = self._uninstall(
            http_op, veiculo.id,
            uninstall_service_product_id=produto_desinstalacao.id,
            uninstall_fee=float(produto_desinstalacao.default_price),
        )
        assert r.status_code == 200

    def test_admin_pode_conceder_desconto_e_fica_registrado(
        self, http, db, veiculo, rastreador_instalado, contrato, produto_desinstalacao,
    ):
        r = self._uninstall(
            http, veiculo.id,
            uninstall_service_product_id=produto_desinstalacao.id,
            uninstall_fee=80.0,
        )
        assert r.status_code == 200
        event = db.query(UninstallEvent).filter_by(vehicle_id=veiculo.id).first()
        assert float(event.fee_amount) == 80.0
        assert 'Desconto autorizado por' in (event.notes or '')

    def test_uninstall_processes_all_trackers_and_contracts(
        self, http, db, cliente, veiculo, rastreador_instalado, contrato, plan,
    ):
        from app.models.contract import Contract
        from app.models.tracker import Tracker

        second = Tracker(
            imei='111112222233333',
            serial_number='111112222233333',
            brand='Teltonika',
            model='FMB920',
            status=TrackerStatus.INSTALLED,
            client_id=cliente.id,
            vehicle_id=veiculo.id,
            install_date=date(2024, 2, 1),
        )
        db.add(second)
        db.flush()
        second_contract = Contract(
            client_id=cliente.id,
            plan_id=plan.id,
            vehicle_id=veiculo.id,
            tracker_id=second.id,
            start_date=date(2024, 2, 1),
            status='ativo',
        )
        db.add(second_contract)
        db.commit()

        r = self._uninstall(http, veiculo.id)
        assert r.status_code == 200
        assert r.json()['trackers_returned_to_stock'] == 2
        db.refresh(rastreador_instalado)
        db.refresh(second)
        db.refresh(contrato)
        db.refresh(second_contract)
        assert rastreador_instalado.vehicle_id is None
        assert second.vehicle_id is None
        assert contrato.status == 'cancelado'
        assert second_contract.status == 'cancelado'

    def test_multiple_assignments_do_not_attribute_fee_to_arbitrary_first_row(
        self, http, db, cliente, veiculo, rastreador_instalado, contrato, plan,
    ):
        from app.models.contract import Contract
        from app.models.tracker import Tracker

        second = Tracker(
            imei='999992222233333', serial_number='999992222233333',
            brand='Teltonika', model='FMB920', status=TrackerStatus.INSTALLED,
            client_id=cliente.id, vehicle_id=veiculo.id,
            install_date=date(2024, 2, 1),
        )
        db.add(second)
        db.flush()
        db.add(Contract(
            client_id=cliente.id, plan_id=plan.id, vehicle_id=veiculo.id,
            tracker_id=second.id, start_date=date(2024, 2, 1), status='ativo',
        ))
        db.commit()

        r = self._uninstall(http, veiculo.id, uninstall_fee=100.0)
        assert r.status_code == 200
        event = db.query(UninstallEvent).filter_by(vehicle_id=veiculo.id).one()
        assert event.tracker_id is None
        assert event.contract_id is None
        assert event.payer_client_id == cliente.id

    def test_uninstall_fee_rejects_ambiguous_financial_responsibility(
        self, http, db, cliente, outro_cliente, veiculo,
        rastreador_instalado, contrato, plan,
    ):
        from app.models.contract import Contract
        from app.models.tracker import Tracker

        second = Tracker(
            imei='999992222244444', serial_number='999992222244444',
            brand='Teltonika', model='FMB920', status=TrackerStatus.INSTALLED,
            client_id=cliente.id, vehicle_id=veiculo.id,
            install_date=date(2024, 2, 1),
        )
        db.add(second)
        db.flush()
        second_contract = Contract(
            client_id=cliente.id, interveniente_client_id=outro_cliente.id,
            plan_id=plan.id, vehicle_id=veiculo.id, tracker_id=second.id,
            start_date=date(2024, 2, 1), status='ativo',
        )
        db.add(second_contract)
        db.commit()

        r = self._uninstall(http, veiculo.id, uninstall_fee=100.0)

        assert r.status_code == 409
        assert r.json()['detail']['code'] == 'ambiguous_financial_responsibility'
        assert db.query(UninstallEvent).filter_by(vehicle_id=veiculo.id).count() == 0
        db.refresh(rastreador_instalado)
        db.refresh(second)
        db.refresh(contrato)
        db.refresh(second_contract)
        assert rastreador_instalado.vehicle_id == veiculo.id
        assert second.vehicle_id == veiculo.id
        assert contrato.status == second_contract.status == 'ativo'
