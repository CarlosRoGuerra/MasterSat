"""
Orquestração da POC de migração SGR → MasterSat (ETAPAS 5 a 8).

READ-ONLY: só executa GET contra o SGR (via SGRClient) e nunca grava no
banco do MasterSat — os resultados ficam em memória / no relatório de
diagnóstico gerado por report.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.sgr_migration.client import RequestLogEntry, SGRApiError, SGRClient
from app.services.sgr_migration.mapping import (
    build_vencimento_index,
    ci_get,
    map_boleto,
    map_cliente,
    map_contrato,
    map_plano,
    map_tracker,
    map_veiculo,
)
from app.services.sgr_migration.normalize import normalize_plate


@dataclass
class TrackerNode:
    raw: dict
    mapped: dict
    issues: list[str]
    # O mesmo registro de vínculo vira duas coisas no MasterSat: o rastreador
    # (acima) e o contrato — no SGR é ele quem guarda plano, vencimento e
    # cobrança. Ver map_contrato() em mapping.py.
    contract: dict = field(default_factory=dict)


@dataclass
class VehicleNode:
    raw: dict
    mapped: dict
    issues: list[str]
    trackers: list[TrackerNode] = field(default_factory=list)


@dataclass
class ClientNode:
    raw: dict
    mapped: dict
    issues: list[str]
    vehicles: list[VehicleNode] = field(default_factory=list)
    fetch_failed: bool = False
    # Histórico de cobrança já achatado: 1 entrada por linha de discriminação
    # do boleto (ver map_boleto). Fica no cliente, e não no veículo, porque no
    # SGR o boleto é consolidado por cliente.
    billings: list[dict] = field(default_factory=list)


@dataclass
class PocRunResult:
    limit: int
    clients: list[ClientNode]
    request_count: int
    request_log: list[RequestLogEntry]
    plans: list[dict] = field(default_factory=list)
    plan_issues: list[str] = field(default_factory=list)


def check_connectivity(client: SGRClient) -> dict:
    """ETAPA 5 — autentica e faz 1 consulta mínima de leitura para validar a integração."""
    client.authenticate()
    body = client.get('/buscar_cliente', {'total': 1, 'indice': 0})
    data = client.extract_data(body)
    return {
        'autenticado': True,
        'registros_retornados': len(data),
        'requisicoes': client.request_count,
    }


_PAGINA_RASTREADOR = 200  # teto por página imposto pela API
_MAX_PAGINAS_RASTREADOR = 15  # teto de segurança da POC (~3.000 rastreadores)


def build_tracker_index(client: SGRClient) -> dict[str, dict]:
    """Índice placa -> rastreador, paginando /buscar_rastreador.

    /buscar_rastreador é o único endpoint que devolve IMEI, situação e placa
    no mesmo registro, e não aceita filtro por placa nem por cliente. Paginar
    uma vez e indexar localmente troca N chamadas (1 por veículo) por poucas,
    e é o que permite preencher o IMEI de qualquer veículo da amostra.
    """
    index: dict[str, dict] = {}
    for pagina in range(_MAX_PAGINAS_RASTREADOR):
        try:
            lote = client.buscar_rastreadores(
                total=_PAGINA_RASTREADOR, indice=pagina * _PAGINA_RASTREADOR,
            )
        except SGRApiError:
            break  # sem índice a POC segue; o IMEI fica ausente e é reportado
        if not lote:
            break
        for rast in lote:
            placa = normalize_plate(ci_get(rast, 'placa') or '')
            if placa:
                index.setdefault(placa, rast)
        if len(lote) < _PAGINA_RASTREADOR:
            break
    return index


def _fetch_trackers_for_vehicle(
    client: SGRClient, plate: str, issues: list[str], tracker_index: dict[str, dict],
    vencimento_index: dict[str, int] | None = None,
) -> list[TrackerNode]:
    trackers: list[TrackerNode] = []
    try:
        vinculos_raw = client.buscar_vinculos_por_placa(plate)
    except SGRApiError as exc:
        issues.append(f'Falha ao buscar vínculo/equipamento deste veículo: {exc}')
        return trackers
    for link in vinculos_raw:
        tmapped, tissues = map_tracker(link, tracker_index.get(plate))
        contract, cissues = map_contrato(link, vencimento_index or {})
        tissues.extend(cissues)
        trackers.append(TrackerNode(raw=link, mapped=tmapped, issues=tissues, contract=contract))
    return trackers


def _fetch_vehicles_for_client(
    client: SGRClient, cod_cliente, issues: list[str], tracker_index: dict[str, dict],
    vencimento_index: dict[str, int] | None = None,
) -> list[VehicleNode]:
    vehicles: list[VehicleNode] = []
    if not cod_cliente:
        issues.append('Cliente sem cod_cliente na resposta — não é possível buscar seus veículos')
        return vehicles
    try:
        veiculos_raw = client.buscar_veiculos_por_cliente(cod_cliente)
    except SGRApiError as exc:
        issues.append(f'Falha ao buscar veículos deste cliente: {exc}')
        return vehicles

    for vraw in veiculos_raw:
        vmapped, vissues = map_veiculo(vraw)
        plate = vmapped.get('plate')
        trackers = (
            _fetch_trackers_for_vehicle(client, plate, vissues, tracker_index, vencimento_index)
            if plate else []
        )
        if not trackers:
            vissues.append('Veículo sem equipamento/rastreador vinculado')
        vehicles.append(VehicleNode(raw=vraw, mapped=vmapped, issues=vissues, trackers=trackers))

    if not vehicles:
        issues.append('Cliente sem veículo cadastrado')
    return vehicles


def _fetch_planos(client: SGRClient) -> tuple[list[dict], list[str], dict[str, int]]:
    """Planos e índice de vencimentos — tabelas de domínio, 1 chamada cada.

    Os planos saem de DUAS fontes: /get_grupo_mensalidade e /get_grupo_adesao.
    Apesar dos nomes, elas são duas visões da MESMA lista de grupos, e o
    `cod_grupo_vinculo` do vínculo aponta para qualquer uma — a numeração não
    se repete entre elas. Confirmado com dado real: 16 veículos do cliente
    ADIR FREITAG têm cod_grupo_vinculo=5, que só existe na tabela de adesão
    ('MASTER ESPECIAL 49,99'), e a discriminação dos boletos de 06, 07 e
    08/2026 cobra exatamente R$ 49,99 por placa. Buscar só a mensalidade
    deixaria esses contratos sem plano.
    """
    planos: list[dict] = []
    issues: list[str] = []
    vistos: set[str] = set()

    for rotulo, metodo in (
        ('/get_grupo_mensalidade', client.get_grupo_mensalidade),
        ('/get_grupo_adesao', client.get_grupo_adesao),
    ):
        try:
            grupos = metodo()
        except SGRApiError as exc:
            issues.append(f'Falha ao buscar os planos ({rotulo}): {exc}')
            continue
        for grupo in grupos:
            mapped, gissues = map_plano(grupo)
            codigo = str(mapped.get('external_id') or '')
            if codigo and codigo in vistos:
                continue  # mensalidade tem precedência: é lida primeiro
            vistos.add(codigo)
            planos.append(mapped)
            issues.extend(gissues)

    vencimento_index: dict[str, int] = {}
    try:
        vencimento_index = build_vencimento_index(client.get_vencimento())
    except SGRApiError as exc:
        issues.append(f'Falha ao buscar os vencimentos (/get_vencimento): {exc}')

    return planos, issues, vencimento_index


def _fetch_boletos(client: SGRClient, cpf_cnpj: str, issues: list[str]) -> list[dict]:
    """Histórico de cobrança do cliente, achatado por linha de discriminação.

    Uma chamada por cliente: /buscar_boletos (sem CPF) responde 500 no
    servidor deles, então não dá para varrer tudo de uma vez. O CPF vai sem
    máscara — com máscara a API devolve lista vazia sem erro nenhum.
    """
    if not cpf_cnpj:
        return []
    try:
        boletos = client.buscar_boletos_cliente(cpf_cnpj)
    except SGRApiError as exc:
        issues.append(f'Falha ao buscar o histórico de boletos: {exc}')
        return []

    achatado: list[dict] = []
    for boleto in boletos:
        linhas = ci_get(boleto, 'discriminacao') or []
        for item in linhas or [None]:
            mapped, bissues = map_boleto(boleto, item)
            # Linha zerada é ruído do SGR: todo boleto traz uma cópia da placa
            # com valor 0,00 ao lado da cobrança real.
            if mapped.get('amount') in (None, 0, 0.0):
                continue
            achatado.append(mapped)
            issues.extend(bissues)
    return achatado


def run_poc(client: SGRClient, limit: int, com_boletos: bool = False) -> PocRunResult:
    """ETAPAS 6/7 — busca exatamente `limit` clientes e caminha os relacionamentos."""
    clients_raw = client.buscar_clientes(total=limit, indice=0)
    tracker_index = build_tracker_index(client)
    planos, plan_issues, vencimento_index = _fetch_planos(client)

    nodes: list[ClientNode] = []
    for raw in clients_raw:
        try:
            mapped, issues = map_cliente(raw)
        except Exception as exc:  # noqa: BLE001 — nunca deixa 1 registro ruim derrubar a POC inteira
            nodes.append(ClientNode(raw=raw, mapped={}, issues=[f'Falha ao mapear cliente: {exc}'], fetch_failed=True))
            continue

        vehicles = _fetch_vehicles_for_client(
            client, ci_get(raw, 'cod_cliente'), issues, tracker_index, vencimento_index,
        )
        billings = _fetch_boletos(client, mapped.get('cpf_cnpj'), issues) if com_boletos else []
        nodes.append(ClientNode(
            raw=raw, mapped=mapped, issues=issues, vehicles=vehicles, billings=billings,
        ))

    return PocRunResult(
        limit=limit,
        clients=nodes,
        request_count=client.request_count,
        request_log=list(client.request_log),
        plans=planos,
        plan_issues=plan_issues,
    )
