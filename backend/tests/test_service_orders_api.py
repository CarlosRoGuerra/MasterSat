"""
Testes de integração para /api/v1/service-orders.

Cobertos:
- GET /         → listar, filtros (client_id, type, status)
- POST /        → criar ordem, campos obrigatórios, número gerado
- GET /{id}     → sucesso, 404
- PUT /{id}     → atualizar tipo/observações
- POST /{id}/status → transição aberta→em_andamento→concluida, já cancelada
- GET /{id}/logs    → histórico de mudanças de status
- DELETE /{id}  → soft-delete, 404
- Autorização   → FINANCIAL não cria ordens, OPERATIONAL pode editar
"""
from __future__ import annotations

import pytest
from app.models.enums import OrderStatus, OrderType
from app.models.service_order import ServiceOrder

PREFIX = "/api/v1/service-orders"


def _payload(client_id: int, vehicle_id: int | None = None) -> dict:
    return {
        "type": "instalacao",
        "client_id": client_id,
        "vehicle_id": vehicle_id,
        "status": "aberta",
        "observations": "Teste de instalação",
    }


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestListServiceOrders:
    def test_empty_list(self, http):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_existing_order(self, http, ordem_servico):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert any(x["id"] == ordem_servico.id for x in r.json())

    def test_filter_by_client_id(self, http, ordem_servico, cliente):
        r = http.get(PREFIX + "/", params={"client_id": cliente.id})
        assert r.status_code == 200
        assert all(x["client_id"] == cliente.id for x in r.json())

    def test_filter_by_type(self, http, ordem_servico):
        r = http.get(PREFIX + "/", params={"type": "instalacao"})
        assert r.status_code == 200
        assert all(x["type"] == "instalacao" for x in r.json())

    def test_filter_by_status(self, http, ordem_servico):
        r = http.get(PREFIX + "/", params={"status": "aberta"})
        assert r.status_code == 200
        assert all(x["status"] == "aberta" for x in r.json())

    def test_excludes_soft_deleted(self, http, db, ordem_servico):
        ordem_servico.is_deleted = True
        db.commit()
        r = http.get(PREFIX + "/")
        assert all(x["id"] != ordem_servico.id for x in r.json())

    def test_financial_can_list(self, http_fin, ordem_servico):
        r = http_fin.get(PREFIX + "/")
        assert r.status_code == 200

    def test_unauthenticated_returns_401(self, http_unauth):
        r = http_unauth.get(PREFIX + "/")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------

class TestCreateServiceOrder:
    def test_create_success(self, http, cliente, veiculo):
        r = http.post(PREFIX + "/", json=_payload(cliente.id, veiculo.id))
        assert r.status_code == 200
        data = r.json()
        assert data["client_id"] == cliente.id
        assert data["type"] == "instalacao"
        assert data["number"] is not None  # número gerado

    def test_number_is_unique(self, http, db, cliente):
        r1 = http.post(PREFIX + "/", json=_payload(cliente.id))
        r2 = http.post(PREFIX + "/", json={**_payload(cliente.id), "observations": "Segunda"})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["number"] != r2.json()["number"]

    def test_missing_client_id_returns_422(self, http):
        r = http.post(PREFIX + "/", json={"type": "instalacao", "status": "aberta"})
        assert r.status_code == 422

    def test_invalid_type_returns_422(self, http, cliente):
        r = http.post(PREFIX + "/", json={**_payload(cliente.id), "type": "tipo_invalido"})
        assert r.status_code == 422

    def test_nonexistent_client_returns_404(self, http):
        r = http.post(PREFIX + "/", json=_payload(99999))
        assert r.status_code == 404

    def test_operational_can_create(self, http_op, cliente):
        r = http_op.post(PREFIX + "/", json=_payload(cliente.id))
        assert r.status_code == 200

    def test_financial_cannot_create(self, http_fin, cliente):
        r = http_fin.post(PREFIX + "/", json=_payload(cliente.id))
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# IntegrityError — UNIQUE(number)
# ---------------------------------------------------------------------------
#
# `number` é único no schema. Quando o operador informa o número manualmente
# e ele já existe, é um erro dele (409). Quando o número é GERADO por nós
# (`_generate_order_number`, uma contagem não atômica com o INSERT), uma
# colisão é uma corrida entre duas aberturas concorrentes — não é um
# conflito do operador, e o endpoint deve regenerar e tentar de novo em vez
# de estourar erro.

class TestServiceOrderNumberIntegrity:
    def test_manual_duplicate_number_returns_409_not_500(self, http, cliente):
        r1 = http.post(PREFIX + "/", json={**_payload(cliente.id), "number": "OS-MANUAL-1"})
        assert r1.status_code == 200

        r2 = http.post(PREFIX + "/", json={**_payload(cliente.id), "number": "OS-MANUAL-1"})

        assert r2.status_code == 409
        detail = r2.json()["detail"]
        assert "já existe" in detail.lower()
        assert "unique" not in detail.lower()

    def test_session_stays_usable_after_manual_duplicate(self, http, db, cliente):
        http.post(PREFIX + "/", json={**_payload(cliente.id), "number": "OS-MANUAL-2"})
        r2 = http.post(PREFIX + "/", json={**_payload(cliente.id), "number": "OS-MANUAL-2"})
        assert r2.status_code == 409

        assert db.query(ServiceOrder).filter(ServiceOrder.number == "OS-MANUAL-2").count() == 1
        r3 = http.get(PREFIX + "/")
        assert r3.status_code == 200

    def test_concurrent_generated_number_collision_is_retried_transparently(
        self, http, db, cliente, monkeypatch,
    ):
        """Simula a corrida: outra abertura concorrente já commitou uma OS
        com o número que `_generate_order_number` calcularia para esta
        requisição, na janela entre a contagem e o INSERT. O endpoint deve
        perceber o IntegrityError, regenerar o número com o estado atual do
        banco e completar a criação em vez de devolver erro ao operador."""
        db.add(ServiceOrder(
            number="OS-COLISAO", type=OrderType.INSTALL, status=OrderStatus.OPEN,
            client_id=cliente.id,
        ))
        db.commit()

        import app.api.v1.endpoints.service_orders as service_orders_module

        calls = {"n": 0}
        real = service_orders_module._generate_order_number

        def flaky_generate(db_arg):
            calls["n"] += 1
            if calls["n"] == 1:
                return "OS-COLISAO"
            return real(db_arg)

        monkeypatch.setattr(
            "app.api.v1.endpoints.service_orders._generate_order_number", flaky_generate,
        )

        r = http.post(PREFIX + "/", json=_payload(cliente.id))

        assert r.status_code == 200
        assert r.json()["number"] != "OS-COLISAO"
        assert calls["n"] == 2
        assert db.query(ServiceOrder).filter(ServiceOrder.number == "OS-COLISAO").count() == 1


# ---------------------------------------------------------------------------
# GET /{id}
# ---------------------------------------------------------------------------

class TestGetServiceOrder:
    def test_get_existing(self, http, ordem_servico):
        r = http.get(f"{PREFIX}/{ordem_servico.id}")
        assert r.status_code == 200
        assert r.json()["id"] == ordem_servico.id

    def test_get_nonexistent_returns_404(self, http):
        r = http.get(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_get_deleted_returns_404(self, http, db, ordem_servico):
        ordem_servico.is_deleted = True
        db.commit()
        r = http.get(f"{PREFIX}/{ordem_servico.id}")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# PUT /{id}
# ---------------------------------------------------------------------------

class TestUpdateServiceOrder:
    def test_update_observations(self, http, ordem_servico):
        r = http.put(f"{PREFIX}/{ordem_servico.id}", json={"observations": "Observação atualizada"})
        assert r.status_code == 200
        assert r.json()["observations"] == "Observação atualizada"

    def test_update_type(self, http, ordem_servico):
        r = http.put(f"{PREFIX}/{ordem_servico.id}", json={"type": "manutencao"})
        assert r.status_code == 200
        assert r.json()["type"] == "manutencao"

    def test_update_nonexistent_returns_404(self, http):
        r = http.put(f"{PREFIX}/99999", json={"observations": "X"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /{id}/status
# ---------------------------------------------------------------------------

class TestServiceOrderStatusTransition:
    def test_open_to_in_progress(self, http, ordem_servico):
        r = http.post(f"{PREFIX}/{ordem_servico.id}/status", json={
            "status": "em_andamento",
            "notes": "Iniciando serviço",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "em_andamento"

    def test_in_progress_to_completed(self, http, db, ordem_servico):
        from datetime import datetime
        ordem_servico.status = OrderStatus.IN_PROGRESS
        ordem_servico.execution_description = "Instalação concluída, testado em campo."
        ordem_servico.technician_signed_at = datetime.utcnow()
        ordem_servico.client_signed_at = datetime.utcnow()
        db.commit()
        r = http.post(f"{PREFIX}/{ordem_servico.id}/status", json={
            "status": "concluida",
            "notes": "Serviço concluído",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "concluida"

    def test_completed_requires_execution_description_and_signatures(self, http, db, ordem_servico):
        """Regra confirmada: concluir sem descrição do serviço executado e
        as duas assinaturas retorna 422 — a OS não fica marcada como
        'concluída' sem esse mínimo de campo."""
        ordem_servico.status = OrderStatus.IN_PROGRESS
        db.commit()
        r = http.post(f"{PREFIX}/{ordem_servico.id}/status", json={"status": "concluida"})
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "descrição do serviço executado" in detail
        assert "assinatura do técnico" in detail
        assert "assinatura do cliente" in detail
        db.refresh(ordem_servico)
        assert ordem_servico.status == OrderStatus.IN_PROGRESS  # não mudou

    def test_completed_via_put_also_requires_gate(self, http, db, ordem_servico):
        r = http.put(f"{PREFIX}/{ordem_servico.id}", json={"status": "concluida"})
        assert r.status_code == 422

    def test_cancel_open_order(self, http, ordem_servico):
        r = http.post(f"{PREFIX}/{ordem_servico.id}/status", json={
            "status": "cancelada",
            "notes": "Cancelado pelo cliente",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "cancelada"

    def test_transition_nonexistent_returns_404(self, http):
        r = http.post(f"{PREFIX}/99999/status", json={"status": "em_andamento"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /{id}/logs
# ---------------------------------------------------------------------------

class TestServiceOrderLogs:
    def test_logs_empty_initially(self, http, ordem_servico):
        r = http.get(f"{PREFIX}/{ordem_servico.id}/logs")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_logs_record_status_change(self, http, ordem_servico):
        http.post(f"{PREFIX}/{ordem_servico.id}/status", json={"status": "em_andamento"})
        r = http.get(f"{PREFIX}/{ordem_servico.id}/logs")
        assert r.status_code == 200
        assert len(r.json()) >= 1


# ---------------------------------------------------------------------------
# DELETE /{id}
# ---------------------------------------------------------------------------

class TestDeleteServiceOrder:
    def test_soft_delete(self, http, db, ordem_servico):
        r = http.delete(f"{PREFIX}/{ordem_servico.id}")
        assert r.status_code == 200
        db.refresh(ordem_servico)
        assert ordem_servico.is_deleted is True

    def test_delete_nonexistent_returns_404(self, http):
        r = http.delete(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_financial_cannot_delete(self, http_fin, ordem_servico):
        r = http_fin.delete(f"{PREFIX}/{ordem_servico.id}")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /{id}/documents — o filename do usuário não pode controlar o caminho
# ---------------------------------------------------------------------------
#
# O nome do arquivo enviado (multipart "filename") vira parte da chave
# composta para o objeto no MinIO (ver upload_documents). Esses testes
# garantem que nenhum filename, por mais hostil que seja, produz uma chave
# fora do prefixo 'service-orders/{id}/documents/'.

class TestUploadDocumentsPathTraversal:
    @pytest.fixture()
    def capturado(self, monkeypatch):
        chamadas: list[str] = []

        def _fake_upload_bytes(object_name, content, content_type):
            chamadas.append(object_name)
            return object_name

        monkeypatch.setattr(
            "app.api.v1.endpoints.service_orders.upload_bytes", _fake_upload_bytes
        )
        return chamadas

    @staticmethod
    def _prefixo(item_id):
        return f"service-orders/{item_id}/documents/"

    def _upload(self, http, item_id, nome, conteudo=b"conteudo"):
        return http.post(
            f"{PREFIX}/{item_id}/documents",
            data={"category": "geral"},
            files=[("files", (nome, conteudo, "application/pdf"))],
        )

    @pytest.mark.parametrize("nome_malicioso", [
        "../../arquivo.txt",
        "../../../etc/passwd",
        "..\\..\\arquivo.txt",
        "..\\..\\..\\windows\\win32.dll",
        "a/../../../b.txt",
        "....//....//arquivo.txt",
    ])
    def test_travessia_de_caminho_nao_escapa_do_prefixo(
        self, http, ordem_servico, capturado, nome_malicioso
    ):
        r = self._upload(http, ordem_servico.id, nome_malicioso)
        assert r.status_code == 200, r.text
        assert len(capturado) == 1
        chave = capturado[0]
        prefixo = self._prefixo(ordem_servico.id)
        assert chave.startswith(prefixo), f"chave escapou do prefixo esperado: {chave}"
        resto = chave[len(prefixo):]
        assert ".." not in resto
        assert "/" not in resto
        assert "\\" not in resto

    def test_nome_com_separadores_fica_contido_no_prefixo(self, http, ordem_servico, capturado):
        r = self._upload(http, ordem_servico.id, "pasta/sub/arquivo.txt")
        assert r.status_code == 200, r.text
        chave = capturado[0]
        prefixo = self._prefixo(ordem_servico.id)
        assert chave.startswith(prefixo)
        assert "/" not in chave[len(prefixo):]

    def test_nome_muito_grande_nao_derruba_o_endpoint(self, http, ordem_servico, capturado):
        nome = ("a" * 3000) + "../../etc/passwd"
        r = self._upload(http, ordem_servico.id, nome)
        assert r.status_code == 200, r.text
        chave = capturado[0]
        prefixo = self._prefixo(ordem_servico.id)
        assert chave.startswith(prefixo)
        assert ".." not in chave[len(prefixo):]

    @pytest.mark.parametrize("nome", [
        "arquivo com espaço e acentuação-ção.pdf",
        "arquivo;rm -rf ~.pdf",
        "arquivo<>|?*.pdf",
        "relatório-ação-início.pdf",
    ])
    def test_caracteres_especiais_nao_quebram_o_prefixo(self, http, ordem_servico, capturado, nome):
        r = self._upload(http, ordem_servico.id, nome)
        assert r.status_code == 200, r.text
        chave = capturado[0]
        prefixo = self._prefixo(ordem_servico.id)
        assert chave.startswith(prefixo)
        resto = chave[len(prefixo):]
        assert "/" not in resto
        assert "\\" not in resto

    def test_duas_ordens_nao_colidem_via_travessia(
        self, http, db, ordem_servico, capturado, cliente
    ):
        """Uma OS não deve conseguir, via filename, produzir uma chave dentro
        do prefixo de OUTRA ordem de serviço."""
        from app.models.enums import OrderStatus, OrderType
        from app.models.service_order import ServiceOrder

        outra = ServiceOrder(
            number="OS-2025-002", type=OrderType.INSTALL, status=OrderStatus.OPEN,
            client_id=cliente.id,
        )
        db.add(outra)
        db.commit()
        db.refresh(outra)

        alvo = f"../{outra.id}/documents/arquivo.txt"
        r = self._upload(http, ordem_servico.id, alvo)
        assert r.status_code == 200, r.text
        chave = capturado[0]
        assert chave.startswith(self._prefixo(ordem_servico.id))
        assert not chave.startswith(self._prefixo(outra.id))
