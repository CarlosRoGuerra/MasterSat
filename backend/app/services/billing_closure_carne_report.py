"""Conferência de carnês por responsável e vencimento, sem emissão bancária."""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from app.services.billing_closure_report import _d, _v


def _payer_key(item: dict):
    return item.get('payer_client_id') or item.get('payer_name') or item.get('interveniente_nome') or item.get('client_name')


def conference_groups(simulation: dict) -> list[dict]:
    """Uma linha por mês, com os mesmos componentes do boleto consolidado."""
    groups = {}
    for item in simulation.get('items') or []:
        if item.get('already_generated') or item.get('carne_renewal_blocked'):
            continue
        key = _payer_key(item)
        group = groups.setdefault(key, {
            'payer_key': key,
            'payer_name': item.get('payer_name') or item.get('interveniente_nome') or item['client_name'],
            'contracts': {}, 'months': defaultdict(list),
            'services': [], 'extra': Decimal('0'),
        })
        group['contracts'].setdefault(item['contract_id'], item)
        due = item['due_date']
        group['months'][(due.year, due.month)].append(item)

    for item in simulation.get('charge_items') or []:
        group = groups.get(_payer_key(item))
        if group is not None:
            amount = Decimal(str(item.get('amount_to_generate') or 0))
            group['extra'] += amount
            group['services'].append((item.get('title') or 'Serviço avulso', amount))
    for event in simulation.get('uninstall_events') or []:
        group = groups.get(_payer_key(event))
        if group is not None and not event.get('deferred'):
            amount = Decimal(str(event.get('fee_amount') or 0))
            group['extra'] += amount
            group['services'].append(('Taxa de desinstalação - ' + (event.get('vehicle_plate') or 'sem placa'), amount))

    result = []
    for group in groups.values():
        schedule = []
        for index, (_, items) in enumerate(sorted(group.pop('months').items()), 1):
            dates = Counter(item['due_date'] for item in items)
            # A consolidação escolhe o vencimento mais frequente; empate: o mais cedo.
            due = min(dates, key=lambda value: (-dates[value], value))
            monthly = sum((Decimal(str(i.get('billing_amount') or 0)) for i in items), Decimal('0'))
            embedded = sum((Decimal(str(c.get('amount') or 0)) for i in items
                            for c in i.get('first_month_charges') or []), Decimal('0'))
            extra = embedded + (group['extra'] if index == 1 else Decimal('0'))
            for i in items:
                for charge in i.get('first_month_charges') or []:
                    group['services'].append((charge.get('title') or 'Serviço na primeira parcela',
                                              Decimal(str(charge.get('amount') or 0))))
            schedule.append({'number': index, 'due_date': due,
                             'service_month': items[0].get('service_period_label') or simulation.get('reference_month'),
                             'monthly': monthly, 'extra': extra, 'amount': monthly + extra})
        group['contracts'] = sorted(group['contracts'].values(),
                                    key=lambda item: (item.get('vehicle_plate') or '', item['contract_id']))
        group['schedule'] = schedule
        group['total'] = sum((row['amount'] for row in schedule), Decimal('0'))
        result.append(group)
    return sorted(result, key=lambda group: (group['payer_name'].casefold(), str(group['payer_key'])))


def generate_carne_closure_pdf(simulation: dict) -> BytesIO:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    groups = conference_groups(simulation)
    width = A4[0] - 24 * mm
    body = ParagraphStyle('carne-body', fontName='Helvetica', fontSize=8, leading=11,
                          textColor=colors.HexColor('#273449'))
    heading = ParagraphStyle('carne-heading', parent=body, fontName='Helvetica-Bold',
                             fontSize=10, leading=14, spaceBefore=12, spaceAfter=5, keepWithNext=True)
    title = ParagraphStyle('carne-title', parent=heading, fontSize=14, leading=18)

    def paragraph(value, style=body):
        return Paragraph(escape(str(value)), style)

    def table(rows, widths, *, money=False):
        result = Table([[paragraph(cell) for cell in row] for row in rows], colWidths=widths, repeatRows=1)
        commands = [('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#edf2f7')),
                    ('LINEBELOW', (0, 0), (-1, 0), .6, colors.HexColor('#cbd5e1')),
                    ('LINEBELOW', (0, 1), (-1, -1), .25, colors.HexColor('#e2e8f0')),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 5),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 5), ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]
        if money:
            commands.append(('ALIGN', (-1, 1), (-1, -1), 'RIGHT'))
        result.setStyle(TableStyle(commands))
        return result

    period = simulation.get('reference_month') or ''
    total_titles = sum(len(group['schedule']) for group in groups)
    total = sum((group['total'] for group in groups), Decimal('0'))
    story = [paragraph('MASTERSAT - CONFERÊNCIA DE CARNÊS SICREDI', title),
             paragraph(f'Mês do serviço: {period} | Primeiro vencimento no mês seguinte.'),
             paragraph('Prévia para conferência. Este relatório não emite boletos nem confirma o fechamento.'),
             Spacer(1, 4 * mm),
             table([['Carnês a preparar', 'Boletos mensais', 'Total dos ciclos'],
                    [len(groups), total_titles, f'R$ {_v(total)}']], [width / 3] * 3),
             Spacer(1, 2 * mm)]

    for group in groups:
        contracts = group['contracts']
        count = len(group['schedule'])
        block = [
            paragraph('RESPONSÁVEL FINANCEIRO: ' + group['payer_name'], heading),
            paragraph(f'{len(contracts)} contrato(s) | {count} parcelas mensais | Total do carnê: R$ {_v(group["total"])}'),
        ]
        rows = [['Placa', 'Cliente atendido / equipamento', 'Plano', 'Mensalidade']]
        for item in contracts:
            client = item['client_name']
            if item.get('tracker_imei'):
                client += ' / ' + item['tracker_imei']
            rows.append([item.get('vehicle_plate') or 'Sem placa', client, item.get('plan_name') or 'Mensalidade',
                         f'R$ {_v(item.get("plan_price") or item.get("billing_amount") or 0)}'])
        block.extend([Spacer(1, 2 * mm), table(rows, [width * x for x in (.15, .32, .35, .18)], money=True)])
        rows = [['Parcela', 'Mês do serviço', 'Vencimento', 'Mensalidades', 'Adicionais', 'Total a cobrar']]
        for row in group['schedule']:
            rows.append([f'{row["number"]}/{count}', row['service_month'], _d(row['due_date']),
                         _v(row['monthly']), _v(row['extra']), f'R$ {_v(row["amount"])}'])
        block.extend([Spacer(1, 2 * mm), table(rows, [width * x for x in (.10, .17, .18, .18, .17, .20)], money=True)])
        if group['services']:
            block.append(paragraph('Adicionais incluídos na primeira parcela:', heading))
            for label, amount in group['services']:
                block.append(paragraph(f'{label}: R$ {_v(amount)}'))
        block.append(Spacer(1, 4 * mm))
        story.append(KeepTogether(block))

    if not groups:
        story.append(paragraph('Nenhum cliente precisa de um novo carnê neste fechamento.', heading))
    blocks = simulation.get('carne_renewal_blocks') or []
    if blocks:
        story.append(paragraph('CLIENTES SEM RENOVAÇÃO NESTE FECHAMENTO', heading))
        for block in blocks:
            story.append(paragraph(f'{block.get("payer_name") or "Responsável"}: {block["reason"]}'))
    story.extend([Spacer(1, 4 * mm), paragraph(
        'Confira os clientes, veículos, valores e vencimentos antes de concluir. '
        'Após corrigir o cadastro, atualize a prévia no sistema. '
        'Depois da emissão, salve o PDF do carnê de cada cliente para imprimir ou encaminhar pelo WhatsApp.')])

    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm,
                                 topMargin=12 * mm, bottomMargin=14 * mm,
                                 title='Conferência de carnês Sicredi - MasterSat', author='MasterSat')

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#64748b'))
        canvas.drawString(12 * mm, 8 * mm, f'Conferência de carnês | {period}')
        canvas.drawRightString(A4[0] - 12 * mm, 8 * mm, f'Página {doc.page}')
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    output.seek(0)
    return output
