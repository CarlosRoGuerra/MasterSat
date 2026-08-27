"""
Precisão financeira: valor_com_juros (financial.py) e unify_billings (billings.py)
devem operar em Decimal de ponta a ponta, com a MESMA política de arredondamento
já usada no resto do serviço (quantize 2 casas, ROUND_HALF_UP — ver
`_quantize_amount`, `generate_item_billings`, `prorated_amount`).

Hoje as duas funções convertem para float antes de somar/multiplicar, o que:
  (a) usa round-half-to-even do Python sobre um valor binário impreciso
      (ex.: 1.50 * 1.03 é matematicamente 1.545, mas round(1.545, 2) em
      float dá 1.54 — a representação binária de 1.545 é ligeiramente menor);
  (b) acumula erro de soma em float (ex.: 10.10 + 10.20 == 20.299999999999997).

Estes testes fixam o comportamento CORRETO (o que deveria valer após a
correção). Rodar antes da alteração deve reproduzir as falhas descritas acima;
depois da alteração, todos devem passar.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models.billing import Billing
from app.models.client_charge_item import ClientChargeItem
from app.models.enums import BillingStatus
from app.services.financial import generate_item_billings, valor_com_juros

PREFIX = '/api/v1/billings'


# ---------------------------------------------------------------------------
# 1. valor simples — sem atraso, sem juros
# ---------------------------------------------------------------------------

class TestValorSimples:
    def test_nao_vencida_retorna_none(self):
        futuro = date.today() + timedelta(days=10)
        assert valor_com_juros(Decimal('150.00'), futuro) is None

    def test_vence_hoje_retorna_none(self):
        # dias == 0 não é atraso.
        assert valor_com_juros(Decimal('150.00'), date.today()) is None


# ---------------------------------------------------------------------------
# 2. juros — multa 2% + 1% a.m. (fração conta como mês cheio)
# ---------------------------------------------------------------------------

class TestJuros:
    def test_um_mes_de_atraso(self):
        venc = date(2025, 1, 10)
        ref = date(2025, 1, 25)  # 15 dias → 1 mês (ceil)
        # 150.00 * 1.03 = 154.50 exato
        assert valor_com_juros(Decimal('150.00'), venc, ref) == 154.50

    def test_dois_meses_de_atraso_arredonda_para_cima(self):
        venc = date(2025, 1, 10)
        ref = date(2025, 2, 10)  # 31 dias → 2 meses (ceil)
        # 100 * 1.02 + 100 * 0.01 * 2 = 104.00 exato
        assert valor_com_juros(Decimal('100.00'), venc, ref) == 104.00

    def test_aceita_decimal_vindo_do_banco(self):
        """billing.amount chega como Decimal (coluna Numeric) — a função não
        deve depender de receber float."""
        from decimal import ROUND_HALF_UP

        venc = date(2025, 1, 1)
        ref = date(2025, 1, 16)  # 15 dias → 1 mês
        resultado = valor_com_juros(Decimal('99.90'), venc, ref)
        esperado = (Decimal('99.90') * Decimal('1.03')).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP,
        )
        assert resultado == float(esperado)


# ---------------------------------------------------------------------------
# 3. múltiplas parcelas — a soma das parcelas deve fechar com o total exato
# ---------------------------------------------------------------------------

class TestMultiplasParcelas:
    def test_soma_das_parcelas_bate_com_total_sem_perda_de_centavos(self, db, cliente):
        item = ClientChargeItem(
            client_id=cliente.id,
            title='Serviço parcelado',
            unit_price=Decimal('100.00'),
            total_amount=Decimal('100.00'),
            installment_count=3,
            start_date=date(2025, 1, 10),
            active=True,
            status='ativo',
        )
        db.add(item)
        db.commit()
        db.refresh(item)

        created = generate_item_billings(db, item)
        assert len(created) == 3
        total = sum((Decimal(str(b.amount)) for b in created), Decimal('0.00'))
        assert total == Decimal('100.00')
        # 100 / 3 = 33.33 com resto de 0.01 acumulado na última parcela.
        assert [Decimal(str(b.amount)) for b in created] == [
            Decimal('33.33'), Decimal('33.33'), Decimal('33.34'),
        ]


# ---------------------------------------------------------------------------
# 4. valores com centavos — soma de duas cobranças na unificação
# ---------------------------------------------------------------------------

class TestValoresComCentavos:
    def _billing(self, db, cliente, amount: Decimal, status=BillingStatus.PENDING, due_date=None):
        b = Billing(
            client_id=cliente.id,
            amount=amount,
            due_date=due_date or (date.today() + timedelta(days=5)),
            status=status,
            billing_type='avulsa',
            title='Cobrança teste',
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        return b

    def test_soma_preserva_precisao_decimal(self, http, db, cliente):
        # Caso conceitual do pedido: Decimal("10.10") + Decimal("10.20") == 20.30
        b1 = self._billing(db, cliente, Decimal('10.10'))
        b2 = self._billing(db, cliente, Decimal('10.20'))

        r = http.post(f'{PREFIX}/unificar', json={
            'billing_ids': [b1.id, b2.id],
            'due_date': '2099-01-15',
        })
        assert r.status_code == 200
        nova = r.json()
        assert nova['amount'] == 20.30
        # A cobrança persistida no banco não pode carregar o resíduo binário
        # de uma soma em float (ex.: 20.299999999999997).
        persisted = db.get(Billing, nova['id'])
        assert Decimal(str(persisted.amount)) == Decimal('20.30')


# ---------------------------------------------------------------------------
# 5. arredondamento — política ROUND_HALF_UP, não round-half-to-even do float
# ---------------------------------------------------------------------------

class TestArredondamento:
    def test_meio_centavo_arredonda_para_cima(self):
        # 1,50 * 1,03 = 1,545 → ROUND_HALF_UP deve dar 1.55.
        # round() do Python sobre o float 1.545 (impreciso em binário) dá 1.54.
        venc = date(2025, 1, 1)
        ref = date(2025, 1, 16)  # 15 dias → 1 mês
        assert valor_com_juros(Decimal('1.50'), venc, ref) == 1.55

    def test_outro_caso_meio_centavo(self):
        # 2,25 * 1,02 + 2,25*0,01*4 = 2,3850 → ROUND_HALF_UP = 2.39
        venc = date(2025, 1, 1)
        ref = date(2025, 4, 15)  # ~104 dias → 4 meses (ceil)
        assert valor_com_juros(Decimal('2.25'), venc, ref) == 2.39


# ---------------------------------------------------------------------------
# 6. soma de vários valores — 3+ cobranças, drift clássico de float
# ---------------------------------------------------------------------------

class TestSomaDeVariosValores:
    def _billing(self, db, cliente, amount: Decimal):
        b = Billing(
            client_id=cliente.id,
            amount=amount,
            due_date=date.today() + timedelta(days=5),
            status=BillingStatus.PENDING,
            billing_type='avulsa',
            title='Cobrança teste',
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        return b

    def test_soma_de_tres_valores_sem_drift_binario(self, http, db, cliente):
        # sum([19.9, 19.9, 19.9]) em float puro == 59.699999999999996
        billings = [self._billing(db, cliente, Decimal('19.90')) for _ in range(3)]
        r = http.post(f'{PREFIX}/unificar', json={
            'billing_ids': [b.id for b in billings],
            'due_date': '2099-01-15',
        })
        assert r.status_code == 200
        assert r.json()['amount'] == 59.70


# ---------------------------------------------------------------------------
# 7. valores próximos aos limites de arredondamento
# ---------------------------------------------------------------------------

class TestLimitesDeArredondamento:
    def test_arredonda_para_baixo_quando_nao_e_meio(self):
        # 0,70 * 1,02 + 0,70*0,01*3 = 0,735 → ROUND_HALF_UP = 0.74 (para cima)
        venc = date(2025, 1, 1)
        ref = date(2025, 2, 20)  # ~50 dias → 2 meses... usamos referência p/ 3 meses
        ref = date(2025, 3, 2)  # 60 dias → 2 meses; ajustamos abaixo
        # Recalcular explicitamente para 3 meses cheios (61-90 dias).
        ref = date(2025, 3, 10)  # 68 dias → 3 meses (ceil)
        assert valor_com_juros(Decimal('0.70'), venc, ref) == 0.74

    def test_nao_arredonda_valor_ja_exato(self):
        # 10,00 * 1,03 = 10,30 — nenhuma ambiguidade de arredondamento.
        venc = date(2025, 1, 1)
        ref = date(2025, 1, 16)
        assert valor_com_juros(Decimal('10.00'), venc, ref) == 10.30
