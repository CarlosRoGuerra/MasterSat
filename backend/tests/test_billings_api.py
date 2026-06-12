"""
Testes de integração para /api/v1/billings.

Cobertos:
- GET /           → listar, filtros (status, client_id, busca, datas)
- POST /          → criar, campos obrigatórios
- GET /{id}       → sucesso, 404
- POST /{id}/receive    → baixa de pagamento, 404, billing já pago
- POST /{id}/cancel     → cancelar, 404, billing já cancelado
- PUT /{id}             → ajustar valor/vencimento
- DELETE /{id}          → soft-delete
- GET /summary          → resumo financeiro
- Autorização     → CLIENT → 403, OPERATIONAL só lê
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.billing import Billing
from app.models.enums import BillingStatus

PREFIX = "/api/v1/billings"


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestListBillings:
    def test_empty_list(self, http):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_existing_billing(self, http, billing_pendente):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert any(x["id"] == billing_pendente.id for x in r.json())

    def test_filter_by_status_pending(self, http, billing_pendente):
        r = http.get(PREFIX + "/", params={"status": "pendente"})
        assert r.status_code == 200
        assert all(x["status"] == "pendente" for x in r.json())

    def test_filter_by_status_overdue(self, http, billing_vencida):
        r = http.get(PREFIX + "/", params={"status": "vencida"})
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_filter_by_client_id(self, http, billing_pendente, cliente):
        r = http.get(PREFIX + "/", params={"client_id": cliente.id})
        assert r.status_code == 200
        assert all(x["client_id"] == cliente.id for x in r.json())

    def test_filter_by_wrong_client_empty(self, http, billing_pendente):
        r = http.get(PREFIX + "/", params={"client_id": 99999})
        assert r.status_code == 200
        assert r.json() == []

    def test_filter_by_due_from(self, http, billing_pendente):
        r = http.get(PREFIX + "/", params={"due_from": "2099-01-01"})
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_filter_by_due_to(self, http, billing_vencida):
        r = http.get(PREFIX + "/", params={"due_to": "2021-01-01"})
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_excludes_soft_deleted(self, http, db, billing_pendente):
        billing_pendente.is_deleted = True
        db.commit()
        r = http.get(PREFIX + "/")
        assert all(x["id"] != billing_pendente.id for x in r.json())

    def test_client_role_cannot_list(self, http_cliente):
        r = http_cliente.get(PREFIX + "/")
        assert r.status_code == 403

    def test_operational_can_list(self, http_op, billing_pendente):
        r = http_op.get(PREFIX + "/")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /summary
# ---------------------------------------------------------------------------

class TestBillingSummary:
    def test_summary_returns_correct_structure(self, http):
        r = http.get(PREFIX + "/summary")
        assert r.status_code == 200
        data = r.json()
        assert "pending_billings" in data
        assert "overdue_billings" in data
        assert "pending_amount" in data
        assert "overdue_amount" in data
        assert "paid_this_month" in data

    def test_summary_counts_pending(self, http, billing_pendente):
        r = http.get(PREFIX + "/summary")
        assert r.status_code == 200
        assert r.json()["pending_billings"] >= 1

    def test_summary_counts_overdue(self, http, billing_vencida):
        r = http.get(PREFIX + "/summary")
        assert r.status_code == 200
        assert r.json()["overdue_billings"] >= 1

    def test_operational_cannot_see_summary(self, http_op):
        r = http_op.get(PREFIX + "/summary")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------

class TestCreateBilling:
    def test_create_success(self, http, cliente):
        r = http.post(PREFIX + "/", json={
            "client_id": cliente.id,
            "amount": 199.90,
            "due_date": "2099-12-31",
            "status": "pendente",
            "billing_type": "item",
            "title": "Cobrança manual",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["client_id"] == cliente.id
        assert abs(data["amount"] - 199.90) < 0.01

    def test_missing_client_id_returns_422(self, http):
        r = http.post(PREFIX + "/", json={"amount": 100.0, "due_date": "2099-12-31"})
        assert r.status_code == 422

    def test_missing_amount_returns_422(self, http, cliente):
        r = http.post(PREFIX + "/", json={"client_id": cliente.id, "due_date": "2099-12-31"})
        assert r.status_code == 422

    def test_operational_cannot_create(self, http_op, cliente):
        r = http_op.post(PREFIX + "/", json={
            "client_id": cliente.id, "amount": 100.0, "due_date": "2099-12-31",
        })
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /{id}
# ---------------------------------------------------------------------------

class TestGetBilling:
    def test_get_existing(self, http, billing_pendente):
        r = http.get(f"{PREFIX}/{billing_pendente.id}")
        assert r.status_code == 200
        assert r.json()["id"] == billing_pendente.id

    def test_get_nonexistent_returns_404(self, http):
        r = http.get(f"{PREFIX}/99999")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /{id}/receive (baixar pagamento)
# ---------------------------------------------------------------------------

class TestReceiveBilling:
    def test_receive_pending_billing(self, http, billing_pendente):
        r = http.post(f"{PREFIX}/{billing_pendente.id}/receive", json={
            "paid_amount": 99.90,
            "payment_date": "2025-05-28",
            "payment_method": "pix",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "paga"

    def test_receive_generates_receipt_number(self, http, billing_pendente):
        r = http.post(f"{PREFIX}/{billing_pendente.id}/receive", json={
            "paid_amount": 99.90,
            "payment_date": "2025-05-28",
            "payment_method": "boleto",
        })
        assert r.status_code == 200
        assert r.json()["receipt_number"] is not None

    def test_receive_nonexistent_returns_404(self, http):
        r = http.post(f"{PREFIX}/99999/receive", json={
            "paid_amount": 100.0, "payment_date": "2025-05-28", "payment_method": "pix",
        })
        assert r.status_code == 404

    def test_receive_already_paid_returns_400(self, http, db, billing_pendente):
        billing_pendente.status = BillingStatus.PAID
        db.commit()
        r = http.post(f"{PREFIX}/{billing_pendente.id}/receive", json={
            "paid_amount": 99.90, "payment_date": "2025-05-28", "payment_method": "pix",
        })
        assert r.status_code == 400

    def test_operational_cannot_receive(self, http_op, billing_pendente):
        r = http_op.post(f"{PREFIX}/{billing_pendente.id}/receive", json={
            "paid_amount": 99.90, "payment_date": "2025-05-28", "payment_method": "pix",
        })
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /{id}/cancel
# ---------------------------------------------------------------------------

class TestCancelBilling:
    def test_cancel_pending_billing(self, http, billing_pendente):
        r = http.post(f"{PREFIX}/{billing_pendente.id}/cancel", json={"reason": "Cancelado por teste"})
        assert r.status_code == 200
        assert r.json()["status"] == "cancelada"

    def test_cancel_nonexistent_returns_404(self, http):
        r = http.post(f"{PREFIX}/99999/cancel", json={"reason": "X"})
        assert r.status_code == 404

    def test_cancel_already_canceled_returns_400(self, http, db, billing_pendente):
        billing_pendente.status = BillingStatus.CANCELED
        db.commit()
        r = http.post(f"{PREFIX}/{billing_pendente.id}/cancel", json={"reason": "X"})
        assert r.status_code == 400

    def test_operational_cannot_cancel(self, http_op, billing_pendente):
        r = http_op.post(f"{PREFIX}/{billing_pendente.id}/cancel", json={"reason": "X"})
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# PUT /{id} (ajuste)
# ---------------------------------------------------------------------------

class TestUpdateBilling:
    def test_update_amount(self, http, billing_pendente):
        r = http.put(f"{PREFIX}/{billing_pendente.id}", json={
            "amount": 150.00,
            "justification": "Ajuste de valor",
        })
        assert r.status_code == 200
        assert abs(r.json()["amount"] - 150.00) < 0.01

    def test_update_due_date(self, http, billing_pendente):
        r = http.put(f"{PREFIX}/{billing_pendente.id}", json={
            "due_date": "2099-11-30",
            "justification": "Prorrogação",
        })
        assert r.status_code == 200

    def test_update_nonexistent_returns_404(self, http):
        r = http.put(f"{PREFIX}/99999", json={"amount": 100.0, "justification": "X"})
        assert r.status_code == 404

    def test_operational_cannot_update(self, http_op, billing_pendente):
        r = http_op.put(f"{PREFIX}/{billing_pendente.id}", json={
            "amount": 150.00, "justification": "X",
        })
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /{id}
# ---------------------------------------------------------------------------

class TestDeleteBilling:
    def test_soft_delete(self, http, db, billing_pendente):
        r = http.delete(f"{PREFIX}/{billing_pendente.id}")
        assert r.status_code == 200
        db.refresh(billing_pendente)
        assert billing_pendente.is_deleted is True

    def test_delete_nonexistent_returns_404(self, http):
        r = http.delete(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_operational_cannot_delete(self, http_op, billing_pendente):
        r = http_op.delete(f"{PREFIX}/{billing_pendente.id}")
        assert r.status_code == 403
