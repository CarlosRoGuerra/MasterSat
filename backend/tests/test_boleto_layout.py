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

def test_boleto_tem_recibo_do_pagador_sem_quitacao(monkeypatch):
    """O topo do boleto é o Recibo do Pagador / fatura (QR Pix, itens por placa,
    faixa-recibo), no modelo Hinova aprovado — e não o recibo de quitação
    assinado (esse continua saindo só pelo botão de cobrança paga)."""
    topo: list[bool] = []
    original = boleto_pdf._draw_recibo_pagador
    monkeypatch.setattr(
        boleto_pdf, '_draw_recibo_pagador',
        lambda c, d, y: (topo.append(True), original(c, d, y))[1],
    )
    recibos: list[str] = []
    monkeypatch.setattr(boleto_pdf, '_draw_recibo', lambda c, d, y: recibos.append('recibo'))
    assert boleto_pdf.gerar_boleto_pdf(_dados())[:4] == b'%PDF'
    assert topo == [True]                                 # recibo do pagador desenhado no topo
    assert recibos == []                                  # sem quitação/assinatura


def test_boleto_mostra_os_itens_cobrados(monkeypatch):
    """O boleto tem de listar os itens/placas cobrados na fatura do topo."""
    visto: dict = {}
    original = boleto_pdf._draw_itens_recibo
    monkeypatch.setattr(
        boleto_pdf, '_draw_itens_recibo',
        lambda c, itens, y: (visto.update(itens=itens), original(c, itens, y))[1],
    )
    dados = _dados(itens=[('ABC1D23 - MENSALIDADE', Decimal('99.90'))],
                   instrucoes=['Referente a: MENSALIDADE MONITORAMENTO.'])
    assert boleto_pdf.gerar_boleto_pdf(dados)[:4] == b'%PDF'
    assert visto['itens'][0][0] == 'ABC1D23 - MENSALIDADE'


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


# ---------------------------------------------------------------------------
# Carnê — modelo Hinova/Itaú de referência: canhoto + ficha compactos,
# 3 parcelas por página A4 (era 2, com muito espaço vazio sobrando).
# ---------------------------------------------------------------------------

def _parcelas(n: int):
    return [_dados(billing_id=100 + i, vencimento=date(2027 + (i // 12), (i % 12) + 1, 15)) for i in range(n)]


def test_carne_cabe_3_por_pagina():
    """Regressão de densidade: 7 parcelas devem ocupar 3 páginas (3+3+1),
    não 4 (3+2+2 ou pior) — era o sintoma de um carnê 'espalhado'."""
    from pypdf import PdfReader
    pdf = boleto_pdf.gerar_carne_pdf(_parcelas(7))
    assert pdf[:4] == b'%PDF'
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 3


@pytest.mark.parametrize('n,paginas', [(1, 1), (3, 1), (4, 2), (6, 2), (9, 3)])
def test_carne_paginacao_por_n_parcelas(n, paginas):
    from pypdf import PdfReader
    pdf = boleto_pdf.gerar_carne_pdf(_parcelas(n))
    assert len(PdfReader(io.BytesIO(pdf)).pages) == paginas


def test_carne_traz_numero_da_parcela():
    """'Parcela X/N' no cabeçalho — é o que diferencia uma ficha da outra
    num carnê de várias mensalidades iguais."""
    from pypdf import PdfReader
    pdf = boleto_pdf.gerar_carne_pdf(_parcelas(3))
    texto = ''.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(pdf)).pages)
    assert 'Parcela 1/3' in texto
    assert 'Parcela 2/3' in texto
    assert 'Parcela 3/3' in texto
