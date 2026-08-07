"""
Layout do boleto após a reunião de 07/08/2026.

Duas decisões do cliente:
  1. o boleto NÃO pode sair com "RECIBO DE PAGAMENTO" assinado no topo —
     quitação é o recibo, emitido só depois da compensação e da baixa;
  2. o pagador tem de entender o motivo da cobrança olhando o boleto.
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdfcanvas

from app.api.v1.endpoints.boletos import descricao_servico
from app.services import boleto_pdf
from app.services.boleto_ailos import gerar_dados_boleto


def _billing(**kw):
    base = dict(id=10, title=None, billing_type='recorrente', period_label=None,
                installment_number=None, installment_total=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _dados(**kw):
    base = dict(
        billing_id=10, valor=Decimal('99.90'), vencimento=date(2027, 6, 30),
        sacado_nome='Cliente Teste', sacado_cpf_cnpj='12345678901',
        sacado_endereco='Rua Teste, 100', data_emissao=date(2027, 6, 1),
    )
    base.update(kw)
    return gerar_dados_boleto(**base)


# ---------------------------------------------------------------------------
# Descritivo do serviço
# ---------------------------------------------------------------------------

def test_mensalidade_ganha_nome_legivel_e_competencia():
    """No fechamento o título é só "Mensalidade" — sozinho não explica nada."""
    d = descricao_servico(_billing(title='Mensalidade', period_label='08/2026'), 'ABC1D23')
    assert d == 'MENSALIDADE DE MONITORAMENTO VEICULAR · REF. 08/2026 · PLACA ABC1D23'


def test_o_titulo_entra_quando_acrescenta_informacao():
    d = descricao_servico(_billing(billing_type='instalacao', title='Instalação 2º veículo'))
    assert 'INSTALAÇÃO DE EQUIPAMENTO DE RASTREAMENTO' in d
    assert 'INSTALAÇÃO 2º VEÍCULO' in d


def test_titulo_redundante_nao_e_repetido():
    d = descricao_servico(_billing(title='Mensalidade'))
    assert d.count('MENSALIDADE') == 1


def test_parcelamento_aparece():
    d = descricao_servico(_billing(installment_number=2, installment_total=5))
    assert 'PARCELA 2/5' in d


def test_parcela_unica_nao_polui_a_descricao():
    assert 'PARCELA' not in descricao_servico(_billing(installment_total=1))


def test_tipo_desconhecido_cai_no_titulo():
    d = descricao_servico(_billing(billing_type='outro_qualquer', title='Taxa de religação'))
    assert d.startswith('TAXA DE RELIGAÇÃO')


def test_sem_tipo_e_sem_titulo_ainda_diz_alguma_coisa():
    assert descricao_servico(_billing(billing_type='', title=None)) == 'SERVIÇO DE RASTREAMENTO'


@pytest.mark.parametrize('tipo, trecho', [
    ('recorrente', 'MENSALIDADE DE MONITORAMENTO'),
    ('prorata', 'PRÓ-RATA'),
    ('instalacao', 'INSTALAÇÃO'),
    ('desinstalacao', 'DESINSTALAÇÃO'),
    ('manutencao', 'MANUTENÇÃO'),
    ('adesao', 'ADESÃO'),
])
def test_cada_tipo_de_cobranca_tem_nome_proprio(tipo, trecho):
    assert trecho in descricao_servico(_billing(billing_type=tipo))


# ---------------------------------------------------------------------------
# O recibo saiu do boleto
# ---------------------------------------------------------------------------

def test_boleto_desenha_demonstrativo_e_nao_recibo(monkeypatch):
    chamadas: list[str] = []

    def _spy(nome, original):
        def _fn(c, d, y_top):
            chamadas.append(nome)
            return original(c, d, y_top)
        return _fn

    monkeypatch.setattr(boleto_pdf, '_draw_recibo', _spy('recibo', boleto_pdf._draw_recibo))
    monkeypatch.setattr(boleto_pdf, '_draw_demonstrativo',
                        _spy('demonstrativo', boleto_pdf._draw_demonstrativo))

    boleto_pdf.gerar_boleto_pdf(_dados())
    assert chamadas == ['demonstrativo']


def test_recibo_avulso_continua_sendo_recibo(monkeypatch):
    """O recibo em si não mudou — só deixou de vir junto do boleto."""
    chamadas: list[str] = []
    original = boleto_pdf._draw_recibo
    monkeypatch.setattr(
        boleto_pdf, '_draw_recibo',
        lambda c, d, y: (chamadas.append('recibo'), original(c, d, y))[1],
    )
    assert boleto_pdf.gerar_recibo_pdf(_dados())[:4] == b'%PDF'
    assert chamadas == ['recibo']


def test_demonstrativo_nao_tem_assinatura_e_recibo_tem():
    """A assinatura é o que dá ao papel cara de quitação; o demonstrativo
    ocupa menos altura justamente por não tê-la."""
    topo = A4[1] - 30
    c = pdfcanvas.Canvas(io.BytesIO(), pagesize=A4)
    fim_recibo = boleto_pdf._draw_recibo(c, _dados(), topo)
    fim_demo = boleto_pdf._draw_demonstrativo(c, _dados(), topo)
    assert fim_demo > fim_recibo
