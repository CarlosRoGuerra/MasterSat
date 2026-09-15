"""
Orquestração da POC de migração SGR → MasterSat (ETAPAS 5 a 8).

READ-ONLY: só executa GET contra o SGR (via SGRClient) e nunca grava no
banco do MasterSat — os resultados ficam em memória / no relatório de
diagnóstico gerado por report.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.sgr_migration.client import RequestLogEntry, SGRApiError, SGRClient
from app.services.sgr_migration.mapping import ci_get, map_cliente, map_tracker, map_veiculo


@dataclass
class TrackerNode:
    raw: dict
    mapped: dict
    issues: list[str]


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


@dataclass
class PocRunResult:
    limit: int
    clients: list[ClientNode]
    request_count: int
    request_log: list[RequestLogEntry]


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


def _fetch_trackers_for_vehicle(client: SGRClient, plate: str, issues: list[str]) -> list[TrackerNode]:
    trackers: list[TrackerNode] = []
    try:
        vinculos_raw = client.buscar_vinculos_por_placa(plate)
    except SGRApiError as exc:
        issues.append(f'Falha ao buscar vínculo/equipamento deste veículo: {exc}')
        return trackers
    for link in vinculos_raw:
        tmapped, tissues = map_tracker(link)
        trackers.append(TrackerNode(raw=link, mapped=tmapped, issues=tissues))
    return trackers


def _fetch_vehicles_for_client(client: SGRClient, cod_cliente, issues: list[str]) -> list[VehicleNode]:
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
        trackers = _fetch_trackers_for_vehicle(client, plate, vissues) if plate else []
        if not trackers:
            vissues.append('Veículo sem equipamento/rastreador vinculado')
        vehicles.append(VehicleNode(raw=vraw, mapped=vmapped, issues=vissues, trackers=trackers))

    if not vehicles:
        issues.append('Cliente sem veículo cadastrado')
    return vehicles


def run_poc(client: SGRClient, limit: int) -> PocRunResult:
    """ETAPAS 6/7 — busca exatamente `limit` clientes e caminha os relacionamentos."""
    clients_raw = client.buscar_clientes(total=limit, indice=0)

    nodes: list[ClientNode] = []
    for raw in clients_raw:
        try:
            mapped, issues = map_cliente(raw)
        except Exception as exc:  # noqa: BLE001 — nunca deixa 1 registro ruim derrubar a POC inteira
            nodes.append(ClientNode(raw=raw, mapped={}, issues=[f'Falha ao mapear cliente: {exc}'], fetch_failed=True))
            continue

        vehicles = _fetch_vehicles_for_client(client, ci_get(raw, 'cod_cliente'), issues)
        nodes.append(ClientNode(raw=raw, mapped=mapped, issues=issues, vehicles=vehicles))

    return PocRunResult(
        limit=limit,
        clients=nodes,
        request_count=client.request_count,
        request_log=list(client.request_log),
    )
