"""
Testes unitários para app/core/integrity.py — o tradutor central de
IntegrityError usado pelos endpoints que fazem INSERT/UPDATE em colunas com
UNIQUE (clients.cpf_cnpj, vehicles.plate/chassis, trackers.imei,
users.email, plans.name, service_products.name, service_orders.number,
system_settings.key).

Cobertos:
- resolução do nome da constraint via diag.constraint_name (Postgres) e via
  assinatura de mensagem (SQLite, que não expõe nome de constraint);
- tradução para 409 com mensagem de domínio só quando a constraint está
  mapeada — qualquer coisa fora do mapa sobe intacta (não vira 500 genérico
  fabricado por este módulo; quem decide o corpo da resposta 500 é o
  handler padrão do FastAPI, fora daqui);
- rollback sempre acontece antes de decidir o que fazer com a exceção;
- logging: mensagem detalhada (podendo conter o valor que colidiu) só no
  servidor — nunca no HTTPException devolvido ao cliente.
"""
from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.core.integrity import integrity_conflict_detail, raise_integrity_conflict


class _FakeDiag:
    def __init__(self, constraint_name):
        self.constraint_name = constraint_name


class _FakeOrig:
    """Substitui psycopg2/sqlite3's driver exception sem precisar de um banco
    real — só o que integrity.py de fato lê: `.diag.constraint_name` (Postgres)
    e `str(orig)` (mensagem crua, usada no fallback do SQLite)."""

    def __init__(self, message, *, constraint_name=None):
        self._message = message
        self.diag = _FakeDiag(constraint_name) if constraint_name is not None else None

    def __str__(self):
        return self._message


def _make_integrity_error(message, *, constraint_name=None) -> IntegrityError:
    orig = _FakeOrig(message, constraint_name=constraint_name)
    return IntegrityError("INSERT ...", {}, orig)


class _FakeSession:
    def __init__(self):
        self.rolled_back = False

    def rollback(self):
        self.rolled_back = True


MESSAGES = {"uq_clients_cpf_cnpj_active": "Já existe cliente com este CPF/CNPJ"}
SQLITE_COLUMNS = {"UNIQUE constraint failed: clients.cpf_cnpj": "uq_clients_cpf_cnpj_active"}


class TestIntegrityConflictDetail:
    def test_postgres_constraint_name_mapped(self):
        exc = _make_integrity_error(
            'duplicate key value violates unique constraint "uq_clients_cpf_cnpj_active"\n'
            "DETAIL:  Key (cpf_cnpj)=(12345678901) already exists.",
            constraint_name="uq_clients_cpf_cnpj_active",
        )
        assert integrity_conflict_detail(exc, MESSAGES) == "Já existe cliente com este CPF/CNPJ"

    def test_postgres_constraint_name_not_mapped_returns_none(self):
        exc = _make_integrity_error(
            "violates foreign key constraint",
            constraint_name="contracts_client_id_fkey",
        )
        assert integrity_conflict_detail(exc, MESSAGES) is None

    def test_sqlite_signature_fallback_when_no_diag(self):
        exc = _make_integrity_error("UNIQUE constraint failed: clients.cpf_cnpj")
        assert (
            integrity_conflict_detail(exc, MESSAGES, sqlite_columns=SQLITE_COLUMNS)
            == "Já existe cliente com este CPF/CNPJ"
        )

    def test_sqlite_unmapped_signature_returns_none(self):
        exc = _make_integrity_error("UNIQUE constraint failed: clients.email")
        assert integrity_conflict_detail(exc, MESSAGES, sqlite_columns=SQLITE_COLUMNS) is None

    def test_no_sqlite_columns_and_no_diag_returns_none(self):
        exc = _make_integrity_error("NOT NULL constraint failed: clients.name")
        assert integrity_conflict_detail(exc, MESSAGES) is None


class TestRaiseIntegrityConflict:
    def test_mapped_constraint_rolls_back_and_raises_409_without_leaking_db_message(self):
        db = _FakeSession()
        exc = _make_integrity_error(
            'duplicate key value violates unique constraint "uq_clients_cpf_cnpj_active"\n'
            "DETAIL:  Key (cpf_cnpj)=(12345678901) already exists.",
            constraint_name="uq_clients_cpf_cnpj_active",
        )

        with pytest.raises(HTTPException) as exc_info:
            raise_integrity_conflict(db, exc, MESSAGES)

        assert db.rolled_back is True
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "Já existe cliente com este CPF/CNPJ"
        # A mensagem devolvida ao cliente é só a de domínio — nunca o texto
        # cru do driver (que aqui incluiria o CPF/CNPJ que colidiu).
        assert "12345678901" not in str(exc_info.value.detail)
        assert "constraint" not in str(exc_info.value.detail).lower()

    def test_unmapped_constraint_rolls_back_and_reraises_original_exception(self):
        db = _FakeSession()
        exc = _make_integrity_error(
            "violates foreign key constraint",
            constraint_name="contracts_client_id_fkey",
        )

        with pytest.raises(IntegrityError) as exc_info:
            raise_integrity_conflict(db, exc, MESSAGES)

        assert db.rolled_back is True
        assert exc_info.value is exc

    def test_rollback_happens_even_when_unmapped(self):
        """Requisito de produção: a sessão nunca pode ficar numa transação
        quebrada, mesmo quando o erro não tem tradução de domínio e vai
        virar 500."""
        db = _FakeSession()
        exc = _make_integrity_error("check constraint failed")
        with pytest.raises(IntegrityError):
            raise_integrity_conflict(db, exc, {})
        assert db.rolled_back is True

    def test_mapped_conflict_logs_at_warning_with_db_detail_server_side(self, caplog):
        db = _FakeSession()
        exc = _make_integrity_error(
            'duplicate key value violates unique constraint "uq_clients_cpf_cnpj_active"\n'
            "DETAIL:  Key (cpf_cnpj)=(12345678901) already exists.",
            constraint_name="uq_clients_cpf_cnpj_active",
        )
        with caplog.at_level(logging.WARNING, logger="app.core.integrity"):
            with pytest.raises(HTTPException):
                raise_integrity_conflict(db, exc, MESSAGES)

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelno == logging.WARNING
        # O log do servidor PODE conter o valor que colidiu — é aí que a
        # equipe de operação vai olhar para diagnosticar, o cliente nunca vê.
        assert "12345678901" in record.getMessage()
        assert "uq_clients_cpf_cnpj_active" in record.getMessage()

    def test_unmapped_conflict_logs_at_error_with_traceback(self, caplog):
        db = _FakeSession()
        exc = _make_integrity_error("violates foreign key constraint", constraint_name="some_fkey")
        with caplog.at_level(logging.WARNING, logger="app.core.integrity"):
            with pytest.raises(IntegrityError):
                raise_integrity_conflict(db, exc, MESSAGES)

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelno == logging.ERROR
        assert record.exc_info is not None
        assert "some_fkey" in record.getMessage()
