"""
Testes de integração para /api/v1/billings.

Cobertos:
- GET /           → listar, filtros (status, client_id, busca, datas)
- POST /          → criar, campos obrigatórios
- GET /{id}       → sucesso, 404
- POST /{id}/receive    → baixa de pagamento, 404, billing já pago
- POST /{id}/cancel     → cancelar, 404, billing já cancelado
- PUT /{id}             → ajustar valor/vencimento
- DELETE /{id}          → soft-delete
- GET /summary          → resumo financeiro
- Autorização     → CLIENT → 403, OPERATIONAL só lê
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.billing import Billing
from app.models.enums import BillingStatus

PREFIX = "/api/v1/billings"


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestListBillings:
    def test_empty_list(self, http):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_existing_billing(self, http, billing_pendente):
        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        assert any(x["id"] == billing_pendente.id for x in r.json())

    def test_filter_by_status_pending(self, http, billing_pendente):
        r = http.get(PREFIX + "/", params={"status": "pendente"})
        assert r.status_code == 200
        assert all(x["status"] == "pendente" for x in r.json())

    def test_filter_by_status_overdue(self, http, billing_vencida):
        r = http.get(PREFIX + "/", params={"status": "vencida"})
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_filter_by_client_id(self, http, billing_pendente, cliente):
        r = http.get(PREFIX + "/", params={"client_id": cliente.id})
        assert r.status_code == 200
        assert all(x["client_id"] == cliente.id for x in r.json())

    def test_filter_by_wrong_client_empty(self, http, billing_pendente):
        r = http.get(PREFIX + "/", params={"client_id": 99999})
        assert r.status_code == 200
        assert r.json() == []

    def test_filter_by_due_from(self, http, billing_pendente):
        r = http.get(PREFIX + "/", params={"due_from": "2099-01-01"})
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_filter_by_due_to(self, http, billing_vencida):
        r = http.get(PREFIX + "/", params={"due_to": "2021-01-01"})
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_search_by_billing_id(self, http, billing_pendente):
        r = http.get(PREFIX + "/", params={"search": str(billing_pendente.id)})
        assert r.status_code == 200
        assert any(x["id"] == billing_pendente.id for x in r.json())

    def test_search_by_nonexistent_id_is_empty(self, http, billing_pendente, billing_vencida):
        r = http.get(PREFIX + "/", params={"search": "999999"})
        assert r.status_code == 200
        assert r.json() == []

    def test_excludes_soft_deleted(self, http, db, billing_pendente):
        billing_pendente.is_deleted = True
        db.commit()
        r = http.get(PREFIX + "/")
        assert all(x["id"] != billing_pendente.id for x in r.json())

    def test_client_role_cannot_list(self, http_cliente):
        r = http_cliente.get(PREFIX + "/")
        assert r.status_code == 403

    def test_operational_can_list(self, http_op, billing_pendente):
        r = http_op.get(PREFIX + "/")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /summary
# ---------------------------------------------------------------------------

class TestBillingSummary:
    def test_summary_returns_correct_structure(self, http):
        r = http.get(PREFIX + "/summary")
        assert r.status_code == 200
        data = r.json()
        assert "pending_billings" in data
        assert "overdue_billings" in data
        assert "pending_amount" in data
        assert "overdue_amount" in data
        assert "paid_this_month" in data

    def test_summary_counts_pending(self, http, billing_pendente):
        r = http.get(PREFIX + "/summary")
        assert r.status_code == 200
        assert r.json()["pending_billings"] >= 1

    def test_summary_counts_overdue(self, http, billing_vencida):
        r = http.get(PREFIX + "/summary")
        assert r.status_code == 200
        assert r.json()["overdue_billings"] >= 1

    def test_operational_cannot_see_summary(self, http_op):
        r = http_op.get(PREFIX + "/summary")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------

class TestCreateBilling:
    def test_create_success(self, http, cliente):
        r = http.post(PREFIX + "/", json={
            "client_id": cliente.id,
            "amount": 199.90,
            "due_date": "2099-12-31",
            "status": "pendente",
            "billing_type": "item",
            "title": "Cobrança manual",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["client_id"] == cliente.id
        assert abs(data["amount"] - 199.90) < 0.01

    def test_missing_client_id_returns_422(self, http):
        r = http.post(PREFIX + "/", json={"amount": 100.0, "due_date": "2099-12-31"})
        assert r.status_code == 422

    def test_contract_billing_uses_intervenient_as_payer(
        self, http, db, contrato, cliente, outro_cliente,
    ):
        contrato.interveniente_client_id = outro_cliente.id
        db.commit()

        r = http.post(PREFIX + "/", json={
            "client_id": cliente.id,
            "contract_id": contrato.id,
            "amount": 100.0,
            "due_date": "2099-12-31",
        })

        assert r.status_code == 200
        assert r.json()['client_id'] == cliente.id
        assert r.json()['payer_client_id'] == outro_cliente.id
        assert r.json()['payer_name'] == outro_cliente.name

    def test_missing_amount_returns_422(self, http, cliente):
        r = http.post(PREFIX + "/", json={"client_id": cliente.id, "due_date": "2099-12-31"})
        assert r.status_code == 422

    def test_operational_cannot_create(self, http_op, cliente):
        r = http_op.post(PREFIX + "/", json={
            "client_id": cliente.id, "amount": 100.0, "due_date": "2099-12-31",
        })
        assert r.status_code == 403

    def test_paid_creation_requires_payment_date(self, http, cliente):
        r = http.post(PREFIX + "/", json={
            "client_id": cliente.id,
            "amount": 100.0,
            "due_date": "2099-12-31",
            "status": "paga",
        })
        assert r.status_code == 400

    def test_rejects_charge_item_from_another_client(self, http, cliente, outro_cliente, contrato):
        item_response = http.post('/api/v1/client-charge-items/', json={
            "client_id": cliente.id,
            "contract_id": contrato.id,
            "title": "Serviço rastreável",
            "quantity": 1,
            "unit_price": 100.0,
            "installment_count": 1,
            "start_date": "2025-06-01",
        })
        assert item_response.status_code == 200

        r = http.post(PREFIX + "/", json={
            "client_id": outro_cliente.id,
            "item_id": item_response.json()["id"],
            "amount": 100.0,
            "due_date": "2099-12-31",
        })
        assert r.status_code == 400


class TestParcelarContrato:
    def test_carne_uses_intervenient_as_payer(
        self, http, db, contrato, outro_cliente,
    ):
        contrato.interveniente_client_id = outro_cliente.id
        db.commit()

        r = http.post(PREFIX + '/parcelar', json={
            'contract_id': contrato.id,
            'num_parcelas': 2,
            'primeiro_vencimento': '2099-01-10',
        })

        assert r.status_code == 200
        assert len(r.json()) == 2
        assert all(row['payer_client_id'] == outro_cliente.id for row in r.json())
        assert all(row['payer_name'] == outro_cliente.name for row in r.json())

# ---------------------------------------------------------------------------
# GET /{id}
# ---------------------------------------------------------------------------

class TestGetBilling:
    def test_get_existing(self, http, billing_pendente):
        r = http.get(f"{PREFIX}/{billing_pendente.id}")
        assert r.status_code == 200
        assert r.json()["id"] == billing_pendente.id

    def test_get_nonexistent_returns_404(self, http):
        r = http.get(f"{PREFIX}/99999")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Reclassificação pendente <-> vencida NÃO é mais efeito colateral de GET (BE-05)
#
# Antes desta mudança, o UPDATE de refresh_overdue_statuses rodava dentro de
# base_query() a cada GET (list/get_by_id/summary) — o teste antigo desta
# classe documentava e travava esse comportamento como tripwire deliberado,
# para o dia em que o worker de background (BE-05, ver
# app.main._overdue_status_refresh_worker) assumisse a reclassificação: o
# comentário original já previa que, uma vez o worker rodando/validado em
# produção, este teste deveria falhar e ser atualizado — o que agora
# aconteceu. GET passou a ser somente leitura; devido a due_date ter
# granularidade de dia, até 1h de atraso do worker não afeta regra de
# negócio nenhuma. A correção de refresh_overdue_statuses em si continua
# coberta isoladamente em test_financial.py.
# ---------------------------------------------------------------------------

class TestOverdueStatusNotRefreshedOnGet:
    def test_list_does_not_update_stale_pending(self, http, db, contrato):
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
        db.refresh(stale)

        r = http.get(PREFIX + "/")
        assert r.status_code == 200
        item = next(x for x in r.json() if x["id"] == stale.id)
        assert item["status"] == "pendente"

        db.refresh(stale)
        assert stale.status == BillingStatus.PENDING

    def test_get_by_id_does_not_update_stale_pending(self, http, db, contrato):
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
        db.refresh(stale)

        r = http.get(f"{PREFIX}/{stale.id}")
        assert r.status_code == 200
        assert r.json()["status"] == "pendente"

        db.refresh(stale)
        assert stale.status == BillingStatus.PENDING


# ---------------------------------------------------------------------------
# POST /{id}/receive (baixar pagamento)
# ---------------------------------------------------------------------------

class TestReceiveBilling:
    def test_receive_pending_billing(self, http, billing_pendente):
        r = http.post(f"{PREFIX}/{billing_pendente.id}/receive", json={
            "paid_amount": 99.90,
            "payment_date": "2025-05-28",
            "payment_method": "pix",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "paga"

    def test_receive_generates_receipt_number(self, http, billing_pendente):
        r = http.post(f"{PREFIX}/{billing_pendente.id}/receive", json={
            "paid_amount": 99.90,
            "payment_date": "2025-05-28",
            "payment_method": "boleto",
        })
        assert r.status_code == 200
        assert r.json()["receipt_number"] is not None

    def test_receive_nonexistent_returns_404(self, http):
        r = http.post(f"{PREFIX}/99999/receive", json={
            "paid_amount": 100.0, "payment_date": "2025-05-28", "payment_method": "pix",
        })
        assert r.status_code == 404


class TestUnificarBoletos:
    def test_unifica_duas_cobrancas_em_uma(self, http, billing_pendente, billing_vencida):
        r = http.post(f"{PREFIX}/unificar", json={
            "billing_ids": [billing_pendente.id, billing_vencida.id],
            "due_date": "2099-01-15",
        })
        assert r.status_code == 200
        nova = r.json()
        assert nova["amount"] == 199.80          # soma das duas
        assert nova["status"] == "pendente"
        assert nova["billing_type"] == "avulsa"
        # originais canceladas com referência
        for bid in (billing_pendente.id, billing_vencida.id):
            rr = http.get(f"{PREFIX}/{bid}")
            assert rr.json()["status"] == "cancelada"
            assert f'#{nova["id"]}' in (rr.json()["notes"] or "")

    def test_titulo_referencia_quantidade_e_periodo_sem_expor_ids_internos(self, http, billing_pendente, billing_vencida):
        r = http.post(f"{PREFIX}/unificar", json={
            "billing_ids": [billing_pendente.id, billing_vencida.id],
            "due_date": "2099-01-15",
        })
        assert r.status_code == 200
        titulo = r.json()["title"]
        assert "2 PARCELA" in titulo
        assert f'#{billing_pendente.id}' not in titulo
        assert f'#{billing_vencida.id}' not in titulo

    def test_valor_negociado_sobrepoe_a_soma(self, http, billing_pendente, billing_vencida):
        r = http.post(f"{PREFIX}/unificar", json={
            "billing_ids": [billing_pendente.id, billing_vencida.id],
            "due_date": "2099-01-15",
            "amount": 150.0,
        })
        assert r.status_code == 200
        assert r.json()["amount"] == 150.0

    def test_rejeita_cobranca_paga(self, http, db, billing_pendente, billing_vencida):
        billing_pendente.status = BillingStatus.PAID
        db.commit()
        r = http.post(f"{PREFIX}/unificar", json={
            "billing_ids": [billing_pendente.id, billing_vencida.id],
            "due_date": "2099-01-15",
        })
        assert r.status_code == 400

    def test_rejeita_clientes_diferentes(self, http, billing_pendente, outro_cliente):
        outra = http.post(f"{PREFIX}/", json={
            "client_id": outro_cliente.id, "amount": 50.0, "due_date": "2099-02-01",
        }).json()
        r = http.post(f"{PREFIX}/unificar", json={
            "billing_ids": [billing_pendente.id, outra["id"]],
            "due_date": "2099-01-15",
        })
        assert r.status_code == 400

    def test_exige_pelo_menos_duas(self, http, billing_pendente):
        r = http.post(f"{PREFIX}/unificar", json={
            "billing_ids": [billing_pendente.id],
            "due_date": "2099-01-15",
        })
        assert r.status_code == 422


class TestUnificarFaixaPeriodo:
    """period_label é 'MM/YYYY' (ex.: '09/2025'). A faixa exibida no título da
    negociação ('REF. X A Y') precisa ordenar esses rótulos por DATA (ano,
    depois mês), não por texto — comparar string compara o mês antes do ano
    e inverte a faixa sempre que os períodos cruzam a virada do ano."""

    def _billing(self, db, contrato, *, period_label, title="Parcela"):
        b = Billing(
            contract_id=contrato.id,
            client_id=contrato.client_id,
            amount=Decimal("100.00"),
            due_date=date(2099, 6, 1),
            status=BillingStatus.PENDING,
            billing_type="recorrente",
            period_label=period_label,
            title=title,
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        return b

    def test_mesmo_ano_ordena_cronologicamente(self, http, db, contrato):
        a = self._billing(db, contrato, period_label="03/2025")
        b = self._billing(db, contrato, period_label="07/2025")
        r = http.post(f"{PREFIX}/unificar", json={
            "billing_ids": [a.id, b.id], "due_date": "2099-01-15",
        })
        assert r.status_code == 200
        assert "REF. 03/2025 A 07/2025" in r.json()["title"]

    def test_virada_de_ano_ordena_por_data_nao_por_texto(self, http, db, contrato):
        # Alfabeticamente "02/2026" < "11/2025" (o mês vem antes do ano na
        # string), mas cronologicamente novembro/2025 é ANTES de
        # fevereiro/2026. A faixa correta é '11/2025 A 02/2026'.
        nov = self._billing(db, contrato, period_label="11/2025")
        fev = self._billing(db, contrato, period_label="02/2026")
        r = http.post(f"{PREFIX}/unificar", json={
            "billing_ids": [nov.id, fev.id], "due_date": "2099-01-15",
        })
        assert r.status_code == 200
        titulo = r.json()["title"]
        assert "REF. 11/2025 A 02/2026" in titulo
        assert "REF. 02/2026 A 11/2025" not in titulo

    def test_mes_de_um_digito_nao_quebra_ordenacao(self, http, db, contrato):
        # Dado malformado (mês sem zero à esquerda) não pode derrubar a rota.
        set_ = self._billing(db, contrato, period_label="9/2025")
        nov = self._billing(db, contrato, period_label="11/2025")
        r = http.post(f"{PREFIX}/unificar", json={
            "billing_ids": [set_.id, nov.id], "due_date": "2099-01-15",
        })
        assert r.status_code == 200

    def test_meses_de_dois_digitos_mesma_faixa(self, http, db, contrato):
        a = self._billing(db, contrato, period_label="01/2025")
        b = self._billing(db, contrato, period_label="12/2025")
        r = http.post(f"{PREFIX}/unificar", json={
            "billing_ids": [a.id, b.id], "due_date": "2099-01-15",
        })
        assert r.status_code == 200
        assert "REF. 01/2025 A 12/2025" in r.json()["title"]

    def test_ordem_de_entrada_nao_afeta_a_faixa(self, http, db, contrato):
        # Passar os ids "fora de ordem" (o mais recente primeiro) não muda o
        # resultado: a faixa é sempre cronológica, não a ordem de envio.
        fev = self._billing(db, contrato, period_label="02/2026")
        nov = self._billing(db, contrato, period_label="11/2025")
        r = http.post(f"{PREFIX}/unificar", json={
            "billing_ids": [fev.id, nov.id], "due_date": "2099-01-15",
        })
        assert r.status_code == 200
        assert "REF. 11/2025 A 02/2026" in r.json()["title"]

    def test_periodo_invalido_nao_derruba_a_rota(self, http, db, contrato):
        valido = self._billing(db, contrato, period_label="05/2025")
        invalido = self._billing(db, contrato, period_label="período indefinido")
        r = http.post(f"{PREFIX}/unificar", json={
            "billing_ids": [valido.id, invalido.id], "due_date": "2099-01-15",
        })
        assert r.status_code == 200
        assert "PARCELA" in r.json()["title"]


class TestFiltroVeiculo:
    def test_filtra_por_vehicle_id(self, http, db, billing_pendente, veiculo, veiculo_outro_cliente):
        billing_pendente.vehicle_id = veiculo.id
        db.commit()

        r = http.get(f"{PREFIX}/?vehicle_id={veiculo.id}")
        assert r.status_code == 200
        assert [b["id"] for b in r.json()] == [billing_pendente.id]

        # Outro veículo não pode receber as cobranças deste
        r2 = http.get(f"{PREFIX}/?vehicle_id={veiculo_outro_cliente.id}")
        assert r2.status_code == 200
        assert r2.json() == []


class TestReceiptDownload:
    def test_receipt_de_cobranca_paga(self, http, billing_pendente):
        http.post(f"{PREFIX}/{billing_pendente.id}/receive", json={
            "paid_amount": 99.90, "payment_date": "2025-05-28", "payment_method": "pix",
        })
        r = http.get(f"{PREFIX}/{billing_pendente.id}/receipt")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content[:4] == b"%PDF"

    def test_receipt_de_pendente_retorna_400(self, http, billing_pendente):
        r = http.get(f"{PREFIX}/{billing_pendente.id}/receipt")
        assert r.status_code == 400


class TestReceiveBillingEdgeCases:
    def test_receive_already_paid_returns_400(self, http, db, billing_pendente):
        billing_pendente.status = BillingStatus.PAID
        db.commit()
        r = http.post(f"{PREFIX}/{billing_pendente.id}/receive", json={
            "paid_amount": 99.90, "payment_date": "2025-05-28", "payment_method": "pix",
        })
        assert r.status_code == 400

    def test_operational_cannot_receive(self, http_op, billing_pendente):
        r = http_op.post(f"{PREFIX}/{billing_pendente.id}/receive", json={
            "paid_amount": 99.90, "payment_date": "2025-05-28", "payment_method": "pix",
        })
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /{id}/cancel
# ---------------------------------------------------------------------------

class TestCancelBilling:
    def test_cancel_pending_billing(self, http, billing_pendente):
        r = http.post(f"{PREFIX}/{billing_pendente.id}/cancel", json={"reason": "Cancelado por teste"})
        assert r.status_code == 200
        assert r.json()["status"] == "cancelada"

    def test_cancel_nonexistent_returns_404(self, http):
        r = http.post(f"{PREFIX}/99999/cancel", json={"reason": "X"})
        assert r.status_code == 404

    def test_cancel_already_canceled_returns_400(self, http, db, billing_pendente):
        billing_pendente.status = BillingStatus.CANCELED
        db.commit()
        r = http.post(f"{PREFIX}/{billing_pendente.id}/cancel", json={"reason": "X"})
        assert r.status_code == 400

    def test_cancel_paga_bloqueado(self, http, db, billing_pendente):
        billing_pendente.status = BillingStatus.PAID
        db.commit()
        r = http.post(f"{PREFIX}/{billing_pendente.id}/cancel", json={"reason": "X"})
        assert r.status_code == 400

    def test_operational_cannot_cancel(self, http_op, billing_pendente):
        r = http_op.post(f"{PREFIX}/{billing_pendente.id}/cancel", json={"reason": "X"})
        assert r.status_code == 403

    def _registrar_boleto(self, db, billing_id):
        from app.models.ailos_boleto import AilosBoleto
        db.add(AilosBoleto(
            billing_id=billing_id, numero_convenio='102004', nosso_numero='000000301',
            linha_digitavel='08591.02006 40045.470206 00000.003012 5 14890000009990',
            codigo_barras='08595148900000099901020040045470200000000301',
        ))
        db.commit()

    def test_cancel_com_boleto_ailos_exige_confirmacao(self, http, db, billing_pendente):
        # Boleto registrado na Ailos → 409 com código para o frontend avisar.
        self._registrar_boleto(db, billing_pendente.id)
        r = http.post(f"{PREFIX}/{billing_pendente.id}/cancel", json={"reason": "X"})
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "boleto_ailos_registrado"
        # Não cancelou ainda.
        db.refresh(billing_pendente)
        assert billing_pendente.status == BillingStatus.PENDING

    def test_cancel_com_boleto_ailos_confirmado_prossegue(self, http, db, billing_pendente):
        self._registrar_boleto(db, billing_pendente.id)
        r = http.post(f"{PREFIX}/{billing_pendente.id}/cancel",
                      json={"reason": "X", "confirmar_boleto_ailos": True})
        assert r.status_code == 200
        assert r.json()["status"] == "cancelada"
        db.refresh(billing_pendente)
        assert "baixa manual pendente" in (billing_pendente.notes or "")


# ---------------------------------------------------------------------------
# POST /lote/situacao (action=receber) → pagamento em lote e recibo consolidado
#
# Requisito do cliente: pagar N parcelas em uma única operação deve gerar UM
# recibo consolidado (mesmo identificador, discriminando as parcelas, com o
# total = soma efetivamente paga) — não um recibo por parcela.
# ---------------------------------------------------------------------------

class TestBatchReceiveConsolidatedReceipt:
    def _parcelas(self, db, contrato, valores: tuple[float, ...]) -> list[Billing]:
        criadas = []
        for i, valor in enumerate(valores, start=1):
            b = Billing(
                contract_id=contrato.id,
                client_id=contrato.client_id,
                amount=Decimal(str(valor)),
                due_date=date(2099, 1, i),
                status=BillingStatus.PENDING,
                billing_type="carne",
                installment_number=i,
                installment_total=len(valores),
                period_label=f"{i:02d}/2099",
                title=f"Parcela {i}/{len(valores)}",
            )
            db.add(b)
            criadas.append(b)
        db.commit()
        for b in criadas:
            db.refresh(b)
        return criadas

    def _receber_em_lote(self, http, ids: list[int], **overrides):
        payload = {
            "billing_ids": ids,
            "action": "receber",
            "payment_date": "2099-01-05",
            "payment_method": "pix",
        }
        payload.update(overrides)
        return http.post(f"{PREFIX}/lote/situacao", json=payload)

    # -- cenário principal do requisito: 3 parcelas de R$ 100 -------------

    def test_tres_parcelas_cem_reais_uma_operacao_um_recibo_consolidado(self, http, db, contrato):
        p1, p2, p3 = self._parcelas(db, contrato, (100.0, 100.0, 100.0))

        r = self._receber_em_lote(http, [p1.id, p2.id, p3.id])
        assert r.status_code == 200
        body = r.json()

        # 1 pagamento processando as 3 parcelas de uma vez
        assert set(body["processados"]) == {p1.id, p2.id, p3.id}
        assert body["ignorados"] == []

        db.refresh(p1); db.refresh(p2); db.refresh(p3)

        # 3 parcelas liquidadas
        assert p1.status == BillingStatus.PAID
        assert p2.status == BillingStatus.PAID
        assert p3.status == BillingStatus.PAID

        # valor total efetivamente pago = R$ 300,00
        total_pago = float(p1.paid_amount) + float(p2.paid_amount) + float(p3.paid_amount)
        assert total_pago == pytest.approx(300.0)

        # 1 único recibo consolidado para a operação: as 3 parcelas compartilham
        # o mesmo identificador de recibo (hoje cada uma recebe o seu próprio)
        assert p1.receipt_number is not None
        assert p1.receipt_number == p2.receipt_number == p3.receipt_number, (
            "cada parcela recebeu um número de recibo diferente — "
            "não há recibo consolidado por operação"
        )

        # o recibo é acessível/discrimina as parcelas a partir de qualquer uma
        # delas, e reflete o total pago (R$ 300,00), não o valor de 1 parcela
        for parcela in (p1, p2, p3):
            rr = http.get(f"{PREFIX}/{parcela.id}/receipt")
            assert rr.status_code == 200
            assert rr.headers["content-type"] == "application/pdf"

    # -- variações de quantidade -------------------------------------------

    def test_uma_parcela_gera_recibo_individual(self, http, db, contrato):
        [p1] = self._parcelas(db, contrato, (100.0,))
        r = self._receber_em_lote(http, [p1.id])
        assert r.status_code == 200
        db.refresh(p1)
        assert p1.status == BillingStatus.PAID
        assert p1.receipt_number is not None

    def test_duas_parcelas_recibo_consolidado(self, http, db, contrato):
        p1, p2 = self._parcelas(db, contrato, (100.0, 100.0))
        r = self._receber_em_lote(http, [p1.id, p2.id])
        assert r.status_code == 200
        db.refresh(p1); db.refresh(p2)
        assert p1.status == BillingStatus.PAID and p2.status == BillingStatus.PAID
        assert p1.receipt_number == p2.receipt_number
        total_pago = float(p1.paid_amount) + float(p2.paid_amount)
        assert total_pago == pytest.approx(200.0)

    # -- valores diferentes por parcela (cobre "juros"/"desconto": o valor já
    # ajustado fica no próprio amount da parcela, não há campo separado) ----

    def test_parcelas_com_valores_diferentes(self, http, db, contrato):
        p1, p2, p3 = self._parcelas(db, contrato, (100.0, 150.0, 80.0))
        r = self._receber_em_lote(http, [p1.id, p2.id, p3.id])
        assert r.status_code == 200
        db.refresh(p1); db.refresh(p2); db.refresh(p3)
        total_pago = float(p1.paid_amount) + float(p2.paid_amount) + float(p3.paid_amount)
        assert total_pago == pytest.approx(330.0)
        assert p1.receipt_number == p2.receipt_number == p3.receipt_number

    def test_parcela_com_juros_amount_ja_reajustado_entra_no_total(self, http, db, contrato):
        # Neste sistema "juros" não é um campo à parte — é refletido no
        # próprio amount da parcela (vencida e cobrada com acréscimo).
        p1, p2 = self._parcelas(db, contrato, (100.0, 100.0))
        p2.amount = Decimal("112.50")  # 100 + juros já calculado
        db.commit()
        r = self._receber_em_lote(http, [p1.id, p2.id])
        assert r.status_code == 200
        db.refresh(p1); db.refresh(p2)
        total_pago = float(p1.paid_amount) + float(p2.paid_amount)
        assert total_pago == pytest.approx(212.50)
        assert p1.receipt_number == p2.receipt_number

    def test_parcela_com_desconto_amount_ja_reajustado_entra_no_total(self, http, db, contrato):
        p1, p2 = self._parcelas(db, contrato, (100.0, 100.0))
        p2.amount = Decimal("90.00")  # desconto já aplicado
        db.commit()
        r = self._receber_em_lote(http, [p1.id, p2.id])
        assert r.status_code == 200
        db.refresh(p1); db.refresh(p2)
        total_pago = float(p1.paid_amount) + float(p2.paid_amount)
        assert total_pago == pytest.approx(190.0)

    def test_parcela_com_multa_amount_ja_reajustado_entra_no_total(self, http, db, contrato):
        p1, p2 = self._parcelas(db, contrato, (100.0, 100.0))
        p2.amount = Decimal("105.00")  # multa já aplicada
        db.commit()
        r = self._receber_em_lote(http, [p1.id, p2.id])
        assert r.status_code == 200
        db.refresh(p1); db.refresh(p2)
        total_pago = float(p1.paid_amount) + float(p2.paid_amount)
        assert total_pago == pytest.approx(205.0)

    # -- pagamento duplicado -------------------------------------------------

    def test_pagamento_duplicado_nao_reprocessa_nem_gera_novo_recibo(self, http, db, contrato):
        p1, p2, p3 = self._parcelas(db, contrato, (100.0, 100.0, 100.0))
        r1 = self._receber_em_lote(http, [p1.id, p2.id, p3.id])
        assert r1.status_code == 200
        db.refresh(p1)
        recibo_original = p1.receipt_number
        paid_amount_original = float(p1.paid_amount)

        # tenta pagar de novo o mesmo lote
        r2 = self._receber_em_lote(http, [p1.id, p2.id, p3.id])
        assert r2.status_code == 200
        body2 = r2.json()
        # já pagas → ignoradas, não reprocessadas
        assert set(body2["ignorados"]) == {p1.id, p2.id, p3.id}
        assert body2["processados"] == []

        db.refresh(p1)
        assert p1.receipt_number == recibo_original
        assert float(p1.paid_amount) == pytest.approx(paid_amount_original)

    # -- falha durante o processamento: tudo ou nada -------------------------

    def test_falha_no_meio_do_lote_nao_deixa_pagamento_parcial(self, http, db, contrato):
        from app.models.ailos_boleto import AilosBoleto

        p1, p2, p3 = self._parcelas(db, contrato, (100.0, 100.0, 100.0))
        # p2 tem boleto Ailos em registro — bloqueia o lote inteiro
        db.add(AilosBoleto(
            billing_id=p2.id, numero_convenio='102004', status_ailos='REGISTRANDO',
        ))
        db.commit()

        r = self._receber_em_lote(http, [p1.id, p2.id, p3.id])
        assert r.status_code == 409

        db.refresh(p1); db.refresh(p2); db.refresh(p3)
        # nenhuma das 3 pode ter sido paga — tudo ou nada
        assert p1.status == BillingStatus.PENDING
        assert p2.status == BillingStatus.PENDING
        assert p3.status == BillingStatus.PENDING
        assert p1.receipt_number is None
        assert p3.receipt_number is None

    # -- concorrência: lotes sobrepostos -------------------------------------

    def test_lotes_sobrepostos_segunda_chamada_nao_duplica_baixa(self, http, db, contrato):
        # Duas operações de lote que compartilham uma parcela (p2). A segunda
        # chamada não pode reprocessar p2 nem gerar um segundo recibo pra ela.
        p1, p2, p3 = self._parcelas(db, contrato, (100.0, 100.0, 100.0))

        r1 = self._receber_em_lote(http, [p1.id, p2.id])
        assert r1.status_code == 200
        assert set(r1.json()["processados"]) == {p1.id, p2.id}

        r2 = self._receber_em_lote(http, [p2.id, p3.id])
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["processados"] == [p3.id]
        assert body2["ignorados"] == [p2.id]

        db.refresh(p2)
        db.refresh(p3)
        # p2 ficou com o recibo da primeira operação (lote 1), não da segunda
        assert p2.receipt_number is not None
        assert p3.status == BillingStatus.PAID


# ---------------------------------------------------------------------------
# POST /lote/situacao (cancelamento em lote)
# ---------------------------------------------------------------------------

class TestBatchCancel:
    def _registrar(self, db, billing_id):
        from app.models.ailos_boleto import AilosBoleto
        db.add(AilosBoleto(
            billing_id=billing_id, numero_convenio='102004', nosso_numero='000000301',
            linha_digitavel='08591.02006 40045.470206 00000.003012 5 14890000009990',
            codigo_barras='08595148900000099901020040045470200000000301',
        ))
        db.commit()

    def test_lote_cancel_reporta_boletos_ativos(self, http, db, billing_pendente):
        self._registrar(db, billing_pendente.id)
        r = http.post(f"{PREFIX}/lote/situacao", json={
            "billing_ids": [billing_pendente.id], "action": "cancelar", "reason": "teste"})
        assert r.status_code == 200
        body = r.json()
        assert billing_pendente.id in body["processados"]
        assert len(body["boletos_ativos"]) == 1
        assert body["boletos_ativos"][0]["nosso_numero"] == "000000301"
        db.refresh(billing_pendente)
        assert "baixa manual pendente" in (billing_pendente.notes or "")

    def test_lote_cancel_sem_boleto_lista_vazia(self, http, db, billing_pendente):
        r = http.post(f"{PREFIX}/lote/situacao", json={
            "billing_ids": [billing_pendente.id], "action": "cancelar", "reason": "teste"})
        assert r.status_code == 200
        assert r.json()["boletos_ativos"] == []


# ---------------------------------------------------------------------------
# PUT /{id} (ajuste)
# ---------------------------------------------------------------------------

class TestUpdateBilling:
    def test_update_amount(self, http, billing_pendente):
        r = http.put(f"{PREFIX}/{billing_pendente.id}", json={
            "amount": 150.00,
            "justification": "Ajuste de valor",
        })
        assert r.status_code == 200
        assert abs(r.json()["amount"] - 150.00) < 0.01

    def test_update_due_date(self, http, billing_pendente):
        r = http.put(f"{PREFIX}/{billing_pendente.id}", json={
            "due_date": "2099-11-30",
            "justification": "Prorrogação",
        })
        assert r.status_code == 200

    def test_update_nonexistent_returns_404(self, http):
        r = http.put(f"{PREFIX}/99999", json={"amount": 100.0, "justification": "X"})
        assert r.status_code == 404

    def test_operational_cannot_update(self, http_op, billing_pendente):
        r = http_op.put(f"{PREFIX}/{billing_pendente.id}", json={
            "amount": 150.00, "justification": "X",
        })
        assert r.status_code == 403

    def test_update_paga_bloqueado(self, http, db, billing_pendente):
        billing_pendente.status = BillingStatus.PAID
        db.commit()
        r = http.put(f"{PREFIX}/{billing_pendente.id}", json={
            "amount": 150.00, "justification": "X",
        })
        assert r.status_code == 400

    def test_update_cancelada_bloqueado(self, http, db, billing_pendente):
        billing_pendente.status = BillingStatus.CANCELED
        db.commit()
        r = http.put(f"{PREFIX}/{billing_pendente.id}", json={
            "due_date": "2099-11-30", "justification": "X",
        })
        assert r.status_code == 400

    def test_update_nao_muda_status(self, http, billing_pendente):
        # Transição de status é via Receber/Cancelar, não pelo PUT genérico.
        r = http.put(f"{PREFIX}/{billing_pendente.id}", json={"status": "paga"})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /{id}
# ---------------------------------------------------------------------------

class TestDeleteBilling:
    def test_soft_delete(self, http, db, billing_pendente):
        r = http.delete(f"{PREFIX}/{billing_pendente.id}")
        assert r.status_code == 200
        db.refresh(billing_pendente)
        assert billing_pendente.is_deleted is True

    def test_delete_nonexistent_returns_404(self, http):
        r = http.delete(f"{PREFIX}/99999")
        assert r.status_code == 404

    def test_operational_cannot_delete(self, http_op, billing_pendente):
        r = http_op.delete(f"{PREFIX}/{billing_pendente.id}")
        assert r.status_code == 403

    def test_paid_billing_cannot_be_deleted(self, http, db, billing_pendente):
        billing_pendente.status = BillingStatus.PAID
        billing_pendente.payment_date = date.today()
        db.commit()

        r = http.delete(f"{PREFIX}/{billing_pendente.id}")
        assert r.status_code == 400
        db.refresh(billing_pendente)
        assert billing_pendente.is_deleted is False
