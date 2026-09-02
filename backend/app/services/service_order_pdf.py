"""Geração do documento de Ordem de Serviço em PDF.

Renderer independente do DOCX (`service_order_docx.py`) — consome o mesmo
`OSDocumentData`/`montar_dados_os()`, sem nenhuma conversão de arquivo entre
os dois formatos (decisão confirmada com o usuário: dois geradores, não
DOCX→PDF via LibreOffice). Usa `reportlab.platypus`, o mesmo padrão de
`contract_pdf.py`/`nfse_danfse.py` — não o `canvas` puro usado antes aqui.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.service_order_docx import OSDocumentData, montar_dados_os  # noqa: F401 — reexportado para os endpoints

logger = logging.getLogger(__name__)

_LOGO_PATH = Path(__file__).parent.parent / 'static' / 'mastersat_logo.png'

_TINTA = colors.HexColor('#102D45')       # brand-700
_ROTULO = colors.HexColor('#64748B')      # slate-500
_BORDA = colors.HexColor('#E2E8F0')       # slate-200
_CINZA = colors.HexColor('#F1F5F9')       # slate-100

_STYLES = getSampleStyleSheet()
_P_TITULO = ParagraphStyle('os_titulo', parent=_STYLES['Title'], fontSize=17, textColor=_TINTA, alignment=0)
_P_SUBTITULO = ParagraphStyle('os_subtitulo', parent=_STYLES['Normal'], fontSize=9, textColor=_ROTULO)
_P_SECAO = ParagraphStyle('os_secao', parent=_STYLES['Normal'], fontSize=10, textColor=_TINTA, fontName='Helvetica-Bold')
_P_ROTULO = ParagraphStyle('os_rotulo', parent=_STYLES['Normal'], fontSize=7.5, textColor=_ROTULO)
_P_VALOR = ParagraphStyle('os_valor', parent=_STYLES['Normal'], fontSize=9.5, textColor=colors.black)
_P_EMPRESA = ParagraphStyle('os_empresa', parent=_STYLES['Normal'], fontSize=10.5, textColor=_TINTA, fontName='Helvetica-Bold', alignment=2)
_P_EMPRESA_LINHA = ParagraphStyle('os_empresa_linha', parent=_STYLES['Normal'], fontSize=8, textColor=_ROTULO, alignment=2)

_LARGURA = A4[0] - 4 * cm


def _logo_flowable(altura: float):
    """Mesmo padrão de `contract_pdf.py`/`nfse_danfse.py`: logo lido em
    memória (o path do repositório tem acento, passar direto já quebrou)."""
    try:
        dados = _LOGO_PATH.read_bytes()
    except OSError:
        return None
    try:
        largura_px, altura_px = ImageReader(io.BytesIO(dados)).getSize()
        return Image(io.BytesIO(dados), width=altura * largura_px / altura_px, height=altura)
    except Exception:
        logger.warning('Falha ao carregar logo para o PDF da OS', exc_info=True)
        return None


def _campo_tabela(campos: list[tuple[str, str | None]]) -> Table:
    linhas = [[Paragraph(rotulo.upper(), _P_ROTULO), Paragraph(valor or '-', _P_VALOR)] for rotulo, valor in campos]
    t = Table(linhas, colWidths=[_LARGURA * 0.32, _LARGURA * 0.68])
    t.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, _BORDA),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    return t


def _secao(texto: str) -> Paragraph:
    return Paragraph(texto.upper(), _P_SECAO)


def _rodape(numero_os: str):
    def _desenhar(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(_ROTULO)
        canvas.drawCentredString(A4[0] / 2, 1.2 * cm, f'{numero_os} · Página {canvas.getPageNumber()}')
        canvas.restoreState()
    return _desenhar


def gerar_os_pdf(data: OSDocumentData) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
        title=f'{data.kind}-{data.order.number}',
    )
    story: list = []

    # ── Cabeçalho ──────────────────────────────────────────────────────
    logo = _logo_flowable(1.4 * cm)
    empresa_paragraphs = [
        Paragraph('MASTERSAT COMERCIO E SERVIÇOS DE RASTREAMENTO LTDA', _P_EMPRESA),
        Paragraph('CNPJ 14.228.344/0001-67', _P_EMPRESA_LINHA),
        Paragraph('RUA MARITIMA, 424 COMASA JOINVILLE SC', _P_EMPRESA_LINHA),
    ]
    header = Table([[logo or '', empresa_paragraphs]], colWidths=[4 * cm, _LARGURA - 4 * cm])
    header.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
    story.append(header)
    story.append(Spacer(1, 12))
    story.append(Paragraph(f'{data.title} — {data.order.number}', _P_TITULO))
    story.append(Paragraph(
        f'Gerado em {data.generated_at.strftime("%d/%m/%Y %H:%M")} · Status: {data.order.status.value} · Prioridade: {data.order.priority.value}',
        _P_SUBTITULO,
    ))
    story.append(Spacer(1, 14))

    if data.kind == 'historico_execucao' and data.status_logs is not None:
        story.append(_secao('Histórico de execução'))
        story.append(Spacer(1, 4))
        linhas = [[Paragraph('Data/Hora', _P_ROTULO), Paragraph('Transição', _P_ROTULO), Paragraph('Notas', _P_ROTULO)]]
        for entry in data.status_logs:
            linhas.append([
                Paragraph(entry.when, _P_VALOR),
                Paragraph(f'{entry.previous} → {entry.new}', _P_VALOR),
                Paragraph(entry.notes or '-', _P_VALOR),
            ])
        tabela = Table(linhas, colWidths=[_LARGURA * 0.22, _LARGURA * 0.28, _LARGURA * 0.50])
        tabela.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, _BORDA),
            ('BACKGROUND', (0, 0), (-1, 0), _CINZA),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(tabela)
    else:
        for titulo, entidade, campos in (
            ('Cliente', data.client, [
                ('Nome/Razão social', getattr(data.client, 'name', None)),
                ('CPF/CNPJ', getattr(data.client, 'cpf_cnpj', None)),
                ('Telefone', getattr(data.client, 'phone', None)),
                ('E-mail', getattr(data.client, 'email', None)),
            ]),
            ('Veículo', data.vehicle, [
                ('Placa', getattr(data.vehicle, 'plate', None)),
                ('Marca/Modelo', ' '.join(filter(None, [getattr(data.vehicle, 'brand', None), getattr(data.vehicle, 'model', None)])) or None),
                ('Chassi', getattr(data.vehicle, 'chassis', None)),
                ('Cor', getattr(data.vehicle, 'color', None)),
            ]),
            ('Rastreador', data.tracker, [
                ('IMEI', getattr(data.tracker, 'imei', None)),
                ('Marca/Modelo', ' '.join(filter(None, [getattr(data.tracker, 'brand', None), getattr(data.tracker, 'model', None)])) or None),
                ('SIM/ICCID', getattr(data.tracker, 'sim_iccid', None) or getattr(data.tracker, 'sim_number', None)),
            ]),
            ('Técnico responsável', data.technician, [
                ('Nome', getattr(data.technician, 'name', None)),
                ('E-mail', getattr(data.technician, 'email', None)),
            ]),
        ):
            if entidade is None:
                continue
            story.append(_secao(titulo))
            story.append(Spacer(1, 3))
            story.append(_campo_tabela(campos))
            story.append(Spacer(1, 10))

        story.append(_secao('Agendamento e execução'))
        story.append(Spacer(1, 3))
        story.append(_campo_tabela([
            ('Tipo de serviço', data.order.type.value),
            ('Agendamento', data.order.scheduled_at.strftime('%d/%m/%Y %H:%M') if data.order.scheduled_at else None),
            ('Data de abertura', data.order.created_at.strftime('%d/%m/%Y %H:%M') if data.order.created_at else None),
            ('Data de conclusão', data.order.executed_at.strftime('%d/%m/%Y %H:%M') if data.order.executed_at else None),
        ]))
        story.append(Spacer(1, 10))

        if data.order.problem_description:
            story.append(_secao('Descrição do problema'))
            story.append(Spacer(1, 3))
            story.append(Paragraph(data.order.problem_description, _P_VALOR))
            story.append(Spacer(1, 10))

        if data.order.execution_description:
            story.append(_secao('Serviço executado'))
            story.append(Spacer(1, 3))
            story.append(Paragraph(data.order.execution_description, _P_VALOR))
            story.append(Spacer(1, 10))

        story.append(_secao('Checklist técnico'))
        story.append(Spacer(1, 3))
        if data.checklist:
            linhas = [[Paragraph('', _P_ROTULO), Paragraph('Item', _P_ROTULO), Paragraph('Observação', _P_ROTULO)]]
            for item in data.checklist:
                linhas.append([
                    # '[X]'/'[ ]' em vez de glifo Unicode ☑/☐: a fonte padrão
                    # do ReportLab (Helvetica) não tem esses glifos e ambos
                    # caiam no mesmo quadrado de fallback — feito/não feito
                    # ficavam visualmente idênticos no PDF.
                    Paragraph('[X]' if item.done else '[ ]', _P_VALOR),
                    Paragraph(item.description, _P_VALOR),
                    Paragraph(item.notes or '-', _P_VALOR),
                ])
            tabela = Table(linhas, colWidths=[1 * cm, _LARGURA * 0.45, _LARGURA - 1 * cm - _LARGURA * 0.45])
            tabela.setStyle(TableStyle([
                ('GRID', (0, 0), (-1, -1), 0.5, _BORDA),
                ('BACKGROUND', (0, 0), (-1, 0), _CINZA),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.append(tabela)
        else:
            story.append(Paragraph('Nenhum item de checklist registrado.', _P_VALOR))
        story.append(Spacer(1, 10))

        story.append(_secao('Materiais utilizados'))
        story.append(Spacer(1, 3))
        if data.materials:
            linhas = [[Paragraph(h, _P_ROTULO) for h in ('Descrição', 'Qtd.', 'Unid.', 'Preço unit.')]]
            for m in data.materials:
                linhas.append([
                    Paragraph(m.description, _P_VALOR),
                    Paragraph(m.quantity, _P_VALOR),
                    Paragraph(m.unit or '-', _P_VALOR),
                    Paragraph(m.unit_price or '-', _P_VALOR),
                ])
            tabela = Table(linhas, colWidths=[_LARGURA * 0.5, _LARGURA * 0.15, _LARGURA * 0.15, _LARGURA * 0.2])
            tabela.setStyle(TableStyle([
                ('GRID', (0, 0), (-1, -1), 0.5, _BORDA),
                ('BACKGROUND', (0, 0), (-1, 0), _CINZA),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.append(tabela)
        else:
            story.append(Paragraph('Nenhum material registrado.', _P_VALOR))
        story.append(Spacer(1, 10))

        if data.order.observations:
            story.append(_secao('Observações'))
            story.append(Spacer(1, 3))
            story.append(Paragraph(data.order.observations, _P_VALOR))
            story.append(Spacer(1, 10))

        if data.photos:
            story.append(_secao('Fotos'))
            story.append(Spacer(1, 3))
            for photo in data.photos:
                try:
                    reader = ImageReader(io.BytesIO(photo.content))
                    largura_px, altura_px = reader.getSize()
                    largura = min(_LARGURA, 10 * cm)
                    altura = largura * altura_px / largura_px
                    story.append(Image(io.BytesIO(photo.content), width=largura, height=altura))
                    story.append(Spacer(1, 6))
                except Exception:
                    logger.warning('Falha ao inserir foto %s no PDF da OS', photo.file_name, exc_info=True)

        story.append(_secao('Assinaturas'))
        story.append(Spacer(1, 6))
        colunas = []
        for signature_png, label, signed_at in (
            (data.technician_signature_png, data.technician.name if data.technician else 'Técnico', data.order.technician_signed_at),
            (data.client_signature_png, data.client.name if data.client else 'Cliente', data.order.client_signed_at),
        ):
            bloco = []
            if signature_png:
                try:
                    reader = ImageReader(io.BytesIO(signature_png))
                    largura_px, altura_px = reader.getSize()
                    largura = min(_LARGURA / 2 - 1 * cm, 6 * cm)
                    bloco.append(Image(io.BytesIO(signature_png), width=largura, height=largura * altura_px / largura_px))
                except Exception:
                    logger.warning('Falha ao inserir assinatura no PDF da OS', exc_info=True)
            texto = label
            if signed_at:
                texto += f'<br/><font size=7 color="#64748B">Assinado em {signed_at.strftime("%d/%m/%Y %H:%M")}</font>'
            bloco.append(Paragraph(texto, _P_VALOR))
            colunas.append(bloco)
        assinatura_table = Table([colunas], colWidths=[_LARGURA / 2, _LARGURA / 2])
        assinatura_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'BOTTOM')]))
        story.append(assinatura_table)

    doc.build(story, onFirstPage=_rodape(data.order.number), onLaterPages=_rodape(data.order.number))
    buffer.seek(0)
    return buffer.getvalue()
