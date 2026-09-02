"""Testes de integração para /api/v1/service-orders/{id}/materials.

Materiais usados na execução da OS — registro operacional, sem gerar
cobrança automática (decisão de escopo confirmada com o usuário).
"""
from __future__ import annotations

PREFIX = "/api/v1/service-orders"


class TestListMaterials:
    def test_empty_list(self, http, ordem_servico):
        r = http.get(f"{PREFIX}/{ordem_servico.id}/materials")
        assert r.status_code == 200
        assert r.json() == []

    def test_nonexistent_order_returns_404(self, http):
        r = http.get(f"{PREFIX}/99999/materials")
        assert r.status_code == 404


class TestCreateMaterial:
    def test_create_free_text(self, http, ordem_servico):
        r = http.post(f"{PREFIX}/{ordem_servico.id}/materials", json={
            "description": "Cabo blindado 3m",
            "quantity": "2",
            "unit": "un",
            "unit_price": "15.00",
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["description"] == "Cabo blindado 3m"
        assert data["service_order_id"] == ordem_servico.id
        assert data["service_product_id"] is None
        assert data["service_product_name"] is None

    def test_create_linked_to_catalog(self, http, ordem_servico, produto_servico):
        r = http.post(f"{PREFIX}/{ordem_servico.id}/materials", json={
            "service_product_id": produto_servico.id,
            "description": produto_servico.name,
            "quantity": "1",
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["service_product_id"] == produto_servico.id
        assert data["service_product_name"] == produto_servico.name

    def test_nonexistent_product_returns_404(self, http, ordem_servico):
        r = http.post(f"{PREFIX}/{ordem_servico.id}/materials", json={
            "service_product_id": 99999,
            "description": "Item qualquer",
        })
        assert r.status_code == 404

    def test_missing_description_returns_422(self, http, ordem_servico):
        r = http.post(f"{PREFIX}/{ordem_servico.id}/materials", json={"quantity": "1"})
        assert r.status_code == 422

    def test_zero_quantity_returns_422(self, http, ordem_servico):
        r = http.post(f"{PREFIX}/{ordem_servico.id}/materials", json={
            "description": "Item", "quantity": "0",
        })
        assert r.status_code == 422

    def test_does_not_create_billing_charge(self, http, ordem_servico, db):
        """Confirma a decisão de escopo: material não vira cobrança."""
        from app.models.client_charge_item import ClientChargeItem
        r = http.post(f"{PREFIX}/{ordem_servico.id}/materials", json={
            "description": "Peça cara", "quantity": "1", "unit_price": "999.00",
        })
        assert r.status_code == 200
        assert db.query(ClientChargeItem).count() == 0

    def test_financial_cannot_create(self, http_fin, ordem_servico):
        r = http_fin.post(f"{PREFIX}/{ordem_servico.id}/materials", json={"description": "Item"})
        assert r.status_code == 403

    def test_operational_can_create(self, http_op, ordem_servico):
        r = http_op.post(f"{PREFIX}/{ordem_servico.id}/materials", json={"description": "Item"})
        assert r.status_code == 200


class TestUpdateMaterial:
    def test_update_success(self, http, ordem_servico):
        created = http.post(f"{PREFIX}/{ordem_servico.id}/materials", json={
            "description": "Original", "quantity": "1",
        }).json()
        r = http.put(f"{PREFIX}/{ordem_servico.id}/materials/{created['id']}", json={
            "description": "Atualizado", "quantity": "3", "unit_price": "20.00",
        })
        assert r.status_code == 200
        assert r.json()["description"] == "Atualizado"
        assert float(r.json()["quantity"]) == 3

    def test_update_nonexistent_returns_404(self, http, ordem_servico):
        r = http.put(f"{PREFIX}/{ordem_servico.id}/materials/99999", json={"description": "X"})
        assert r.status_code == 404


class TestDeleteMaterial:
    def test_delete_removes_from_list(self, http, db, ordem_servico):
        from app.models.service_order_material import ServiceOrderMaterial
        created = http.post(f"{PREFIX}/{ordem_servico.id}/materials", json={
            "description": "Descartável", "quantity": "1",
        }).json()
        r = http.delete(f"{PREFIX}/{ordem_servico.id}/materials/{created['id']}")
        assert r.status_code == 200
        listed = http.get(f"{PREFIX}/{ordem_servico.id}/materials").json()
        assert all(m["id"] != created["id"] for m in listed)
        # soft delete — a linha continua existindo no banco
        row = db.get(ServiceOrderMaterial, created["id"])
        assert row is not None
        assert row.is_deleted is True

    def test_delete_nonexistent_returns_404(self, http, ordem_servico):
        r = http.delete(f"{PREFIX}/{ordem_servico.id}/materials/99999")
        assert r.status_code == 404


class TestMaterialScopedToOrder:
    def test_material_not_visible_via_other_order(self, http, db, ordem_servico, cliente):
        from app.models.enums import OrderStatus, OrderType
        from app.models.service_order import ServiceOrder

        outra_os = ServiceOrder(
            number="OS-2025-999", type=OrderType.MAINTENANCE, status=OrderStatus.OPEN,
            client_id=cliente.id,
        )
        db.add(outra_os)
        db.commit()
        db.refresh(outra_os)

        created = http.post(f"{PREFIX}/{ordem_servico.id}/materials", json={
            "description": "Item da OS 1",
        }).json()

        r = http.put(f"{PREFIX}/{outra_os.id}/materials/{created['id']}", json={"description": "Hack"})
        assert r.status_code == 404
        r = http.delete(f"{PREFIX}/{outra_os.id}/materials/{created['id']}")
        assert r.status_code == 404
