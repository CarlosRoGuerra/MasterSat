"""Testes de integração para geração de documento da OS (PDF/DOCX),
versionamento, prioridade e o formato tipado do checklist."""
from __future__ import annotations

import io

import pytest
from pypdf import PdfReader
from docx import Document as DocxDocument

PREFIX = "/api/v1/service-orders"

_PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture()
def storage_dublê(monkeypatch):
    """Substitui o storage MinIO por um dicionário em memória — usado tanto
    para capturar o upload (`upload_bytes`) quanto para servir de volta o
    conteúdo quando `montar_dados_os` busca fotos/assinaturas já salvas
    (`get_object_stream`)."""
    objetos: dict[str, bytes] = {}

    def _fake_upload_bytes(object_name, content, content_type):
        objetos[object_name] = content
        return object_name

    class _FakeStream:
        def __init__(self, content: bytes):
            self._content = content

        def read(self):
            return self._content

        def close(self):
            pass

        def release_conn(self):
            pass

    def _fake_get_object_stream(object_name):
        return _FakeStream(objetos[object_name])

    monkeypatch.setattr("app.api.v1.endpoints.service_orders.upload_bytes", _fake_upload_bytes)
    monkeypatch.setattr("app.services.service_order_docx.get_object_stream", _fake_get_object_stream)
    return objetos


class TestGenerateDocument:
    def test_generate_pdf_default_format(self, http, ordem_servico, storage_dublê):
        r = http.post(f"{PREFIX}/{ordem_servico.id}/generate-document", json={"kind": "ordem_servico"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["content_type"] == "application/pdf"
        conteudo = next(iter(storage_dublê.values()))
        reader = PdfReader(io.BytesIO(conteudo))
        assert len(reader.pages) >= 1

    def test_generate_docx_format(self, http, ordem_servico, storage_dublê):
        r = http.post(f"{PREFIX}/{ordem_servico.id}/generate-document", json={
            "kind": "ordem_servico", "format": "docx",
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["content_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        conteudo = next(iter(storage_dublê.values()))
        doc = DocxDocument(io.BytesIO(conteudo))
        assert len(doc.paragraphs) > 0

    def test_generate_with_materials_checklist_and_signature(self, http, ordem_servico, storage_dublê):
        http.post(f"{PREFIX}/{ordem_servico.id}/materials", json={"description": "Cabo", "quantity": "2"})
        http.put(f"{PREFIX}/{ordem_servico.id}", json={
            "checklist": [{"description": "Testar GPS", "done": True, "notes": "ok"}],
            "problem_description": "Sem sinal",
        })
        http.post(f"{PREFIX}/{ordem_servico.id}/signature", json={"signer": "technician", "image_base64": _PNG_1X1_B64})
        r = http.post(f"{PREFIX}/{ordem_servico.id}/generate-document", json={"kind": "ordem_servico"})
        assert r.status_code == 200, r.text

    def test_historico_execucao_kind(self, http, ordem_servico, storage_dublê):
        http.post(f"{PREFIX}/{ordem_servico.id}/status", json={"status": "em_andamento", "notes": "Iniciado"})
        r = http.post(f"{PREFIX}/{ordem_servico.id}/generate-document", json={"kind": "historico_execucao"})
        assert r.status_code == 200, r.text

    def test_versioning_deactivates_previous_and_links_supersedes(self, http, ordem_servico, storage_dublê):
        first = http.post(f"{PREFIX}/{ordem_servico.id}/generate-document", json={"kind": "ordem_servico"}).json()
        second = http.post(f"{PREFIX}/{ordem_servico.id}/generate-document", json={"kind": "ordem_servico"}).json()

        active = http.get(f"{PREFIX}/{ordem_servico.id}/documents").json()
        assert [d["id"] for d in active if d["category"] == "ordem_servico"] == [second["id"]]

        all_docs = http.get(f"{PREFIX}/{ordem_servico.id}/documents", params={"include_inactive": "true"}).json()
        ids = {d["id"] for d in all_docs}
        assert first["id"] in ids
        assert second["id"] in ids

    def test_pdf_and_docx_of_same_kind_stay_both_active(self, http, ordem_servico, storage_dublê):
        """PDF e DOCX da mesma OS não são 'versões' um do outro — são dois
        formatos que o usuário quer manter disponíveis ao mesmo tempo (achado
        na validação manual: gerar o DOCX estava desativando o PDF gerado
        antes, porque o versionamento chaveava só por kind, não por formato)."""
        pdf_doc = http.post(f"{PREFIX}/{ordem_servico.id}/generate-document", json={"kind": "ordem_servico", "format": "pdf"}).json()
        docx_doc = http.post(f"{PREFIX}/{ordem_servico.id}/generate-document", json={"kind": "ordem_servico", "format": "docx"}).json()

        active = http.get(f"{PREFIX}/{ordem_servico.id}/documents").json()
        active_ids = {d["id"] for d in active}
        assert pdf_doc["id"] in active_ids
        assert docx_doc["id"] in active_ids

    def test_financial_cannot_generate(self, http_fin, ordem_servico):
        r = http_fin.post(f"{PREFIX}/{ordem_servico.id}/generate-document", json={"kind": "ordem_servico"})
        assert r.status_code == 403


class TestStreamGeneratedDocument:
    def test_stream_pdf(self, http, ordem_servico):
        r = http.get(f"{PREFIX}/{ordem_servico.id}/pdf/ordem_servico")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"

    def test_stream_docx(self, http, ordem_servico):
        r = http.get(f"{PREFIX}/{ordem_servico.id}/docx/ordem_servico")
        assert r.status_code == 200
        assert "wordprocessingml" in r.headers["content-type"]

    def test_stream_invalid_kind_returns_400(self, http, ordem_servico):
        assert http.get(f"{PREFIX}/{ordem_servico.id}/pdf/invalido").status_code == 400
        assert http.get(f"{PREFIX}/{ordem_servico.id}/docx/invalido").status_code == 400

    def test_stream_nonexistent_order_returns_404(self, http):
        assert http.get(f"{PREFIX}/99999/pdf/ordem_servico").status_code == 404


class TestPriority:
    def test_default_priority_is_normal(self, http, cliente):
        r = http.post(PREFIX + "/", json={"type": "instalacao", "client_id": cliente.id})
        assert r.status_code == 200
        assert r.json()["priority"] == "normal"

    def test_explicit_priority(self, http, cliente):
        r = http.post(PREFIX + "/", json={"type": "instalacao", "client_id": cliente.id, "priority": "urgente"})
        assert r.status_code == 200
        assert r.json()["priority"] == "urgente"

    def test_invalid_priority_returns_422(self, http, cliente):
        r = http.post(PREFIX + "/", json={"type": "instalacao", "client_id": cliente.id, "priority": "inexistente"})
        assert r.status_code == 422


class TestChecklistFormat:
    def test_typed_checklist_round_trip(self, http, ordem_servico):
        r = http.put(f"{PREFIX}/{ordem_servico.id}", json={
            "checklist": [
                {"description": "Testar GPS", "done": True, "notes": "ok"},
                {"description": "Testar bateria", "done": False},
            ],
        })
        assert r.status_code == 200, r.text
        checklist = r.json()["checklist"]
        assert checklist[0]["description"] == "Testar GPS"
        assert checklist[0]["done"] is True
        assert checklist[1]["done"] is False

    def test_legacy_dict_format_is_tolerated_on_read(self, http, db, ordem_servico):
        """Ordens já existentes antes desta mudança guardaram o checklist
        como {"items": ["texto", ...]} — a leitura precisa continuar
        funcionando sem exigir migração de dado."""
        ordem_servico.checklist = {"items": ["Item antigo em texto solto"]}
        db.commit()
        r = http.get(f"{PREFIX}/{ordem_servico.id}")
        assert r.status_code == 200, r.text
        checklist = r.json()["checklist"]
        assert checklist == [{"description": "Item antigo em texto solto", "done": False, "notes": None}]
