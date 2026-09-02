"""Testes de integração para /api/v1/service-orders/{id}/signature e para o
gate de conclusão (execution_description + as duas assinaturas)."""
from __future__ import annotations

import pytest

from app.models.enums import OrderStatus

PREFIX = "/api/v1/service-orders"

# PNG 1x1 transparente válido, gerado uma vez e fixado aqui — só precisa
# passar pela checagem de magic bytes do endpoint, não representar nada.
_PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture()
def capturado_upload(monkeypatch):
    chamadas: list[tuple[str, bytes, str]] = []

    def _fake_upload_bytes(object_name, content, content_type):
        chamadas.append((object_name, content, content_type))
        return object_name

    monkeypatch.setattr(
        "app.api.v1.endpoints.service_orders.upload_bytes", _fake_upload_bytes
    )
    return chamadas


class TestUploadSignature:
    def test_technician_signature_sets_fields(self, http, ordem_servico, capturado_upload):
        r = http.post(f"{PREFIX}/{ordem_servico.id}/signature", json={
            "signer": "technician",
            "image_base64": _PNG_1X1_B64,
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["technician_signed_at"] is not None
        assert data["client_signed_at"] is None
        assert len(capturado_upload) == 1
        object_key, content, content_type = capturado_upload[0]
        assert object_key.startswith(f"service-orders/{ordem_servico.id}/signatures/")
        assert content_type == "image/png"

    def test_client_signature_sets_fields(self, http, ordem_servico, capturado_upload):
        r = http.post(f"{PREFIX}/{ordem_servico.id}/signature", json={
            "signer": "client",
            "image_base64": _PNG_1X1_B64,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["client_signed_at"] is not None
        assert data["technician_signed_at"] is None

    def test_accepts_data_url_prefix(self, http, ordem_servico, capturado_upload):
        r = http.post(f"{PREFIX}/{ordem_servico.id}/signature", json={
            "signer": "technician",
            "image_base64": f"data:image/png;base64,{_PNG_1X1_B64}",
        })
        assert r.status_code == 200, r.text

    def test_invalid_base64_returns_400(self, http, ordem_servico):
        r = http.post(f"{PREFIX}/{ordem_servico.id}/signature", json={
            "signer": "technician",
            "image_base64": "isto-nao-eh-base64-valido!!!",
        })
        assert r.status_code == 400

    def test_non_png_content_returns_415(self, http, ordem_servico):
        import base64
        fake = base64.b64encode(b"nao e um png").decode()
        r = http.post(f"{PREFIX}/{ordem_servico.id}/signature", json={
            "signer": "technician",
            "image_base64": fake,
        })
        assert r.status_code == 415

    def test_nonexistent_order_returns_404(self, http):
        r = http.post(f"{PREFIX}/99999/signature", json={
            "signer": "technician", "image_base64": _PNG_1X1_B64,
        })
        assert r.status_code == 404

    def test_second_signature_replaces_document_reference(self, http, ordem_servico, capturado_upload):
        first = http.post(f"{PREFIX}/{ordem_servico.id}/signature", json={
            "signer": "technician", "image_base64": _PNG_1X1_B64,
        }).json()
        second = http.post(f"{PREFIX}/{ordem_servico.id}/signature", json={
            "signer": "technician", "image_base64": _PNG_1X1_B64,
        }).json()
        assert first["technician_signed_at"] != second["technician_signed_at"]


class TestCompletionGate:
    def test_blocked_without_any_requirement(self, http, db, ordem_servico):
        ordem_servico.status = OrderStatus.IN_PROGRESS
        db.commit()
        r = http.post(f"{PREFIX}/{ordem_servico.id}/status", json={"status": "concluida"})
        assert r.status_code == 422

    def test_blocked_with_only_execution_description(self, http, db, ordem_servico):
        ordem_servico.status = OrderStatus.IN_PROGRESS
        ordem_servico.execution_description = "Feito."
        db.commit()
        r = http.post(f"{PREFIX}/{ordem_servico.id}/status", json={"status": "concluida"})
        assert r.status_code == 422
        assert "assinatura do técnico" in r.json()["detail"]
        assert "assinatura do cliente" in r.json()["detail"]

    def test_blocked_with_only_technician_signature(self, http, ordem_servico, capturado_upload):
        http.post(f"{PREFIX}/{ordem_servico.id}/signature", json={
            "signer": "technician", "image_base64": _PNG_1X1_B64,
        })
        r = http.post(f"{PREFIX}/{ordem_servico.id}/status", json={"status": "concluida"})
        assert r.status_code == 422
        assert "descrição do serviço executado" in r.json()["detail"]
        assert "assinatura do cliente" in r.json()["detail"]

    def test_completes_once_all_three_present(self, http, db, ordem_servico, capturado_upload):
        http.put(f"{PREFIX}/{ordem_servico.id}", json={"execution_description": "Serviço concluído em campo."})
        http.post(f"{PREFIX}/{ordem_servico.id}/signature", json={"signer": "technician", "image_base64": _PNG_1X1_B64})
        http.post(f"{PREFIX}/{ordem_servico.id}/signature", json={"signer": "client", "image_base64": _PNG_1X1_B64})
        r = http.post(f"{PREFIX}/{ordem_servico.id}/status", json={"status": "concluida"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "concluida"
        assert r.json()["executed_at"] is not None
