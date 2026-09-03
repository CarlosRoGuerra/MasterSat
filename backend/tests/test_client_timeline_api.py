"""
Testes de integração para GET /api/v1/clients/{id}/timeline (Linha do Tempo).

Cobertos:
- Cada categoria aparece com dado real (cliente/veículo/rastreador/contrato/
  documento/financeiro/os/auditoria)
- Ordenação cronológica cruzando categorias
- Paginação (skip/limit/total)
- Filtro por categoria
- RBAC: operacional não recebe financeiro/contrato; só admin recebe
  auditoria; role cliente é 403 no endpoint inteiro; sem auth é 401
- Cliente inexistente → 404
- Cliente sem dado relacionado → só o evento de cadastro
- Documento com dono (veículo) soft-deletado não vira link morto
"""
from __future__ import annotations

from datetime import date, datetime

from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.enums import BillingStatus, OrderStatus
from app.models.billing import Billing
from app.models.service_order_status_log import ServiceOrderStatusLog
from app.models.tracker_history import TrackerHistory

PREFIX = "/api/v1/clients"


def _url(client_id: int) -> str:
    return f"{PREFIX}/{client_id}/timeline"


class TestTimelineClientCategory:
    def test_client_created_event(self, http, cliente):
        r = http.get(_url(cliente.id))
        assert r.status_code == 200
        body = r.json()
        assert any(e["type"] == "client_created" for e in body["items"])

    def test_client_with_no_related_data_shows_only_creation_event(self, http, outro_cliente):
        r = http.get(_url(outro_cliente.id))
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["type"] == "client_created"

    def test_client_not_found(self, http):
        r = http.get(_url(999999))
        assert r.status_code == 404


class TestTimelineVehicleCategory:
    def test_vehicle_added_event(self, http, cliente, veiculo):
        r = http.get(_url(cliente.id), params={"category": "veiculo"})
        assert r.status_code == 200
        body = r.json()
        assert any(e["type"] == "vehicle_added" and "ABC1D23" in e["description"] for e in body["items"])

    def test_category_with_no_data_returns_empty(self, http, outro_cliente):
        r = http.get(_url(outro_cliente.id), params={"category": "veiculo"})
        assert r.status_code == 200
        assert r.json() == {"items": [], "total": 0}


class TestTimelineTrackerCategory:
    def test_tracker_history_event(self, http, db, cliente, veiculo, rastreador_instalado):
        h = TrackerHistory(
            tracker_id=rastreador_instalado.id,
            action="linked",
            previous_vehicle_id=None,
            new_vehicle_id=veiculo.id,
            previous_client_id=None,
            new_client_id=cliente.id,
            event_date=date(2024, 1, 15),
        )
        db.add(h)
        db.commit()

        r = http.get(_url(cliente.id), params={"category": "rastreador"})
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 1
        event = body["items"][0]
        assert event["type"] == "tracker_linked"
        assert "987654321098765" in event["description"]
        assert "ABC1D23" in event["description"]
        assert event["link"]["entity"] == "tracker"
        assert event["link"]["id"] == rastreador_instalado.id


class TestTimelineContractCategory:
    def test_contract_created_event(self, http, cliente, contrato):
        r = http.get(_url(cliente.id), params={"category": "contrato"})
        assert r.status_code == 200
        assert any(e["type"] == "contract_created" for e in r.json()["items"])

    def test_contract_signed_event(self, http, db, cliente, contrato):
        contrato.signed = True
        contrato.signed_at = date(2024, 2, 1)
        db.commit()

        r = http.get(_url(cliente.id), params={"category": "contrato"})
        assert any(e["type"] == "contract_signed" for e in r.json()["items"])

    def test_contract_canceled_event(self, http, db, cliente, contrato):
        contrato.status = "cancelado"
        contrato.updated_at = datetime(2024, 6, 1, 10, 0)
        db.commit()

        r = http.get(_url(cliente.id), params={"category": "contrato"})
        canceled = [e for e in r.json()["items"] if e["type"] == "contract_status_changed"]
        assert len(canceled) == 1
        assert canceled[0]["severity"] == "danger"


class TestTimelineDocumentCategory:
    def _criar_documento(self, db, *, reference_type, reference_id, file_name="cnh.pdf"):
        doc = Document(
            file_name=file_name,
            object_key=f"test/{reference_type}/{reference_id}/{file_name}",
            content_type="application/pdf",
            size_bytes=1234,
            reference_type=reference_type,
            reference_id=reference_id,
            category="geral",
            active=True,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc

    def test_document_uploaded_via_client(self, http, db, cliente):
        doc = self._criar_documento(db, reference_type="client", reference_id=cliente.id)
        r = http.get(_url(cliente.id), params={"category": "documento"})
        body = r.json()
        found = next(e for e in body["items"] if e["id"] == f"document:{doc.id}:uploaded")
        assert found["link"]["client_id"] == cliente.id

    def test_document_uploaded_via_vehicle(self, http, db, cliente, veiculo):
        doc = self._criar_documento(db, reference_type="vehicle", reference_id=veiculo.id)
        r = http.get(_url(cliente.id), params={"category": "documento"})
        found = next(e for e in r.json()["items"] if e["id"] == f"document:{doc.id}:uploaded")
        assert found["link"]["vehicle_id"] == veiculo.id

    def test_deleted_vehicle_owner_does_not_leak_navigation_link(self, http, db, cliente, veiculo):
        doc = self._criar_documento(db, reference_type="vehicle", reference_id=veiculo.id)
        veiculo.is_deleted = True
        db.commit()

        r = http.get(_url(cliente.id), params={"category": "documento"})
        found = next(e for e in r.json()["items"] if e["id"] == f"document:{doc.id}:uploaded")
        assert found["link"] is None


class TestTimelineFinancialCategory:
    def _billing(self, db, contrato, **kwargs):
        defaults = dict(
            contract_id=contrato.id,
            client_id=contrato.client_id,
            amount=99.90,
            due_date=date(2024, 3, 10),
            status=BillingStatus.PENDING,
            billing_type="recorrente",
            title="Mensalidade",
        )
        defaults.update(kwargs)
        b = Billing(**defaults)
        db.add(b)
        db.commit()
        db.refresh(b)
        return b

    def test_billing_paid_event(self, http, db, cliente, contrato):
        b = self._billing(db, contrato, status=BillingStatus.PAID, payment_date=date(2024, 3, 9))
        r = http.get(_url(cliente.id), params={"category": "financeiro"})
        types = [e["type"] for e in r.json()["items"] if e["id"].startswith(f"billing:{b.id}")]
        assert "billing_created" in types
        assert "billing_paid" in types

    def test_billing_overdue_event(self, http, db, cliente, contrato):
        b = self._billing(db, contrato, status=BillingStatus.OVERDUE)
        r = http.get(_url(cliente.id), params={"category": "financeiro"})
        types = [e["type"] for e in r.json()["items"] if e["id"].startswith(f"billing:{b.id}")]
        assert "billing_overdue" in types


class TestTimelineServiceOrderCategory:
    def test_os_created_event(self, http, db, cliente, ordem_servico):
        log = ServiceOrderStatusLog(
            service_order_id=ordem_servico.id, previous_status=None, new_status=OrderStatus.OPEN,
        )
        db.add(log)
        db.commit()

        r = http.get(_url(cliente.id), params={"category": "os"})
        body = r.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["type"] == "service_order_created"
        assert ordem_servico.number in body["items"][0]["description"]

    def test_os_completed_event(self, http, db, cliente, ordem_servico):
        db.add(ServiceOrderStatusLog(service_order_id=ordem_servico.id, previous_status=None, new_status=OrderStatus.OPEN))
        db.add(ServiceOrderStatusLog(service_order_id=ordem_servico.id, previous_status=OrderStatus.OPEN, new_status=OrderStatus.COMPLETED))
        db.commit()

        r = http.get(_url(cliente.id), params={"category": "os"})
        types = [e["type"] for e in r.json()["items"]]
        assert "service_order_created" in types
        assert "service_order_completed" in types


class TestTimelineAuditCategory:
    def test_admin_sees_audit_event(self, http, db, cliente):
        db.add(AuditLog(
            user_id=1, user_name="Admin Teste", user_role="admin", method="PUT",
            path=f"/api/v1/clients/{cliente.id}", entity_type="cliente", entity_id=cliente.id,
            status_code=200, description="Editou cliente",
        ))
        db.commit()

        r = http.get(_url(cliente.id), params={"category": "auditoria"})
        assert r.status_code == 200
        assert any(e["type"] == "audit_action" for e in r.json()["items"])

    def test_get_requests_excluded_from_audit(self, http, db, cliente):
        db.add(AuditLog(
            user_id=1, user_name="Admin Teste", user_role="admin", method="GET",
            path=f"/api/v1/clients/{cliente.id}", entity_type="cliente", entity_id=cliente.id,
            status_code=200, description="Consultou cliente",
        ))
        db.commit()

        r = http.get(_url(cliente.id), params={"category": "auditoria"})
        assert r.json()["items"] == []

    def test_operational_never_receives_audit(self, http_op, db, cliente):
        db.add(AuditLog(
            user_id=1, user_name="Admin Teste", user_role="admin", method="PUT",
            path=f"/api/v1/clients/{cliente.id}", entity_type="cliente", entity_id=cliente.id,
            status_code=200, description="Editou cliente",
        ))
        db.commit()

        r = http_op.get(_url(cliente.id), params={"category": "auditoria"})
        assert r.status_code == 200
        assert r.json()["items"] == []


class TestTimelineOrderingAndPagination:
    def test_chronological_order_across_categories(self, http, db, cliente, veiculo, contrato):
        # Cliente é criado "agora" pelo fixture — força as outras duas datas
        # em pontos claramente distintos do passado pra comparar com certeza.
        veiculo.created_at = datetime(2023, 1, 1, 10, 0)
        db.commit()
        contrato.created_at = datetime(2023, 6, 1, 10, 0)
        db.commit()

        r = http.get(_url(cliente.id))
        items = r.json()["items"]
        dates = [i["occurred_at"] for i in items]
        assert dates == sorted(dates, reverse=True)

    def test_pagination(self, http, db, cliente):
        from app.models.vehicle import Vehicle

        for i in range(5):
            v = Vehicle(client_id=cliente.id, plate=f"PAG{i}D23", type="passeio")
            db.add(v)
        db.commit()

        r1 = http.get(_url(cliente.id), params={"category": "veiculo", "skip": 0, "limit": 2})
        body1 = r1.json()
        assert len(body1["items"]) == 2
        assert body1["total"] == 5

        r2 = http.get(_url(cliente.id), params={"category": "veiculo", "skip": 2, "limit": 2})
        body2 = r2.json()
        assert len(body2["items"]) == 2
        assert {i["id"] for i in body1["items"]}.isdisjoint({i["id"] for i in body2["items"]})


class TestTimelinePermissions:
    def test_client_role_forbidden(self, http_cliente, cliente):
        r = http_cliente.get(_url(cliente.id))
        assert r.status_code == 403

    def test_unauthenticated(self, http_unauth, cliente):
        r = http_unauth.get(_url(cliente.id))
        assert r.status_code == 401

    def test_operational_never_receives_contracts(self, http_op, cliente, contrato):
        r = http_op.get(_url(cliente.id), params={"category": "contrato"})
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_financial_sees_contracts(self, http_fin, cliente, contrato):
        r = http_fin.get(_url(cliente.id), params={"category": "contrato"})
        assert any(e["type"] == "contract_created" for e in r.json()["items"])

    def test_operational_never_receives_financial(self, http_op, db, cliente, contrato):
        from decimal import Decimal
        b = Billing(
            contract_id=contrato.id, client_id=contrato.client_id, amount=Decimal("50.00"),
            due_date=date(2024, 4, 1), status=BillingStatus.PENDING, billing_type="recorrente",
        )
        db.add(b)
        db.commit()

        r = http_op.get(_url(cliente.id), params={"category": "financeiro"})
        assert r.status_code == 200
        assert r.json()["items"] == []
