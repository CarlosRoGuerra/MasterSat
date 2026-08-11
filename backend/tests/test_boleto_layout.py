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
# O topo saiu do boleto (pedido de 08/08/2026)
# ---------------------------------------------------------------------------

def test_boleto_nao_desenha_bloco_no_topo(monkeypatch):
    """O boleto é só a ficha de compensação; a parte de cima (o antigo
    recibo/demonstrativo) foi removida por inteiro."""
    chamadas: list[str] = []
    monkeypatch.setattr(
        boleto_pdf, '_draw_recibo',
        lambda c, d, y: (chamadas.append('recibo'), boleto_pdf._draw_ficha(c, d, y))[1],
    )
    assert boleto_pdf.gerar_boleto_pdf(_dados())[:4] == b'%PDF'
    assert chamadas == []          # nada de bloco de cobrança no topo


def test_boleto_mostra_servico_e_valor(monkeypatch):
    """O boleto (layout padrão) tem de trazer o plano/produto e o valor."""
    visto: dict = {}
    original = boleto_pdf._draw_boleto_itau_style
    monkeypatch.setattr(
        boleto_pdf, '_draw_boleto_itau_style',
        lambda c, d, y, parcela=None: (
            visto.update(instrucoes=d.instrucoes or [], itens=d.itens or []),
            original(c, d, y, parcela),
        )[1],
    )
    dados = _dados(itens=[('MENSALIDADE MONITORAMENTO', Decimal('99.90'))],
                   instrucoes=['Referente a: MENSALIDADE MONITORAMENTO.'])
    assert boleto_pdf.gerar_boleto_pdf(dados)[:4] == b'%PDF'
    assert any('MENSALIDADE MONITORAMENTO' in i for i in visto['instrucoes'])
    assert visto['itens'][0][0] == 'MENSALIDADE MONITORAMENTO'


def test_recibo_avulso_continua_sendo_recibo(monkeypatch):
    """O recibo em si não mudou — continua saindo pelo botão de cobrança paga."""
    chamadas: list[str] = []
    original = boleto_pdf._draw_recibo
    monkeypatch.setattr(
        boleto_pdf, '_draw_recibo',
        lambda c, d, y: (chamadas.append('recibo'), original(c, d, y))[1],
    )
    assert boleto_pdf.gerar_recibo_pdf(_dados())[:4] == b'%PDF'
    assert chamadas == ['recibo']


def test_recibo_tem_assinatura():
    """A assinatura é o que dá ao papel cara de quitação — o recibo a mantém."""
    topo = A4[1] - 30
    c = pdfcanvas.Canvas(io.BytesIO(), pagesize=A4)
    # com assinatura, o recibo desce mais na página do que um bloco sem ela
    fim_com = boleto_pdf._draw_recibo(c, _dados(), topo)
    fim_sem = boleto_pdf._draw_bloco_cobranca(
        c, _dados(), topo, titulo='X', rotulo_total='X:', com_assinatura=False)
    assert fim_sem > fim_com
