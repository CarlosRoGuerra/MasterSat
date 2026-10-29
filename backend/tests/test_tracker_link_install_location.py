"""Local de instalação salvo na mesma operação do vínculo do equipamento."""
import pytest


@pytest.mark.parametrize('location,expected', [
    ('  Atrás do painel, lado do motorista  ', 'Atrás do painel, lado do motorista'),
    ('   ', None),
    (None, None),
])
def test_saves_install_location_while_linking(http, db, rastreador, veiculo, location, expected):
    response = http.post(f'/api/v1/trackers/{rastreador.id}/link-vehicle', json={
        'vehicle_id': veiculo.id, 'install_location': location,
    })
    assert response.status_code == 200, response.text
    assert response.json()['tracker']['install_location'] == expected
    db.refresh(rastreador)
    assert rastreador.install_location == expected
    assert rastreador.vehicle_id == veiculo.id
    assert http.get(f'/api/v1/trackers/{rastreador.id}').json()['install_location'] == expected


def test_omitted_location_preserves_existing_value(http, db, rastreador, veiculo):
    rastreador.install_location = 'Porta-luvas'
    db.commit()
    response = http.post(f'/api/v1/trackers/{rastreador.id}/link-vehicle', json={'vehicle_id': veiculo.id})
    assert response.status_code == 200, response.text
    db.refresh(rastreador)
    assert rastreador.install_location == 'Porta-luvas'


def test_location_limit_rejects_before_linking(http, db, rastreador, veiculo):
    response = http.post(f'/api/v1/trackers/{rastreador.id}/link-vehicle', json={
        'vehicle_id': veiculo.id, 'install_location': 'x' * 121,
    })
    assert response.status_code == 422
    db.refresh(rastreador)
    assert rastreador.vehicle_id is None
    assert rastreador.install_location is None


def test_failed_link_does_not_save_location(http, db, rastreador):
    response = http.post(f'/api/v1/trackers/{rastreador.id}/link-vehicle', json={
        'vehicle_id': 999999, 'install_location': 'Painel',
    })
    assert response.status_code == 404
    db.refresh(rastreador)
    assert rastreador.install_location is None


def test_operational_can_set_location_during_link(http_op, db, rastreador, veiculo):
    response = http_op.post(f'/api/v1/trackers/{rastreador.id}/link-vehicle', json={
        'vehicle_id': veiculo.id, 'install_location': 'Painel',
    })
    assert response.status_code == 200, response.text
    db.refresh(rastreador)
    assert rastreador.install_location == 'Painel'
