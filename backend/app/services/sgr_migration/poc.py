"""
Orquestração da POC de migração SGR → MasterSat (ETAPAS 5 a 8).

READ-ONLY: só executa GET contra o SGR (via SGRClient) e nunca grava no
banco do MasterSat — os resultados ficam em memória / no relatório de
diagnóstico gerado por report.py.
"""
from __future__ import annotations

import inspect
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from time import sleep

from app.services.sgr_migration.client import (
    ColetaPaginada,
    RequestLogEntry,
    SGRApiError,
    SGRAuthenticationError,
    SGRClient,
    iterar_paginas,
    paginar,
)
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
    # Notas fiscais do cliente: {'cod_boleto', 'numero_nf', 'data_emissao',
    # 'url'}. Uma NF cobre o boleto consolidado inteiro, por isso fica aqui e
    # não junto de uma cobrança específica.
    invoices: list[dict] = field(default_factory=list)
    # Motivos pelos quais a coleta deste cliente não é comprovadamente
    # completa (página que falhou, página repetida, boletos em aberto não
    # confirmados). Cliente com coleta incompleta NÃO é importado: entraria
    # com parte da frota/histórico e pareceria completo (SGR-05).
    coleta_incompleta: list[str] = field(default_factory=list)


@dataclass
class PocRunResult:
    limit: int
    clients: list[ClientNode]
    request_count: int
    request_log: list[RequestLogEntry]
    plans: list[dict] = field(default_factory=list)
    plan_issues: list[str] = field(default_factory=list)
    # Clientes lidos do SGR mas fora do escopo da migração (situação não é
    # ATIVO/INADIMPLENTE) — {situação: quantidade}. Não entram em `clients`
    # nem geram veículo/contrato/cobrança; ficam só como contagem para o
    # relatório, para "10 clientes" não virar silenciosamente "6 clientes"
    # sem explicação nenhuma.
    clients_out_of_scope: dict[str, int] = field(default_factory=dict)
    # Mesma ideia para veículos: {situação: quantidade} dos que não são
    # ATIVO/INADIMPLENTE. As cobranças antigas dessas placas continuam vindo
    # no histórico do cliente (entram sem veículo — ver _import_billings).
    vehicles_out_of_scope: dict[str, int] = field(default_factory=dict)
    # Manifesto de completude: uma entrada por coleção paginada (ver
    # ColetaPaginada) — é o que prova que a leitura chegou ao fim.
    coletas: list[dict] = field(default_factory=list)


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
_PAGINA_PADRAO = 200


def _com_tentativas(buscar):
    """Repete uma página que falhou por instabilidade (5xx, timeout). Erro de
    credencial não é instabilidade: sobe na hora."""
    def _pagina(total, indice):
        for tentativa in range(_TENTATIVAS_POR_MES):
            try:
                return buscar(total, indice)
            except SGRAuthenticationError:
                raise
            except SGRApiError:
                if tentativa == _TENTATIVAS_POR_MES - 1:
                    raise
                sleep(_ESPERA_ENTRE_TENTATIVAS * (tentativa + 1))
        return []  # inalcançável
    return _pagina


def build_tracker_index(client: SGRClient, coletas: list[dict] | None = None) -> dict[str, dict]:
    """Índice placa -> rastreador, paginando /buscar_rastreador ATÉ O FIM.

    /buscar_rastreador é o único endpoint que devolve IMEI, situação e placa
    no mesmo registro, e não aceita filtro por placa nem por cliente. Paginar
    uma vez e indexar localmente troca N chamadas (1 por veículo) por poucas,
    e é o que permite preencher o IMEI de qualquer veículo da amostra.

    Antes havia um teto silencioso de 15 páginas e um `break` em qualquer
    erro: rastreador além disso entrava "sem IMEI" como se não existisse.
    Agora índice incompleto interrompe a leitura (SGRIncompleteScan).
    """
    coleta = ColetaPaginada('/buscar_rastreador', 'todos')
    index: dict[str, dict] = {}
    try:
        buscar = _com_tentativas(lambda total, indice: client.buscar_rastreadores(total=total, indice=indice))
        for lote in iterar_paginas(buscar, _PAGINA_RASTREADOR, coleta):
            for rast in lote:
                placa = normalize_plate(ci_get(rast, 'placa') or '')
                if placa:
                    index.setdefault(placa, rast)
    except SGRApiError as exc:
        raise SGRIncompleteScan(
            f'índice de rastreadores incompleto ({type(exc).__name__}: {exc}) — sem ele os '
            f'rastreadores das páginas não lidas entrariam como "sem IMEI"'
        ) from exc
    finally:
        if coletas is not None:
            coletas.append(coleta.as_dict())
    return index


def _fetch_trackers_for_vehicle(
    client: SGRClient, plate: str, issues: list[str], tracker_index: dict[str, dict],
    vencimento_index: dict[str, int] | None = None,
    pendencias: list[str] | None = None, coletas: list[dict] | None = None, cod_veiculo=None,
) -> list[TrackerNode]:
    trackers: list[TrackerNode] = []
    coleta = ColetaPaginada('/buscar_vinculo', f'veiculo:{cod_veiculo or "?"}')
    try:
        vinculos_raw = paginar(
            lambda total, indice: client.buscar_vinculos_por_placa(plate, total=total, indice=indice),
            _PAGINA_PADRAO, coleta,
        )
    except SGRApiError as exc:
        issues.append(f'Falha ao buscar vínculo/equipamento deste veículo: {exc}')
        if pendencias is not None:
            pendencias.append(f'vínculos do veículo #{cod_veiculo or "?"}: {type(exc).__name__}')
        return trackers
    finally:
        if coletas is not None:
            coletas.append(coleta.as_dict())
    for link in vinculos_raw:
        tmapped, tissues = map_tracker(link, tracker_index.get(plate))
        contract, cissues = map_contrato(link, vencimento_index or {})
        tissues.extend(cissues)
        trackers.append(TrackerNode(raw=link, mapped=tmapped, issues=tissues, contract=contract))
    return trackers


# No SGR a inadimplência é situação do VEÍCULO (ver _VEHICLE_STATUS_MAP) —
# veículo INADIMPLENTE continua rastreado e cobrado, então entra junto.
_SITUACOES_VEICULO_MIGRAVEIS = {'ATIVO', 'ATIVADO', 'INADIMPLENTE'}


def _situacao_veiculo(raw: dict) -> str:
    return str(ci_get(raw, 'situacao_veiculo') or '(sem situação)').strip().upper()


def _fetch_vehicles_for_client(
    client: SGRClient, cod_cliente, issues: list[str], tracker_index: dict[str, dict],
    vencimento_index: dict[str, int] | None = None,
    fora_do_escopo: dict[str, int] | None = None,
    pendencias: list[str] | None = None, coletas: list[dict] | None = None,
) -> list[VehicleNode]:
    vehicles: list[VehicleNode] = []
    if not cod_cliente:
        issues.append('Cliente sem cod_cliente na resposta — não é possível buscar seus veículos')
        if pendencias is not None:
            pendencias.append('cliente sem cod_cliente: frota não pode ser lida')
        return vehicles
    coleta = ColetaPaginada('/buscar_veiculo', f'cliente:{cod_cliente}')
    try:
        veiculos_raw = paginar(
            lambda total, indice: client.buscar_veiculos_por_cliente(cod_cliente, total=total, indice=indice),
            _PAGINA_PADRAO, coleta,
        )
    except SGRApiError as exc:
        issues.append(f'Falha ao buscar veículos deste cliente: {exc}')
        if pendencias is not None:
            pendencias.append(f'veículos do cliente: {type(exc).__name__}')
        return vehicles
    finally:
        if coletas is not None:
            coletas.append(coleta.as_dict())

    for vraw in veiculos_raw:
        situacao = _situacao_veiculo(vraw)
        if situacao not in _SITUACOES_VEICULO_MIGRAVEIS:
            if fora_do_escopo is not None:
                fora_do_escopo[situacao] = fora_do_escopo.get(situacao, 0) + 1
            continue
        vmapped, vissues = map_veiculo(vraw)
        plate = vmapped.get('plate')
        trackers = (
            _fetch_trackers_for_vehicle(
                client, plate, vissues, tracker_index, vencimento_index,
                pendencias, coletas, vmapped.get('external_id'),
            )
            if plate else []
        )
        if not trackers:
            vissues.append('Veículo sem equipamento/rastreador vinculado')
        vehicles.append(VehicleNode(raw=vraw, mapped=vmapped, issues=vissues, trackers=trackers))

    if not vehicles:
        issues.append('Cliente sem veículo ativo' if veiculos_raw else 'Cliente sem veículo cadastrado')
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


def _fetch_boletos(
    client: SGRClient, cpf_cnpj: str, issues: list[str],
    pendencias: list[str] | None = None, coletas: list[dict] | None = None, cod_cliente=None,
) -> tuple[list[dict], list[dict]]:
    """Histórico de cobrança do cliente: (cobranças achatadas, boletos crus).

    Uma chamada por cliente: /buscar_boletos (sem CPF) responde 500 no
    servidor deles, então não dá para varrer tudo de uma vez. O CPF vai sem
    máscara — com máscara a API devolve lista vazia sem erro nenhum.

    Os boletos crus voltam junto porque a nota fiscal é buscada a partir
    deles (ver _fetch_notas_fiscais), sem repetir a consulta.
    """
    if not cpf_cnpj:
        return [], []
    pendencias = pendencias if pendencias is not None else []
    coleta = ColetaPaginada('/buscar_boletos_cliente', f'cliente:{cod_cliente or "?"}')
    try:
        boletos = paginar(
            lambda total, indice: client.buscar_boletos_cliente(cpf_cnpj, total=total, indice=indice),
            _PAGINA_PADRAO, coleta,
        )
    except SGRApiError as exc:
        issues.append(f'Falha ao buscar o histórico de boletos: {exc}')
        pendencias.append(f'histórico de boletos: {type(exc).__name__}')
        return [], []
    finally:
        if coletas is not None:
            coletas.append(coleta.as_dict())

    # A situação do boleto NÃO diz o que continua em aberto: 'APROVADO'
    # significa apenas registrado, e na base real há boletos de 2019 e 2023
    # ainda com esse status. Tratá-los como dívida inflaria a inadimplência
    # e poderia gerar cobrança indevida. Quem responde isso é
    # /buscar_boletos_abertos_cliente, e é ele a fonte da verdade aqui.
    coleta_abertos = ColetaPaginada('/buscar_boletos_abertos_cliente', f'cliente:{cod_cliente or "?"}')
    try:
        abertos = {
            str(ci_get(b, 'cod_boleto'))
            for b in paginar(
                lambda total, indice: client.buscar_boletos_abertos_cliente(cpf_cnpj, total=total, indice=indice),
                _PAGINA_PADRAO, coleta_abertos,
            )
            if ci_get(b, 'cod_boleto')
        }
    except SGRApiError as exc:
        # Antes: seguia com `abertos` vazio e importava as dívidas em aberto
        # como CANCELADAS. Sem a confirmação, o cliente inteiro fica de fora.
        issues.append(f'Falha ao confirmar quais boletos estão em aberto ({exc})')
        pendencias.append(f'boletos em aberto não confirmados: {type(exc).__name__}')
        return [], boletos
    finally:
        if coletas is not None:
            coletas.append(coleta_abertos.as_dict())

    achatado: list[dict] = []
    for boleto in boletos:
        linhas = ci_get(boleto, 'discriminacao') or []
        for item in linhas or [None]:
            mapped, bissues = map_boleto(boleto, item, em_aberto=str(ci_get(boleto, 'cod_boleto')) in abertos)
            # Linha zerada é ruído do SGR: todo boleto traz uma cópia da placa
            # com valor 0,00 ao lado da cobrança real. Valor ausente ou
            # malformado NÃO é descartado aqui: o importador confere a soma do
            # documento e decide (antes sumia em silêncio).
            if mapped.get('amount_cents') == 0:
                continue
            achatado.append(mapped)
            issues.extend(bissues)
    return achatado, boletos


# A API do SGR oscila (já respondeu 502 e estourou timeout de 30s no meio de
# uma varredura). Tentar de novo com espera crescente evita perder um mês
# inteiro do histórico por causa de uma falha passageira.
_TENTATIVAS_POR_MES = 3
_ESPERA_ENTRE_TENTATIVAS = 5  # segundos, multiplicados pela tentativa


class SGRIncompleteScan(RuntimeError):
    """A varredura não cobriu todos os meses pedidos.

    É erro, não aviso: uma importação com meses faltando gera um histórico
    financeiro silenciosamente incompleto, que ninguém descobre olhando a
    tela — foi assim que 846 cobranças sumiram sem ninguém notar.
    """


def _aceita_coleta(client) -> bool:
    """O SGRClient real preenche o manifesto de páginas do mês; dublês de
    teste que não aceitam o parâmetro ficam com a contagem simples."""
    try:
        return 'coleta' in inspect.signature(client.buscar_boletos_periodo).parameters
    except (TypeError, ValueError):
        return False


def _meses(inicio: date, fim: date):
    """Gera (primeiro_dia, ultimo_dia) de cada mês do intervalo.

    O /buscar_boletos aceita no máximo 1 mês por consulta, então a varredura
    é obrigatoriamente mês a mês.
    """
    atual = date(inicio.year, inicio.month, 1)
    ultimo = date(fim.year, fim.month, 1)
    while atual <= ultimo:
        yield atual, date(atual.year, atual.month, monthrange(atual.year, atual.month)[1])
        atual = date(atual.year + (atual.month == 12), atual.month % 12 + 1, 1)


def fetch_boletos_por_periodo(
    client: SGRClient, inicio: date, fim: date, issues: list[str],
    coletas: list[dict] | None = None,
) -> dict[str, list[dict]]:
    """Boletos de TODA a base no período, indexados por cod_cliente.

    Substitui a varredura cliente a cliente: uma passada mês a mês cobre os
    520 clientes (um ano de histórico saiu em ~40 requisições), e ainda traz
    campos que a versão por cliente não devolve — `cod_cliente` no boleto e
    `produto`/`situacao_veiculo` em cada item da discriminação.
    """
    por_cliente: dict[str, list[dict]] = {}
    falhas: list[str] = []

    for mes_inicio, mes_fim in _meses(inicio, fim):
        lote = None
        coleta = ColetaPaginada('/buscar_boletos', f'vencimento:{mes_inicio:%Y-%m}')
        for tentativa in range(_TENTATIVAS_POR_MES):
            coleta = ColetaPaginada('/buscar_boletos', f'vencimento:{mes_inicio:%Y-%m}')
            try:
                # linha_digitavel=True é o que traz link/linha/código de barras
                # dos boletos ainda em aberto (ver map_boleto).
                if _aceita_coleta(client):
                    lote = client.buscar_boletos_periodo(
                        mes_inicio.isoformat(), mes_fim.isoformat(), linha_digitavel=True, coleta=coleta,
                    )
                else:
                    lote = client.buscar_boletos_periodo(
                        mes_inicio.isoformat(), mes_fim.isoformat(), linha_digitavel=True,
                    )
                    coleta.registros, coleta.completa = len(lote or []), True
                break
            except SGRApiError as exc:
                if tentativa == _TENTATIVAS_POR_MES - 1:
                    falhas.append(f'{mes_inicio:%m/%Y} ({exc})')
                else:
                    sleep(_ESPERA_ENTRE_TENTATIVAS * (tentativa + 1))
        if coletas is not None:
            coletas.append(coleta.as_dict())

        for boleto in lote or []:
            cod_cliente = ci_get(boleto, 'cod_cliente')
            if cod_cliente:
                por_cliente.setdefault(str(cod_cliente), []).append(boleto)

    if falhas:
        # Um mês que falha some INTEIRO do histórico. Isso não pode virar só
        # um aviso no meio do relatório: quem roda a migração precisa saber
        # que o resultado está incompleto e quais meses refazer.
        raise SGRIncompleteScan(
            f'{len(falhas)} mês(es) não puderam ser lidos e ficariam de fora do histórico: '
            + ', '.join(falhas[:12])
            + (f' ... e mais {len(falhas) - 12}' if len(falhas) > 12 else '')
        )
    return por_cliente


def achatar_boletos(boletos: list[dict], issues: list[str]) -> list[dict]:
    """Boletos crus → uma cobrança por linha de discriminação.

    `em_aberto` fica em None de propósito: vindo do /buscar_boletos a própria
    `situacao` distingue ABERTO (dívida) de APROVADO (só registrado), o que
    dispensa a consulta extra de boletos abertos por cliente.
    """
    achatado: list[dict] = []
    for boleto in boletos:
        linhas = ci_get(boleto, 'discriminacao') or []
        for item in linhas or [None]:
            mapped, bissues = map_boleto(boleto, item)
            # Linha zerada é ruído do SGR: todo boleto traz uma cópia da placa
            # com valor 0,00 ao lado da cobrança real. Valor ausente ou
            # malformado NÃO é descartado aqui: o importador confere a soma do
            # documento e decide (antes sumia em silêncio).
            if mapped.get('amount_cents') == 0:
                continue
            achatado.append(mapped)
            issues.extend(bissues)
    return achatado


def _fetch_notas_fiscais(client: SGRClient, boletos: list[dict], issues: list[str]) -> list[dict]:
    """URL do XML da NFS-e de cada boleto que tem nota.

    Uma chamada por boleto COM nota — na amostra real, 51 notas em 10
    clientes, concentradas nos que têm nota_fiscal_cliente = 'S'. Os boletos
    sem `numero_nf` são pulados sem gastar requisição.
    """
    notas: list[dict] = []
    for boleto in boletos:
        numero_nf = ci_get(boleto, 'numero_nf')
        cod_boleto = ci_get(boleto, 'cod_boleto')
        if not numero_nf or not cod_boleto:
            continue
        # Nota sem URL entra na lista com url=None em vez de sumir: assim o
        # importador a contabiliza como ignorada e o motivo aparece no resumo.
        # Antes isso virava só um aviso interno, e a diferença entre 51 notas
        # existentes e 49 importadas passava despercebida.
        try:
            url = client.buscar_xml_nota_fiscal(cod_boleto)
            motivo = None if url else 'o SGR não devolveu XML para esta nota'
        except SGRApiError as exc:
            url, motivo = None, f'{type(exc).__name__} ao pedir o XML ao SGR'

        if motivo:
            issues.append(f'Nota fiscal {numero_nf} (boleto {cod_boleto}): {motivo}')
        notas.append({
            'cod_boleto': str(cod_boleto),
            'numero_nf': str(numero_nf),
            'data_emissao': ci_get(boleto, 'data_emissao'),
            'url': url,
            'erro': motivo,
        })
    return notas


_PAGINA_CLIENTES = 200

# Só migramos cliente com contrato em vigor ou em cobrança — quem já
# cancelou, foi suspenso ou nunca chegou a ativar não deveria gerar
# contrato/cobrança no MasterSat. 'ATIVADO' é tratado como sinônimo de
# 'ATIVO': apareceu 1 vez em 520 clientes reais e tudo indica erro de
# digitação do mesmo estado — excluir esse único cliente por causa de uma
# letra a mais seria mais arbitrário do que incluir.
_SITUACOES_MIGRAVEIS = {'ATIVO', 'ATIVADO', 'INADIMPLENTE'}


def _situacao_cliente(raw: dict) -> str:
    situacao = ci_get(raw, 'situacao')
    return str(ci_get(situacao, 'descricao') or '(sem situação)').strip().upper()


def _clientes_elegiveis(
    client: SGRClient, limit: int, fora_do_escopo: dict[str, int],
    coletas: list[dict] | None = None,
) -> list[dict]:
    """Pagina /buscar_cliente até juntar `limit` clientes com situação
    migrável (ver _SITUACOES_MIGRAVEIS), pulando os demais sem gastar
    requisição com os veículos/vínculos/boletos deles.

    `limit` conta clientes ELEGÍVEIS, não lidos: na base real (~59% ativo,
    ~33% cancelado), pedir 10 lê bem mais que 10 até juntar 10 utilizáveis.
    """
    elegiveis: list[dict] = []
    if limit <= 0:
        return elegiveis
    coleta = ColetaPaginada('/buscar_cliente', 'todos')
    try:
        for lote in iterar_paginas(
            lambda total, indice: client.buscar_clientes(total=total, indice=indice),
            _PAGINA_CLIENTES, coleta,
        ):
            for raw in lote:
                situacao = _situacao_cliente(raw)
                if situacao in _SITUACOES_MIGRAVEIS:
                    elegiveis.append(raw)
                    if len(elegiveis) >= limit:
                        break
                else:
                    fora_do_escopo[situacao] = fora_do_escopo.get(situacao, 0) + 1
            if len(elegiveis) >= limit:
                coleta.observacao = f'leitura interrompida ao juntar o limite de {limit} cliente(s)'
                break
    finally:
        if coletas is not None:
            coletas.append(coleta.as_dict())
    return elegiveis


def run_poc(
    client: SGRClient, limit: int, com_boletos: bool = False, com_notas: bool = False,
    periodo: tuple[date, date] | None = None,
) -> PocRunResult:
    """ETAPAS 6/7 — busca `limit` clientes ATIVOS/INADIMPLENTES e caminha os
    relacionamentos. Cliente cancelado/suspenso/inativo/só cadastrado é
    contado em `clients_out_of_scope` e não entra na migração."""
    fora_do_escopo: dict[str, int] = {}
    veiculos_fora: dict[str, int] = {}
    coletas: list[dict] = []
    clients_raw = _clientes_elegiveis(client, limit, fora_do_escopo, coletas)
    tracker_index = build_tracker_index(client, coletas)
    planos, plan_issues, vencimento_index = _fetch_planos(client)

    # Uma varredura só para a base inteira, em vez de 1 chamada por cliente.
    boletos_por_cliente: dict[str, list[dict]] = {}
    if (com_boletos or com_notas) and periodo:
        boletos_por_cliente = fetch_boletos_por_periodo(client, periodo[0], periodo[1], plan_issues, coletas)

    nodes: list[ClientNode] = []
    for raw in clients_raw:
        try:
            mapped, issues = map_cliente(raw)
        except Exception as exc:  # noqa: BLE001 — nunca deixa 1 registro ruim derrubar a POC inteira
            nodes.append(ClientNode(raw=raw, mapped={}, issues=[f'Falha ao mapear cliente: {exc}'], fetch_failed=True))
            continue

        pendencias: list[str] = []
        vehicles = _fetch_vehicles_for_client(
            client, ci_get(raw, 'cod_cliente'), issues, tracker_index, vencimento_index,
            veiculos_fora, pendencias, coletas,
        )
        billings: list[dict] = []
        invoices: list[dict] = []
        if com_boletos or com_notas:
            if periodo:
                boletos_crus = boletos_por_cliente.get(str(ci_get(raw, 'cod_cliente') or ''), [])
                billings = achatar_boletos(boletos_crus, issues) if com_boletos else []
            else:
                billings, boletos_crus = _fetch_boletos(
                    client, mapped.get('cpf_cnpj'), issues, pendencias, coletas, ci_get(raw, 'cod_cliente'),
                )
                if not com_boletos:
                    billings = []
            if com_notas:
                invoices = _fetch_notas_fiscais(client, boletos_crus, issues)
        nodes.append(ClientNode(
            raw=raw, mapped=mapped, issues=issues, vehicles=vehicles,
            billings=billings, invoices=invoices, coleta_incompleta=pendencias,
        ))

    return PocRunResult(
        limit=limit,
        clients=nodes,
        request_count=client.request_count,
        request_log=list(client.request_log),
        plans=planos,
        plan_issues=plan_issues,
        clients_out_of_scope=fora_do_escopo,
        vehicles_out_of_scope=veiculos_fora,
        coletas=coletas,
    )
