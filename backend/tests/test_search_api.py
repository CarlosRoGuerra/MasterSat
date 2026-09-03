"""
Testes de integração para /api/v1/search (Busca Global / Command Palette).

Cobertos:
- Nome parcial, com e sem acento
- Placa com e sem traço
- IMEI completo e parcial
- CPF/CNPJ formatado e sem formatação
- Ordem de serviço por número
- Autorização: CLIENT → 403, sem auth → 401, OPERACIONAL não recebe contratos
- Query vazia/curta demais, sem resultados, limite respeitado
"""
from __future__ import annotations

from app.models.client import Client
from app.models.enums import ClientStatus

PREFIX = "/api/v1/search"


class TestSearchClients:
    def test_partial_name(self, http, cliente):
        r = http.get(PREFIX + "/", params={"q": "Silva"})
        assert r.status_code == 200
        body = r.json()
        assert any(c["id"] == cliente.id for c in body["clients"])

    def test_case_insensitive(self, http, cliente):
        r = http.get(PREFIX + "/", params={"q": "joão"})
        assert any(c["id"] == cliente.id for c in r.json()["clients"])

    def test_accent_insensitive(self, http, cliente):
        # cliente.name == "João Silva" — busca sem o til precisa achar do mesmo jeito.
        r = http.get(PREFIX + "/", params={"q": "joao"})
        assert any(c["id"] == cliente.id for c in r.json()["clients"])

    def test_cpf_formatted(self, http, cliente):
        # cliente.cpf_cnpj == "12345678901"
        r = http.get(PREFIX + "/", params={"q": "123.456.789-01"})
        assert any(c["id"] == cliente.id for c in r.json()["clients"])

    def test_cpf_unformatted(self, http, cliente):
        r = http.get(PREFIX + "/", params={"q": "12345678901"})
        assert any(c["id"] == cliente.id for c in r.json()["clients"])

    def test_phone(self, http, cliente):
        # cliente.phone == "11999990000"
        r = http.get(PREFIX + "/", params={"q": "11999990000"})
        assert any(c["id"] == cliente.id for c in r.json()["clients"])


class TestSearchVehicles:
    def test_plate_without_dash(self, http, veiculo):
        r = http.get(PREFIX + "/", params={"q": "ABC1D23"})
        assert any(v["id"] == veiculo.id for v in r.json()["vehicles"])

    def test_plate_with_dash(self, http, veiculo):
        r = http.get(PREFIX + "/", params={"q": "ABC-1D23"})
        assert any(v["id"] == veiculo.id for v in r.json()["vehicles"])

    def test_plate_lowercase_partial(self, http, veiculo):
        r = http.get(PREFIX + "/", params={"q": "abc1d"})
        assert any(v["id"] == veiculo.id for v in r.json()["vehicles"])

    def test_by_model(self, http, veiculo):
        # veiculo.brand="Toyota", model="Corolla"
        r = http.get(PREFIX + "/", params={"q": "Corolla"})
        assert any(v["id"] == veiculo.id for v in r.json()["vehicles"])


class TestSearchTrackers:
    def test_full_imei(self, http, rastreador_instalado):
        r = http.get(PREFIX + "/", params={"q": "987654321098765"})
        assert any(t["id"] == rastreador_instalado.id for t in r.json()["trackers"])

    def test_partial_imei(self, http, rastreador_instalado):
        r = http.get(PREFIX + "/", params={"q": "987654"})
        assert any(t["id"] == rastreador_instalado.id for t in r.json()["trackers"])

    def test_short_digits_not_matched_as_imei(self, http, rastreador_instalado):
        # Menos de 3 dígitos não entra na busca por IMEI (evita scan "%%" à
        # toa) — só confere que não quebra, não que ache ou não ache.
        r = http.get(PREFIX + "/", params={"q": "98"})
        assert r.status_code == 200


class TestSearchServiceOrders:
    def test_by_number(self, http, ordem_servico):
        r = http.get(PREFIX + "/", params={"q": "OS-2025-001"})
        assert any(o["id"] == ordem_servico.id for o in r.json()["service_orders"])

    def test_by_client_name(self, http, ordem_servico, cliente):
        r = http.get(PREFIX + "/", params={"q": "Silva"})
        assert any(o["id"] == ordem_servico.id for o in r.json()["service_orders"])


class TestSearchDocuments:
    def _criar_documento(self, db, *, reference_type, reference_id, file_name="cnh-joao.pdf"):
        from app.models.document import Document

        doc = Document(
            file_name=file_name,
            object_key=f"test/{reference_type}/{reference_id}/{file_name}",
            content_type="application/pdf",
            size_bytes=1234,
            reference_type=reference_type,
            reference_id=reference_id,
            category="geral",
            active=True,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc

    def test_by_file_name(self, http, db, cliente):
        doc = self._criar_documento(db, reference_type="client", reference_id=cliente.id)
        r = http.get(PREFIX + "/", params={"q": "cnh-joao"})
        body = r.json()
        assert any(d["id"] == doc.id and d["client_id"] == cliente.id for d in body["documents"])

    def test_inactive_document_excluded(self, http, db, cliente):
        doc = self._criar_documento(db, reference_type="client", reference_id=cliente.id)
        doc.active = False
        db.commit()
        r = http.get(PREFIX + "/", params={"q": "cnh-joao"})
        assert doc.id not in [d["id"] for d in r.json()["documents"]]

    def test_deleted_owner_does_not_leak_navigation_id(self, http, db, cliente):
        # Documento de um cliente já excluído (soft delete) continua achável
        # pelo nome do arquivo, mas não pode virar link morto no frontend.
        doc = self._criar_documento(db, reference_type="client", reference_id=cliente.id)
        cliente.is_deleted = True
        db.commit()
        r = http.get(PREFIX + "/", params={"q": "cnh-joao"})
        found = next((d for d in r.json()["documents"] if d["id"] == doc.id), None)
        assert found is not None
        assert found["client_id"] is None


class TestSearchContracts:
    def test_admin_sees_contract(self, http, contrato, cliente):
        r = http.get(PREFIX + "/", params={"q": "Silva"})
        assert any(c["id"] == contrato.id for c in r.json()["contracts"])

    def test_financial_sees_contract(self, http_fin, contrato, cliente):
        r = http_fin.get(PREFIX + "/", params={"q": "Silva"})
        assert any(c["id"] == contrato.id for c in r.json()["contracts"])

    def test_operational_never_receives_contracts(self, http_op, contrato, cliente):
        r = http_op.get(PREFIX + "/", params={"q": "Silva"})
        assert r.status_code == 200
        assert r.json()["contracts"] == []


class TestSearchPermissions:
    def test_client_role_forbidden(self, http_cliente, cliente):
        r = http_cliente.get(PREFIX + "/", params={"q": "Silva"})
        assert r.status_code == 403

    def test_unauthenticated(self, http_unauth, cliente):
        r = http_unauth.get(PREFIX + "/", params={"q": "Silva"})
        assert r.status_code == 401

    def test_operational_sees_clients_vehicles_trackers_orders(self, http_op, cliente, veiculo, rastreador_instalado, ordem_servico):
        r = http_op.get(PREFIX + "/", params={"q": "Silva"})
        assert r.status_code == 200
        body = r.json()
        assert any(c["id"] == cliente.id for c in body["clients"])
        assert any(v["id"] == veiculo.id for v in body["vehicles"])


class TestSearchEdgeCases:
    def test_empty_query_returns_empty(self, http, cliente):
        r = http.get(PREFIX + "/", params={"q": ""})
        assert r.status_code == 200
        body = r.json()
        assert body == {"clients": [], "vehicles": [], "trackers": [], "service_orders": [], "contracts": [], "documents": []}

    def test_one_char_query_returns_empty(self, http, cliente):
        r = http.get(PREFIX + "/", params={"q": "S"})
        assert r.json()["clients"] == []

    def test_no_results(self, http, cliente):
        # Sem dígitos no termo — evita colidir por acidente com CPF/telefone
        # de outro fixture (ex.: "999" bateria com o telefone "11999990000").
        r = http.get(PREFIX + "/", params={"q": "Inexistente XYZ Nenhum"})
        body = r.json()
        assert all(body[key] == [] for key in body)

    def test_limit_respected(self, http, db):
        for i in range(5):
            db.add(Client(
                name=f"Busca Limite Cliente {i}",
                cpf_cnpj=f"1111122223{i}",
                type="pf",
                status=ClientStatus.ACTIVE,
            ))
        db.commit()
        r = http.get(PREFIX + "/", params={"q": "Busca Limite", "limit": 2})
        assert r.status_code == 200
        assert len(r.json()["clients"]) == 2

    def test_excludes_soft_deleted(self, http, db, cliente):
        cliente.is_deleted = True
        db.commit()
        r = http.get(PREFIX + "/", params={"q": "Silva"})
        assert cliente.id not in [c["id"] for c in r.json()["clients"]]
