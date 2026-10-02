"""Toda rota HTTP informa o tempo gasto até iniciar a resposta."""

import re


def test_dashboard_exposes_server_timing(http):
    response = http.get('/api/v1/dashboard/')

    assert response.status_code == 200
    assert re.fullmatch(r'app;dur=\d+\.\d;desc="queries=\d+"', response.headers['server-timing'])
