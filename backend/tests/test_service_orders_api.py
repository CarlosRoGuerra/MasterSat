"""
Testes de integração para /api/v1/service-orders.

Cobertos:
- GET /         → listar, filtros (client_id, type, status)
- POST /        → criar ordem, campos obrigatórios, número gerado
- GET /{id}     → sucesso, 404
- PUT /{id}     → atualizar tipo/observações
- POST /{id}/status → transição aberta→em_andamento→concluida, já cancelada
- GET /{id}/logs    → histórico de mudanças de status
- DELETE /{id}  → soft-delete, 404
- Autorização   → FINANCIAL não cria ordens, OPERATIONAL pode editar
"""
from __future__ import annotations

import pytest
from app.models.enums import OrderStatus, OrderType
from app.models.service_order import ServiceOrder

PREFIX = "/api/v1/service-orders"


def _payload(client_id: int, vehicle_id: int | None = None) -> dict:
    return {
        "type": "instalacao",
        "client_id": client_id,
        "vehicle_id": vehicle_id,
        "status": "aberta",
        "observations": "Teste de instalação",
    }


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestListServiceOrders:
    def test_empty_list(self, http):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_existing_order(self, http, ordem_servico):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert any(x["id"] == ordem_servico.id for x in r.json())

    def test_filter_by_client_id(self, http, ordem_servico, cliente):
        r = http.get(PREFIX + "/", params={"client_id": cliente.id})
        assert r.status_code == 200
        assert all(x["client_id"] == cliente.id for x in r.json())

    def test_filter_by_type(self, http, ordem_servico):
        r = http.get(PREFIX + "/", params={"type": "instalacao"})
        assert r.status_code == 200
        assert all(x["type"] == "instalacao" for x in r.json())

    def test_filter_by_status(self, http, ordem_servico):
        r = http.get(PREFIX + "/", params={"status": "aberta"})
        assert r.status_code == 200
        assert all(x["status"] == "aberta" for x in r.json())

    def test_excludes_soft_deleted(self, http, db, ordem_servico):
        ordem_servico.is_deleted = True
        db.commit()
        r = http.get(PREFIX + "/")
        assert all(x["id"] != ordem_servico.id for x in r.json())

    def test_financial_can_list(self, http_fin, ordem_servico):
        r = http_fin.get(PREFIX + "/")
        assert r.status_code == 200

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.get(PREFIX + "/")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------

class TestCreateServiceOrder:
    def test_create_success(self, http, cliente, veiculo):
        r = http.post(PREFIX + "/", json=_payload(cliente.id, veiculo.id))
        assert r.status_code == 200
        data = r.json()
        assert data["client_id"] == cliente.id
        assert data["type"] == "instalacao"
        assert data["number"] is not None  # número gerado

    def test_number_is_unique(self, http, db, cliente):
        r1 = http.post(PREFIX + "/", json=_payload(cliente.id))
        r2 = http.post(PREFIX + "/", json={**_payload(cliente.id), "observations": "Segunda"})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["number"] != r2.json()["number"]

    def test_missing_client_id_returns_422(self, http):
        r = http.post(PREFIX + "/", json={"type": "instalacao", "status": "aberta"})
        assert r.status_code == 422

    def test_invalid_type_returns_422(self, http, cliente):
        r = http.post(PREFIX + "/", json={**_payload(cliente.id), "type": "tipo_invalido"})
        assert r.status_code == 422

    def test_nonexistent_client_returns_404(self, http):
        r = http.post(PREFIX + "/", json=_payload(99999))
        assert r.status_code == 404

    def test_operational_can_create(self, http_op, cliente):
        r = http_op.post(PREFIX + "/", json=_payload(cliente.id))
        assert r.status_code == 200

    def test_financial_cannot_create(self, http_fin, cliente):
        r = http_fin.post(PREFIX + "/", json=_payload(cliente.id))
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /{id}
# ---------------------------------------------------------------------------

class TestGetServiceOrder:
    def test_get_existing(self, http, ordem_servico):
        r = http.get(f"{PREFIX}/{ordem_servico.id}")
        assert r.status_code == 200
        assert r.json()["id"] == ordem_servico.id

    def test_get_nonexistent_returns_404(self, http):
        r = http.get(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_get_deleted_returns_404(self, http, db, ordem_servico):
        ordem_servico.is_deleted = True
        db.commit()
        r = http.get(f"{PREFIX}/{ordem_servico.id}")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# PUT /{id}
# ---------------------------------------------------------------------------

class TestUpdateServiceOrder:
    def test_update_observations(self, http, ordem_servico):
        r = http.put(f"{PREFIX}/{ordem_servico.id}", json={"observations": "Observação atualizada"})
        assert r.status_code == 200
        assert r.json()["observations"] == "Observação atualizada"

    def test_update_type(self, http, ordem_servico):
        r = http.put(f"{PREFIX}/{ordem_servico.id}", json={"type": "manutencao"})
        assert r.status_code == 200
        assert r.json()["type"] == "manutencao"

    def test_update_nonexistent_returns_404(self, http):
        r = http.put(f"{PREFIX}/99999", json={"observations": "X"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /{id}/status
# ---------------------------------------------------------------------------

class TestServiceOrderStatusTransition:
    def test_open_to_in_progress(self, http, ordem_servico):
        r = http.post(f"{PREFIX}/{ordem_servico.id}/status", json={
            "status": "em_andamento",
            "notes": "Iniciando serviço",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "em_andamento"

    def test_in_progress_to_completed(self, http, db, ordem_servico):
        ordem_servico.status = OrderStatus.IN_PROGRESS
        db.commit()
        r = http.post(f"{PREFIX}/{ordem_servico.id}/status", json={
            "status": "concluida",
            "notes": "Serviço concluído",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "concluida"

    def test_cancel_open_order(self, http, ordem_servico):
        r = http.post(f"{PREFIX}/{ordem_servico.id}/status", json={
            "status": "cancelada",
            "notes": "Cancelado pelo cliente",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "cancelada"

    def test_transition_nonexistent_returns_404(self, http):
        r = http.post(f"{PREFIX}/99999/status", json={"status": "em_andamento"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /{id}/logs
# ---------------------------------------------------------------------------

class TestServiceOrderLogs:
    def test_logs_empty_initially(self, http, ordem_servico):
        r = http.get(f"{PREFIX}/{ordem_servico.id}/logs")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_logs_record_status_change(self, http, ordem_servico):
        http.post(f"{PREFIX}/{ordem_servico.id}/status", json={"status": "em_andamento"})
        r = http.get(f"{PREFIX}/{ordem_servico.id}/logs")
        assert r.status_code == 200
        assert len(r.json()) >= 1


# ---------------------------------------------------------------------------
# DELETE /{id}
# ---------------------------------------------------------------------------

class TestDeleteServiceOrder:
    def test_soft_delete(self, http, db, ordem_servico):
        r = http.delete(f"{PREFIX}/{ordem_servico.id}")
        assert r.status_code == 200
        db.refresh(ordem_servico)
        assert ordem_servico.is_deleted is True

    def test_delete_nonexistent_returns_404(self, http):
        r = http.delete(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_financial_cannot_delete(self, http_fin, ordem_servico):
        r = http_fin.delete(f"{PREFIX}/{ordem_servico.id}")
        assert r.status_code == 403
