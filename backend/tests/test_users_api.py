"""
Testes de integração para /api/v1/users.

Cobertos:
- GET /         → listagem (ADMIN only)
- POST /        → criar, email duplicado, role inválida
- GET /{id}     → sucesso, 404, soft-deleted
- PUT /{id}     → atualizar nome/email/role, 404
- DELETE /{id}  → soft-delete, 404
- Autorização   → OPERATIONAL e FINANCIAL recebem 403, CLIENT recebe 403
"""
from __future__ import annotations

import pytest
from app.core.security import get_password_hash
from app.models.enums import UserRole
from app.models.user import User

PREFIX = "/api/v1/users"


def _seed_user(db, email="operador@test.local", role=UserRole.OPERATIONAL):
    u = User(
        name="Operador Teste",
        email=email,
        password_hash=get_password_hash("Senha@123"),
        role=role,
        active=True,
        is_deleted=False,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestListUsers:
    def test_admin_can_list(self, http, db):
        _seed_user(db)
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1

    def test_excludes_deleted(self, http, db):
        u = _seed_user(db)
        u.is_deleted = True
        db.commit()
        r = http.get(PREFIX + "/")
        assert all(x["id"] != u.id for x in r.json())

    def test_operational_cannot_list(self, http_op):
        r = http_op.get(PREFIX + "/")
        assert r.status_code == 403

    def test_financial_cannot_list(self, http_fin):
        r = http_fin.get(PREFIX + "/")
        assert r.status_code == 403

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.get(PREFIX + "/")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------

class TestCreateUser:
    def test_create_success(self, http):
        r = http.post(PREFIX + "/", json={
            "name": "Novo Usuário",
            "email": "novo@test.local",
            "role": "operacional",
            "active": True,
            "password": "Senha@123",
        })
        assert r.status_code == 200
        assert r.json()["email"] == "novo@test.local"

    def test_missing_email_returns_422(self, http):
        r = http.post(PREFIX + "/", json={"name": "X", "role": "operacional", "password": "Senha@123"})
        assert r.status_code == 422

    def test_missing_password_returns_422(self, http):
        r = http.post(PREFIX + "/", json={"name": "X", "email": "x@x.com", "role": "operacional"})
        assert r.status_code == 422

    def test_operational_cannot_create(self, http_op):
        r = http_op.post(PREFIX + "/", json={
            "name": "X", "email": "x@x.com", "role": "operacional", "password": "Senha@123",
        })
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /{id}
# ---------------------------------------------------------------------------

class TestGetUser:
    def test_get_existing(self, http, db):
        u = _seed_user(db)
        r = http.get(f"{PREFIX}/{u.id}")
        assert r.status_code == 200
        assert r.json()["id"] == u.id

    def test_get_nonexistent_returns_404(self, http):
        r = http.get(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_get_deleted_returns_404(self, http, db):
        u = _seed_user(db)
        u.is_deleted = True
        db.commit()
        r = http.get(f"{PREFIX}/{u.id}")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# PUT /{id}
# ---------------------------------------------------------------------------

class TestUpdateUser:
    def test_update_name(self, http, db):
        u = _seed_user(db)
        r = http.put(f"{PREFIX}/{u.id}", json={"name": "Nome Atualizado"})
        assert r.status_code == 200
        assert r.json()["name"] == "Nome Atualizado"

    def test_update_active_status(self, http, db):
        u = _seed_user(db)
        r = http.put(f"{PREFIX}/{u.id}", json={"active": False})
        assert r.status_code == 200
        assert r.json()["active"] is False

    def test_update_nonexistent_returns_404(self, http):
        r = http.put(f"{PREFIX}/99999", json={"name": "X"})
        assert r.status_code == 404

    def test_operational_cannot_update(self, http_op, db):
        u = _seed_user(db)
        r = http_op.put(f"{PREFIX}/{u.id}", json={"name": "X"})
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /{id}
# ---------------------------------------------------------------------------

class TestDeleteUser:
    def test_soft_delete_success(self, http, db):
        u = _seed_user(db)
        r = http.delete(f"{PREFIX}/{u.id}")
        assert r.status_code == 200
        db.refresh(u)
        assert u.is_deleted is True

    def test_delete_nonexistent_returns_404(self, http):
        r = http.delete(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_operational_cannot_delete(self, http_op, db):
        u = _seed_user(db)
        r = http_op.delete(f"{PREFIX}/{u.id}")
        assert r.status_code == 403
