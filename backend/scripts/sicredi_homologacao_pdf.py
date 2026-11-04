"""Impressão pelo beneficiário, exclusiva para homologação sem registro bancário.

Fontes: API Cobrança 4.0, pp. 216–219; CNAB 400 2.9, pp. 39–49;
Manual QR Code Cobrança. Pix aleatório serve apenas à demonstração do layout.
Este módulo não autentica nem cadastra títulos na API.
"""
from __future__ import annotations

from binascii import crc_hqx
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import os
from uuid import uuid4

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.common import I2of5
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import black
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas


def _tlv(campo: str, valor: str) -> str:
    if not valor.isascii() or len(valor) > 99:
        raise ValueError('Campo Pix fora do limite de 99 caracteres ASCII.')
    return f'{campo}{len(valor):02d}{valor}'


def crc_pix(payload_sem_crc: str) -> str:
    """CRC16-CCITT, polinômio 0x1021, início 0xFFFF (guia QR fornecido)."""
    return f'{crc_hqx(payload_sem_crc.encode("ascii"), 0xFFFF):04X}'


def gerar_pix_demonstrativo(valor: Decimal) -> str:
    """BR Code dinâmico com location aleatória NÃO registrada no Sicredi.

    Não usa chave Pix de pessoa/empresa nem faz requisições ao endereço.
    O domínio de homologação é o do retorno tipo 8, CNAB 400 2.9, p. 39.
    """
    if not valor.is_finite() or valor <= 0 or valor != valor.quantize(Decimal('0.01')):
        raise ValueError('Valor Pix deve ser positivo com até duas casas decimais.')
    location = f'pix-qrcode-h.sicredi.com.br/qr/v2/cobv/{uuid4().hex}'
    conta_pix = _tlv('00', 'br.gov.bcb.pix') + _tlv('25', location)
    campos = [('00', '01'), ('01', '12'), ('26', conta_pix), ('52', '0000'),
              ('53', '986'), ('54', f'{valor:.2f}'), ('58', 'BR'),
              ('59', 'MASTERSAT'), ('60', 'HOMOLOGACAO'), ('62', _tlv('05', '***'))]
    payload = ''.join(_tlv(campo, conteudo) for campo, conteudo in campos) + '6304'
    return payload + crc_pix(payload)


def _fonte_banco() -> str:
    # CNAB 400 2.9, p. 41: Arial em negrito. Embed local, sem distribuir a fonte.
    nome = 'SicrediArialBold'
    if nome not in pdfmetrics.getRegisteredFontNames():
        fonte = Path(os.environ.get('SICREDI_HOMOLOGACAO_FONTE_ARIAL_NEGRITO')
                     or Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/arialbd.ttf')
        if not fonte.is_file():
            raise ValueError('Configure SICREDI_HOMOLOGACAO_FONTE_ARIAL_NEGRITO com o caminho de arialbd.ttf.')
        pdfmetrics.registerFont(TTFont(nome, str(fonte)))
    return nome


from app.services.sicredi_codigos import (  # noqa: F401
    _digitos, modulo_10, modulo_11, fator_vencimento, codigo_barras, linha_digitavel,
)


@dataclass(frozen=True)
class DadosHomologacao:
    beneficiario_nome: str
    beneficiario_cnpj: str
    cooperativa: str
    posto: str
    codigo_beneficiario: str
    conta: str
    pagador_nome: str
    pagador_documento: str
    nosso_numero: str
    seu_numero: str
    emissao: date
    vencimento: date
    valor: Decimal
    especie: str = 'DSI'
    pagador_endereco: str = ''
    pagador_cidade: str = ''
    pagador_uf: str = ''
    pagador_cep: str = ''
    endereco_ficticio: bool = False
    pix_demonstrativo: bool = False


def _documento(texto: str) -> str:
    if len(texto) == 11:
        return f'{texto[:3]}.{texto[3:6]}.{texto[6:9]}-{texto[9:]}'
    if len(texto) == 14:
        return f'{texto[:2]}.{texto[2:5]}.{texto[5:8]}/{texto[8:12]}-{texto[12:]}'
    raise ValueError('Documento deve conter CPF ou CNPJ sem pontuação.')


def gerar_pdf(d: DadosHomologacao) -> tuple[bytes, dict]:
    """Boleto A4 com recibo, ficha de compensação e I2of5 de 103 × 13 mm."""
    barras = codigo_barras(d.cooperativa, d.posto, d.codigo_beneficiario,
                           d.nosso_numero, d.vencimento, d.valor)
    linha = linha_digitavel(barras)
    pix = gerar_pix_demonstrativo(d.valor) if d.pix_demonstrativo else ''
    fonte_banco = _fonte_banco()
    buf = BytesIO()
    c = Canvas(buf, pagesize=A4, pageCompression=0)
    c.setTitle('MasterSat - boleto de homologação - sem validade financeira')
    c.setAuthor('MasterSat')
    c.setSubject('Impressão pelo beneficiário, conforme manual API Cobrança 4.0, pp. 218–219')
    c.setLineWidth(0.35)

    def texto(x, y, valor, tamanho=7, negrito=False, largura=None):
        fonte = 'Helvetica-Bold' if negrito else 'Helvetica'
        if largura:
            while tamanho > 5 and stringWidth(valor, fonte, tamanho) > largura * mm:
                tamanho -= 0.2
        c.setFillColor(black)
        c.setFont(fonte, tamanho)
        c.drawString(x * mm, y * mm, valor)

    def celula(x, topo, largura, altura, rotulo, valor='', *, negrito=False):
        c.rect(x * mm, (topo - altura) * mm, largura * mm, altura * mm)
        texto(x + 1, topo - 1.7, rotulo, 5.2, largura=largura - 2)
        if valor:
            texto(x + 1, topo - altura + 1, valor, 7.4, negrito, largura=largura - 2)

    # Arquivo original em preto e branco fornecido com a documentação CNAB.
    logo = Path(__file__).resolve().parents[1] / 'app/static/sicredi_logo.png'

    def cabecalho(topo, recibo=False):
        if logo.is_file():
            c.drawImage(str(logo), 5 * mm, (topo - 7) * mm, width=27 * mm,
                        height=8 * mm, preserveAspectRatio=True, mask='auto')
        else:
            texto(5, topo - 5, 'Sicredi', 17, True)
        c.line(35 * mm, (topo - 7) * mm, 35 * mm, topo * mm)
        c.setFont(fonte_banco, 15)
        c.drawString(37 * mm, (topo - 5) * mm, '748-X')
        c.line(57 * mm, (topo - 7) * mm, 57 * mm, topo * mm)
        if recibo:
            texto(153, topo - 5, 'Recibo do Pagador', 11, True, largura=52)
        else:
            texto(60, topo - 5, linha, 9.5, True, largura=145)
        return topo - 8

    valor = f'{d.valor:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')
    numero = f'{d.nosso_numero[:2]}/{d.nosso_numero[2:8]}-{d.nosso_numero[8:]}'
    agencia = f'{d.cooperativa}.{d.posto}.{d.codigo_beneficiario}'

    def qr_code(x, y):
        # Quatro módulos de margem e saída vetorial, sem perda por rasterização.
        qr = QrCodeWidget(pix, barLevel='M', barBorder=4)
        x0, y0, x1, y1 = qr.getBounds()
        lado = 25 * mm
        desenho = Drawing(lado, lado, transform=[lado / (x1 - x0), 0, 0,
                                                lado / (y1 - y0), -x0, -y0])
        desenho.add(qr)
        renderPDF.draw(desenho, c, x * mm, y * mm)

    def formulario(topo, recibo=False):
        y = cabecalho(topo, recibo)
        celula(5, y, 155, 5.5, 'Local de pagamento',
               'PAGAVEL PREFERENCIALMENTE EM CANAIS ELETRONICOS DA SUA INSTITUICAO FINANCEIRA')
        celula(160, y, 45, 5.5, 'Vencimento', d.vencimento.strftime('%d/%m/%Y'), negrito=True)
        y -= 5.5
        celula(5, y, 155, 9, 'Beneficiário')
        texto(6, y - 5, d.beneficiario_nome, 7.2, True, largura=153)
        texto(6, y - 8, f'CNPJ: {_documento(d.beneficiario_cnpj)}  |  Conta corrente: {d.conta}', 6.8)
        celula(160, y, 45, 9, 'Agência / Posto / Código do Beneficiário', agencia, negrito=True)
        y -= 9
        for x, w, rotulo, conteudo in [(5, 32, 'Data do documento', d.emissao.strftime('%d/%m/%Y')),
                                       (37, 53, 'Número do documento', d.seu_numero),
                                       (90, 24, 'Espécie doc.', d.especie), (114, 12, 'Aceite', 'N'),
                                       (126, 34, 'Data do processamento', d.emissao.strftime('%d/%m/%Y'))]:
            celula(x, y, w, 5.5, rotulo, conteudo)
        celula(160, y, 45, 5.5, 'Nosso Número', numero, negrito=True)
        y -= 5.5
        for x, w, rotulo, conteudo in [(5, 26, 'Uso do banco', ''), (31, 25, 'Carteira', '1'),
                                       (56, 26, 'Espécie moeda', 'REAL'), (82, 36, 'Quantidade', ''),
                                       (118, 42, 'Valor moeda', '')]:
            celula(x, y, w, 5.5, rotulo, conteudo)
        celula(160, y, 45, 5.5, '(=) Valor do documento', f'R$ {valor}', negrito=True)
        y -= 5.5
        celula(5, y, 155, 27, 'Instruções (texto de responsabilidade do beneficiário)')
        texto(7, y - 6, 'HOMOLOGACAO - SEM VALIDADE FINANCEIRA. NAO PAGAR.', 7.2, True, largura=124)
        texto(7, y - 10, 'SEM JUROS, MULTA, DESCONTO, PROTESTO OU NEGATIVACAO.', 6.5, largura=124)
        if pix:
            texto(7, y - 15, 'PIX DEMONSTRATIVO - SEM COBRANCA VINCULADA.', 7, True, largura=124)
            texto(7, y - 19, 'Copia e Cola no recibo. QR Code apenas para teste de leitura.', 6.5, largura=124)
            qr_code(134, y - 26)
        if d.endereco_ficticio:
            texto(7, y - 24, 'ENDERECO DO PAGADOR FICTICIO PARA TESTE DE LAYOUT.', 6.5, largura=124)
        for i, rotulo in enumerate(['(-) Desconto / Abatimento', '(-) Outras deduções', '(+) Mora / Multa',
                                     '(+) Outros acréscimos', '(=) Valor cobrado']):
            celula(160, y - i * 5.4, 45, 5.4, rotulo)
        y -= 27
        celula(5, y, 200, 11, 'Pagador')
        texto(7, y - 4.7, f'{d.pagador_nome} - CPF/CNPJ: {_documento(d.pagador_documento)}', 7, True, largura=195)
        if d.pagador_endereco:
            texto(7, y - 7.3, d.pagador_endereco, 6.5, largura=195)
        if d.pagador_cidade:
            cep = d.pagador_cep
            if cep:
                _digitos(cep, 8, 'CEP do pagador')
                cep = f' - CEP {cep[:5]}-{cep[5:]}'
            texto(7, y - 9.8, f'{d.pagador_cidade} - {d.pagador_uf}{cep}', 6.5, largura=195)
        y -= 11
        celula(5, y, 155, 3.5, 'Beneficiário final')
        celula(160, y, 45, 3.5, 'Código de baixa')

    formulario(285, recibo=True)
    if pix:
        texto(6, 202, 'Pix Copia e Cola - DEMONSTRATIVO, SEM COBRANCA VINCULADA', 7, True)
        c.rect(5 * mm, 186 * mm, 200 * mm, 14 * mm)
        c.setFont('Courier', 7)
        for i, inicio in enumerate(range(0, len(pix), 110)):
            c.drawString(7 * mm, (196 - i * 3.5) * mm, pix[inicio:inicio + 110])
    c.setDash(2, 2)
    c.line(5 * mm, 100 * mm, 205 * mm, 100 * mm)
    c.setDash()
    texto(169, 101.5, 'Corte na linha pontilhada', 5.5)
    # Ficha inteira abaixo do corte: 200 mm de largura e 100 mm de altura.
    formulario(97)

    # CNAB 400 2.9, p. 45: 103 × 13 mm, centro a 12 mm do rodapé.
    c.setFillColor(black)
    c.setStrokeColor(black)
    barcode = I2of5(barras, barWidth=0.75, ratio=2.5, checksum=0,
                    humanReadable=False, bearers=0, quiet=0, barHeight=13 * mm)
    c.saveState()
    c.translate(5 * mm, 5.5 * mm)
    c.scale(103 * mm / barcode.width, 1)
    barcode.drawOn(c, 0, 0)
    c.restoreState()
    texto(138, 17, 'Autenticação mecânica', 6)
    texto(144, 9, 'FICHA DE COMPENSAÇÃO', 9, True)
    c.showPage()
    c.save()
    codigos = {'codigo_barras': barras, 'linha_digitavel': linha,
               'fator_vencimento': fator_vencimento(d.vencimento)}
    if pix:
        codigos.update(pix_copia_cola=pix, pix_demonstrativo=True,
                       origem_pix='gerado_localmente_para_demonstracao')
    return buf.getvalue(), codigos
