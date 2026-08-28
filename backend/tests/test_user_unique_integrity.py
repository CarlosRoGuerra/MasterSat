from __future__ import annotations

from app.core.security import get_password_hash
from app.models.enums import UserRole
from app.models.user import User


PREFIX = "/api/v1/users"


def _user_payload(email: str, *, name: str = "Usuário Teste") -> dict[str, object]:
    return {
        "name": name,
        "email": email,
        "role": "operacional",
        "active": True,
        "password": "Senha@123",
    }


def _seed_user(db, *, email: str, is_deleted: bool = False) -> User:
    user = User(
        name="Usuário Existente",
        email=email,
        password_hash=get_password_hash("Senha@123"),
        role=UserRole.OPERATIONAL,
        active=True,
        is_deleted=is_deleted,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_active_email_duplicate_returns_specific_conflict_and_rolls_back(http, db):
    _seed_user(db, email="duplicado@test.local")

    duplicate = http.post(PREFIX + "/", json=_user_payload("duplicado@test.local"))

    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "E-mail já cadastrado"}

    after_conflict = http.post(PREFIX + "/", json=_user_payload("outro@test.local"))
    assert after_conflict.status_code == 200
    assert after_conflict.json()["email"] == "outro@test.local"


def test_update_normalizes_email_like_create(http, db):
    user = _seed_user(db, email="original@test.local")

    response = http.put(
        f"{PREFIX}/{user.id}",
        json={"email": "  Novo.Email@Example.COM  "},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "novo.email@example.com"

    login = http.post(
        "/api/v1/auth/login",
        json={"email": "novo.email@example.com", "password": "Senha@123"},
    )
    assert login.status_code == 200


def test_update_email_duplicate_returns_specific_conflict_and_rolls_back(http, db):
    user = _seed_user(db, email="primeiro@test.local")
    _seed_user(db, email="ocupado@test.local")

    duplicate = http.put(
        f"{PREFIX}/{user.id}",
        json={"name": "Não deve persistir", "email": "ocupado@test.local"},
    )

    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "E-mail já cadastrado"}

    after_conflict = http.put(
        f"{PREFIX}/{user.id}",
        json={"name": "Alteração posterior"},
    )
    assert after_conflict.status_code == 200
    assert after_conflict.json()["name"] == "Alteração posterior"
    assert after_conflict.json()["email"] == "primeiro@test.local"


def test_create_restores_soft_deleted_user_identity(http, db):
    deleted = _seed_user(
        db,
        email="restaurar@test.local",
        is_deleted=True,
    )

    response = http.post(
        PREFIX + "/",
        json=_user_payload(
            "  RESTAURAR@Test.Local  ",
            name="Usuário Restaurado",
        ),
    )

    assert response.status_code == 200
    assert response.json()["id"] == deleted.id
    assert response.json()["name"] == "Usuário Restaurado"
    assert response.json()["email"] == "restaurar@test.local"

    restored = http.get(f"{PREFIX}/{deleted.id}")
    assert restored.status_code == 200

    login = http.post(
        "/api/v1/auth/login",
        json={"email": "restaurar@test.local", "password": "Senha@123"},
    )
    assert login.status_code == 200


def test_client_document_can_be_reused_after_soft_delete(http, db, cliente):
    deleted_id = cliente.id
    cliente.is_deleted = True
    db.commit()

    response = http.post(
        "/api/v1/clients/",
        json={
            "name": "Novo Titular",
            "cpf_cnpj": cliente.cpf_cnpj,
            "type": "pf",
            "status": "ativo",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] != deleted_id
    assert response.json()["cpf_cnpj"] == cliente.cpf_cnpj


def test_vehicle_plate_can_be_reused_after_soft_delete(http, db, cliente, veiculo):
    deleted_id = veiculo.id
    veiculo.is_deleted = True
    db.commit()

    response = http.post(
        "/api/v1/vehicles/",
        json={
            "client_id": cliente.id,
            "plate": veiculo.plate,
            "chassis": "9BWZZZ377VT004999",
            "type": "passeio",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] != deleted_id
    assert response.json()["plate"] == veiculo.plate


def test_vehicle_chassis_can_be_reused_after_soft_delete(http, db, cliente, veiculo):
    deleted_id = veiculo.id
    veiculo.is_deleted = True
    db.commit()

    response = http.post(
        "/api/v1/vehicles/",
        json={
            "client_id": cliente.id,
            "plate": "NEW1A23",
            "chassis": veiculo.chassis,
            "type": "passeio",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] != deleted_id
    assert response.json()["chassis"] == veiculo.chassis


def test_tracker_imei_can_be_reused_after_soft_delete(http, db, rastreador):
    deleted_id = rastreador.id
    rastreador.is_deleted = True
    db.commit()

    response = http.post(
        "/api/v1/trackers/",
        json={
            "imei": rastreador.imei,
            "brand": "Teltonika",
            "model": "FMB920",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] != deleted_id
    assert response.json()["imei"] == rastreador.imei


def test_user_email_is_globally_case_insensitive(http, db):
    _seed_user(db, email="Legacy.User@Test.Local")

    response = http.post(
        PREFIX + "/",
        json=_user_payload("legacy.user@test.local"),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "E-mail já cadastrado"}
