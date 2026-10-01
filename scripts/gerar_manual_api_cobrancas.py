"""Gera o manual público de integração, a coleção Postman e o pacote de entrega.

Executar da raiz: python scripts/gerar_manual_api_cobrancas.py
Dependências: reportlab, markdown-it-py, Pillow, pypdf. Fontes Arial/Consolas do Windows
ou DejaVu Sans/Mono do Linux. Não acessa o backend nem lê credenciais.
"""
from __future__ import annotations

import html
import json
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from markdown_it import MarkdownIt
from PIL import Image as PILImage
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
    Table, TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs' / 'integracao-cobranca-whatsapp'
OUT = ROOT / 'output'
PDF = OUT / 'pdf' / 'MasterSat-Manual-API-Cobrancas-WhatsApp.pdf'
MD = DOCS / 'manual-api-cobrancas.md'
COLLECTION = DOCS / 'mastersat-cobrancas.postman_collection.json'
LOGO = DOCS / 'assets' / 'mastersat-logo.png'
YELLOW = colors.HexColor('#FFC800')
INK = colors.HexColor('#202020')
GRAY = colors.HexColor('#5B6168')
PALE = colors.HexColor('#F3F4F5')
RULE = colors.HexColor('#DFE2E5')
PAGE_W, PAGE_H = A4
MARGIN = 46
CONTENT_W = PAGE_W - 2 * MARGIN
PAGE_COUNT = 14


def fonts():
    windows = Path('C:/Windows/Fonts')
    linux = Path('/usr/share/fonts/truetype/dejavu')
    candidates = {
        'Body': (windows / 'arial.ttf', linux / 'DejaVuSans.ttf'),
        'BodyBold': (windows / 'arialbd.ttf', linux / 'DejaVuSans-Bold.ttf'),
        'BodyItalic': (windows / 'ariali.ttf', linux / 'DejaVuSans-Oblique.ttf'),
        'BodyBoldItalic': (windows / 'arialbi.ttf', linux / 'DejaVuSans-BoldOblique.ttf'),
        'Mono': (windows / 'consola.ttf', linux / 'DejaVuSansMono.ttf'),
        'MonoBold': (windows / 'consolab.ttf', linux / 'DejaVuSansMono-Bold.ttf'),
    }
    for name, paths in candidates.items():
        path = next((p for p in paths if p.exists()), None)
        if path is None:
            raise FileNotFoundError(f'Fonte indisponível: {name}; instalar Arial/Consolas ou DejaVu.')
        pdfmetrics.registerFont(TTFont(name, str(path)))
    pdfmetrics.registerFontFamily('Body', normal='Body', bold='BodyBold', italic='BodyItalic', boldItalic='BodyBoldItalic')
    pdfmetrics.registerFontFamily('Mono', normal='Mono', bold='MonoBold', italic='Mono', boldItalic='MonoBold')


STYLES = {
    'body': ParagraphStyle('body', fontName='Body', fontSize=9.4, leading=13.3, textColor=INK, spaceAfter=8),
    'h2': ParagraphStyle('h2', fontName='BodyBold', fontSize=22, leading=26, textColor=INK, spaceAfter=17, keepWithNext=True),
    'h3': ParagraphStyle('h3', fontName='BodyBold', fontSize=12, leading=16, textColor=INK, spaceBefore=8, spaceAfter=8, keepWithNext=True),
    'table': ParagraphStyle('table', fontName='Body', fontSize=8.45, leading=11.5, textColor=INK),
    'th': ParagraphStyle('th', fontName='BodyBold', fontSize=8.5, leading=11.7, textColor=colors.white),
    'note': ParagraphStyle('note', fontName='Body', fontSize=9, leading=13, textColor=INK),
    'list': ParagraphStyle('list', fontName='Body', fontSize=9.35, leading=12.8, textColor=INK, leftIndent=13, firstLineIndent=-13, spaceAfter=5),
}


def inline(token):
    result = []
    for child in token.children or []:
        if child.type == 'text':
            result.append(html.escape(child.content))
        elif child.type == 'code_inline':
            result.append(f'<font name="Mono">{html.escape(child.content)}</font>')
        elif child.type == 'strong_open':
            result.append('<b>')
        elif child.type == 'strong_close':
            result.append('</b>')
        elif child.type == 'em_open':
            result.append('<i>')
        elif child.type == 'em_close':
            result.append('</i>')
        elif child.type in ('softbreak', 'hardbreak'):
            result.append(' ' if child.type == 'softbreak' else '<br/>')
        elif child.type == 'link_open':
            result.append(f'<a href="{html.escape(child.attrGet("href"), quote=True)}" color="#725600">')
        elif child.type == 'link_close':
            result.append('</a>')
    return ''.join(result)


class CodeBlock(Flowable):
    def __init__(self, content, language):
        super().__init__()
        self.lines = content.rstrip('\n').splitlines()
        self.language = language.upper() or 'EXEMPLO'
        longest = max((pdfmetrics.stringWidth(line, 'Mono', 1) for line in self.lines), default=1)
        self.size = min(8.1, (CONTENT_W - 24) / max(longest, 1))
        if self.size < 6.7:
            raise ValueError(f'Linha de código longa demais: {self.language} ({self.size:.2f} pt)')
        self.leading = self.size * 1.4
        self.width = CONTENT_W
        self.height = 30 + len(self.lines) * self.leading
        self.spaceAfter = 12

    def draw(self):
        c = self.canv
        c.setFillColor(PALE)
        c.roundRect(0, 0, self.width, self.height, 5, fill=1, stroke=0)
        c.setFillColor(GRAY)
        c.setFont('BodyBold', 7)
        c.drawString(12, self.height - 15, self.language)
        c.setFillColor(INK)
        c.setFont('Mono', self.size)
        y = self.height - 30
        for line in self.lines:
            c.drawString(12, y, line)
            y -= self.leading


def make_table(rows):
    n = len(rows[0])
    if n == 4:
        widths = [CONTENT_W * .41, CONTENT_W * .09, CONTENT_W * .41, CONTENT_W * .09]
    elif n == 3:
        widths = [CONTENT_W * .285, CONTENT_W * .195, CONTENT_W * .52]
        if rows[0][0] == 'HTTP':
            widths = [CONTENT_W * .14, CONTENT_W * .36, CONTENT_W * .50]
    elif rows[0][0] == 'MasterSat':
        widths = [CONTENT_W / 2, CONTENT_W / 2]
    else:
        widths = [CONTENT_W * .33, CONTENT_W * .67]
    cells = [[Paragraph(s, STYLES['th' if i == 0 else 'table']) for s in row] for i, row in enumerate(rows)]
    table = Table(cells, colWidths=widths, repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), INK),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, PALE]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LINEBELOW', (0, 1), (-1, -1), .35, RULE),
    ]))
    table.spaceAfter = 11
    return table


def render_section(markdown):
    tokens = MarkdownIt('commonmark').enable('table').parse(markdown)
    out = []
    i = 0
    lists = []
    list_prefix = None
    in_quote = False
    quote_parts = []
    while i < len(tokens):
        t = tokens[i]
        if t.type == 'heading_open':
            out.append(Paragraph(inline(tokens[i + 1]), STYLES['h2' if t.tag in ('h1', 'h2') else 'h3']))
            i += 3
            continue
        if t.type == 'paragraph_open':
            value = inline(tokens[i + 1])
            if in_quote:
                quote_parts.append(Paragraph(value, STYLES['note']))
            else:
                style = 'list' if list_prefix is not None else 'body'
                out.append(Paragraph((list_prefix or '') + value, STYLES[style]))
                list_prefix = None
            i += 3
            continue
        if t.type == 'fence':
            out.append(CodeBlock(t.content, t.info))
        elif t.type in ('bullet_list_open', 'ordered_list_open'):
            lists.append({'ordered': t.type == 'ordered_list_open', 'n': int(t.attrGet('start') or 1)})
        elif t.type in ('bullet_list_close', 'ordered_list_close'):
            lists.pop()
        elif t.type == 'list_item_open':
            current = lists[-1]
            list_prefix = f'<b>{current["n"]}.</b> ' if current['ordered'] else '<b>-</b> '
            current['n'] += 1
        elif t.type == 'blockquote_open':
            in_quote = True
            quote_parts = []
        elif t.type == 'blockquote_close':
            note = Table([[quote_parts]], colWidths=[CONTENT_W])
            note.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFF8D9')),
                ('LINEBEFORE', (0, 0), (0, -1), 3, YELLOW),
                ('LEFTPADDING', (0, 0), (-1, -1), 12),
                ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                ('TOPPADDING', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ]))
            note.spaceAfter = 11
            out.append(KeepTogether([note]))
            in_quote = False
        elif t.type == 'table_open':
            rows = []
            while tokens[i].type != 'table_close':
                if tokens[i].type == 'tr_open':
                    rows.append([])
                elif tokens[i].type == 'inline':
                    rows[-1].append(inline(tokens[i]))
                i += 1
            out.append(make_table(rows))
        i += 1
    return out


def draw_logo(c, x, y, width):
    with PILImage.open(LOGO) as im:
        height = width * im.height / im.width
    c.drawImage(str(LOGO), x, y, width=width, height=height, mask='auto')
    return height


def cover(c):
    c.setFillColor(colors.white)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    c.setFillColor(YELLOW)
    c.rect(0, PAGE_H - 13, PAGE_W, 13, fill=1, stroke=0)
    draw_logo(c, 46, 663, 340)
    c.setFillColor(INK)
    c.setFont('BodyBold', 10)
    c.drawString(48, 599, 'DOCUMENTAÇÃO PARA INTEGRADORAS')
    c.setFont('BodyBold', 43)
    c.drawString(45, 531, 'API de cobranças')
    c.setFont('Body', 22)
    c.drawString(47, 490, 'Notificações via WhatsApp')
    c.setFillColor(YELLOW)
    c.rect(47, 462, 91, 5, fill=1, stroke=0)
    p = Paragraph('Manual técnico para consultar cobranças, obter dados de pagamento e disponibilizar boletos aos clientes MasterSat.', ParagraphStyle('coverBody', fontName='Body', fontSize=13, leading=20, textColor=GRAY))
    _, h = p.wrap(443, 100)
    p.drawOn(c, 47, 416 - h)
    x, y = 47, 252
    for label, desc, width in [('CONSULTA', 'Cobranças e pagadores', 168), ('DOCUMENTOS', 'Boleto em PDF e link', 167), ('INTEGRAÇÃO', 'Exemplos e homologação', 168)]:
        c.setFillColor(INK)
        c.setFont('BodyBold', 9)
        c.drawString(x, y, label)
        c.setFillColor(GRAY)
        c.setFont('Body', 8.7)
        c.drawString(x, y - 20, desc)
        x += width
    c.setFillColor(INK)
    c.rect(0, 0, PAGE_W, 169, fill=1, stroke=0)
    c.setFillColor(YELLOW)
    c.setFont('BodyBold', 11)
    c.drawString(47, 127, 'MASTERSAT')
    c.setFillColor(colors.white)
    c.setFont('Body', 10)
    c.drawString(47, 104, 'Versão documental 1.0  |  29 de setembro de 2026')
    p = Paragraph('Baseado na implementação do projeto. Endereço, credencial e ambiente de operação devem ser confirmados pela MasterSat. Exemplos fictícios, sem dados de clientes ou credenciais reais.', ParagraphStyle('coverNote', fontName='Body', fontSize=8.5, leading=12.5, textColor=colors.HexColor('#DDDDDD')))
    _, h = p.wrap(CONTENT_W, 55)
    p.drawOn(c, 47, 79 - h)


def page_decoration(c, doc):
    c.saveState()
    if doc.page == 1:
        cover(c)
    else:
        draw_logo(c, MARGIN, PAGE_H - 57, 94)
        c.setFont('BodyBold', 8)
        c.setFillColor(GRAY)
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - 38, 'INTEGRAÇÃO DE COBRANÇAS | WHATSAPP')
        c.setStrokeColor(RULE)
        c.setLineWidth(.6)
        c.line(MARGIN, PAGE_H - 70, PAGE_W - MARGIN, PAGE_H - 70)
        c.setStrokeColor(YELLOW)
        c.setLineWidth(3)
        c.line(MARGIN, PAGE_H - 70, MARGIN + 39, PAGE_H - 70)
        c.setStrokeColor(RULE)
        c.setLineWidth(.6)
        c.line(MARGIN, 42, PAGE_W - MARGIN, 42)
        c.setFont('Body', 7.5)
        c.setFillColor(GRAY)
        c.drawString(MARGIN, 27, 'MasterSat | Manual da API de cobranças | v1.0')
        c.drawRightString(PAGE_W - MARGIN, 27, f'{doc.page:02d} / {PAGE_COUNT}')
    c.restoreState()


def postman():
    def req(name, path, description, accept='application/json', noauth=False):
        request = {
            'method': 'GET',
            'header': [{'key': 'Accept', 'value': accept}],
            'url': path,
            'description': description,
        }
        if noauth:
            request['auth'] = {'type': 'noauth'}
        return {'name': name, 'request': request, 'response': []}

    base = '{{base_url}}/api/v1/integrations/cobrancas'
    collection = {
        'info': {
            'name': 'MasterSat | Cobranças para WhatsApp',
            'description': 'Coleção de consulta, sem envio de mensagens. Manual v1.0, 29/09/2026. Configure em ambiente privado: base_url (domínio HTTPS sem /api/v1), api_key, billing_id e boleto_link_cliente. Confirme o endereço com a MasterSat. Os arquivos distribuídos não contêm credenciais. Antes de enviar: revalide status, canal do pagador, telefone, registro e disponibilidade do link público. A lista não tem paginação; total é a quantidade retornada. Não confunda download autenticado com confirmação de elegibilidade.',
            'schema': 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json',
        },
        'auth': {'type': 'apikey', 'apikey': [
            {'key': 'key', 'value': 'X-API-Key', 'type': 'string'},
            {'key': 'value', 'value': '{{api_key}}', 'type': 'string'},
            {'key': 'in', 'value': 'header', 'type': 'string'},
        ]},
        'variable': [
            {'key': 'base_url', 'value': '', 'type': 'string', 'description': 'Domínio HTTPS confirmado pela MasterSat, sem barra final e sem /api/v1.'},
            {'key': 'api_key', 'value': '', 'type': 'string', 'description': 'Preencher somente no ambiente privado. Não exportar a chave.'},
            {'key': 'billing_id', 'value': '', 'type': 'string', 'description': 'ID inteiro selecionado de uma cobrança de homologação.'},
            {'key': 'boleto_link_cliente', 'value': '', 'type': 'string', 'description': 'Copiar o campo boleto_link_cliente retornado para a cobrança validada.'},
        ],
        'item': [
            req('01 | Listar cobranças para WhatsApp', base + '?forma_envio=whatsapp&limit=500', 'Retorna {total,cobrancas}. Inclui pendentes/vencidas. Limite 1..2000, padrão 500. total == limit indica possível truncamento. Revalidar canal do pagador retornado, especialmente com interveniente.'),
            req('02 | Consultar detalhe antes de enviar', base + '/{{billing_id}}', 'Retorna o objeto diretamente, sem envelope. Pode retornar paga/cancelada: nesses casos, suspender o envio. Usar apenas o destinatário retornado no objeto cliente.'),
            req('03 | Baixar PDF autenticado', base + '/{{billing_id}}/pdf', 'Retorna bytes application/pdf. Usar Save Response para salvar. A rota pode gerar PDF sem registro bancário ou para título não aberto: só distribuir após revalidar o detalhe e a disponibilidade do link público.', accept='application/pdf'),
            req('04 | Abrir link público do cliente', '{{boleto_link_cliente}}', 'Copiar a URL da API; não montar token. Esta requisição NÃO envia X-API-Key. Esperado: PDF. Token inválido, cancelamento ou ausência dos dados oficiais pode retornar 404. Um título pago pode continuar acessível; abrir o PDF não comprova dívida.', accept='application/pdf', noauth=True),
        ],
    }
    COLLECTION.write_text(json.dumps(collection, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    PDF.parent.mkdir(parents=True, exist_ok=True)
    LOGO.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'logotipo.png', LOGO)
    fonts()
    sections = MD.read_text(encoding='utf-8').split('<!-- pagebreak -->')
    if len(sections) != PAGE_COUNT:
        raise ValueError(f'Esperadas {PAGE_COUNT} seções/páginas; encontradas {len(sections)}.')
    story = [Spacer(1, 1), PageBreak()]
    for i, section in enumerate(sections[1:]):
        story.extend(render_section(section))
        if i < len(sections) - 2:
            story.append(PageBreak())
    doc = SimpleDocTemplate(
        str(PDF), pagesize=A4, rightMargin=MARGIN, leftMargin=MARGIN,
        topMargin=91, bottomMargin=58,
        title='MasterSat - API de cobranças: integração WhatsApp',
        author='MasterSat', subject='Manual técnico para empresas integradoras',
        pageCompression=1,
    )
    doc.build(story, onFirstPage=page_decoration, onLaterPages=page_decoration)
    rendered = PdfReader(PDF)
    if len(rendered.pages) != PAGE_COUNT:
        raise ValueError(f'Quebra inesperada: PDF com {len(rendered.pages)} páginas; índice prevê {PAGE_COUNT}.')
    postman()
    package = OUT / 'MasterSat-Kit-Integracao-Cobrancas-WhatsApp.zip'
    with ZipFile(package, 'w', ZIP_DEFLATED) as z:
        z.write(PDF, PDF.name)
        z.write(MD, MD.name)
        z.write(COLLECTION, COLLECTION.name)
        z.write(LOGO, 'assets/mastersat-logo.png')
    print(json.dumps({'pdf': str(PDF), 'postman': str(COLLECTION), 'kit': str(package)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
