from __future__ import annotations


def test_cpf_cnpj_can_be_reused_after_client_soft_delete(http, db, cliente):
    cliente.is_deleted = True
    db.commit()

    response = http.post(
        "/api/v1/clients/",
        json={
            "name": "Novo cadastro do mesmo documento",
            "cpf_cnpj": cliente.cpf_cnpj,
            "type": "pf",
            "status": "ativo",
        },
    )

    assert response.status_code == 200, response.json()
    assert response.json()["id"] != cliente.id
    assert response.json()["cpf_cnpj"] == cliente.cpf_cnpj


def test_plate_and_chassis_can_be_reused_after_vehicle_soft_delete(
    http, db, cliente, veiculo,
):
    veiculo.is_deleted = True
    db.commit()

    response = http.post(
        "/api/v1/vehicles/",
        json={
            "client_id": cliente.id,
            "plate": veiculo.plate,
            "chassis": veiculo.chassis,
            "type": "passeio",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] != veiculo.id
    assert response.json()["plate"] == veiculo.plate
    assert response.json()["chassis"] == veiculo.chassis


def test_imei_can_be_reused_after_tracker_soft_delete(http, db, rastreador):
    rastreador.is_deleted = True
    db.commit()

    response = http.post(
        "/api/v1/trackers/",
        json={"imei": rastreador.imei, "brand": "Reentrada", "model": "R1"},
    )

    assert response.status_code == 200, response.json()
    assert response.json()["id"] != rastreador.id
    assert response.json()["imei"] == rastreador.imei
