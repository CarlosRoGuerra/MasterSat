"""
Testes do worker de reclassificação pendente<->vencida (BE-05).

O laço em si (main._overdue_status_refresh_worker) não é testado diretamente
porque depende de pg_advisory_lock (Postgres-only, sem equivalente no SQLite
usado nos testes) — mesma razão pela qual os demais workers de main.py
(_ailos_log_retention_worker, _audit_log_retention_worker, etc.) também não
têm teste de laço. O que importa validar é o trabalho em si
(_refresh_overdue_status_job), que é uma função nomeada isolada exatamente
para isso.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.main import _refresh_overdue_status_job
from app.models.billing import Billing
from app.models.enums import BillingStatus


def test_job_marks_stale_pending_as_overdue(db, contrato):
    stale = Billing(
        contract_id=contrato.id,
        client_id=contrato.client_id,
        amount=Decimal("50.00"),
        due_date=date(2020, 1, 1),
        status=BillingStatus.PENDING,
        billing_type="recorrente",
        period_label="01/2020",
        title="Pendente vencida (stale)",
    )
    db.add(stale)
    db.commit()

    _refresh_overdue_status_job(db)

    db.refresh(stale)
    assert stale.status == BillingStatus.OVERDUE


def test_job_reverts_overdue_back_to_pending_if_due_date_in_future(db, contrato):
    # Cobrança vencida cujo vencimento foi ajustado pra frente (ex.: renegociação)
    # sem que o status tenha sido corrigido junto — o job também cobre esse sentido.
    billing = Billing(
        contract_id=contrato.id,
        client_id=contrato.client_id,
        amount=Decimal("50.00"),
        due_date=date(2099, 1, 1),
        status=BillingStatus.OVERDUE,
        billing_type="recorrente",
        period_label="01/2099",
        title="Vencida com novo prazo",
    )
    db.add(billing)
    db.commit()

    _refresh_overdue_status_job(db)

    db.refresh(billing)
    assert billing.status == BillingStatus.PENDING


def test_job_commits_so_a_fresh_query_sees_the_change(db, contrato):
    stale = Billing(
        contract_id=contrato.id,
        client_id=contrato.client_id,
        amount=Decimal("50.00"),
        due_date=date(2020, 1, 1),
        status=BillingStatus.PENDING,
        billing_type="recorrente",
        period_label="01/2020",
        title="Pendente vencida (stale)",
    )
    db.add(stale)
    db.commit()
    stale_id = stale.id

    _refresh_overdue_status_job(db)
    db.expire_all()  # força reler do banco, não do cache de identidade da sessão

    reloaded = db.get(Billing, stale_id)
    assert reloaded.status == BillingStatus.OVERDUE
