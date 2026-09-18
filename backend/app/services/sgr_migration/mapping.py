"""
Mapeamento SGR → MasterSat (ETAPA 9) e classificação de compatibilidade
(ETAPA 10). Usa exclusivamente nomes de campo encontrados na documentação
oficial do SGR (api_data.js do /doc/) e nos models atuais do MasterSat
(app/models/client.py, vehicle.py, tracker.py) — nada aqui é inventado.

Duas exceções documentadas onde a própria doc do SGR não fornece exemplo de
resposta (só os parâmetros de busca):
  - GET /buscar_veiculo — nenhum "Exemplo Retorno" no /doc/.
  - GET /buscar_vinculo — o "Exemplo Retorno" da doc é, aparentemente, uma
    cópia colada do de /buscar_equipamento (não bate com os parâmetros do
    próprio endpoint, que citam placa_vinculo/cpf_vinculo/cod_rastreador_vinculo/
    cod_chip_vinculo). Ambos os casos viram FIELD_MAP com situação
    'nao_documentado' e o parsing usa múltiplos nomes candidatos — o
    resultado real da 1ª chamada contra a API viva é o que confirma a forma
    exata (ver report.py, seção "problemas_documentacao").
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import ClientStatus, TrackerStatus, VehicleStatus
from app.services.sgr_migration.normalize import (
    is_valid_cpf_cnpj,
    is_valid_email,
    is_valid_plate,
    normalize_cpf_cnpj,
    normalize_email,
    normalize_phone,
    normalize_plate,
    only_digits,
    parse_br_date,
)

_VALID_UF = {
    'AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS', 'MG',
    'PA', 'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC', 'SP', 'SE', 'TO',
}


def ci_get(payload, *keys: str):
    """Busca a 1ª chave presente em `payload`, case-insensitive. `payload` pode
    não ser um dict (ex.: lista/str) — nesse caso sempre retorna None."""
    if not isinstance(payload, dict):
        return None
    lowered = {str(k).lower(): v for k, v in payload.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, ''):
            return value
    return None


@dataclass
class FieldMapping:
    sgr_field: str
    mastersat_field: str
    situacao: str  # 'compativel' | 'transformacao' | 'nao_existe' | 'obrigatorio_ausente' | 'nao_documentado'
    nota: str = ''


SITUACAO_SYMBOL = {
    'compativel': '🟢',
    'transformacao': '🟡',
    'nao_existe': '🔴',
    'obrigatorio_ausente': '⚠️',
    'nao_documentado': '🔴',
}


# ---------------------------------------------------------------------------
# Tabelas de mapeamento (ETAPA 9/10)
# ---------------------------------------------------------------------------

CLIENT_FIELD_MAP: list[FieldMapping] = [
    FieldMapping('cod_cliente', 'client.external_id', 'nao_existe',
                 'MasterSat não tem coluna external_id hoje — necessária para a migração '
                 'definitiva ser idempotente (re-rodar sem duplicar clientes)'),
    FieldMapping('nome_cliente', 'client.name', 'compativel'),
    FieldMapping('nome_fantasia_cliente', 'client.trade_name', 'compativel'),
    FieldMapping('cpf_cliente', 'client.cpf_cnpj', 'transformacao',
                 'remover máscara (pontos/traço/barra); validar dígito verificador'),
    FieldMapping('cpf_cliente (nº de dígitos)', 'client.type', 'transformacao',
                 "11 dígitos → 'pf', 14 dígitos → 'pj'"),
    FieldMapping('situacao.descricao', 'client.status', 'transformacao',
                 'ATIVO/INATIVO/... (SGR) → ClientStatus (MasterSat) — ver _map_client_status'),
    FieldMapping('email[].email', 'client.email', 'transformacao',
                 'lista → usa o 1º e-mail válido; os demais viram client.extra_emails'),
    FieldMapping('telefone[].ddd + telefone[].telefone', 'client.phone', 'transformacao',
                 'concatena ddd+número do 1º telefone da lista'),
    FieldMapping('endereco.cep', 'client.zip_code', 'compativel'),
    FieldMapping('endereco.logradouro', 'client.address_line', 'compativel'),
    FieldMapping('endereco.numero', 'client.address_number', 'compativel'),
    FieldMapping('endereco.complemento', 'client.address_complement', 'compativel'),
    FieldMapping('endereco.bairro', 'client.neighborhood', 'compativel'),
    FieldMapping('endereco.cidade', 'client.city', 'compativel'),
    FieldMapping('endereco.uf', 'client.state', 'compativel'),
    FieldMapping('rg_cliente', 'client.rg_ie', 'compativel'),
    FieldMapping('data_nascimento_cliente', 'client.birth_date', 'transformacao', "'dd/mm/aaaa' → date ISO"),
    FieldMapping('reter_iss_cliente', 'client.iss_retido', 'transformacao', "'S'/'N' → 'sim'/'nao'"),
    FieldMapping('optante_simples_cliente', 'client.optante_simples', 'transformacao', "'S'/'N' → 'sim'/'nao'"),
    FieldMapping('nota_fiscal_cliente', 'client.issue_invoice', 'transformacao', "'S'/'N' → 'sim'/'nao'"),
    FieldMapping('taxa_emissao_boleto_cliente', 'client.boleto_fee', 'transformacao', "'S'/'N' → 'sim'/'nao'"),
    FieldMapping('formato_boleto_cliente', 'client.boleto_format', 'transformacao',
                 "código do SGR (ex.: 'U') → 'unico'/'individual' — confirmar tabela completa de códigos"),
    FieldMapping('numero_contrato_cliente', '-', 'nao_existe',
                 'referência ao contrato antigo — a migração definitiva precisa recriar o registro em contracts'),
    FieldMapping('cnh_cliente', '-', 'nao_existe', 'sem coluna equivalente no MasterSat'),
    FieldMapping('profissao_cliente', '-', 'nao_existe', 'sem coluna equivalente no MasterSat'),
    FieldMapping('sexo_cliente', '-', 'nao_existe', 'sem coluna equivalente no MasterSat'),
    FieldMapping('estado_civil_cliente', '-', 'nao_existe', 'sem coluna equivalente no MasterSat'),
    FieldMapping('inscricao_municipal_cliente', '-', 'nao_existe', 'sem coluna equivalente no MasterSat'),
    FieldMapping('segmento_cliente', '-', 'nao_existe', 'sem coluna equivalente no MasterSat'),
    FieldMapping('senha_cliente', '(não migrar)', 'nao_existe',
                 'credencial do portal do cliente legado — NUNCA migrar/expor'),
    FieldMapping('-', 'client.billing_day', 'obrigatorio_ausente',
                 'SGR não expõe o dia de vencimento no cadastro do cliente (ver /get_vencimento) '
                 '— precisa vir de outra fonte/regra de negócio na migração definitiva'),
]

VEHICLE_FIELD_MAP: list[FieldMapping] = [
    FieldMapping('cod_veiculo', 'vehicle.external_id', 'nao_existe', 'MasterSat não tem coluna external_id'),
    FieldMapping('cod_cliente', 'vehicle.client_id', 'transformacao',
                 'FK do SGR → resolver para o id interno do MasterSat pelo external_id do cliente'),
    FieldMapping('placa_veiculo', 'vehicle.plate', 'transformacao',
                 'remover hífen/espaços, caixa alta. ATENÇÃO: na base real há ativos SEM placa '
                 '(máquinas pesadas — CASE580H, VOLVO220, MAQ002) usando o campo como identificador '
                 'livre, além de erros de cadastro (nome de pessoa). O MasterSat exige placa válida '
                 'de 7 caracteres no schema da API — decidir como tratar esses ativos'),
    FieldMapping('chassi_veiculo', 'vehicle.chassis', 'compativel'),
    FieldMapping('renavam_veiculo', 'vehicle.renavam', 'compativel'),
    FieldMapping('marca_veiculo', 'vehicle.brand', 'compativel'),
    FieldMapping('modelo_veiculo', 'vehicle.model', 'compativel'),
    FieldMapping('cor_veiculo', 'vehicle.color', 'compativel'),
    FieldMapping('combustivel_veiculo', 'vehicle.fuel_type', 'compativel'),
    FieldMapping('tipo_veiculo_veiculo', 'vehicle.type', 'compativel'),
    FieldMapping('anofab_veiculo', 'vehicle.manufacture_year', 'transformacao', 'string → int'),
    FieldMapping('anomod_veiculo', 'vehicle.model_year', 'transformacao', 'string → int'),
    FieldMapping('fipe_codigo_veiculo', 'vehicle.fipe_code', 'compativel'),
    FieldMapping('fipe_valor_veiculo', 'vehicle.fipe_value', 'transformacao', "'1.234,56' → decimal"),
    FieldMapping('contrato_veiculo', 'vehicle.contract_number', 'compativel'),
    FieldMapping('data_contrato_veiculo', 'vehicle.contract_date', 'transformacao', 'dd/mm/aaaa → date'),
    FieldMapping('data_final_contrato_veiculo', 'vehicle.contract_end_date', 'transformacao', 'dd/mm/aaaa → date'),
    FieldMapping('cep_veiculo', 'vehicle.address_zip_code', 'transformacao', 'só dígitos'),
    FieldMapping('logradouro_veiculo', 'vehicle.address_line', 'compativel'),
    FieldMapping('numero_veiculo', 'vehicle.address_number', 'compativel'),
    FieldMapping('complemento_veiculo', 'vehicle.address_complement', 'compativel'),
    FieldMapping('bairro_veiculo', 'vehicle.neighborhood', 'compativel'),
    FieldMapping('cidade_veiculo', 'vehicle.city', 'compativel'),
    FieldMapping('uf_veiculo', 'vehicle.state', 'compativel'),
    FieldMapping('nome_ponto_venda', 'vehicle.sales_point', 'compativel'),
    FieldMapping('nome_consultor_veiculo', 'vehicle.seller_consultant', 'compativel'),
    FieldMapping('classificacao_veiculo', 'vehicle.vehicle_classification', 'compativel'),
    FieldMapping('alerta_veiculo', 'vehicle.user_alert', 'compativel'),
    FieldMapping('situacao_veiculo', 'vehicle.status', 'transformacao',
                 'campo PLANO (não é objeto). Valores vistos na base real: ATIVO, CANCELADO, '
                 'RETIRADA, CADASTRADO, TROCA DE VEICULO, INADIMPLENTE'),
    FieldMapping('situacao_veiculo = INADIMPLENTE', 'vehicle.status', 'nao_existe',
                 'no SGR a inadimplência é situação do VEÍCULO; no MasterSat só existe no cliente '
                 '(ClientStatus.DELINQUENT) — exige decisão de negócio na migração definitiva'),
    FieldMapping('km_veiculo', '-', 'nao_existe', 'MasterSat não guarda quilometragem do veículo'),
    FieldMapping('numero_motor_veiculo', '-', 'nao_existe', 'sem coluna equivalente'),
]

# O rastreador do MasterSat é montado com DUAS fontes: /buscar_vinculo (os
# relacionamentos e as datas) e /buscar_rastreador (IMEI, situação, chip). O
# vínculo sozinho NÃO tem o IMEI do equipamento — só o do chip.
TRACKER_FIELD_MAP: list[FieldMapping] = [
    FieldMapping('rastreador.imei_equipamento (/buscar_rastreador)', 'tracker.imei', 'compativel',
                 'o IMEI NÃO está em /buscar_vinculo (lá só existe o IMEI do chip) — exige cruzar '
                 'com /buscar_rastreador pela placa'),
    FieldMapping('vinculo.veiculo.placa_vinculo', 'tracker.vehicle_id', 'transformacao',
                 'placa → resolver para o id interno do veículo já migrado'),
    FieldMapping('vinculo.rastreador.cod_rastreador_vinculo', 'tracker.external_id', 'nao_existe',
                 'MasterSat não tem coluna external_id'),
    FieldMapping('vinculo.rastreador.equipamento.numero_equipamento_vinculo', 'tracker.serial_number', 'compativel'),
    FieldMapping('rastreador.tipo_rastreador', 'tracker.model', 'compativel'),
    FieldMapping('rastreador.situacao', 'tracker.status', 'transformacao',
                 'campo PLANO. Valores vistos: ATIVO; disponibilidade (VINCULADO) é campo separado'),
    FieldMapping('rastreador.ddd + rastreador.telefone', 'tracker.sim_number', 'transformacao',
                 'concatena ddd+telefone do chip vinculado'),
    FieldMapping('vinculo.rastreador.chip_equipamento.imei_chip_vinculo', 'tracker.sim_iccid', 'transformacao',
                 'o SGR expõe o IMEI do chip; o ICCID só vem em /buscar_chip (campo iccid, '
                 'frequentemente nulo na base real) — avaliar qual guardar'),
    FieldMapping('vinculo.data_instalacao', 'tracker.install_date', 'transformacao', 'dd/mm/aaaa → date'),
    FieldMapping('vinculo.valor_instalacao', 'tracker.installation_fee', 'transformacao', "'150,00' → decimal"),
    FieldMapping('-', 'tracker.brand', 'obrigatorio_ausente',
                 '/buscar_rastreador não traz marca/fabricante; só /buscar_equipamento '
                 '(modelo.marca.descricao) tem — exigiria 1 chamada extra por equipamento'),
    FieldMapping('vinculo.cod_interveniente_vinculo', 'contract.interveniente_client_id', 'transformacao',
                 'o SGR já modela interveniente financeiro, igual ao MasterSat'),
]


# ---------------------------------------------------------------------------
# Situação (SGR) → enum MasterSat
# ---------------------------------------------------------------------------

_CLIENT_STATUS_MAP = {
    'ATIVO': ClientStatus.ACTIVE,
    'CADASTRADO': ClientStatus.ACTIVE,
    'INATIVO': ClientStatus.INACTIVE,
    'INADIMPLENTE': ClientStatus.DELINQUENT,
    'SUSPENSO': ClientStatus.SUSPENDED,
    'BLOQUEADO': ClientStatus.SUSPENDED,
    'CANCELADO': ClientStatus.INACTIVE,
}

# Valores confirmados na base real (campo plano `situacao_veiculo`); a doc do
# SGR não lista o domínio e /get_situacao_veiculo exige permissão que o nosso
# usuário de integração não tem — então esta tabela cresce por observação.
_VEHICLE_STATUS_MAP = {
    'ATIVO': VehicleStatus.ACTIVE,
    'INATIVO': VehicleStatus.REMOVED,
    # CANCELADO é situação de cadastro/contrato no SGR — distinto de RETIRADA
    # (desinstalação física do equipamento). O MasterSat agora tem os dois
    # (VehicleStatus.CANCELED vs .REMOVED); antes ambos caíam em REMOVED.
    'CANCELADO': VehicleStatus.CANCELED,
    'RETIRADA': VehicleStatus.REMOVED,
    'RETIRADO': VehicleStatus.REMOVED,
    'DESINSTALADO': VehicleStatus.REMOVED,
    'TROCA DE VEICULO': VehicleStatus.REMOVED,
    'CADASTRADO': VehicleStatus.PENDING_VALIDATION,
    'BLOQUEADO': VehicleStatus.BLOCKED,
    'SEM RASTREADOR': VehicleStatus.NO_TRACKER,
    # 'INADIMPLENTE' fica DE FORA de propósito: no SGR a inadimplência é
    # situação do VEÍCULO; no MasterSat ela existe só no cliente
    # (ClientStatus.DELINQUENT). Decidir na migração definitiva se vira
    # bloqueio do veículo ou inadimplência do cliente — enquanto não se
    # decide, o relatório continua sinalizando o caso.
}

_TRACKER_STATUS_MAP = {
    'ATIVO': TrackerStatus.INSTALLED,
    'VINCULADO': TrackerStatus.INSTALLED,
    'ESTOQUE': TrackerStatus.STOCK,
    'EM ESTOQUE': TrackerStatus.STOCK,
    'MANUTENCAO': TrackerStatus.MAINTENANCE,
    'EXTRAVIADO': TrackerStatus.LOST,
    'DESCARTADO': TrackerStatus.DISCARDED,
}


def _map_status(descricao, table: dict, default, label: str) -> tuple[object, list[str]]:
    issues: list[str] = []
    key = str(descricao or '').strip().upper()
    status = table.get(key)
    if status is None:
        issues.append(f"Situação de {label} sem mapeamento conhecido: '{descricao}' (usando default: {default.value})")
        status = default
    return status, issues


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------

def map_cliente(raw: dict) -> tuple[dict, list[str]]:
    issues: list[str] = []

    nome = ci_get(raw, 'nome_cliente')
    if not nome:
        issues.append('Cliente sem nome (nome_cliente ausente)')

    cpf_raw = ci_get(raw, 'cpf_cliente')
    digits, tipo = normalize_cpf_cnpj(cpf_raw)
    if not digits:
        issues.append('Cliente sem CPF/CNPJ (cpf_cliente ausente)')
    elif tipo is None:
        issues.append(f"CPF/CNPJ com quantidade de dígitos inválida: {len(digits)} dígitos")
    elif not is_valid_cpf_cnpj(digits, tipo):
        issues.append(f'{"CPF" if tipo == "pf" else "CNPJ"} com dígito verificador inválido')

    situacao = ci_get(raw, 'situacao')
    status, status_issues = _map_status(ci_get(situacao, 'descricao'), _CLIENT_STATUS_MAP, ClientStatus.ACTIVE, 'cliente')
    issues.extend(status_issues)

    endereco = ci_get(raw, 'endereco') or {}

    email_list: list[str] = []
    emails_raw = ci_get(raw, 'email') or []
    if isinstance(emails_raw, list):
        for item in emails_raw:
            addr = normalize_email(ci_get(item, 'email') if isinstance(item, dict) else item)
            if not addr:
                continue
            if not is_valid_email(addr):
                issues.append(f'E-mail com formato inválido ignorado: {addr[:2]}***')
                continue
            if addr not in email_list:
                email_list.append(addr)
    if not email_list:
        issues.append('Cliente sem e-mail cadastrado')

    # Estrutura real de cada telefone: {descricao, contato, operadora, alerta,
    # situacao} — o número fica em 'contato' (a doc não mostra este bloco).
    phone = None
    telefones_raw = ci_get(raw, 'telefone') or []
    if isinstance(telefones_raw, list):
        for item in telefones_raw:
            numero = ci_get(item, 'contato') if isinstance(item, dict) else item
            phone = normalize_phone(str(numero or '')) or None
            if phone:
                break
    if not phone:
        issues.append('Cliente sem telefone cadastrado')

    # contatos[] = pessoas de contato: {descricao, contato, operadora, nome}
    contacts = []
    for item in (ci_get(raw, 'contatos') or []):
        if not isinstance(item, dict):
            continue
        contacts.append({
            'name': ci_get(item, 'nome'),
            'phone': normalize_phone(str(ci_get(item, 'contato') or '')) or None,
            'role': ci_get(item, 'descricao'),
        })

    birth_raw = ci_get(raw, 'data_nascimento_cliente')
    birth = parse_br_date(birth_raw)
    if birth_raw and birth is None:
        issues.append(f"Data de nascimento em formato não reconhecido: '{birth_raw}'")

    uf = ci_get(endereco, 'uf')
    if uf and uf.strip().upper() not in _VALID_UF:
        issues.append(f"Estado (UF) desconhecido: '{uf}'")

    mapped = {
        'external_id': ci_get(raw, 'cod_cliente'),
        'name': nome,
        'trade_name': ci_get(raw, 'nome_fantasia_cliente'),
        'cpf_cnpj': digits or None,
        'type': tipo or 'pf',
        'status': status.value,
        'email': email_list[0] if email_list else None,
        'extra_emails': email_list[1:] or None,
        'phone': phone,
        'contacts': contacts or None,
        'zip_code': only_digits(ci_get(endereco, 'cep')) or None,
        'address_line': ci_get(endereco, 'logradouro'),
        'address_number': ci_get(endereco, 'numero'),
        'address_complement': ci_get(endereco, 'complemento'),
        'neighborhood': ci_get(endereco, 'bairro'),
        'city': ci_get(endereco, 'cidade'),
        'state': ci_get(endereco, 'uf'),
        'rg_ie': ci_get(raw, 'rg_cliente'),
        'birth_date': birth.isoformat() if birth else None,
    }
    return mapped, issues


# ---------------------------------------------------------------------------
# Veículo — resposta do /buscar_veiculo não documentada (ver VEHICLE_FIELD_MAP)
# ---------------------------------------------------------------------------

def _to_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_decimal_br(value) -> float | None:
    """'1.234,56' / '0,00' (formato do SGR) -> float."""
    if value in (None, ''):
        return None
    txt = str(value).strip().replace('.', '').replace(',', '.')
    try:
        return float(txt)
    except ValueError:
        return None


def map_veiculo(raw: dict) -> tuple[dict, list[str]]:
    issues: list[str] = []

    plate_raw = ci_get(raw, 'placa_veiculo')
    plate = normalize_plate(plate_raw) if plate_raw else ''
    if not plate:
        issues.append('Veículo sem placa na resposta')
    elif not is_valid_plate(plate):
        # Na base real isto não é só erro de digitação: máquinas pesadas
        # (retroescavadeira, escavadeira) são rastreadas sem placa e o SGR usa
        # o campo como identificador livre. O MasterSat exige placa válida de
        # 7 caracteres no schema da API — ver relatório de compatibilidade.
        issues.append(f"Placa fora do padrão brasileiro: '{plate}' (máquina sem placa ou erro de cadastro)")

    status, status_issues = _map_status(
        ci_get(raw, 'situacao_veiculo'), _VEHICLE_STATUS_MAP, VehicleStatus.ACTIVE, 'veículo',
    )
    issues.extend(status_issues)

    contract_date = parse_br_date(ci_get(raw, 'data_contrato_veiculo'))
    contract_end = parse_br_date(ci_get(raw, 'data_final_contrato_veiculo'))

    mapped = {
        'external_id': ci_get(raw, 'cod_veiculo'),
        'client_external_id': ci_get(raw, 'cod_cliente'),
        'plate': plate or None,
        'chassis': ci_get(raw, 'chassi_veiculo'),
        'renavam': ci_get(raw, 'renavam_veiculo'),
        'brand': ci_get(raw, 'marca_veiculo'),
        'model': ci_get(raw, 'modelo_veiculo'),
        'color': ci_get(raw, 'cor_veiculo'),
        'fuel_type': ci_get(raw, 'combustivel_veiculo'),
        'type': ci_get(raw, 'tipo_veiculo_veiculo'),
        'manufacture_year': _to_int(ci_get(raw, 'anofab_veiculo')),
        'model_year': _to_int(ci_get(raw, 'anomod_veiculo')),
        'fipe_code': ci_get(raw, 'fipe_codigo_veiculo'),
        'fipe_value': _to_decimal_br(ci_get(raw, 'fipe_valor_veiculo')),
        'contract_number': ci_get(raw, 'contrato_veiculo'),
        'contract_date': contract_date.isoformat() if contract_date else None,
        'contract_end_date': contract_end.isoformat() if contract_end else None,
        'address_zip_code': only_digits(ci_get(raw, 'cep_veiculo')) or None,
        'address_line': ci_get(raw, 'logradouro_veiculo'),
        'address_number': ci_get(raw, 'numero_veiculo'),
        'address_complement': ci_get(raw, 'complemento_veiculo'),
        'neighborhood': ci_get(raw, 'bairro_veiculo'),
        'city': ci_get(raw, 'cidade_veiculo'),
        'state': ci_get(raw, 'uf_veiculo'),
        'sales_point': ci_get(raw, 'nome_ponto_venda'),
        'seller_consultant': ci_get(raw, 'nome_consultor_veiculo'),
        'vehicle_classification': ci_get(raw, 'classificacao_veiculo'),
        'user_alert': ci_get(raw, 'alerta_veiculo'),
        'status': status.value,
    }
    return mapped, issues


# ---------------------------------------------------------------------------
# Rastreador/equipamento — a partir do registro de vínculo (ver TRACKER_FIELD_MAP)
# ---------------------------------------------------------------------------

def map_tracker(vinculo: dict, rastreador: dict | None = None) -> tuple[dict, list[str]]:
    """Monta o rastreador do MasterSat a partir do registro de vínculo.

    O vínculo traz os relacionamentos (veículo/rastreador/equipamento/chip) e
    as datas de instalação, mas NÃO o IMEI do equipamento — só o do chip. O
    IMEI real vem de /buscar_rastreador (`rastreador`, opcional aqui), que é
    o único endpoint com IMEI, situação e placa no mesmo registro.
    """
    issues: list[str] = []
    rastreador = rastreador or {}

    rast_vinc = ci_get(vinculo, 'rastreador') or {}
    equip_vinc = ci_get(rast_vinc, 'equipamento') or {}
    chip_vinc = ci_get(rast_vinc, 'chip_equipamento') or {}
    veic_vinc = ci_get(vinculo, 'veiculo') or {}

    imei = ci_get(rastreador, 'imei_equipamento')
    if not imei:
        issues.append('Rastreador sem IMEI (não encontrado em /buscar_rastreador para esta placa)')

    status, status_issues = _map_status(
        ci_get(rastreador, 'situacao'), _TRACKER_STATUS_MAP, TrackerStatus.INSTALLED, 'rastreador',
    )
    if rastreador:
        issues.extend(status_issues)

    sim_number = normalize_phone(
        f"{ci_get(rastreador, 'ddd') or ''}{ci_get(rastreador, 'telefone') or ''}"
    ) or None

    install_date = parse_br_date(ci_get(vinculo, 'data_instalacao'))

    mapped = {
        'external_id': ci_get(rast_vinc, 'cod_rastreador_vinculo') or ci_get(vinculo, 'cod_rastreador_vinculo'),
        'vinculo_external_id': ci_get(vinculo, 'cod_vinculo'),
        'equipment_external_id': ci_get(rast_vinc, 'cod_equipamento_vinculo'),
        'chip_external_id': ci_get(rast_vinc, 'cod_chip_vinculo'),
        'vehicle_plate': normalize_plate(ci_get(veic_vinc, 'placa_vinculo') or '') or None,
        'imei': imei,
        'serial_number': ci_get(equip_vinc, 'numero_equipamento_vinculo') or ci_get(rastreador, 'numero_equipamento'),
        'model': ci_get(rastreador, 'tipo_rastreador'),
        'sim_number': sim_number,
        'sim_imei': ci_get(chip_vinc, 'imei_chip_vinculo') or ci_get(rastreador, 'imei_chip'),
        'install_date': install_date.isoformat() if install_date else None,
        'installation_fee': _to_decimal_br(ci_get(vinculo, 'valor_instalacao')),
        'status': status.value,
    }
    return mapped, issues
