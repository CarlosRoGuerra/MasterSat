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
    FieldMapping('(resposta de /buscar_veiculo não documentada)', '-', 'nao_documentado',
                 'a doc oficial do SGR não traz "Exemplo Retorno" para /buscar_veiculo — só os '
                 'parâmetros de busca (cod_veiculo, cod_cliente, placa_veiculo). Os campos abaixo '
                 'são inferidos por convenção com /buscar_cliente e precisam ser confirmados contra '
                 'a resposta real na 1ª execução da POC'),
    FieldMapping('cod_veiculo', 'vehicle.external_id', 'nao_existe', 'MasterSat não tem coluna external_id'),
    FieldMapping('placa_veiculo', 'vehicle.plate', 'transformacao', 'remover hífen/espaços, caixa alta'),
    FieldMapping('chassi_veiculo', 'vehicle.chassis', 'transformacao', 'remover espaços, caixa alta'),
    FieldMapping('cod_cliente', 'vehicle.client_id', 'transformacao',
                 'FK do SGR (cod_cliente) → resolver para o id interno do MasterSat pelo external_id do cliente'),
    FieldMapping('marca_veiculo', 'vehicle.brand', 'compativel'),
    FieldMapping('modelo_veiculo', 'vehicle.model', 'compativel'),
    FieldMapping('renavam_veiculo', 'vehicle.renavam', 'compativel'),
    FieldMapping('cor_veiculo', 'vehicle.color', 'compativel'),
    FieldMapping('situacao.descricao', 'vehicle.status', 'transformacao', 'situação SGR → VehicleStatus'),
]

TRACKER_FIELD_MAP: list[FieldMapping] = [
    FieldMapping('(resposta de /buscar_vinculo inconsistente com a doc)', '-', 'nao_documentado',
                 'o "Exemplo Retorno" documentado para /buscar_vinculo tem o mesmo formato do de '
                 '/buscar_equipamento e não menciona placa/cliente/rastreador/chip, que são justamente '
                 'os parâmetros de busca do próprio endpoint — parece um erro de cópia na doc do '
                 'fornecedor. O parsing usa nomes candidatos e é tolerante a variações; a forma real '
                 'só é confirmada rodando a POC contra a API viva'),
    FieldMapping('Imei_equipamento / imei_chip_vinculo', 'tracker.imei', 'transformacao',
                 'usa o 1º valor não vazio entre os candidatos — confirmar qual é o IMEI do rastreador '
                 'em si (por oposição ao IMEI do chip/SIM)'),
    FieldMapping('Modelo.Marca.Descricao', 'tracker.brand', 'compativel'),
    FieldMapping('Modelo.Descricao', 'tracker.model', 'compativel'),
    FieldMapping('Situacao.Descricao', 'tracker.status', 'transformacao', 'situação SGR → TrackerStatus'),
    FieldMapping('Ddd + Telefone (chip)', 'tracker.sim_number', 'transformacao', 'concatena ddd+telefone do chip'),
    FieldMapping('Iccid', 'tracker.sim_iccid', 'compativel'),
    FieldMapping('Operadora.Descricao', 'tracker.carrier', 'compativel'),
    FieldMapping('cod_rastreador_vinculo', 'tracker.external_id', 'nao_existe', 'MasterSat não tem coluna external_id'),
    FieldMapping('placa_vinculo / Placa', 'tracker.vehicle_id', 'transformacao',
                 'placa → resolver para o id interno do veículo já migrado'),
]


# ---------------------------------------------------------------------------
# Situação (SGR) → enum MasterSat
# ---------------------------------------------------------------------------

_CLIENT_STATUS_MAP = {
    'ATIVO': ClientStatus.ACTIVE,
    'INATIVO': ClientStatus.INACTIVE,
    'INADIMPLENTE': ClientStatus.DELINQUENT,
    'SUSPENSO': ClientStatus.SUSPENDED,
    'BLOQUEADO': ClientStatus.SUSPENDED,
    'CANCELADO': ClientStatus.INACTIVE,
}

_VEHICLE_STATUS_MAP = {
    'ATIVO': VehicleStatus.ACTIVE,
    'INATIVO': VehicleStatus.REMOVED,
    'RETIRADO': VehicleStatus.REMOVED,
    'BLOQUEADO': VehicleStatus.BLOCKED,
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

    phone = None
    telefones_raw = ci_get(raw, 'telefone') or []
    if isinstance(telefones_raw, list) and telefones_raw:
        first = telefones_raw[0]
        if isinstance(first, dict):
            raw_phone = f"{ci_get(first, 'ddd') or ''}{ci_get(first, 'telefone', 'numero') or ''}"
        else:
            raw_phone = str(first)
        phone = normalize_phone(raw_phone) or None
    if not phone:
        issues.append('Cliente sem telefone cadastrado')

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

def map_veiculo(raw: dict) -> tuple[dict, list[str]]:
    issues: list[str] = []

    plate_raw = ci_get(raw, 'placa_veiculo', 'placa')
    plate = normalize_plate(plate_raw) if plate_raw else ''
    if not plate:
        issues.append('Veículo sem placa identificável na resposta (campo não encontrado — ver nota de documentação)')

    situacao = ci_get(raw, 'situacao')
    status, status_issues = _map_status(ci_get(situacao, 'descricao'), _VEHICLE_STATUS_MAP, VehicleStatus.ACTIVE, 'veículo')
    issues.extend(status_issues)

    mapped = {
        'external_id': ci_get(raw, 'cod_veiculo'),
        'client_external_id': ci_get(raw, 'cod_cliente'),
        'plate': plate or None,
        'chassis': ci_get(raw, 'chassi_veiculo', 'chassi'),
        'brand': ci_get(raw, 'marca_veiculo', 'marca'),
        'model': ci_get(raw, 'modelo_veiculo', 'modelo'),
        'renavam': ci_get(raw, 'renavam_veiculo', 'renavam'),
        'color': ci_get(raw, 'cor_veiculo', 'cor'),
        'status': status.value,
    }
    return mapped, issues


# ---------------------------------------------------------------------------
# Rastreador/equipamento — a partir do registro de vínculo (ver TRACKER_FIELD_MAP)
# ---------------------------------------------------------------------------

def map_tracker(raw: dict) -> tuple[dict, list[str]]:
    issues: list[str] = []

    imei = ci_get(raw, 'imei_equipamento', 'imei_chip_vinculo', 'imei_chip')
    if not imei:
        issues.append('Equipamento sem IMEI identificável na resposta (ver nota de documentação)')

    modelo = ci_get(raw, 'modelo') or {}
    marca = ci_get(modelo, 'marca') or {}

    situacao = ci_get(raw, 'situacao')
    status, status_issues = _map_status(ci_get(situacao, 'descricao'), _TRACKER_STATUS_MAP, TrackerStatus.INSTALLED, 'rastreador')
    issues.extend(status_issues)

    sim_ddd = ci_get(raw, 'ddd')
    sim_numero = ci_get(raw, 'telefone')
    sim_number = normalize_phone(f'{sim_ddd or ""}{sim_numero or ""}') or None

    mapped = {
        'external_id': ci_get(raw, 'cod_equipamento', 'cod_rastreador_vinculo', 'cod_rastreador'),
        'vehicle_plate': normalize_plate(ci_get(raw, 'placa', 'placa_vinculo') or '') or None,
        'imei': imei,
        'brand': ci_get(marca, 'descricao') or ci_get(raw, 'marca'),
        'model': ci_get(modelo, 'descricao') or ci_get(raw, 'modelo'),
        'sim_number': sim_number,
        'sim_iccid': ci_get(raw, 'iccid'),
        'carrier': ci_get(ci_get(raw, 'operadora') or {}, 'descricao'),
        'status': status.value,
    }
    return mapped, issues
