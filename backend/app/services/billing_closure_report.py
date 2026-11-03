"""
Relatório de simulação de fechamento (formato do sistema antigo).

Extraído de billing_closure.py (BE-03): esta metade do arquivo original não
tocava em banco — recebe só o ``dict`` de simulação já pronto (produzido por
``simulate_closure``) e o transforma em texto monoespaçado, XLSX ou PDF. Same
comportamento de antes, só em módulo próprio por não compartilhar
responsabilidade nenhuma com o cálculo/persistência do fechamento.

Texto monoespaçado, agrupado por INTERVENIENTE (quem paga o boleto), com um
bloco por veículo e uma linha por rastreador — um veículo pode ter mais de um
equipamento, e cada um tem a própria mensalidade.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

_LARGURA = 96          # colunas do relatório
_SEP = '=' * _LARGURA
_EMPRESA = 'MASTERSAT COMERCIO E SERVIÇOS DE RASTREAMENTO LTDA'


def _v(valor: float) -> str:
    """Valor no padrão brasileiro, sem símbolo (o relatório é monoespaçado)."""
    return f'{valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _d(valor: date | None) -> str:
    return valor.strftime('%d/%m/%Y') if valor else '//'


def _par(esquerda: str, direita: str, largura: int = _LARGURA) -> str:
    """Duas colunas na mesma linha: uma à esquerda, outra à direita."""
    espaco = max(1, largura - len(esquerda) - len(direita))
    return f'{esquerda}{" " * espaco}{direita}'


def _total(rotulo: str, valor: float, recuo: int = 40) -> str:
    """
    Linha de total: rótulo recuado e valor à direita.

    Os 12 caracteres finais alinham na MESMA coluna do valor das linhas de
    rastreador (8 + 74 + 12 = 94), senão a coluna de valores fica em degrau.
    """
    corpo = f'{rotulo:<42}{_v(valor):>12}'
    return ' ' * recuo + corpo


def _chave_veiculo(item: dict) -> object:
    """Sem veículo, cada contrato é seu próprio grupo."""
    return item.get('vehicle_id') or f'c{item["contract_id"]}'


def _plural(n: int, singular: str, plural: str) -> str:
    return f'{n} {singular if n == 1 else plural}'


def _no_mes(valor: date | None, mes_ref: str) -> bool:
    """A data cai no mês de referência ('MM/AAAA')?"""
    if not valor or '/' not in (mes_ref or ''):
        return False
    mes, _, ano = mes_ref.partition('/')
    return valor.month == int(mes) and valor.year == int(ano)


def _contagens(itens: list[dict], desinstalacoes: list[dict], mes_ref: str) -> dict[str, int]:
    """Veículos, equipamentos, instalações e desinstalações de um conjunto."""
    return {
        'veiculos': len({_chave_veiculo(i) for i in itens}),
        # Um veículo pode ter mais de um rastreador — por isso a contagem é
        # separada da de veículos (ex.: ACQUE, 1 veículo com 2 equipamentos).
        'equipamentos': len({i['tracker_imei'] for i in itens if i.get('tracker_imei')}),
        'instalacoes': sum(1 for i in itens if _no_mes(i.get('tracker_install_date'), mes_ref)),
        'desinstalacoes': len(desinstalacoes),
    }


_MESES_EXT = ['', 'JANEIRO', 'FEVEREIRO', 'MARÇO', 'ABRIL', 'MAIO', 'JUNHO',
              'JULHO', 'AGOSTO', 'SETEMBRO', 'OUTUBRO', 'NOVEMBRO', 'DEZEMBRO']


def _mes_extenso(mes_ref: str) -> str:
    """'08/2026' → 'AGOSTO/2026'; devolve o original se não bater o formato."""
    mm, _, aaaa = (mes_ref or '').partition('/')
    if aaaa and mm.isdigit() and 1 <= int(mm) <= 12:
        return f'{_MESES_EXT[int(mm)]}/{aaaa}'
    return mes_ref or ''


def _totais_financeiros(itens: list[dict], simulation: dict) -> dict[str, float]:
    """Somatórios do fechamento, por natureza — a mesma conta no topo e no rodapé."""
    mensalidades = sum(float(i.get('billing_amount') or 0) for i in itens)
    produtos = sum(
        float(p.get('amount') or 0)
        for i in itens for p in (i.get('first_month_charges') or [])
    )
    taxas = float(simulation.get('total_uninstall_fees') or 0)
    servicos = float(simulation.get('total_services') or 0)
    return {
        'mensalidades': mensalidades, 'produtos': produtos,
        'taxas': taxas, 'servicos': servicos,
        'geral': mensalidades + produtos + taxas + servicos,
    }


def _painel_totais(contagem: dict[str, int], fin: dict[str, float]) -> list[str]:
    """
    Painel de totais no topo do relatório — a quantidade de tudo antes do
    detalhamento por cliente (pedido de 08/08/2026). Tabela de 5 colunas em
    caixa, no monoespaçado do relatório.
    """
    colunas = [
        ('Veículos', str(contagem['veiculos'])),
        ('Rastreadores', str(contagem['equipamentos'])),
        ('Instalações', str(contagem['instalacoes'])),
        ('Desinstalações', str(contagem['desinstalacoes'])),
        ('Total Geral', f'R$ {_v(fin["geral"])}'),
    ]
    w = 18  # 5 células de 18 + 6 bordas = 96 = _LARGURA
    borda = '+' + '+'.join('-' * w for _ in colunas) + '+'

    def _linha(celulas: list[str]) -> str:
        return '|' + '|'.join(c.center(w) for c in celulas) + '|'

    return [
        borda,
        _linha([rot for rot, _ in colunas]),
        borda,
        _linha([val for _, val in colunas]),
        borda,
    ]


def montar_linhas_simulacao(simulation: dict) -> list[str]:
    """
    Monta o relatório como lista de linhas de texto.

    Separado da geração do PDF para poder ser testado sem abrir o arquivo.
    """
    agora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    mes_ref = simulation.get('reference_month', '')
    titulo = f'PRÉVIA DE FECHAMENTO — {_mes_extenso(mes_ref)}'.strip(' —')
    linhas: list[str] = [_SEP, titulo.center(_LARGURA), _SEP]

    itens = simulation.get('items') or []
    if not itens:
        linhas += ['', 'Nenhum contrato a faturar no período.']
        return linhas

    # 1) agrupa por interveniente  2) dentro dele, por veículo
    por_interveniente: dict[str, list[dict]] = defaultdict(list)
    for item in itens:
        por_interveniente[item.get('interveniente_nome') or item['client_name']].append(item)

    # As desinstalações vêm por cliente; o relatório agrupa por interveniente.
    # Este mapa leva uma à outra para a contagem cair no bloco certo.
    interveniente_do_cliente = {
        i.get('client_id'): (i.get('interveniente_nome') or i['client_name']) for i in itens
    }
    desinst_por_grupo: dict[str, list[dict]] = defaultdict(list)
    for ev in simulation.get('uninstall_events') or []:
        grupo = (
            ev.get('payer_name')
            or interveniente_do_cliente.get(ev.get('client_id'))
            or ev.get('client_name') or ''
        )
        desinst_por_grupo[grupo].append(ev)

    fin = _totais_financeiros(itens, simulation)

    # ── Painel de totais no topo (a quantidade de tudo antes dos clientes) ──
    linhas.append(_par(f'Mês de referência: {mes_ref}', f'Gerado em {agora}'))
    linhas.append('')
    linhas += _painel_totais(
        _contagens(itens, simulation.get('uninstall_events') or [], mes_ref), fin)
    linhas.append('')

    for interveniente, do_grupo in sorted(por_interveniente.items()):
        por_veiculo: dict[object, list[dict]] = defaultdict(list)
        for item in do_grupo:
            por_veiculo[_chave_veiculo(item)].append(item)

        venc = do_grupo[0].get('due_date')
        mes_venc = venc.strftime('%m/%Y') if venc else ''
        qtd = _contagens(do_grupo, desinst_por_grupo.get(interveniente, []), mes_ref)

        linhas.append(f'INTERVENIENTE: {interveniente}')
        linhas.append(f'MATRIZ/FILIAL: {_EMPRESA}')
        linhas.append('')
        linhas.append(_par(
            f'MÊS REFERENTE: {mes_ref}',
            _par(f'MÊS VENCIMENTO: {mes_venc}',
                 f'QUANTIDADE VEÍCULOS: {qtd["veiculos"]}', 62),
        ))
        linhas.append(_par(
            f'QUANTIDADE EQUIPAMENTOS: {qtd["equipamentos"]}',
            _par(f'INSTALAÇÕES NO MÊS: {qtd["instalacoes"]}',
                 f'DESINSTALAÇÕES NO MÊS: {qtd["desinstalacoes"]}', 62),
        ))
        linhas.append('')

        total_grupo = 0.0
        for contratos in por_veiculo.values():
            primeiro = contratos[0]
            venc_v = primeiro.get('due_date')
            dia = primeiro.get('billing_day') or (venc_v.day if venc_v else '')

            linhas.append(f'CLIENTE: {primeiro["client_name"]}')
            linhas.append(_par(
                f'PLACA: {primeiro.get("vehicle_plate") or "—"}',
                f'TIPO VEÍCULO: {(primeiro.get("vehicle_type") or "—").upper()}', 78))
            linhas.append(_par(
                f'DATA CADASTRO: {_d(primeiro.get("vehicle_created_at"))}',
                f'RASTREADOR: {primeiro.get("tracker_imei") or "—"}', 78))
            linhas.append(_par(
                f'DATA CONTRATO: {_d(primeiro.get("contract_start_date"))}',
                f'VENCIMENTO: {dia}[{_d(venc_v)}]', 78))
            linhas.append('')

            total_veiculo = 0.0
            for c in contratos:
                mensalidade = float(c.get('billing_amount') or 0)
                instalacao = _d(c.get('tracker_install_date'))
                rotulo = f'RASTREADOR: DATA INSTALAÇÃO [{instalacao}] - MENSALIDADE {_v(mensalidade)}:'
                linhas.append(' ' * 8 + f'{rotulo:<74}{_v(mensalidade):>12}'.rstrip())

                # Serviços/produtos embutidos na primeira cobrança
                produtos = c.get('first_month_charges') or []
                soma_produtos = 0.0
                for prod in produtos:
                    val = float(prod.get('amount') or 0)
                    soma_produtos += val
                    desc = f'PRODUTO - {str(prod.get("title") or "").upper()}:'
                    linhas.append(' ' * 8 + f'{desc:<74}{_v(val):>12}'.rstrip())
                if produtos:
                    linhas.append(_total('SOMA PRODUTOS:', soma_produtos))

                linhas.append(_total('TOTAL RASTREADOR:', mensalidade))
                linhas.append('')
                total_veiculo += mensalidade + soma_produtos

            linhas.append(_total('TOTAL VEÍCULO:', total_veiculo))
            linhas.append('')
            total_grupo += total_veiculo

        # Sem impostos configurados, os dois totais são iguais — as duas linhas
        # existem porque o relatório de referência as traz.
        linhas.append(_total('TOTAL BOLETO S/ IMPOSTOS:', total_grupo))
        linhas.append(_total('TOTAL BOLETO C/ IMPOSTOS:', total_grupo))
        linhas.append(_SEP)

    linhas += _movimentacao_do_mes(itens, simulation, por_interveniente, mes_ref)
    linhas += _resumo_geral(simulation, itens, por_interveniente, mes_ref, fin)
    return linhas


def _movimentacao_do_mes(itens: list[dict], simulation: dict,
                         por_interveniente: dict[str, list[dict]],
                         mes_ref: str) -> list[str]:
    """
    Instalações e desinstalações do período, uma a uma.

    Pedido da reunião de 07/08/2026: o resumo diz quantas foram; aqui se vê
    quais — por interveniente e por cliente, com data, placa e equipamento.
    """
    interveniente_do_cliente = {
        i.get('client_id'): (i.get('interveniente_nome') or i['client_name']) for i in itens
    }

    instalacoes: dict[str, list[dict]] = defaultdict(list)
    for item in itens:
        if _no_mes(item.get('tracker_install_date'), mes_ref):
            instalacoes[item.get('interveniente_nome') or item['client_name']].append(item)

    desinstalacoes: dict[str, list[dict]] = defaultdict(list)
    for ev in simulation.get('uninstall_events') or []:
        grupo = (
            ev.get('payer_name')
            or interveniente_do_cliente.get(ev.get('client_id'))
            or ev.get('client_name') or ''
        )
        desinstalacoes[grupo].append(ev)

    if not instalacoes and not desinstalacoes:
        return []

    linhas = ['', f'MOVIMENTAÇÃO DO PERÍODO — {mes_ref}'.center(_LARGURA), _SEP]

    for grupo in sorted(set(instalacoes) | set(desinstalacoes)):
        linhas.append(f'INTERVENIENTE: {grupo}')

        for item in sorted(instalacoes.get(grupo, []),
                           key=lambda i: (i.get('tracker_install_date') or date.min,
                                          i.get('vehicle_plate') or '')):
            linhas.append(_par(
                f'    INSTALAÇÃO  {_d(item.get("tracker_install_date"))}  '
                f'{(item.get("vehicle_plate") or "SEM PLACA"):<10} '
                f'RASTREADOR {item.get("tracker_imei") or "—"}',
                item['client_name'][:34],
            ))

        for ev in sorted(desinstalacoes.get(grupo, []),
                         key=lambda e: (e.get('uninstall_date') or date.min,
                                        e.get('vehicle_plate') or '')):
            taxa = float(ev.get('fee_amount') or 0)
            # O valor pequeno continua devido e será acumulado; o relatório não
            # pode apresentá-lo como isento ou descartado.
            sufixo = (
                f'TAXA {_v(taxa)} (AGUARDANDO ACUMULAÇÃO)'
                if ev.get('deferred') else f'TAXA {_v(taxa)}'
            )
            linhas.append(_par(
                f'    DESINSTALAÇÃO  {_d(ev.get("uninstall_date"))}  '
                f'{(ev.get("vehicle_plate") or "SEM PLACA"):<10} {sufixo}',
                (ev.get('client_name') or '')[:34],
            ))

        linhas.append('    Subtotal: ' + ' · '.join((
            _plural(len(instalacoes.get(grupo, [])), 'instalação', 'instalações'),
            _plural(len(desinstalacoes.get(grupo, [])), 'desinstalação', 'desinstalações'),
        )))
        linhas.append('')

    linhas.append(_SEP)
    return linhas


def _resumo_geral(simulation: dict, itens: list[dict],
                  por_interveniente: dict[str, list[dict]], mes_ref: str,
                  fin: dict[str, float]) -> list[str]:
    """Fecha o relatório com o consolidado do período (mesma conta do topo)."""
    desinstalacoes = simulation.get('uninstall_events') or []
    qtd = _contagens(itens, desinstalacoes, mes_ref)

    linhas = ['', f'RESUMO DO FECHAMENTO — {mes_ref}'.center(_LARGURA), _SEP]
    linhas.append(_par(
        f'INTERVENIENTES: {len(por_interveniente)}',
        _par(f'VEÍCULOS: {qtd["veiculos"]}',
             f'EQUIPAMENTOS: {qtd["equipamentos"]}', 62),
    ))
    linhas.append(_par(
        f'INSTALAÇÕES NO MÊS: {qtd["instalacoes"]}',
        _par(f'DESINSTALAÇÕES NO MÊS: {qtd["desinstalacoes"]}',
             f'CONTRATOS: {len(itens)}', 62),
    ))

    # Cobranças já geradas continuam listadas no detalhamento; sem a linha, o
    # total do resumo pareceria não bater com o que o fechamento vai gerar.
    ja_geradas = int(simulation.get('already_generated') or 0)
    if ja_geradas:
        linhas.append(f'CONTRATOS JÁ FATURADOS NO PERÍODO (inclusos acima): {ja_geradas}')

    linhas.append('')
    linhas.append(_total('TOTAL MENSALIDADES:', fin['mensalidades']))
    if fin['produtos']:
        linhas.append(_total('TOTAL PRODUTOS/SERVIÇOS NA 1ª COBRANÇA:', fin['produtos']))
    if fin['taxas']:
        linhas.append(_total('TOTAL TAXAS DE DESINSTALAÇÃO:', fin['taxas']))
    if fin['servicos']:
        linhas.append(_total('TOTAL SERVIÇOS AVULSOS:', fin['servicos']))
    linhas.append(_total('TOTAL GERAL:', fin['geral']))
    linhas.append(_SEP)
    return linhas


def _grupos_conferencia_xlsx(simulation: dict) -> list[dict]:
    """Reúne todas as naturezas pelo pagador do boleto, sem somar parcelas futuras."""
    itens = simulation.get('items') or []
    pagador_do_cliente = {
        i.get('client_id'): (
            i.get('payer_client_id') or i.get('client_id'),
            i.get('payer_name') or i.get('interveniente_nome') or i.get('client_name'),
        ) for i in itens
    }
    grupos: dict[tuple, dict] = {}

    def adicionar(origem, natureza, descricao, valor, situacao='a_gerar', parcela=''):
        pagador_id, nome = pagador_do_cliente.get(origem.get('client_id'), (None, None))
        pagador_id = origem.get('payer_client_id') or pagador_id or origem.get('client_id')
        nome = origem.get('payer_name') or origem.get('interveniente_nome') or nome
        nome = nome or origem.get('client_name') or f'Cliente #{pagador_id}'
        chave = ('id', pagador_id) if pagador_id is not None else ('nome', nome)
        if chave not in grupos:
            grupos[chave] = {
                'id': pagador_id, 'nome': nome, 'clientes': set(), 'placas': set(),
                'mensalidades': Decimal('0.00'), 'produtos': Decimal('0.00'),
                'taxas': Decimal('0.00'), 'servicos': Decimal('0.00'),
                'ja_faturado': Decimal('0.00'), 'acumulacao': Decimal('0.00'),
                'detalhes': [],
            }
        grupo = grupos[chave]
        cliente = origem.get('client_name') or f'Cliente #{origem.get("client_id")}'
        placa = origem.get('vehicle_plate') or ''
        grupo['clientes'].add(cliente)
        if placa:
            grupo['placas'].add(placa)
        valor = Decimal(str(valor or 0)).quantize(Decimal('0.01'))
        grupo[natureza if situacao == 'a_gerar' else situacao] += valor
        grupo['detalhes'].append({
            'cliente': cliente, 'placa': placa, 'natureza': natureza,
            'descricao': descricao, 'parcela': parcela,
            'a_gerar': valor if situacao == 'a_gerar' else Decimal('0.00'),
            'ja_faturado': valor if situacao == 'ja_faturado' else Decimal('0.00'),
            'acumulacao': valor if situacao == 'acumulacao' else Decimal('0.00'),
        })

    for item in itens:
        situacao = 'ja_faturado' if item.get('already_generated') else 'a_gerar'
        descricao = ('Pró-rata final' if item.get('is_final_prorata') else
                     'Pró-rata inicial' if item.get('is_prorata') else 'Mensalidade')
        if item.get('plan_name'):
            descricao += f' — {item["plan_name"]}'
        adicionar(item, 'mensalidades', descricao, item.get('billing_amount'), situacao)
        for produto in item.get('first_month_charges') or []:
            adicionar(item, 'produtos', produto.get('title') or 'Produto / serviço',
                      produto.get('amount'), situacao)
    for evento in simulation.get('uninstall_events') or []:
        situacao = 'acumulacao' if evento.get('deferred') or evento.get('skipped') else 'a_gerar'
        adicionar(evento, 'taxas', 'Taxa de desinstalação', evento.get('fee_amount'), situacao)
    for servico in simulation.get('charge_items') or []:
        parcelas = int(servico.get('installment_count') or 1)
        parcela = f'{int(servico.get("generated_count") or 0) + 1}/{parcelas}'
        adicionar(servico, 'servicos', servico.get('title') or 'Serviço avulso',
                  servico.get('amount_to_generate'), parcela=parcela)

    for grupo in grupos.values():
        grupo['total'] = sum((grupo[k] for k in ('mensalidades', 'produtos', 'taxas', 'servicos')),
                             Decimal('0.00'))
    return sorted(grupos.values(), key=lambda g: (g['nome'].casefold(), g['id'] or 0))


def generate_closure_xlsx(simulation: dict) -> BytesIO:
    """
    Excel de conferência: a primeira aba reúne todas as cobranças por pagador;
    a segunda mantém seus lançamentos juntos para conferir a composição.
    Resumo, Contratos e Movimentação preservam as informações complementares.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    mes_ref = simulation.get('reference_month', '')
    itens = simulation.get('items') or []
    desinst = simulation.get('uninstall_events') or []
    grupos = _grupos_conferencia_xlsx(simulation)
    fin = {
        chave: sum((g[chave] for g in grupos), Decimal('0.00'))
        for chave in ('mensalidades', 'produtos', 'taxas', 'servicos', 'total', 'ja_faturado', 'acumulacao')
    }
    fin['geral'] = fin['total']
    cont = _contagens(itens, desinst, mes_ref)
    agora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

    interveniente_do_cliente = {
        i.get('client_id'): (i.get('interveniente_nome') or i['client_name']) for i in itens
    }

    MONEY = '"R$" #,##0.00'
    DATA = 'DD/MM/YYYY'
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill('solid', fgColor='1F2A44')
    bold = Font(bold=True)

    def _cabecalho(ws, colunas: list[str]) -> None:
        for ci, nome in enumerate(colunas, 1):
            cell = ws.cell(1, ci, nome)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center', wrap_text=True)
        ws.row_dimensions[1].height = 30
        ws.freeze_panes = 'A2'

    def _linha_conferencia(ws, valores: list) -> None:
        ws.append(valores)
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = 's'

    wb = Workbook()

    # ── Conferência principal: uma linha com TODAS as cobranças do pagador ───
    conferencia = wb.active
    conferencia.title = 'Conferência por cliente'
    _cabecalho(conferencia, [
        'Responsável financeiro', 'Cód. cliente', 'Cliente(s)', 'Placa(s)',
        'Mensalidades', 'Produtos / 1ª cobrança', 'Taxas de desinstalação',
        'Serviços avulsos', 'Total a gerar', 'Já faturado', 'Aguardando acumulação',
    ])
    for grupo in grupos:
        _linha_conferencia(conferencia, [
            grupo['nome'], grupo['id'], '; '.join(sorted(grupo['clientes'])),
            '; '.join(sorted(grupo['placas'])),
            *[float(grupo[k]) for k in (
                'mensalidades', 'produtos', 'taxas', 'servicos', 'total', 'ja_faturado', 'acumulacao',
            )],
        ])
    conferencia.auto_filter.ref = f'A1:K{conferencia.max_row}'
    _linha_conferencia(conferencia, [
        'TOTAL GERAL', None, None, None,
        *[float(fin[k]) for k in (
            'mensalidades', 'produtos', 'taxas', 'servicos', 'total', 'ja_faturado', 'acumulacao',
        )],
    ])
    for row in conferencia.iter_rows(min_row=2):
        for cell in row[4:]:
            cell.number_format = MONEY
    for cell in conferencia[conferencia.max_row]:
        cell.font = bold
        cell.fill = PatternFill('solid', fgColor='E8EDF5')
    for ci, largura in enumerate([32, 13, 38, 24, 17, 23, 23, 18, 19, 18, 25], 1):
        conferencia.column_dimensions[get_column_letter(ci)].width = largura

    # ── Todos os lançamentos contíguos por cliente, inclusive só avulsas ──────
    detalhe = wb.create_sheet('Detalhamento por cliente')
    _cabecalho(detalhe, [
        'Responsável financeiro', 'Cód. cliente', 'Cliente', 'Placa', 'Tipo',
        'Descrição', 'Parcela', 'A gerar', 'Já faturado', 'Aguardando acumulação',
    ])
    naturezas = {
        'mensalidades': 'Mensalidade / pró-rata', 'produtos': 'Produto / 1ª cobrança',
        'taxas': 'Desinstalação', 'servicos': 'Serviço avulso',
    }
    for numero, grupo in enumerate(grupos):
        for item in grupo['detalhes']:
            _linha_conferencia(detalhe, [
                grupo['nome'], grupo['id'], item['cliente'], item['placa'],
                naturezas[item['natureza']], item['descricao'], item['parcela'],
                float(item['a_gerar']), float(item['ja_faturado']), float(item['acumulacao']),
            ])
            for cell in detalhe[detalhe.max_row]:
                if numero % 2 == 0:
                    cell.fill = PatternFill('solid', fgColor='F0F4FA')
            for cell in detalhe[detalhe.max_row][7:]:
                cell.number_format = MONEY
    detalhe.auto_filter.ref = f'A1:J{detalhe.max_row}'
    for ci, largura in enumerate([32, 13, 32, 12, 24, 48, 12, 18, 18, 25], 1):
        detalhe.column_dimensions[get_column_letter(ci)].width = largura

    # ── Aba Resumo ──────────────────────────────────────────────────────────
    ws = wb.create_sheet('Resumo')
    ws['A1'] = 'PRÉVIA DE FECHAMENTO'
    ws['A1'].font = Font(bold=True, size=14)
    ws['A2'] = f'Mês de referência: {mes_ref}'
    ws['A3'] = f'Gerado em: {agora}'

    ws['A5'] = 'Totais do período'
    ws['A5'].font = bold
    linha = 6
    for rotulo, valor in (
        ('Veículos', cont['veiculos']),
        ('Rastreadores', cont['equipamentos']),
        ('Instalações', cont['instalacoes']),
        ('Desinstalações', cont['desinstalacoes']),
    ):
        ws.cell(linha, 1, rotulo).font = bold
        ws.cell(linha, 2, valor)
        linha += 1

    linha += 1
    ws.cell(linha, 1, 'Totais financeiros').font = bold
    linha += 1
    for rotulo, valor in (
        ('Mensalidades a gerar', fin['mensalidades']),
        ('Produtos/serviços a gerar (1ª cobrança)', fin['produtos']),
        ('Taxas de desinstalação a gerar', fin['taxas']),
        ('Serviços avulsos a gerar', fin['servicos']),
        ('TOTAL GERAL A GERAR', fin['geral']),
        ('Já faturado (fora do total a gerar)', fin['ja_faturado']),
        ('Aguardando acumulação (fora do total)', fin['acumulacao']),
    ):
        c1 = ws.cell(linha, 1, rotulo)
        c2 = ws.cell(linha, 2, float(valor))
        c2.number_format = MONEY
        if rotulo.startswith('TOTAL GERAL'):
            c1.font = bold
            c2.font = bold
        linha += 1
    ws.column_dimensions['A'].width = 40
    ws.column_dimensions['B'].width = 20

    # ── Aba Contratos ───────────────────────────────────────────────────────
    ws2 = wb.create_sheet('Contratos')
    colunas = [
        'Interveniente', 'Cliente', 'Placa', 'Tipo veículo', 'Rastreador (IMEI)',
        'Data instalação', 'Data contrato', 'Plano', 'Mensalidade',
        'Produtos 1ª cobrança', 'Total', 'Vencimento', 'Período', 'Já faturado',
    ]
    _cabecalho(ws2, colunas)
    rr = 2
    for it in itens:
        produtos = sum(float(p.get('amount') or 0) for p in (it.get('first_month_charges') or []))
        valores = [
            it.get('interveniente_nome') or it.get('client_name'),
            it.get('client_name'),
            it.get('vehicle_plate') or '',
            (it.get('vehicle_type') or '').upper(),
            it.get('tracker_imei') or '',
            it.get('tracker_install_date'),
            it.get('contract_start_date'),
            it.get('plan_name'),
            float(it.get('billing_amount') or 0),
            produtos,
            float(it.get('total_first_billing') or 0),
            it.get('due_date'),
            it.get('service_period_label') or it.get('period_label') or '',
            'Sim' if it.get('already_generated') else 'Não',
        ]
        for ci, val in enumerate(valores, 1):
            cell = ws2.cell(rr, ci, val)
            if ci in (6, 7, 12):
                cell.number_format = DATA
            elif ci in (9, 10, 11):
                cell.number_format = MONEY
        rr += 1
    for ci, largura in enumerate([28, 28, 10, 14, 20, 15, 15, 20, 14, 18, 14, 13, 10, 12], 1):
        ws2.column_dimensions[get_column_letter(ci)].width = largura

    # ── Aba Movimentação ────────────────────────────────────────────────────
    ws3 = wb.create_sheet('Movimentação')
    _cabecalho(ws3, ['Tipo', 'Data', 'Interveniente', 'Cliente', 'Placa', 'Rastreador', 'Taxa', 'Status'])
    rr = 2
    for it in itens:
        if _no_mes(it.get('tracker_install_date'), mes_ref):
            valores = [
                'Instalação', it.get('tracker_install_date'),
                it.get('interveniente_nome') or it.get('client_name'),
                it.get('client_name'), it.get('vehicle_plate') or '',
                it.get('tracker_imei') or '', None, '',
            ]
            for ci, val in enumerate(valores, 1):
                cell = ws3.cell(rr, ci, val)
                if ci == 2:
                    cell.number_format = DATA
            rr += 1
    for ev in desinst:
        taxa = float(ev.get('fee_amount') or 0)
        grupo = (
            ev.get('payer_name')
            or interveniente_do_cliente.get(ev.get('client_id'))
            or ev.get('client_name') or ''
        )
        valores = [
            'Desinstalação', ev.get('uninstall_date'), grupo,
            ev.get('client_name') or '', ev.get('vehicle_plate') or '', '', taxa,
            ('Aguardando acumulação' if ev.get('deferred') else 'A faturar'),
        ]
        for ci, val in enumerate(valores, 1):
            cell = ws3.cell(rr, ci, val)
            if ci == 2:
                cell.number_format = DATA
            elif ci == 7 and val is not None:
                cell.number_format = MONEY
        rr += 1
    for ci, largura in enumerate([14, 12, 28, 28, 10, 20, 12, 24], 1):
        ws3.column_dimensions[get_column_letter(ci)].width = largura

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def generate_closure_pdf(simulation: dict) -> BytesIO:
    """Simulação de fechamento em PDF, no formato monoespaçado do SGR."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    linhas = montar_linhas_simulacao(simulation)

    buffer = BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=A4)
    c.setTitle('Simulação de Fechamento — MasterSat')
    largura, altura = A4
    margem = 10 * mm
    topo = altura - margem
    passo = 3.3 * mm
    fonte = 6.5

    y = topo
    pagina = 1
    for linha in linhas:
        if y < margem + 8 * mm:
            c.setFont('Courier', 6)
            c.drawCentredString(largura / 2, margem, f'Página {pagina}')
            c.showPage()
            pagina += 1
            y = topo
        c.setFont('Courier', fonte)
        c.drawString(margem, y, linha)
        y -= passo

    c.setFont('Courier', 6)
    c.drawCentredString(largura / 2, margem, f'Página {pagina}')
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer
