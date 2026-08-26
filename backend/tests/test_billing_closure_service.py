"""
Testes unitários para o serviço billing_closure.

Cobertos:
- simulate_closure: estrutura, to_generate, already_generated, uninstall_events,
                    skipped abaixo de MIN_BILLING_AMOUNT, filtros pf/pj/client,
                    eventos fora do mês excluídos, grand_total = mensalidades + taxas
- execute_closure: gera billings recorrentes, não duplica billing já existente,
                   processa UninstallEvent (→ processed + billing_id),
                   skip de evento abaixo de MIN_BILLING_AMOUNT, billing type correto,
                   segundo run não reprocessa evento já tratado
- generate_closure_pdf: retorna BytesIO não vazio com header %PDF
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from sqlalchemy import select

from app.models.billing import Billing
from app.models.billing_charge_item import BillingChargeItem
from app.models.client_charge_item import ClientChargeItem
from app.models.contract import Contract
from app.models.enums import BillingStatus
from app.models.plan import Plan
from app.models.uninstall_event import UninstallEvent
from app.services.billing_closure import (
    MIN_BILLING_AMOUNT,
    execute_closure,
    generate_closure_pdf,
    simulate_closure,
)
from app.services.financial import (
    generate_item_billings,
    marcar_billing_pago,
    refresh_charge_items_for_billing,
    transfer_charge_items_to_billing,
)

# Mês de referência usado na maioria dos testes
REF_MONTH = date(2025, 5, 1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_active_contract(db, client, plan, billing_day: int = 15) -> Contract:
    """Contrato ativo com start_date que gera cobrança em Maio/2025."""
    c = Contract(
        client_id=client.id,
        plan_id=plan.id,
        start_date=date(2025, 1, billing_day),
        status="ativo",
        billing_day=billing_day,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _make_pending_event(db, client, vehicle, fee_amount: Decimal = Decimal("120.00")) -> UninstallEvent:
    """UninstallEvent pendente com uninstall_date em Maio/2025."""
    e = UninstallEvent(
        vehicle_id=vehicle.id,
        client_id=client.id,
        uninstall_date=date(2025, 5, 10),
        fee_amount=fee_amount,
        status="pending",
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


# ---------------------------------------------------------------------------
# simulate_closure
# ---------------------------------------------------------------------------

class TestSimulateClosure:
    def test_returns_required_keys(self, db, cliente, plan):
        _make_active_contract(db, cliente, plan)
        result = simulate_closure(db, REF_MONTH)
        for key in (
            "reference_month", "total_contracts", "to_generate", "already_generated",
            "total_amount", "items", "uninstall_events", "total_uninstall_fees", "grand_total",
        ):
            assert key in result

    def test_contract_due_in_month_counted(self, db, cliente, plan):
        _make_active_contract(db, cliente, plan, billing_day=15)
        result = simulate_closure(db, REF_MONTH)
        assert result["total_contracts"] >= 1
        assert result["to_generate"] >= 1

    def test_inactive_contract_excluded(self, db, cliente, plan):
        c = Contract(
            client_id=cliente.id,
            plan_id=plan.id,
            start_date=date(2025, 1, 1),
            status="cancelado",
            billing_day=1,
        )
        db.add(c)
        db.commit()
        result = simulate_closure(db, REF_MONTH)
        assert result["total_contracts"] == 0

    def test_expired_contract_excluded(self, db, cliente, plan):
        """Contrato com vigência encerrada ANTES do mês não entra no fechamento."""
        c = Contract(
            client_id=cliente.id, plan_id=plan.id,
            start_date=date(2025, 1, 15), end_date=date(2025, 4, 30),
            status="ativo", billing_day=15,
        )
        db.add(c)
        db.commit()
        result = simulate_closure(db, REF_MONTH)   # maio/2025
        assert result["total_contracts"] == 0

    def test_contract_ending_in_reference_month_still_billed(self, db, cliente, plan):
        """Contrato que termina no próprio mês ainda é faturado (último mês)."""
        c = Contract(
            client_id=cliente.id, plan_id=plan.id,
            start_date=date(2025, 1, 15), end_date=date(2025, 5, 20),
            status="ativo", billing_day=15,
        )
        db.add(c)
        db.commit()
        result = simulate_closure(db, REF_MONTH)
        assert result["total_contracts"] >= 1

    def test_quarterly_contract_not_due_in_may_excluded(self, db, cliente, plan):
        # Plano trimestral começando em Março → ciclos: Mar, Jun, Set, Dez → Maio fora
        plan_q = Plan(name="Trimestral", price=Decimal("300.00"), active=True, billing_interval_months=3)
        db.add(plan_q)
        db.commit()
        c = Contract(
            client_id=cliente.id,
            plan_id=plan_q.id,
            start_date=date(2025, 3, 1),
            status="ativo",
            billing_day=1,
        )
        db.add(c)
        db.commit()
        result = simulate_closure(db, REF_MONTH)
        assert not any(i["contract_id"] == c.id for i in result["items"])

    def test_already_generated_not_in_to_generate(self, db, cliente, plan):
        contract = _make_active_contract(db, cliente, plan)
        b = Billing(
            contract_id=contract.id,
            client_id=cliente.id,
            amount=plan.price,
            due_date=date(2025, 5, 15),
            status=BillingStatus.PENDING,
            billing_type="recorrente",
            period_label="05/2025",
            title="Plano Teste",
        )
        db.add(b)
        db.commit()
        result = simulate_closure(db, REF_MONTH)
        assert result["already_generated"] >= 1
        assert result["to_generate"] == 0

    def test_parcela_de_carne_bloqueia_mensalidade_duplicada(self, db, cliente, plan):
        """Regressão: contrato com uma parcela de carnê no mês de referência não
        pode gerar TAMBÉM uma mensalidade recorrente por cima (cobrança dobrada)."""
        contract = _make_active_contract(db, cliente, plan)
        b = Billing(
            contract_id=contract.id,
            client_id=cliente.id,
            amount=plan.price,
            due_date=date(2025, 5, 15),
            status=BillingStatus.PENDING,
            billing_type="carne",
            installment_number=3,
            installment_total=12,
            period_label="05/2025",
            title="Plano Teste • parcela 3/12",
        )
        db.add(b)
        db.commit()
        result = simulate_closure(db, REF_MONTH)
        assert result["already_generated"] >= 1
        assert result["to_generate"] == 0

    def test_uninstall_event_appears_in_response(self, db, cliente, veiculo):
        _make_pending_event(db, cliente, veiculo, fee_amount=Decimal("100.00"))
        result = simulate_closure(db, REF_MONTH)
        assert len(result["uninstall_events"]) >= 1
        item = result["uninstall_events"][0]
        assert "event_id" in item
        assert "client_id" in item
        assert "fee_amount" in item
        assert "skipped" in item
        assert item["client_id"] == cliente.id
        assert item["skipped"] is False

    def test_event_below_minimum_marked_skipped(self, db, cliente, veiculo):
        _make_pending_event(db, cliente, veiculo, fee_amount=Decimal("1.00"))
        result = simulate_closure(db, REF_MONTH)
        skipped = [e for e in result["uninstall_events"] if e["skipped"]]
        assert len(skipped) >= 1
        assert skipped[0]["skip_reason"] is not None

    def test_total_uninstall_fees_excludes_skipped(self, db, cliente, veiculo):
        _make_pending_event(db, cliente, veiculo, fee_amount=Decimal("100.00"))
        _make_pending_event(db, cliente, veiculo, fee_amount=Decimal("2.00"))
        result = simulate_closure(db, REF_MONTH)
        assert result["total_uninstall_fees"] == pytest.approx(100.0)

    def test_grand_total_sums_both(self, db, cliente, plan, veiculo):
        _make_active_contract(db, cliente, plan, billing_day=15)
        _make_pending_event(db, cliente, veiculo, fee_amount=Decimal("100.00"))
        result = simulate_closure(db, REF_MONTH)
        expected = round(result["total_amount"] + result["total_uninstall_fees"], 2)
        assert result["grand_total"] == pytest.approx(expected)

    def test_filter_pf_includes_pf_client(self, db, cliente, plan):
        assert cliente.type == "pf"
        _make_active_contract(db, cliente, plan)
        result = simulate_closure(db, REF_MONTH, filter_type="pf")
        assert result["total_contracts"] >= 1

    def test_filter_pj_excludes_pf_client(self, db, cliente, plan):
        _make_active_contract(db, cliente, plan)
        result = simulate_closure(db, REF_MONTH, filter_type="pj")
        assert result["total_contracts"] == 0

    def test_filter_by_client_id(self, db, cliente, outro_cliente, plan):
        _make_active_contract(db, cliente, plan)
        c2 = Contract(
            client_id=outro_cliente.id,
            plan_id=plan.id,
            start_date=date(2025, 1, 15),
            status="ativo",
            billing_day=15,
        )
        db.add(c2)
        db.commit()
        result = simulate_closure(db, REF_MONTH, filter_type="client", client_id=cliente.id)
        assert all(i["client_id"] == cliente.id for i in result["items"])

    def test_event_outside_month_not_included(self, db, cliente, veiculo):
        e = UninstallEvent(
            vehicle_id=veiculo.id,
            client_id=cliente.id,
            uninstall_date=date(2025, 4, 15),
            fee_amount=Decimal("100.00"),
            status="pending",
        )
        db.add(e)
        db.commit()
        result = simulate_closure(db, REF_MONTH)
        assert len(result["uninstall_events"]) == 0

    def test_reference_month_in_response(self, db):
        result = simulate_closure(db, REF_MONTH)
        assert result["reference_month"] == "05/2025"


# ---------------------------------------------------------------------------
# execute_closure
# ---------------------------------------------------------------------------

class TestBoletoUnico:
    def test_consolida_mensalidades_do_cliente_unico(self, db, cliente, plan):
        cliente.boleto_format = 'unico'
        _make_active_contract(db, cliente, plan)
        _make_active_contract(db, cliente, plan)
        db.commit()

        result = execute_closure(db, REF_MONTH)
        assert result['consolidated_unico'] == 1
        assert result['generated'] == 1  # 2 mensalidades viraram 1 boleto único

        unico = db.get(Billing, result['billing_ids'][0])
        assert float(unico.amount) == pytest.approx(199.80)  # 2 × 99.90
        assert 'boleto único' in (unico.title or '').lower()

        # individuais canceladas com referência
        canceladas = db.query(Billing).filter(
            Billing.status == BillingStatus.CANCELED, Billing.client_id == cliente.id
        ).all()
        assert len(canceladas) == 2
        assert all(f'#{unico.id}' in (b.notes or '') for b in canceladas)

    def test_nao_duplica_no_segundo_fechamento(self, db, cliente, plan):
        cliente.boleto_format = 'unico'
        _make_active_contract(db, cliente, plan)
        _make_active_contract(db, cliente, plan)
        db.commit()

        execute_closure(db, REF_MONTH)
        segunda = execute_closure(db, REF_MONTH)
        assert segunda['generated'] == 0  # idempotente: canceladas ainda marcam o período

    def test_cliente_sem_opcao_explicita_mantem_individuais(self, db, cliente, plan):
        # boleto_format vazio (cliente nunca editado) → comportamento antigo
        _make_active_contract(db, cliente, plan)
        _make_active_contract(db, cliente, plan)
        db.commit()

        result = execute_closure(db, REF_MONTH)
        assert result['consolidated_unico'] == 0
        assert result['generated'] == 2


class TestExecuteClosure:
    def test_generates_billings_for_contracts(self, db, cliente, plan):
        _make_active_contract(db, cliente, plan)
        result = execute_closure(db, REF_MONTH)
        assert result["generated"] >= 1
        assert len(result["billing_ids"]) >= 1

    def test_does_not_duplicate_existing_billing(self, db, cliente, plan):
        contract = _make_active_contract(db, cliente, plan)
        b = Billing(
            contract_id=contract.id,
            client_id=cliente.id,
            amount=plan.price,
            due_date=date(2025, 5, 15),
            status=BillingStatus.PENDING,
            billing_type="recorrente",
            period_label="05/2025",
            title="Plano Teste",
        )
        db.add(b)
        db.commit()
        result = execute_closure(db, REF_MONTH)
        assert result["generated"] == 0

    def test_uninstall_event_gets_processed(self, db, uninstall_event):
        # uninstall_event fixture já tem uninstall_date=date(2025, 5, 10)
        result = execute_closure(db, REF_MONTH)
        db.refresh(uninstall_event)
        assert uninstall_event.status == "processed"
        assert uninstall_event.billing_id is not None
        assert uninstall_event.processed_at is not None
        assert result["uninstall_fees_generated"] >= 1

    def test_event_below_minimum_gets_skipped(self, db, cliente, veiculo):
        e = UninstallEvent(
            vehicle_id=veiculo.id,
            client_id=cliente.id,
            uninstall_date=date(2025, 5, 10),
            fee_amount=Decimal("1.00"),
            status="pending",
        )
        db.add(e)
        db.commit()
        result = execute_closure(db, REF_MONTH)
        db.refresh(e)
        assert e.status == "skipped"
        assert e.billing_id is None
        assert result["uninstall_fees_skipped"] >= 1
        assert result["uninstall_fees_generated"] == 0

    def test_returns_required_summary_keys(self, db):
        result = execute_closure(db, REF_MONTH)
        for key in (
            "generated", "billing_ids", "total_amount",
            "uninstall_fees_generated", "uninstall_fees_skipped",
            "uninstall_billing_ids", "grand_total",
        ):
            assert key in result

    def test_generated_billing_is_recorrente(self, db, cliente, plan):
        _make_active_contract(db, cliente, plan)
        result = execute_closure(db, REF_MONTH)
        for bid in result["billing_ids"]:
            b = db.get(Billing, bid)
            assert b.billing_type == "recorrente"

    def test_uninstall_billing_is_taxa_desinstalacao(self, db, cliente, veiculo, contrato):
        e = UninstallEvent(
            vehicle_id=veiculo.id,
            client_id=cliente.id,
            contract_id=contrato.id,
            uninstall_date=date(2025, 5, 10),
            fee_amount=Decimal("120.00"),
            status="pending",
        )
        db.add(e)
        db.commit()
        result = execute_closure(db, REF_MONTH)
        for bid in result["uninstall_billing_ids"]:
            b = db.get(Billing, bid)
            assert b.billing_type == "taxa_desinstalacao"

    def test_second_run_does_not_reprocess_events(self, db, cliente, veiculo):
        _make_pending_event(db, cliente, veiculo, fee_amount=Decimal("100.00"))
        execute_closure(db, REF_MONTH)
        result2 = execute_closure(db, REF_MONTH)
        assert result2["uninstall_fees_generated"] == 0

    def test_empty_month_returns_zeros(self, db):
        result = execute_closure(db, REF_MONTH)
        assert result["generated"] == 0
        assert result["uninstall_fees_generated"] == 0
        assert result["grand_total"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# generate_closure_pdf
# ---------------------------------------------------------------------------

class TestGenerateClosurePdf:
    def test_returns_bytes_io(self, db, cliente, plan):
        _make_active_contract(db, cliente, plan)
        sim = simulate_closure(db, REF_MONTH)
        pdf = generate_closure_pdf(sim)
        assert isinstance(pdf, BytesIO)
        assert len(pdf.getvalue()) > 0

    def test_pdf_starts_with_pdf_header(self, db, cliente, plan):
        _make_active_contract(db, cliente, plan)
        sim = simulate_closure(db, REF_MONTH)
        pdf = generate_closure_pdf(sim)
        assert pdf.read(4) == b"%PDF"

    def test_empty_simulation_produces_pdf(self, db):
        sim = simulate_closure(db, REF_MONTH)
        pdf = generate_closure_pdf(sim)
        assert isinstance(pdf, BytesIO)
        assert len(pdf.getvalue()) > 0

    def test_with_uninstall_events_produces_pdf(self, db, cliente, veiculo):
        _make_pending_event(db, cliente, veiculo, fee_amount=Decimal("100.00"))
        sim = simulate_closure(db, REF_MONTH)
        pdf = generate_closure_pdf(sim)
        assert isinstance(pdf, BytesIO)
        assert len(pdf.getvalue()) > 0

    def test_buffer_position_reset_to_zero(self, db):
        sim = simulate_closure(db, REF_MONTH)
        pdf = generate_closure_pdf(sim)
        assert pdf.tell() == 0


# ---------------------------------------------------------------------------
# Relatório de simulação no formato do sistema antigo
# ---------------------------------------------------------------------------

class TestFormatoSimulacao:
    """O relatório agrupa por INTERVENIENTE e detalha veículo → rastreadores.
    Um veículo pode ter mais de um equipamento, cada um com sua mensalidade."""

    def _item(self, **kw):
        from datetime import date as _date
        base = dict(
            contract_id=1, client_name='CLIENTE X', interveniente_nome='CLIENTE X',
            vehicle_id=10, vehicle_plate='ABC1234', vehicle_type='caminhao',
            vehicle_created_at=_date(2024, 1, 2), contract_start_date=_date(2024, 1, 3),
            tracker_imei='111111', tracker_install_date=_date(2024, 1, 4),
            billing_amount=64.99, billing_day=10, due_date=_date(2026, 9, 10),
            first_month_charges=[],
        )
        base.update(kw)
        return base

    def _texto(self, itens, ref='08/2026'):
        from app.services.billing_closure import montar_linhas_simulacao
        return '\n'.join(montar_linhas_simulacao({'reference_month': ref, 'items': itens}))

    def test_cabecalho_traz_interveniente_e_matriz(self):
        txt = self._texto([self._item(interveniente_nome='ABR EXPRESS')])
        assert 'INTERVENIENTE: ABR EXPRESS' in txt
        assert 'MATRIZ/FILIAL: MASTERSAT' in txt
        assert 'MÊS REFERENTE: 08/2026' in txt
        assert 'MÊS VENCIMENTO: 09/2026' in txt

    def test_quantidade_de_veiculos_conta_veiculos_nao_contratos(self):
        """ABR tem 4 veículos → QUANTIDADE VEÍCULOS: 4."""
        itens = [self._item(contract_id=i, vehicle_id=i, vehicle_plate=f'AAA{i}')
                 for i in range(1, 5)]
        assert 'QUANTIDADE VEÍCULOS: 4' in self._texto(itens)

    def test_um_veiculo_com_dois_equipamentos(self):
        """ACQUE tem 1 veículo com 2 rastreadores: conta 1 veículo, soma os dois."""
        itens = [
            self._item(contract_id=1, tracker_imei='7352113'),
            self._item(contract_id=2, tracker_imei='7352114'),
        ]
        txt = self._texto(itens)
        assert 'QUANTIDADE VEÍCULOS: 1' in txt
        assert txt.count('RASTREADOR: DATA INSTALAÇÃO') == 2
        assert txt.count('TOTAL RASTREADOR:') == 2
        assert 'TOTAL VEÍCULO:' in txt and '129,98' in txt

    def test_produtos_entram_com_soma_e_no_total_do_veiculo(self):
        itens = [self._item(first_month_charges=[
            {'title': 'MENSALIDADE EM ABERTO EM 5X 1/5', 'amount': 96.19}])]
        txt = self._texto(itens)
        assert 'PRODUTO - MENSALIDADE EM ABERTO EM 5X 1/5' in txt
        assert 'SOMA PRODUTOS:' in txt and '96,19' in txt
        assert '161,18' in txt          # 64,99 + 96,19

    def test_totais_do_boleto_por_interveniente(self):
        itens = [
            self._item(contract_id=1, vehicle_id=1, interveniente_nome='A'),
            self._item(contract_id=2, vehicle_id=2, interveniente_nome='B'),
        ]
        txt = self._texto(itens)
        assert txt.count('TOTAL BOLETO S/ IMPOSTOS:') == 2
        assert txt.count('TOTAL BOLETO C/ IMPOSTOS:') == 2

    def test_valores_alinhados_na_mesma_coluna(self):
        """Rastreador e totais têm de terminar na mesma coluna, senão a
        coluna de valores sai em degrau."""
        txt = self._texto([self._item()])
        linhas = [l for l in txt.splitlines()
                  if 'MENSALIDADE 64,99:' in l or 'TOTAL RASTREADOR:' in l]
        assert len({len(l.rstrip()) for l in linhas}) == 1

    def test_sem_itens_nao_quebra(self):
        assert 'Nenhum contrato' in self._texto([])

    # ── Resumo ──────────────────────────────────────────────────────────────

    def _texto_completo(self, simulation):
        from app.services.billing_closure import montar_linhas_simulacao
        return '\n'.join(montar_linhas_simulacao(simulation))

    def test_bloco_do_interveniente_conta_equipamentos_alem_de_veiculos(self):
        """1 veículo com 2 rastreadores: 1 veículo, 2 equipamentos."""
        txt = self._texto([
            self._item(contract_id=1, tracker_imei='7352113'),
            self._item(contract_id=2, tracker_imei='7352114'),
        ])
        assert 'QUANTIDADE VEÍCULOS: 1' in txt
        assert 'QUANTIDADE EQUIPAMENTOS: 2' in txt

    def test_instalacoes_contam_so_as_do_mes_de_referencia(self):
        from datetime import date as _date
        txt = self._texto([
            self._item(contract_id=1, tracker_imei='A', tracker_install_date=_date(2026, 8, 3)),
            self._item(contract_id=2, tracker_imei='B', tracker_install_date=_date(2026, 8, 29)),
            self._item(contract_id=3, tracker_imei='C', tracker_install_date=_date(2026, 7, 30)),
            self._item(contract_id=4, tracker_imei='D', tracker_install_date=None),
        ], ref='08/2026')
        assert 'INSTALAÇÕES NO MÊS: 2' in txt

    def test_desinstalacoes_caem_no_bloco_do_interveniente_do_cliente(self):
        """O evento é do cliente; o relatório agrupa por interveniente."""
        txt = self._texto_completo({
            'reference_month': '08/2026',
            'items': [self._item(client_id=7, interveniente_nome='ABR EXPRESS')],
            'uninstall_events': [{'client_id': 7, 'client_name': 'CLIENTE X', 'fee_amount': 50.0}],
            'total_uninstall_fees': 50.0,
        })
        assert 'DESINSTALAÇÕES NO MÊS: 1' in txt

    def test_resumo_consolida_o_periodo_inteiro(self):
        txt = self._texto_completo({
            'reference_month': '08/2026',
            'items': [
                self._item(contract_id=1, client_id=1, interveniente_nome='A',
                           vehicle_id=1, tracker_imei='A1'),
                self._item(contract_id=2, client_id=1, interveniente_nome='A',
                           vehicle_id=1, tracker_imei='A2'),
                self._item(contract_id=3, client_id=2, interveniente_nome='B',
                           vehicle_id=2, tracker_imei='B1'),
            ],
            'uninstall_events': [{'client_id': 2, 'client_name': 'B', 'fee_amount': 50.0}],
            'total_uninstall_fees': 50.0,
            'total_services': 120.0,
        })
        resumo = txt.split('RESUMO DO FECHAMENTO')[1]
        assert 'INTERVENIENTES: 2' in resumo
        assert 'VEÍCULOS: 2' in resumo
        assert 'EQUIPAMENTOS: 3' in resumo
        assert 'DESINSTALAÇÕES NO MÊS: 1' in resumo
        assert 'CONTRATOS: 3' in resumo
        assert 'TOTAL TAXAS DE DESINSTALAÇÃO:' in resumo and '50,00' in resumo
        assert 'TOTAL SERVIÇOS AVULSOS:' in resumo and '120,00' in resumo
        # 3 × 64,99 + 50 + 120 = 364,97
        assert 'TOTAL GERAL:' in resumo and '364,97' in resumo

    def test_resumo_omite_linhas_zeradas(self):
        """Sem desinstalação nem serviço avulso, essas linhas não aparecem."""
        resumo = self._texto([self._item()]).split('RESUMO DO FECHAMENTO')[1]
        assert 'TOTAL TAXAS DE DESINSTALAÇÃO' not in resumo
        assert 'TOTAL SERVIÇOS AVULSOS' not in resumo
        assert 'TOTAL GERAL:' in resumo

    def test_resumo_avisa_quando_ha_cobranca_ja_gerada(self):
        """Sem o aviso, o total do resumo pareceria não bater com o que o
        fechamento vai gerar — o detalhamento lista as duas situações."""
        txt = self._texto_completo({
            'reference_month': '08/2026', 'items': [self._item()], 'already_generated': 1,
        })
        assert 'JÁ FATURADOS NO PERÍODO' in txt

    def test_resumo_nao_aparece_quando_nao_ha_nada_a_faturar(self):
        assert 'RESUMO DO FECHAMENTO' not in self._texto([])

    def test_pdf_gera(self):
        from app.services.billing_closure import generate_closure_pdf
        buf = generate_closure_pdf({'reference_month': '08/2026', 'items': [self._item()]})
        assert buf.getvalue()[:5] == b'%PDF-'

    # ── Movimentação do período (instalações e desinstalações detalhadas) ────

    def test_movimentacao_lista_cada_instalacao_do_mes(self):
        from datetime import date as _date
        txt = self._texto([
            self._item(contract_id=1, tracker_imei='AAA', vehicle_plate='ABC1D23',
                       tracker_install_date=_date(2026, 8, 4)),
            self._item(contract_id=2, tracker_imei='BBB', vehicle_plate='DEF4G56',
                       tracker_install_date=_date(2025, 3, 1)),   # fora do mês
        ], ref='08/2026')
        mov = txt.split('MOVIMENTAÇÃO DO PERÍODO')[1].split('RESUMO DO FECHAMENTO')[0]
        assert 'INSTALAÇÃO  04/08/2026  ABC1D23' in mov
        assert 'RASTREADOR AAA' in mov
        assert 'DEF4G56' not in mov          # instalada em 2025, não é do período

    def test_movimentacao_mostra_a_desinstalacao_com_a_taxa(self):
        from datetime import date as _date
        txt = self._texto_completo({
            'reference_month': '08/2026',
            'items': [self._item(client_id=7, interveniente_nome='ABR EXPRESS')],
            'uninstall_events': [{
                'client_id': 7, 'client_name': 'CLIENTE X', 'vehicle_plate': 'XYZ9K88',
                'uninstall_date': _date(2026, 8, 20), 'fee_amount': 70.0, 'skipped': False,
            }],
            'total_uninstall_fees': 70.0,
        })
        mov = txt.split('MOVIMENTAÇÃO DO PERÍODO')[1]
        assert 'DESINSTALAÇÃO  20/08/2026  XYZ9K88' in mov
        assert 'TAXA 70,00' in mov
        assert 'INTERVENIENTE: ABR EXPRESS' in mov

    def test_desinstalacao_sem_cobranca_continua_aparecendo(self):
        """Taxa abaixo do mínimo não vira cobrança, mas o serviço foi feito —
        sumir com ela do relatório esconderia trabalho da equipe."""
        from datetime import date as _date
        txt = self._texto_completo({
            'reference_month': '08/2026',
            'items': [self._item(client_id=7)],
            'uninstall_events': [{
                'client_id': 7, 'client_name': 'CLIENTE X', 'vehicle_plate': 'AAA1B22',
                'uninstall_date': _date(2026, 8, 3), 'fee_amount': 3.0, 'skipped': True,
            }],
        })
        assert 'SEM COBRANÇA' in txt.split('MOVIMENTAÇÃO DO PERÍODO')[1]

    def test_subtotal_por_interveniente_concorda_no_singular(self):
        from datetime import date as _date
        txt = self._texto([self._item(tracker_install_date=_date(2026, 8, 4))], ref='08/2026')
        mov = txt.split('MOVIMENTAÇÃO DO PERÍODO')[1]
        assert 'Subtotal: 1 instalação · 0 desinstalações' in mov

    def test_mes_sem_movimentacao_nao_cria_a_secao(self):
        from datetime import date as _date
        txt = self._texto([self._item(tracker_install_date=_date(2024, 1, 4))], ref='08/2026')
        assert 'MOVIMENTAÇÃO DO PERÍODO' not in txt

    # ── Painel de totais no topo (pedido de 08/08/2026) ─────────────────────

    def test_painel_de_totais_vem_antes_do_primeiro_cliente(self):
        txt = self._texto_completo({
            'reference_month': '08/2026',
            'items': [
                self._item(contract_id=1, client_id=1, vehicle_id=1, tracker_imei='A1'),
                self._item(contract_id=2, client_id=1, vehicle_id=1, tracker_imei='A2'),
                self._item(contract_id=3, client_id=2, vehicle_id=2, tracker_imei='B1',
                           client_name='OUTRO'),
            ],
        })
        pos_painel = txt.index('Total Geral')
        pos_cliente = txt.index('CLIENTE:')
        assert pos_painel < pos_cliente          # totais no topo
        # cabeçalho do painel com as 5 colunas do pedido
        for col in ('Veículos', 'Rastreadores', 'Instalações', 'Desinstalações', 'Total Geral'):
            assert col in txt

    def test_painel_conta_veiculos_e_rastreadores_separadamente(self):
        """1 veículo com 2 rastreadores → 1 veículo, 2 rastreadores no topo."""
        txt = self._texto_completo({
            'reference_month': '08/2026',
            'items': [
                self._item(contract_id=1, vehicle_id=1, tracker_imei='A1'),
                self._item(contract_id=2, vehicle_id=1, tracker_imei='A2'),
            ],
        })
        painel = txt.split('Total Geral')[0]
        # a linha de valores do painel: 1 veículo, 2 rastreadores
        linha_valores = [l for l in txt.splitlines() if l.strip().startswith('|') and '|' in l][1]
        celulas = [c.strip() for c in linha_valores.strip('|').split('|')]
        assert celulas[0] == '1'                 # veículos
        assert celulas[1] == '2'                 # rastreadores

    def test_total_geral_do_topo_bate_com_o_rodape(self):
        sim = {
            'reference_month': '08/2026',
            'items': [self._item(billing_amount=64.99,
                                 first_month_charges=[{'title': 'X', 'amount': 150.0}])],
            'uninstall_events': [{'client_id': 1, 'client_name': 'CLIENTE X', 'fee_amount': 70.0}],
            'total_uninstall_fees': 70.0, 'total_services': 30.0,
        }
        txt = self._texto_completo(sim)
        # 64,99 + 150 + 70 + 30 = 314,99 — no painel do topo e no TOTAL GERAL do rodapé
        assert txt.count('314,99') == 2

    def test_titulo_traz_o_mes_por_extenso(self):
        txt = self._texto([self._item()], ref='08/2026')
        assert 'PRÉVIA DE FECHAMENTO — AGOSTO/2026' in txt

    def test_sem_itens_nao_desenha_painel(self):
        txt = self._texto([])
        assert 'Total Geral' not in txt
        assert 'PRÉVIA DE FECHAMENTO' in txt     # o título continua


# ---------------------------------------------------------------------------
# Taxa de desinstalação: valor congelado, nunca somado
# ---------------------------------------------------------------------------

class TestTaxaDesinstalacaoNaoDuplica:
    """A tela preenche a taxa direta com o preço do produto ao selecioná-lo e
    envia os dois campos. Somar produto + taxa cobrava o dobro de forma
    determinística; o valor gravado no evento é o acordado e manda sempre."""

    def _evento(self, db, cliente, veiculo, **kw) -> UninstallEvent:
        e = UninstallEvent(
            vehicle_id=veiculo.id,
            client_id=cliente.id,
            uninstall_date=date(2025, 5, 10),
            status='pending',
            **kw,
        )
        db.add(e)
        db.commit()
        db.refresh(e)
        return e

    def test_produto_e_taxa_juntos_nao_somam(self, db, cliente, veiculo, produto_desinstalacao):
        # Regressão do bug: produto de R$ 120 + taxa de R$ 120 cobrava R$ 240.
        evento = self._evento(
            db, cliente, veiculo,
            fee_amount=Decimal('120.00'),
            service_product_id=produto_desinstalacao.id,
        )
        result = execute_closure(db, REF_MONTH)
        db.refresh(evento)
        billing = db.get(Billing, evento.billing_id)
        assert billing.amount == Decimal('120.00')
        assert result['uninstall_fees_generated'] >= 1

    def test_simulacao_mostra_o_mesmo_valor_da_execucao(self, db, cliente, veiculo, produto_desinstalacao):
        # A prévia também somava — o operador via o dobro antes mesmo de fechar.
        self._evento(
            db, cliente, veiculo,
            fee_amount=Decimal('120.00'),
            service_product_id=produto_desinstalacao.id,
        )
        sim = simulate_closure(db, REF_MONTH)
        evento_sim = [e for e in sim['uninstall_events'] if e['vehicle_plate'] == veiculo.plate][0]
        assert evento_sim['fee_amount'] == pytest.approx(120.0)

    def test_valor_negociado_prevalece_sobre_o_preco_de_tabela(self, db, cliente, veiculo, produto_desinstalacao):
        # Desconto acordado na retirada: R$ 80 num serviço de tabela R$ 120.
        evento = self._evento(
            db, cliente, veiculo,
            fee_amount=Decimal('80.00'),
            service_product_id=produto_desinstalacao.id,
        )
        execute_closure(db, REF_MONTH)
        db.refresh(evento)
        assert db.get(Billing, evento.billing_id).amount == Decimal('80.00')

    def test_evento_antigo_sem_valor_cai_no_preco_do_produto(self, db, cliente, veiculo, produto_desinstalacao):
        # Compatibilidade: eventos gravados antes do congelamento do valor.
        evento = self._evento(db, cliente, veiculo, service_product_id=produto_desinstalacao.id)
        execute_closure(db, REF_MONTH)
        db.refresh(evento)
        assert db.get(Billing, evento.billing_id).amount == Decimal('120.00')

    def test_mudanca_de_preco_no_catalogo_nao_altera_taxa_ja_acordada(
        self, db, cliente, veiculo, produto_desinstalacao,
    ):
        evento = self._evento(
            db, cliente, veiculo,
            fee_amount=Decimal('120.00'),
            service_product_id=produto_desinstalacao.id,
        )
        produto_desinstalacao.default_price = Decimal('500.00')
        db.commit()

        execute_closure(db, REF_MONTH)
        db.refresh(evento)
        assert db.get(Billing, evento.billing_id).amount == Decimal('120.00')

    def test_titulo_usa_o_nome_do_servico(self, db, cliente, veiculo, produto_desinstalacao):
        evento = self._evento(
            db, cliente, veiculo,
            fee_amount=Decimal('120.00'),
            service_product_id=produto_desinstalacao.id,
        )
        execute_closure(db, REF_MONTH)
        db.refresh(evento)
        assert db.get(Billing, evento.billing_id).title == produto_desinstalacao.name


# ---------------------------------------------------------------------------
# Rastreabilidade dos itens da primeira cobrança
# ---------------------------------------------------------------------------

class TestItensPrimeiraCobranca:
    def _contract(self, db, cliente, plan, *, vehicle_id=None):
        contract = Contract(
            client_id=cliente.id,
            plan_id=plan.id,
            vehicle_id=vehicle_id,
            start_date=date(2025, 5, 1),
            status='ativo',
            billing_day=15,
        )
        db.add(contract)
        db.flush()
        return contract

    def _item(self, db, cliente, *, contract_id=None, vehicle_id=None, title='Instalação'):
        item = ClientChargeItem(
            client_id=cliente.id,
            contract_id=contract_id,
            vehicle_id=vehicle_id,
            title=title,
            quantity=1,
            unit_price=Decimal('150.00'),
            total_amount=Decimal('150.00'),
            installment_count=1,
            start_date=date(2025, 5, 1),
            active=True,
            remove_after_payment=True,
            status='ativo',
        )
        db.add(item)
        db.flush()
        return item

    def test_item_sem_contrato_nao_duplica_em_varios_contratos(self, db, cliente, plan):
        self._contract(db, cliente, plan)
        self._contract(db, cliente, plan)
        item = self._item(db, cliente, contract_id=None, vehicle_id=None, title='Serviço global')
        db.commit()

        simulation = simulate_closure(db, REF_MONTH)

        assert all(
            item.id not in {charge['item_id'] for charge in row['first_month_charges']}
            for row in simulation['items']
        )
        assert [row['item_id'] for row in simulation['charge_items']].count(item.id) == 1

        execute_closure(db, REF_MONTH)
        assert db.query(Billing).filter(Billing.item_id == item.id).count() == 1

    def test_item_so_entra_no_contrato_ao_qual_foi_vinculado(self, db, cliente, plan):
        first = self._contract(db, cliente, plan)
        second = self._contract(db, cliente, plan)
        item = self._item(db, cliente, contract_id=first.id)
        db.commit()

        simulation = simulate_closure(db, REF_MONTH)
        by_contract = {row['contract_id']: row for row in simulation['items']}

        assert [charge['item_id'] for charge in by_contract[first.id]['first_month_charges']] == [item.id]
        assert by_contract[second.id]['first_month_charges'] == []

    def test_emissao_fatura_mas_so_pagamento_conclui(self, db, cliente, plan, veiculo):
        contract = self._contract(db, cliente, plan, vehicle_id=veiculo.id)
        item = self._item(db, cliente, contract_id=contract.id, vehicle_id=veiculo.id)
        db.commit()

        result = execute_closure(db, REF_MONTH)
        combined = db.scalar(
            select(Billing).where(
                Billing.id.in_(result['billing_ids']),
                Billing.contract_id == contract.id,
            )
        )
        db.refresh(item)

        assert combined is not None
        assert combined.item_id is None  # vários itens possíveis: usa tabela associativa
        assert item.status == 'faturado'
        assert item.active is False
        assert item.completed_at is None
        link = db.scalar(
            select(BillingChargeItem).where(
                BillingChargeItem.billing_id == combined.id,
                BillingChargeItem.item_id == item.id,
            )
        )
        assert link is not None
        assert link.amount == Decimal('150.00')

        marcar_billing_pago(
            db,
            combined,
            payment_date=date(2025, 5, 20),
            paid_amount=combined.amount,
            payment_method='pix',
        )
        db.refresh(item)
        assert item.status == 'concluido'
        assert item.completed_at == date(2025, 5, 20)

    def test_cancelamento_recoloca_item_na_fila(self, db, cliente, plan):
        contract = self._contract(db, cliente, plan)
        item = self._item(db, cliente, contract_id=contract.id)
        db.commit()
        result = execute_closure(db, REF_MONTH)
        combined = db.get(Billing, result['billing_ids'][0])

        combined.status = BillingStatus.CANCELED
        refresh_charge_items_for_billing(db, combined, commit=False)
        db.commit()
        db.refresh(item)

        assert item.status == 'ativo'
        assert item.active is True
        assert item.completed_at is None

    def test_item_parcelado_conclui_somente_apos_quitar_todas_as_parcelas(self, db, cliente):
        item = self._item(db, cliente, title='Serviço parcelado')
        item.installment_count = 2
        db.commit()

        billings = generate_item_billings(db, item)
        db.refresh(item)
        assert len(billings) == 2
        assert item.status == 'faturado'
        assert item.completed_at is None

        marcar_billing_pago(
            db, billings[0], payment_date=date(2025, 5, 20),
            paid_amount=billings[0].amount, payment_method='pix',
        )
        db.refresh(item)
        assert item.status == 'faturado'
        assert item.completed_at is None

        marcar_billing_pago(
            db, billings[1], payment_date=date(2025, 6, 20),
            paid_amount=billings[1].amount, payment_method='pix',
        )
        db.refresh(item)
        assert item.status == 'concluido'
        assert item.completed_at == date(2025, 6, 20)

    def test_unificacao_preserva_item_e_soma_valores_das_parcelas(self, db, cliente):
        item = self._item(db, cliente, title='Serviço unificado')
        item.installment_count = 2
        db.commit()
        sources = generate_item_billings(db, item)

        target = Billing(
            client_id=cliente.id,
            title='Cobrança unificada',
            billing_type='avulsa',
            amount=sum(Decimal(str(row.amount)) for row in sources),
            due_date=date(2025, 7, 15),
            status=BillingStatus.PENDING,
            period_label='07/2025',
        )
        db.add(target)
        db.flush()
        transfer_charge_items_to_billing(db, sources, target)
        for source in sources:
            source.status = BillingStatus.CANCELED
        refresh_charge_items_for_billing(db, target, commit=False)
        db.commit()

        link = db.scalar(select(BillingChargeItem).where(
            BillingChargeItem.billing_id == target.id,
            BillingChargeItem.item_id == item.id,
        ))
        assert link is not None
        assert link.amount == Decimal('150.00')

        marcar_billing_pago(
            db, target, payment_date=date(2025, 7, 10),
            paid_amount=target.amount, payment_method='pix',
        )
        db.refresh(item)
        assert item.status == 'concluido'
        assert item.completed_at == date(2025, 7, 10)


# ---------------------------------------------------------------------------
# Atomicidade do fechamento
# ---------------------------------------------------------------------------

class TestFechamentoAtomico:
    """O fechamento toma um pg_advisory_xact_lock da competência, que só vale
    enquanto a transação viver. Serviços chamados no meio comitavam por conta
    própria — matando o lock e deixando cobranças gravadas mesmo se o
    fechamento falhasse adiante."""

    def _charge_item(self, db, cliente):
        """Item de cobrança pendente: é o que faz o fechamento chegar em
        generate_item_billings, o último passo antes do commit."""
        from app.models.client_charge_item import ClientChargeItem
        item = ClientChargeItem(
            client_id=cliente.id,
            title='Servico avulso',
            quantity=1,
            unit_price=Decimal('50.00'),
            total_amount=Decimal('50.00'),
            installment_count=1,
            start_date=date(2025, 5, 5),
            active=True,
            status='ativo',
        )
        db.add(item)
        db.commit()
        return item

    def _explodir_no_ultimo_passo(self, monkeypatch):
        import app.services.billing_closure as bc

        def _explode(*args, **kwargs):
            raise RuntimeError('falha simulada apos criar mensalidades e taxas')

        monkeypatch.setattr(bc, 'generate_item_billings', _explode)

    def test_falha_no_meio_nao_deixa_cobranca_gravada(self, db, cliente, plan, monkeypatch):
        _make_active_contract(db, cliente, plan)
        self._charge_item(db, cliente)
        antes = db.query(Billing).count()

        self._explodir_no_ultimo_passo(monkeypatch)
        with pytest.raises(RuntimeError):
            execute_closure(db, REF_MONTH)

        db.rollback()
        assert db.query(Billing).count() == antes

    def test_evento_de_desinstalacao_nao_fica_marcado_se_o_fechamento_falhar(
        self, db, cliente, veiculo, plan, monkeypatch,
    ):
        _make_active_contract(db, cliente, plan)
        self._charge_item(db, cliente)
        evento = _make_pending_event(db, cliente, veiculo)

        self._explodir_no_ultimo_passo(monkeypatch)
        with pytest.raises(RuntimeError):
            execute_closure(db, REF_MONTH)

        db.rollback()
        db.refresh(evento)
        # Se o evento ficasse 'processed' sem a cobranca existir, a taxa
        # sumiria: nunca mais seria coletada por um novo fechamento.
        assert evento.status == 'pending'
        assert evento.billing_id is None

    def test_falha_no_segundo_item_desfaz_o_primeiro(self, db, cliente, plan, monkeypatch):
        """O cenario que expoe o bug: com dois itens, o commit interno do
        primeiro ja persistia mensalidades, taxas e o proprio item. Um erro no
        segundo nao tinha como desfazer isso — sobrava meio fechamento."""
        _make_active_contract(db, cliente, plan)
        self._charge_item(db, cliente)
        self._charge_item(db, cliente)
        antes = db.query(Billing).count()

        import app.services.billing_closure as bc
        original = bc.generate_item_billings
        chamadas = {'n': 0}

        def _falha_no_segundo(*args, **kwargs):
            chamadas['n'] += 1
            if chamadas['n'] >= 2:
                raise RuntimeError('falha no segundo item')
            return original(*args, **kwargs)

        monkeypatch.setattr(bc, 'generate_item_billings', _falha_no_segundo)

        with pytest.raises(RuntimeError):
            execute_closure(db, REF_MONTH)

        db.rollback()
        assert chamadas['n'] == 2, 'o teste precisa chegar no segundo item'
        assert db.query(Billing).count() == antes

    def test_fechamento_bem_sucedido_continua_gravando(self, db, cliente, plan):
        _make_active_contract(db, cliente, plan)
        self._charge_item(db, cliente)
        result = execute_closure(db, REF_MONTH)
        assert result['generated'] >= 1
        assert result['services_generated'] >= 1
        assert db.query(Billing).count() >= 1
