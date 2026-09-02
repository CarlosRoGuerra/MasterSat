"""Testes para GET /documents/{id}/view — em especial a distinção entre
documento EXCLUÍDO (continua escondido) e documento SUBSTITUÍDO por uma
versão mais nova via o versionamento da OS (deve continuar acessível).

`view_document` abre sua PRÓPRIA sessão via `SessionLocal` importado direto
no módulo (`from app.db.session import SessionLocal`) em vez de usar
`Depends(get_db)` — o monkeypatch de `app.db.session.SessionLocal` que o
fixture `db` já faz não alcança esse nome (foi importado por valor, não por
referência ao módulo). Por isso repatcheamos o nome local do próprio módulo
`documents`, apontando pro mesmo engine :memory: da sessão do teste.
"""
from __future__ import annotations

import pytest

from app.core.security import create_file_access_token
from app.models.document import Document

PREFIX = "/api/v1/documents"


@pytest.fixture(autouse=True)
def _patch_documents_session_local(db, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr("app.api.v1.endpoints.documents.SessionLocal", Session)


def _make_document(db, *, active: bool, supersedes_document_id: int | None = None) -> Document:
    doc = Document(
        file_name="arquivo.pdf",
        object_key=f"service-orders/1/generated/{id(object())}-arquivo.pdf",
        content_type="application/pdf",
        size_bytes=10,
        reference_type="service_order",
        reference_id=1,
        category="ordem_servico",
        active=active,
        supersedes_document_id=supersedes_document_id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


class TestViewDocument:
    def test_active_document_is_viewable(self, http, db, monkeypatch):
        monkeypatch.setattr(
            "app.api.v1.endpoints.documents.get_object_stream",
            lambda object_name: __import__("io").BytesIO(b"%PDF-conteudo"),
        )
        doc = _make_document(db, active=True)
        token = create_file_access_token(doc.id)
        r = http.get(f"{PREFIX}/{doc.id}/view", params={"token": token})
        assert r.status_code == 200

    def test_deleted_document_returns_404(self, http, db):
        """active=False sem ninguém referenciando via supersedes = exclusão
        de verdade — continua escondido, como sempre foi."""
        doc = _make_document(db, active=False)
        token = create_file_access_token(doc.id)
        r = http.get(f"{PREFIX}/{doc.id}/view", params={"token": token})
        assert r.status_code == 404

    def test_superseded_document_remains_viewable(self, http, db, monkeypatch):
        """active=False MAS é a versão anterior de um documento mais novo
        (versionamento da OS) — precisa continuar acessível, é a estratégia
        de reimpressão: histórico completo, não só a última versão."""
        monkeypatch.setattr(
            "app.api.v1.endpoints.documents.get_object_stream",
            lambda object_name: __import__("io").BytesIO(b"%PDF-versao-antiga"),
        )
        antigo = _make_document(db, active=False)
        _make_document(db, active=True, supersedes_document_id=antigo.id)

        token = create_file_access_token(antigo.id)
        r = http.get(f"{PREFIX}/{antigo.id}/view", params={"token": token})
        assert r.status_code == 200

    def test_invalid_token_returns_401(self, http, db):
        doc = _make_document(db, active=True)
        r = http.get(f"{PREFIX}/{doc.id}/view", params={"token": "token-invalido"})
        assert r.status_code == 401

    def test_token_for_different_document_returns_401(self, http, db):
        doc1 = _make_document(db, active=True)
        doc2 = _make_document(db, active=True)
        token = create_file_access_token(doc1.id)
        r = http.get(f"{PREFIX}/{doc2.id}/view", params={"token": token})
        assert r.status_code == 401
