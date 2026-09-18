"""
Relatório de compatibilidade da POC de migração SGR → MasterSat
(ETAPAS 10 a 14). Constrói a estrutura de dados a partir de um
`PocRunResult` (ver poc.py), mascarando dados pessoais antes de expor via
texto no console ou gravar em disco.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from app.services.sgr_migration.mapping import (
    CLIENT_FIELD_MAP,
    SITUACAO_SYMBOL,
    TRACKER_FIELD_MAP,
    VEHICLE_FIELD_MAP,
    FieldMapping,
)
from app.services.sgr_migration.masking import mask_document, mask_email, mask_imei, mask_phone
from app.services.sgr_migration.poc import PocRunResult

KNOWN_LIMITATIONS = [
    'Veículos são descobertos só a partir dos 10 clientes selecionados (filtro cod_cliente em '
    '/buscar_veiculo) — não há varredura global, então "veículos sem cliente" é sempre 0 por '
    'construção nesta POC (detectar órfãos globalmente exigiria uma varredura completa, fora do '
    'escopo desta etapa).',
    'Equipamentos são descobertos só a partir da placa de cada veículo (filtro placa_vinculo em '
    '/buscar_vinculo) — "equipamento sem veículo" não é observável por essa via.',
    'Os nomes de campo de /buscar_veiculo e /buscar_vinculo foram descobertos inspecionando a API '
    'real (a doc do fornecedor não traz exemplo de resposta do primeiro e mostra um exemplo errado '
    'no segundo). O domínio de situações é observado da amostra — valores fora dos 10 clientes '
    'lidos podem existir e aparecerão como "situação sem mapeamento conhecido".',
    'O IMEI do rastreador exige cruzar /buscar_vinculo com /buscar_rastreador pela placa; o índice '
    'é paginado até 15 páginas de 200. Numa base maior que ~3.000 rastreadores, os excedentes '
    'ficariam sem IMEI e seriam sinalizados no relatório.',
]


def _overall_symbol(field_map: list[FieldMapping]) -> str:
    """Veredito da entidade pela VIABILIDADE da migração, não pela contagem
    bruta de campos.

    Campo do SGR sem equivalente no MasterSat (`nao_existe`, ex.: km do
    veículo) NÃO derruba o veredito: é dado que simplesmente não migra, e
    isso é uma decisão aceitável — ele continua listado na tabela. O que
    pesa é não saber a estrutura (bloqueia) ou o MasterSat exigir um campo
    que a origem não fornece (exige decisão antes de migrar).
    """
    if any(f.situacao == 'nao_documentado' for f in field_map):
        return SITUACAO_SYMBOL['nao_documentado']
    if any(f.situacao == 'obrigatorio_ausente' for f in field_map):
        return SITUACAO_SYMBOL['obrigatorio_ausente']
    if any(f.situacao == 'transformacao' for f in field_map):
        return SITUACAO_SYMBOL['transformacao']
    return SITUACAO_SYMBOL['compativel']


def _field_map_table(field_map: list[FieldMapping]) -> list[dict]:
    return [
        {
            'sgr': f.sgr_field,
            'mastersat': f.mastersat_field,
            'situacao': f.situacao,
            'simbolo': SITUACAO_SYMBOL[f.situacao],
            'nota': f.nota,
        }
        for f in field_map
    ]


def _duplicates(pairs: list[tuple[str, object]]) -> dict:
    grouped: dict[str, list] = defaultdict(list)
    for value, ident in pairs:
        if value:
            grouped[value].append(ident)
    return {value: ids for value, ids in grouped.items() if len(ids) > 1}


def build_report(result: PocRunResult) -> dict:
    clients = result.clients

    total_clientes = len(clients)
    clientes_com_erro = sum(1 for c in clients if c.fetch_failed)
    clientes_processados = total_clientes - clientes_com_erro

    all_vehicles = [v for c in clients for v in c.vehicles]
    all_trackers = [t for v in all_vehicles for t in v.trackers]

    veiculos_sem_equipamento = sum(1 for v in all_vehicles if not v.trackers)
    clientes_sem_veiculo = sum(1 for c in clients if not c.vehicles)

    problemas: list[str] = []
    for c in clients:
        ident = c.mapped.get('external_id', '?')
        for issue in c.issues:
            problemas.append(f'cliente #{ident}: {issue}')
        for v in c.vehicles:
            vident = v.mapped.get('external_id') or mask_document(v.mapped.get('plate') or '') or '?'
            for issue in v.issues:
                problemas.append(f'cliente #{ident} / veículo #{vident}: {issue}')
            for t in v.trackers:
                tident = t.mapped.get('external_id', '?')
                for issue in t.issues:
                    problemas.append(f'cliente #{ident} / veículo #{vident} / equipamento #{tident}: {issue}')

    cpf_pairs = [(c.mapped.get('cpf_cnpj'), c.mapped.get('external_id')) for c in clients]
    email_pairs = [
        (e, c.mapped.get('external_id'))
        for c in clients
        for e in ([c.mapped.get('email')] if c.mapped.get('email') else []) + (c.mapped.get('extra_emails') or [])
    ]
    phone_pairs = [(c.mapped.get('phone'), c.mapped.get('external_id')) for c in clients]
    plate_pairs = [(v.mapped.get('plate'), v.mapped.get('external_id')) for v in all_vehicles]
    imei_pairs = [(t.mapped.get('imei'), t.mapped.get('external_id')) for t in all_trackers]

    duplicidades = {
        'cpf_cnpj': _duplicates(cpf_pairs),
        'email': {mask_email(k): v for k, v in _duplicates(email_pairs).items()},
        'telefone': {mask_phone(k): v for k, v in _duplicates(phone_pairs).items()},
        'placa': _duplicates(plate_pairs),
        'imei': {mask_imei(k): v for k, v in _duplicates(imei_pairs).items()},
    }
    for label, dupes in duplicidades.items():
        for value, ids in dupes.items():
            problemas.append(f'{label} duplicado ({value}) nos registros: {ids}')

    endpoint_counts: dict[str, int] = defaultdict(int)
    for entry in result.request_log:
        endpoint_counts[f'{entry.method} {entry.path}'] += 1

    arvore = []
    for c in clients:
        arvore.append({
            'cliente_external_id': c.mapped.get('external_id'),
            'nome': c.mapped.get('name'),
            'cpf_cnpj_mascarado': mask_document(c.mapped.get('cpf_cnpj')),
            'telefone_mascarado': mask_phone(c.mapped.get('phone')),
            'email_mascarado': mask_email(c.mapped.get('email')),
            'issues': c.issues,
            'veiculos': [
                {
                    'veiculo_external_id': v.mapped.get('external_id'),
                    'placa': v.mapped.get('plate'),
                    'marca': v.mapped.get('brand'),
                    'modelo': v.mapped.get('model'),
                    'situacao': v.mapped.get('status'),
                    'issues': v.issues,
                    'equipamentos': [
                        {
                            'equipamento_external_id': t.mapped.get('external_id'),
                            'imei_mascarado': mask_imei(t.mapped.get('imei')),
                            'marca': t.mapped.get('brand'),
                            'modelo': t.mapped.get('model'),
                            'issues': t.issues,
                        }
                        for t in v.trackers
                    ],
                }
                for v in c.vehicles
            ],
        })

    return {
        'origem': 'SGR Hinova',
        'modo': 'READ_ONLY',
        'gerado_em': datetime.now(timezone.utc).isoformat(),
        'limite_clientes': result.limit,
        'clientes': {
            'consultados': total_clientes,
            'processados': clientes_processados,
            'com_erro': clientes_com_erro,
            'sem_veiculo': clientes_sem_veiculo,
        },
        'veiculos': {
            'encontrados': len(all_vehicles),
            'com_cliente': len(all_vehicles),  # ver limitacoes_conhecidas — construção garante isso
            'sem_cliente': 0,
            'sem_equipamento': veiculos_sem_equipamento,
        },
        'equipamentos': {
            'encontrados': len(all_trackers),
            'com_veiculo': len(all_trackers),  # idem — descobertos via placa do veículo
            'sem_veiculo': None,  # não observável nesta abordagem — ver limitacoes_conhecidas
        },
        'compatibilidade': {
            'clientes': _overall_symbol(CLIENT_FIELD_MAP),
            'veiculos': _overall_symbol(VEHICLE_FIELD_MAP),
            'equipamentos': _overall_symbol(TRACKER_FIELD_MAP),
        },
        'problemas': problemas,
        'duplicidades': duplicidades,
        'limitacoes_conhecidas': KNOWN_LIMITATIONS,
        'mapeamento': {
            'cliente': _field_map_table(CLIENT_FIELD_MAP),
            'veiculo': _field_map_table(VEHICLE_FIELD_MAP),
            'rastreador': _field_map_table(TRACKER_FIELD_MAP),
        },
        'requisicoes': {
            'total': result.request_count,
            'por_endpoint': dict(endpoint_counts),
        },
        'arvore': arvore,
    }


def render_text_report(report: dict) -> str:
    lines = [
        f'Origem: {report["origem"]}',
        f'Modo: {report["modo"]}',
        f'Limite: {report["limite_clientes"]} clientes',
        '',
        'CLIENTES',
        f'  Consultados: {report["clientes"]["consultados"]}',
        f'  Processados: {report["clientes"]["processados"]}',
        f'  Com erro: {report["clientes"]["com_erro"]}',
        f'  Sem veículo: {report["clientes"]["sem_veiculo"]}',
        '',
        'VEÍCULOS',
        f'  Encontrados: {report["veiculos"]["encontrados"]}',
        f'  Com cliente: {report["veiculos"]["com_cliente"]}',
        f'  Sem cliente: {report["veiculos"]["sem_cliente"]}',
        f'  Sem equipamento: {report["veiculos"]["sem_equipamento"]}',
        '',
        'EQUIPAMENTOS',
        f'  Encontrados: {report["equipamentos"]["encontrados"]}',
        f'  Com veículo: {report["equipamentos"]["com_veiculo"]}',
        f'  Sem veículo: {report["equipamentos"]["sem_veiculo"]} (não observável nesta abordagem)',
        '',
        'COMPATIBILIDADE',
        f'  Clientes: {report["compatibilidade"]["clientes"]}',
        f'  Veículos: {report["compatibilidade"]["veiculos"]}',
        f'  Equipamentos: {report["compatibilidade"]["equipamentos"]}',
        '',
        f'PROBLEMAS ({len(report["problemas"])})',
    ]
    if report['problemas']:
        lines.extend(f'  - {p}' for p in report['problemas'])
    else:
        lines.append('  (nenhum)')

    lines += ['', 'REQUISIÇÕES']
    lines.append(f'  Total: {report["requisicoes"]["total"]}')
    for endpoint, count in report['requisicoes']['por_endpoint'].items():
        lines.append(f'  {endpoint}: {count}')

    lines += ['', 'LIMITAÇÕES CONHECIDAS DESTA POC']
    lines.extend(f'  - {limitation}' for limitation in report['limitacoes_conhecidas'])

    lines += ['', 'POC concluída.']
    return '\n'.join(lines)
