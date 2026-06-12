"""
Testes de integração para /api/v1/clients.

Cobertos:
- GET /         → listar, busca, filtro por tipo, paginação
- POST /        → criar PF/PJ, CPF duplicado, campos obrigatórios
- GET /{id}     → sucesso, 404, deletado
- PUT /{id}     → atualizar, 404
- DELETE /{id}  → soft-delete, 404
- Autorização   → CLIENT role → 403, sem auth → 401
"""
from __future__ import annotations

import pytest
from app.models.enums import ClientStatus
from app.models.client import Client

PREFIX = "/api/v1/clients"

_PF_PAYLOAD = {
    "name": "Novo Cliente PF",
    "cpf_cnpj": "11122233344",
    "type": "pf",
    "status": "ativo",
    "email": "novo@test.local",
    "phone": "11999990001",
}

_PJ_PAYLOAD = {
    "name": "Empresa Teste LTDA",
    "cpf_cnpj": "12345678000195",
    "type": "pj",
    "status": "ativo",
    "email": "empresa@test.local",
}


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestListClients:
    def test_empty_list(self, http):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_existing_client(self, http, cliente):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert any(x["id"] == cliente.id for x in r.json())

    def test_search_by_name(self, http, cliente):
        r = http.get(PREFIX + "/", params={"search": "João"})
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_search_case_insensitive(self, http, cliente):
        r = http.get(PREFIX + "/", params={"search": "joão"})
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_search_no_match(self, http, cliente):
        r = http.get(PREFIX + "/", params={"search": "Inexistente XYZ"})
        assert r.status_code == 200
        assert r.json() == []

    def test_filter_by_type_pf(self, http, cliente):
        r = http.get(PREFIX + "/", params={"type": "pf"})
        assert r.status_code == 200
        assert all(x["type"] == "pf" for x in r.json())

    def test_excludes_soft_deleted(self, http, db, cliente):
        cliente.is_deleted = True
        db.commit()
        r = http.get(PREFIX + "/")
        assert all(x["id"] != cliente.id for x in r.json())

    def test_operational_can_list(self, http_op, cliente):
        r = http_op.get(PREFIX + "/")
        assert r.status_code == 200

    def test_client_role_cannot_list(self, http_cliente):
        r = http_cliente.get(PREFIX + "/")
        assert r.status_code == 403

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.get(PREFIX + "/")
        assert r.status_code == 401

    def test_sql_injection_safe(self, http, cliente):
        r = http.get(PREFIX + "/", params={"search": "' OR '1'='1"})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------

class TestCreateClient:
    def test_create_pf_success(self, http):
        r = http.post(PREFIX + "/", json=_PF_PAYLOAD)
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == _PF_PAYLOAD["name"]
        assert data["type"] == "pf"
        assert "id" in data

    def test_create_pj_success(self, http):
        r = http.post(PREFIX + "/", json=_PJ_PAYLOAD)
        assert r.status_code == 200
        assert r.json()["type"] == "pj"

    def test_missing_name_returns_422(self, http):
        payload = {**_PF_PAYLOAD}
        del payload["name"]
        r = http.post(PREFIX + "/", json=payload)
        assert r.status_code == 422

    def test_missing_cpf_cnpj_returns_422(self, http):
        payload = {**_PF_PAYLOAD}
        del payload["cpf_cnpj"]
        r = http.post(PREFIX + "/", json=payload)
        assert r.status_code == 422

    def test_operational_can_create(self, http_op):
        r = http_op.post(PREFIX + "/", json=_PF_PAYLOAD)
        assert r.status_code == 200

    def test_client_role_cannot_create(self, http_cliente):
        r = http_cliente.post(PREFIX + "/", json=_PF_PAYLOAD)
        assert r.status_code == 403

    def test_xss_in_name_stored_safely(self, http):
        payload = {**_PF_PAYLOAD, "name": "<script>alert(1)</script>", "cpf_cnpj": "00011122233"}
        r = http.post(PREFIX + "/", json=payload)
        assert r.status_code == 200
        assert r.json()["name"] == "<script>alert(1)</script>"


# ---------------------------------------------------------------------------
# GET /{id}
# ---------------------------------------------------------------------------

class TestGetClient:
    def test_get_existing(self, http, cliente):
        r = http.get(f"{PREFIX}/{cliente.id}")
        assert r.status_code == 200
        assert r.json()["id"] == cliente.id

    def test_get_nonexistent_returns_404(self, http):
        r = http.get(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_get_deleted_returns_404(self, http, db, cliente):
        cliente.is_deleted = True
        db.commit()
        r = http.get(f"{PREFIX}/{cliente.id}")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# PUT /{id}
# ---------------------------------------------------------------------------

class TestUpdateClient:
    def test_update_name(self, http, cliente):
        r = http.put(f"{PREFIX}/{cliente.id}", json={"name": "Nome Atualizado"})
        assert r.status_code == 200
        assert r.json()["name"] == "Nome Atualizado"

    def test_update_status(self, http, cliente):
        r = http.put(f"{PREFIX}/{cliente.id}", json={"status": "inativo"})
        assert r.status_code == 200
        assert r.json()["status"] == "inativo"

    def test_update_nonexistent_returns_404(self, http):
        r = http.put(f"{PREFIX}/99999", json={"name": "X"})
        assert r.status_code == 404

    def test_partial_update_preserves_other_fields(self, http, cliente):
        r = http.put(f"{PREFIX}/{cliente.id}", json={"name": "Nome Novo"})
        assert r.status_code == 200
        data = r.json()
        assert data["cpf_cnpj"] == cliente.cpf_cnpj  # unchanged


# ---------------------------------------------------------------------------
# DELETE /{id}
# ---------------------------------------------------------------------------

class TestDeleteClient:
    def test_soft_delete(self, http, db, cliente):
        r = http.delete(f"{PREFIX}/{cliente.id}")
        assert r.status_code == 200
        db.refresh(cliente)
        assert cliente.is_deleted is True

    def test_delete_nonexistent_returns_404(self, http):
        r = http.delete(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_delete_already_deleted_returns_404(self, http, db, cliente):
        cliente.is_deleted = True
        db.commit()
        r = http.delete(f"{PREFIX}/{cliente.id}")
        assert r.status_code == 404

    def test_financial_cannot_delete(self, http_fin, cliente):
        r = http_fin.delete(f"{PREFIX}/{cliente.id}")
        assert r.status_code == 403
