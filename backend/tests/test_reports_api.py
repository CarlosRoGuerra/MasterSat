"""
Testes de integração para /api/v1/reports.

Cobertos:
- GET /revenue           → relatório de receita mensal
- GET /delinquents       → relatório de inadimplentes
- GET /client-statement  → extrato individual (por client_id)
- GET /summary           → resumo executivo (contratos por plano, receita 6 meses)
- Autorização → OPERATIONAL pode ver, CLIENT → 403
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from app.models.billing import Billing
from app.models.enums import BillingStatus

PREFIX = "/api/v1/reports"


def _make_paid_billing(db, contrato, amount=150.0):
    b = Billing(
        contract_id=contrato.id,
        client_id=contrato.client_id,
        amount=Decimal(str(amount)),
        due_date=date.today(),
        payment_date=date.today(),
        status=BillingStatus.PAID,
        paid_amount=Decimal(str(amount)),
        billing_type="recorrente",
        period_label=date.today().strftime("%m/%Y"),
        title="Pago",
    )
    db.add(b)
    db.commit()
    return b


class TestRevenueReport:
    """A API devolve {ano, periodo, meses[], totais{}} em portugues. Os testes
    antigos ainda descreviam um contrato que nao existe mais (lista crua e
    chaves em ingles), entao passavam a impressao de cobertura sem exercitar
    nada."""

    def _billing(self, db, contrato, *, amount, due, status, paid=None):
        b = Billing(
            contract_id=contrato.id,
            client_id=contrato.client_id,
            amount=Decimal(str(amount)),
            due_date=due,
            payment_date=due if status == BillingStatus.PAID else None,
            paid_amount=Decimal(str(paid if paid is not None else amount)) if status == BillingStatus.PAID else None,
            status=status,
            billing_type="recorrente",
            period_label=due.strftime("%m/%Y"),
            title="Cobranca",
        )
        db.add(b)
        db.commit()
        return b

    def test_returns_expected_structure(self, http, db, contrato):
        self._billing(db, contrato, amount=150, due=date(2025, 3, 10), status=BillingStatus.PAID)
        r = http.get(PREFIX + "/revenue", params={"year": 2025})
        assert r.status_code == 200
        body = r.json()
        assert "meses" in body and "totais" in body and "periodo" in body
        assert body["totais"]["total_emitido"] == pytest.approx(150.0)
        assert body["totais"]["total_recebido"] == pytest.approx(150.0)

    def test_canceladas_nao_entram_no_emitido(self, http, db, contrato):
        """Regressao: canceladas somavam no total emitido. Como a consolidacao
        em boleto unico CANCELA as cobrancas originais, cada cliente com boleto
        unico era contado duas vezes — a receita aparecia inflada."""
        self._billing(db, contrato, amount=100, due=date(2025, 3, 10), status=BillingStatus.PENDING)
        self._billing(db, contrato, amount=999, due=date(2025, 3, 15), status=BillingStatus.CANCELED)

        r = http.get(PREFIX + "/revenue", params={"year": 2025})
        totais = r.json()["totais"]
        assert totais["total_emitido"] == pytest.approx(100.0)
        assert totais["total_aberto"] == pytest.approx(100.0)

    def test_cancelada_nao_distorce_taxa_de_recebimento(self, http, db, contrato):
        self._billing(db, contrato, amount=100, due=date(2025, 3, 10), status=BillingStatus.PAID)
        self._billing(db, contrato, amount=900, due=date(2025, 3, 11), status=BillingStatus.CANCELED)

        totais = http.get(PREFIX + "/revenue", params={"year": 2025}).json()["totais"]
        # 100 de 100 recebidos = 100%. Com a cancelada no denominador dava 10%.
        assert totais["taxa_recebimento"] == pytest.approx(100.0)

    def test_intervalo_entre_anos_traz_os_dois_anos(self, http, db, contrato):
        """Regressao: so o ano da data inicial era consultado, entao um periodo
        dez/2025 -> jan/2026 perdia janeiro inteiro."""
        self._billing(db, contrato, amount=100, due=date(2025, 12, 20), status=BillingStatus.PENDING)
        self._billing(db, contrato, amount=200, due=date(2026, 1, 20), status=BillingStatus.PENDING)

        r = http.get(PREFIX + "/revenue", params={"date_from": "2025-12-01", "date_to": "2026-01-31"})
        assert r.status_code == 200
        body = r.json()
        anos = {m["ano"] for m in body["meses"]}
        assert anos == {2025, 2026}
        assert body["totais"]["total_emitido"] == pytest.approx(300.0)

    def test_intervalo_respeita_os_limites(self, http, db, contrato):
        self._billing(db, contrato, amount=100, due=date(2025, 1, 15), status=BillingStatus.PENDING)
        self._billing(db, contrato, amount=500, due=date(2025, 6, 15), status=BillingStatus.PENDING)

        body = http.get(
            PREFIX + "/revenue", params={"date_from": "2025-06-01", "date_to": "2025-06-30"},
        ).json()
        assert body["totais"]["total_emitido"] == pytest.approx(500.0)

    def test_date_from_posterior_a_date_to_e_rejeitado(self, http):
        r = http.get(PREFIX + "/revenue", params={"date_from": "2025-06-01", "date_to": "2025-01-01"})
        assert r.status_code == 422

    def test_sem_parametros_usa_o_ano_corrente(self, http):
        r = http.get(PREFIX + "/revenue")
        assert r.status_code == 200
        assert r.json()["periodo"]["de"] == f"{date.today().year}-01-01"

    def test_operational_nao_acessa_relatorio_financeiro(self, http_op):
        # VIEW_ROLES = (ADMIN, FINANCIAL): dado financeiro nao e do operacional.
        r = http_op.get(PREFIX + "/revenue")
        assert r.status_code == 403

    def test_client_role_cannot_access(self, http_cliente):
        r = http_cliente.get(PREFIX + "/revenue")
        assert r.status_code == 403

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.get(PREFIX + "/revenue")
        assert r.status_code == 401


class TestDelinquentsReport:
    def test_returns_list(self, http):
        r = http.get(PREFIX + "/delinquents")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_includes_delinquent_client(self, http, db, contrato):
        b = Billing(
            contract_id=contrato.id,
            client_id=contrato.client_id,
            amount=Decimal("200.00"),
            due_date=date(2020, 1, 1),
            status=BillingStatus.OVERDUE,
            billing_type="recorrente",
            period_label="01/2020",
            title="Vencida",
        )
        db.add(b)
        db.commit()
        r = http.get(PREFIX + "/delinquents")
        assert r.status_code == 200
        assert any(x["client_id"] == contrato.client_id for x in r.json())

    def test_structure(self, http, db, contrato):
        b = Billing(
            contract_id=contrato.id,
            client_id=contrato.client_id,
            amount=Decimal("200.00"),
            due_date=date(2020, 1, 1),
            status=BillingStatus.OVERDUE,
            billing_type="recorrente",
            period_label="01/2020",
            title="Vencida",
        )
        db.add(b)
        db.commit()
        r = http.get(PREFIX + "/delinquents")
        if r.json():
            item = r.json()[0]
            assert "client_id" in item
            assert "client_name" in item
            assert "total_open" in item
            assert "overdue_count" in item


class TestClientStatement:
    def test_returns_statement_for_existing_client(self, http, db, contrato):
        _make_paid_billing(db, contrato)
        r = http.get(f"{PREFIX}/client-statement/{contrato.client_id}")
        assert r.status_code == 200

    def test_nonexistent_client_returns_404(self, http):
        r = http.get(f"{PREFIX}/client-statement/99999")
        assert r.status_code == 404

    def test_operational_can_access(self, http_op, db, contrato):
        r = http_op.get(f"{PREFIX}/client-statement/{contrato.client_id}")
        assert r.status_code == 200


class TestExecutiveSummary:
    def test_returns_200(self, http):
        r = http.get(PREFIX + "/summary")
        assert r.status_code == 200

    def test_response_has_structure(self, http):
        r = http.get(PREFIX + "/summary")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)

    def test_contracts_by_plan_included(self, http, contrato):
        r = http.get(PREFIX + "/summary")
        assert r.status_code == 200
        data = r.json()
        assert "contracts_by_plan" in data

    def test_financial_can_access(self, http_fin):
        r = http_fin.get(PREFIX + "/summary")
        assert r.status_code == 200

    def test_client_role_cannot_access(self, http_cliente):
        r = http_cliente.get(PREFIX + "/summary")
        assert r.status_code == 403
