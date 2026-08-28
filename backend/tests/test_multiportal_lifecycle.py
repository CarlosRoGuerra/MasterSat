"""
Testes de commit_with_compensation (BE-01).

Extraído de trackers.py/vehicles.py: o bloco try/commit/rollback/compensa/
recomita era idêntico nos dois endpoints. Nenhum teste de API exercitava o
caminho de falha de commit (exige o banco recusar o commit, difícil de
simular via HTTP) — por isso a cobertura entra aqui, na unidade extraída.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.multiportal_lifecycle import LifecycleCall, commit_with_compensation
from app.services.multiportal import CallResult


def _call(entity_type='tracker', entity_id=1, phase='link_equipment', success=True) -> LifecycleCall:
    return LifecycleCall(
        result=CallResult(
            operation='op', transaction_id=None, status_code='0',
            status_description=None, success=success, response_payload=None,
        ),
        entity_type=entity_type,
        entity_id=entity_id,
        phase=phase,
    )


class _FailingCommitSession:
    """Encapsula uma Session real, mas faz o Nº commit falhar sob controle."""
    def __init__(self, real_session, fail_on_calls: set[int]):
        self._real = real_session
        self._fail_on_calls = fail_on_calls
        self._commit_count = 0

    def commit(self):
        self._commit_count += 1
        if self._commit_count in self._fail_on_calls:
            raise RuntimeError('commit falhou (simulado)')
        return self._real.commit()

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_commit_success_does_not_touch_compensation(db):
    fake_db = _FailingCommitSession(db, fail_on_calls=set())
    ran_compensation = []

    commit_with_compensation(
        fake_db,
        lifecycle_calls=[_call()],
        should_compensate=True,
        run_compensation=lambda: ran_compensation.append(1) or ([], False),
    )

    assert ran_compensation == []


def test_commit_fails_without_compensation_reraises_original_error(db):
    fake_db = _FailingCommitSession(db, fail_on_calls={1})

    with pytest.raises(RuntimeError, match='commit falhou'):
        commit_with_compensation(
            fake_db,
            lifecycle_calls=[_call()],
            should_compensate=False,
            run_compensation=lambda: (_ for _ in ()).throw(AssertionError('não deveria compensar')),
        )


def test_commit_fails_compensation_succeeds_reraises_original_error(db):
    """Comportamento atual (preservado da extração): se a compensação externa
    funciona, o código ainda repropaga o erro original do commit — não vira
    um 200 nem um 500 estruturado. Só compensação FALHA vira HTTPException."""
    fake_db = _FailingCommitSession(db, fail_on_calls={1})

    with pytest.raises(RuntimeError, match='commit falhou'):
        commit_with_compensation(
            fake_db,
            lifecycle_calls=[_call()],
            should_compensate=True,
            run_compensation=lambda: ([_call(phase='compensate')], False),
        )


def test_commit_fails_and_compensation_reports_failure_raises_structured_500(db):
    fake_db = _FailingCommitSession(db, fail_on_calls={1})

    with pytest.raises(HTTPException) as exc_info:
        commit_with_compensation(
            fake_db,
            lifecycle_calls=[_call()],
            should_compensate=True,
            run_compensation=lambda: ([_call(phase='compensate')], True),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail['code'] == 'multiportal_compensation_failed'
    assert exc_info.value.detail['reconciliation_required'] is True


def test_commit_fails_and_compensation_raises_raises_structured_500(db):
    fake_db = _FailingCommitSession(db, fail_on_calls={1})

    def _boom():
        raise ConnectionError('Multiportal indisponível')

    with pytest.raises(HTTPException) as exc_info:
        commit_with_compensation(
            fake_db,
            lifecycle_calls=[_call()],
            should_compensate=True,
            run_compensation=_boom,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail['code'] == 'multiportal_compensation_failed'
