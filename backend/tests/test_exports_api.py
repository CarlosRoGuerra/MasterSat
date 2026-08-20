"""Testes das exportações (foco no PDF de inadimplentes)."""
from __future__ import annotations

from datetime import date

from app.models.enums import BillingStatus

PREFIX = '/api/v1/exports'


def test_inadimplentes_pdf(http, db, cliente, billing_vencida):
    # billing_vencida (2020) é OVERDUE → cliente aparece no relatório
    r = http.get(f'{PREFIX}/delinquents', params={'fmt': 'pdf'})
    assert r.status_code == 200
    assert r.headers['content-type'] == 'application/pdf'
    assert r.content[:4] == b'%PDF'


def test_inadimplentes_csv_ainda_funciona(http, billing_vencida):
    r = http.get(f'{PREFIX}/delinquents', params={'fmt': 'csv'})
    assert r.status_code == 200
    assert 'text/csv' in r.headers['content-type']


def test_formato_invalido_422(http):
    r = http.get(f'{PREFIX}/delinquents', params={'fmt': 'docx'})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Inadimplentes: uma linha por cobrança vencida + filtro de período
# ---------------------------------------------------------------------------

class TestInadimplentesDetalhado:
    """O relatório passou a detalhar cada cobrança (valor, vencimento, boleto)
    em vez de agregar por cliente."""

    def _vencida(self, db, cliente, valor, vencimento, nosso_numero=None):
        from decimal import Decimal

        from app.models.ailos_boleto import AilosBoleto
        from app.models.billing import Billing

        b = Billing(
            client_id=cliente.id, amount=Decimal(str(valor)), due_date=vencimento,
            status=BillingStatus.OVERDUE, title='MENSALIDADE', billing_type='recorrente',
        )
        db.add(b); db.commit(); db.refresh(b)
        if nosso_numero:
            db.add(AilosBoleto(billing_id=b.id, numero_convenio='102004', nosso_numero=nosso_numero))
            db.commit()
        return b

    def test_csv_traz_uma_linha_por_cobranca(self, http, db, cliente):
        self._vencida(db, cliente, 100, date(2026, 7, 10))
        self._vencida(db, cliente, 200, date(2026, 7, 20))

        r = http.get(f'{PREFIX}/delinquents', params={'fmt': 'csv'})
        assert r.status_code == 200
        linhas = [l for l in r.text.strip().splitlines() if l.strip()]
        assert len(linhas) == 3          # cabeçalho + 2 cobranças
        assert 'Número do Boleto' in linhas[0]
        # colunas removidas a pedido do cliente
        assert 'CPF/CNPJ' not in linhas[0] and 'Telefone' not in linhas[0]

    def test_inclui_o_nosso_numero_do_boleto(self, http, db, cliente):
        self._vencida(db, cliente, 150, date(2026, 7, 15), nosso_numero='2587')
        r = http.get(f'{PREFIX}/delinquents', params={'fmt': 'csv'})
        assert '2587' in r.text

    def test_filtra_por_periodo_de_vencimento(self, http, db, cliente):
        self._vencida(db, cliente, 100, date(2026, 7, 5))
        self._vencida(db, cliente, 200, date(2026, 7, 25))

        r = http.get(f'{PREFIX}/delinquents', params={
            'fmt': 'csv', 'due_from': '2026-07-20', 'due_to': '2026-07-31'})
        linhas = [l for l in r.text.strip().splitlines() if l.strip()]
        assert len(linhas) == 2          # cabeçalho + só a de 25/07
        assert '25/07/2026' in r.text and '05/07/2026' not in r.text

    def test_pdf_com_periodo_continua_gerando(self, http, db, cliente):
        self._vencida(db, cliente, 100, date(2026, 7, 15))
        r = http.get(f'{PREFIX}/delinquents', params={
            'fmt': 'pdf', 'due_from': '2026-07-01', 'due_to': '2026-07-31'})
        assert r.status_code == 200
        assert r.content[:5] == b'%PDF-'


# ---------------------------------------------------------------------------
# Relatório de cobranças por período
# ---------------------------------------------------------------------------

class TestRelatorioCobrancas:
    """"Quais clientes pagaram do dia 10 até o dia 15" — o período precisa
    filtrar a DATA DE PAGAMENTO, não a de vencimento."""

    def _cobranca(self, db, cliente, valor, vencimento, pagamento=None, status=None):
        from decimal import Decimal

        from app.models.billing import Billing

        b = Billing(
            client_id=cliente.id, amount=Decimal(str(valor)), due_date=vencimento,
            payment_date=pagamento,
            paid_amount=Decimal(str(valor)) if pagamento else None,
            status=status or (BillingStatus.PAID if pagamento else BillingStatus.PENDING),
            title='MENSALIDADE', billing_type='recorrente',
        )
        db.add(b); db.commit(); db.refresh(b)
        return b

    def test_pagos_no_periodo_filtra_pela_data_de_pagamento(self, http, db, cliente):
        # vencem no mesmo dia, mas foram pagos em datas diferentes
        self._cobranca(db, cliente, 100, date(2026, 7, 1), pagamento=date(2026, 7, 12))
        self._cobranca(db, cliente, 200, date(2026, 7, 1), pagamento=date(2026, 7, 20))

        r = http.get(f'{PREFIX}/billings-report', params={
            'fmt': 'csv', 'situacao': 'paga', 'periodo_por': 'pagamento',
            'date_from': '2026-07-10', 'date_to': '2026-07-15'})
        assert r.status_code == 200
        assert '12/07/2026' in r.text and '20/07/2026' not in r.text

    def test_periodo_por_vencimento_usa_o_vencimento(self, http, db, cliente):
        self._cobranca(db, cliente, 100, date(2026, 7, 12))
        self._cobranca(db, cliente, 200, date(2026, 7, 25))

        r = http.get(f'{PREFIX}/billings-report', params={
            'fmt': 'csv', 'situacao': 'pendente', 'periodo_por': 'vencimento',
            'date_from': '2026-07-10', 'date_to': '2026-07-15'})
        linhas = [l for l in r.text.strip().splitlines() if l.strip()]
        assert len(linhas) == 2          # cabeçalho + a que vence em 12/07

    def test_em_aberto_nao_traz_pagas(self, http, db, cliente):
        self._cobranca(db, cliente, 100, date(2026, 7, 12))
        self._cobranca(db, cliente, 200, date(2026, 7, 13), pagamento=date(2026, 7, 13))

        r = http.get(f'{PREFIX}/billings-report', params={
            'fmt': 'csv', 'situacao': 'pendente', 'periodo_por': 'vencimento'})
        linhas = [l for l in r.text.strip().splitlines() if l.strip()]
        assert len(linhas) == 2

    def test_todas_traz_pagas_e_em_aberto(self, http, db, cliente):
        self._cobranca(db, cliente, 100, date(2026, 7, 12))
        self._cobranca(db, cliente, 200, date(2026, 7, 13), pagamento=date(2026, 7, 13))

        r = http.get(f'{PREFIX}/billings-report', params={
            'fmt': 'csv', 'situacao': 'todas', 'periodo_por': 'vencimento'})
        linhas = [l for l in r.text.strip().splitlines() if l.strip()]
        assert len(linhas) == 3

    def test_pdf_gera(self, http, db, cliente):
        self._cobranca(db, cliente, 100, date(2026, 7, 12), pagamento=date(2026, 7, 12))
        r = http.get(f'{PREFIX}/billings-report', params={
            'fmt': 'pdf', 'situacao': 'paga', 'periodo_por': 'pagamento',
            'date_from': '2026-07-01', 'date_to': '2026-07-31'})
        assert r.status_code == 200 and r.content[:5] == b'%PDF-'

    def test_situacao_invalida_rejeitada(self, http):
        assert http.get(f'{PREFIX}/billings-report',
                        params={'situacao': 'inventada'}).status_code == 422


# ---------------------------------------------------------------------------
# CSV/Excel injection: célula que começa com fórmula é neutralizada
# ---------------------------------------------------------------------------

class TestFormulaInjection:
    def test_neutralize_formula_unitario(self):
        from app.api.v1.endpoints.exports import _neutralize_formula
        assert _neutralize_formula('=1+1') == "'=1+1"
        assert _neutralize_formula('@SUM(A1)') == "'@SUM(A1)"
        assert _neutralize_formula('-2+3') == "'-2+3"
        assert _neutralize_formula('João Silva') == 'João Silva'   # texto comum intacto
        assert _neutralize_formula(120.0) == 120.0                 # número intacto

    def test_export_clients_csv_neutraliza_nome_malicioso(self, http, db):
        from app.models.client import Client
        from app.models.enums import ClientStatus
        db.add(Client(name='=HYPERLINK("http://x","clique")', cpf_cnpj='111',
                      type='pf', status=ClientStatus.ACTIVE))
        db.commit()
        r = http.get(f'{PREFIX}/clients', params={'fmt': 'csv'})
        assert r.status_code == 200
        assert "'=HYPERLINK" in r.text          # prefixado com apóstrofo
        assert '\n=HYPERLINK' not in r.text      # nunca cru no início de campo
