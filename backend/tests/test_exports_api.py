"""Testes das exportações (foco no PDF de inadimplentes)."""
from __future__ import annotations

from datetime import date

from app.models.enums import BillingStatus

PREFIX = '/api/v1/exports'


def test_inadimplentes_pdf(http, db, cliente, billing_vencida):
    # billing_vencida (2020) é OVERDUE → cliente aparece no relatório
    r = http.get(f'{PREFIX}/delinquents', params={'fmt': 'pdf'})
    assert r.status_code == 200
    assert r.headers['content-type'] == 'application/pdf'
    assert r.content[:4] == b'%PDF'


def test_inadimplentes_csv_ainda_funciona(http, billing_vencida):
    r = http.get(f'{PREFIX}/delinquents', params={'fmt': 'csv'})
    assert r.status_code == 200
    assert 'text/csv' in r.headers['content-type']


def test_formato_invalido_422(http):
    r = http.get(f'{PREFIX}/delinquents', params={'fmt': 'docx'})
    assert r.status_code == 422
