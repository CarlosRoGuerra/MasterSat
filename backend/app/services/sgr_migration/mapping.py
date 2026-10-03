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

from pydantic import ValidationError

from app.models.enums import BillingStatus, ClientStatus, TrackerStatus, VehicleStatus
from app.schemas.vehicle import VehicleBase
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
    parse_centavos,
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
        # ContactItem.name é obrigatório; no SGR 'nome' costuma vir null quando
        # o contato é só um telefone — e um único contato sem nome derruba a
        # listagem inteira de clientes (500 na serialização de ClientOut).
        nome_contato = str(ci_get(item, 'nome') or '').strip()
        descricao = str(ci_get(item, 'descricao') or '').strip()
        contact_phone = normalize_phone(str(ci_get(item, 'contato') or '')) or None
        if not nome_contato and not contact_phone:
            continue
        contacts.append({
            'name': nome_contato or descricao or 'Contato',
            'phone': contact_phone,
            'role': descricao or None,
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
        'issue_invoice': {
            'S': 'sim', 'SIM': 'sim', 'N': 'nao', 'NAO': 'nao', 'NÃO': 'nao',
        }.get(str(ci_get(raw, 'nota_fiscal_cliente') or '').strip().upper()),
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
    _descartar_campos_fora_do_schema(mapped, issues)
    return mapped, issues


# Campos opcionais que o VehicleOut valida na SAÍDA: o importador grava direto
# no model (sem passar pelo schema), então um único valor fora da regra
# (ex.: ano 2, chassi curto) faz GET /vehicles responder 500 para todo mundo.
_VEHICLE_CAMPOS_SANEAVEIS = (
    'chassis', 'renavam', 'address_zip_code', 'state',
    'manufacture_year', 'model_year', 'fipe_value',
)


def _descartar_campos_fora_do_schema(mapped: dict, issues: list[str]) -> None:
    dados = {'client_id': 0, **mapped}
    for _ in range(len(_VEHICLE_CAMPOS_SANEAVEIS) + 1):
        try:
            VehicleBase.model_validate(dados)
            return
        except ValidationError as exc:
            ruins = set()
            for err in exc.errors():
                campo = err['loc'][0] if err['loc'] else None
                if campo in _VEHICLE_CAMPOS_SANEAVEIS and dados.get(campo) is not None:
                    ruins.add(campo)
                elif campo is None and dados.get('contract_end_date'):
                    ruins.add('contract_end_date')  # fim do contrato antes do início
            if not ruins:
                return  # sobra só placa inválida — tratada à parte
            for campo in sorted(ruins):
                issues.append(f"{campo} inválido descartado: {dados[campo]!r}")
                mapped[campo] = None
                dados[campo] = None


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


# ---------------------------------------------------------------------------
# Plano e contrato — a partir de /get_grupo_mensalidade e do VÍNCULO
#
# Descoberto testando a API real (21/09): o contrato do SGR não está no
# cliente nem no veículo (esses campos vêm vazios) e o módulo de vendas nunca
# foi usado. Quem guarda a cobrança é o vínculo, e o valor mensal vem de
# /get_grupo_mensalidade pelo `cod_grupo_vinculo`.
# ---------------------------------------------------------------------------

PLAN_FIELD_MAP: list[FieldMapping] = [
    FieldMapping('grupo_mensalidade.cod_grupo_mensalidade', 'plan.external_id', 'nao_existe',
                 'MasterSat não tem coluna external_id — a chave natural usada é o nome do plano'),
    FieldMapping('grupo_mensalidade.descricao', 'plan.name', 'compativel',
                 "ex.: 'MENSALIDADE 64,99' (o valor está embutido no nome, no padrão deles)"),
    FieldMapping('grupo_mensalidade.valor', 'plan.price', 'transformacao', "'64,99' → decimal"),
    FieldMapping('-', 'plan.billing_interval_months', 'obrigatorio_ausente',
                 'o grupo de mensalidade não declara periodicidade; assumido mensal (1) — '
                 'o SGR tem /get_periodo, mas ele não é referenciado pelo vínculo'),
]

CONTRACT_FIELD_MAP: list[FieldMapping] = [
    FieldMapping('vinculo.cod_vinculo', 'contract.external_id', 'nao_existe',
                 'sem external_id no MasterSat — dedup por (cliente, veículo)'),
    FieldMapping('vinculo.veiculo.placa_vinculo', 'contract.vehicle_id', 'transformacao',
                 'placa → id interno do veículo já migrado'),
    FieldMapping('veiculo.cod_cliente', 'contract.client_id', 'transformacao',
                 'o vínculo não traz o cliente: vem do veículo ao qual a placa pertence'),
    FieldMapping('vinculo.cod_grupo_vinculo', 'contract.plan_id', 'transformacao',
                 'código do grupo → Plano criado a partir de /get_grupo_mensalidade'),
    FieldMapping('vinculo.cod_vencimento_vinculo', 'contract.billing_day', 'transformacao',
                 "código → dia do mês via /get_vencimento (ex.: 127 → 15). 'último dia mês' → 31"),
    FieldMapping('vinculo.data_instalacao', 'contract.start_date', 'transformacao',
                 'dd/mm/aaaa → date; sem ela cai para data_vinculo'),
    FieldMapping('vinculo.valor_instalacao', 'contract.installation_fee', 'transformacao', "'150,00' → decimal"),
    FieldMapping('vinculo.gerar_cobranca', 'contract.status', 'transformacao',
                 "'S' → 'ativo', 'N' → 'inativo'"),
    FieldMapping('vinculo.cod_interveniente_vinculo + interveniente.cpf', 'contract.interveniente_client_id',
                 'transformacao', 'CPF do interveniente → id do cliente correspondente, se já migrado'),
    FieldMapping('-', 'contract.billing_modality', 'transformacao',
                 "sempre 'boleto': formato_boleto_cliente = 'U' (boleto único) em toda a base lida"),
    FieldMapping('vinculo.acrescimo / desconto', '-', 'nao_existe',
                 'MasterSat não tem acréscimo/desconto por contrato — sinalizado, não migrado'),
    FieldMapping('vinculo.produtos[]', '-', 'nao_existe',
                 'cobranças avulsas/negociações do SGR; viram histórico de boleto, não contrato'),
]

# 'último dia mês' não é um número — o MasterSat guarda billing_day como int.
_ULTIMO_DIA = 31


def map_plano(grupo: dict) -> tuple[dict, list[str]]:
    """/get_grupo_mensalidade ou /get_grupo_adesao → Plan do MasterSat.

    As duas tabelas têm o mesmo formato e mudam só o nome da chave
    (cod_grupo_mensalidade / cod_grupo_adesao) — ver _fetch_planos() em
    poc.py sobre por que as duas precisam ser lidas.
    """
    issues: list[str] = []
    descricao = ci_get(grupo, 'descricao')
    preco = _to_decimal_br(ci_get(grupo, 'valor'))
    if not descricao:
        issues.append('Grupo sem descrição')
    if preco is None:
        issues.append(f"Grupo '{descricao}' sem valor utilizável")
    return {
        'external_id': ci_get(grupo, 'cod_grupo_mensalidade', 'cod_grupo_adesao', 'cod_grupo'),
        'name': descricao,
        'price': preco,
        'billing_interval_months': 1,
    }, issues


def build_vencimento_index(registros: list[dict]) -> dict[str, int]:
    """/get_vencimento → {cod_vencimento: dia_do_mes}."""
    index: dict[str, int] = {}
    for reg in registros or []:
        codigo = ci_get(reg, 'cod_vencimento')
        descricao = str(ci_get(reg, 'descricao') or '').strip()
        if not codigo:
            continue
        if descricao.isdigit():
            index[str(codigo)] = int(descricao)
        elif 'último' in descricao.lower() or 'ultimo' in descricao.lower():
            index[str(codigo)] = _ULTIMO_DIA
    return index


def map_contrato(vinculo: dict, vencimento_index: dict[str, int]) -> tuple[dict, list[str]]:
    """Vínculo do SGR → Contract do MasterSat."""
    issues: list[str] = []
    veic_vinc = ci_get(vinculo, 'veiculo') or {}
    interveniente = ci_get(vinculo, 'interveniente') or {}

    grupo = ci_get(vinculo, 'cod_grupo_vinculo')
    if not grupo:
        issues.append('Vínculo sem cod_grupo_vinculo — não dá para saber o plano')

    cod_venc = ci_get(vinculo, 'cod_vencimento_vinculo')
    billing_day = vencimento_index.get(str(cod_venc)) if cod_venc else None
    if cod_venc and billing_day is None:
        issues.append(f"Código de vencimento '{cod_venc}' não está em /get_vencimento")

    # data_instalacao é a que reflete o início da prestação do serviço;
    # data_vinculo costuma ser a data do registro (às vezes o dia de hoje).
    start = parse_br_date(ci_get(vinculo, 'data_instalacao')) or parse_br_date(ci_get(vinculo, 'data_vinculo'))
    if not start:
        issues.append('Vínculo sem data de instalação nem data de vínculo — contrato ficaria sem início')

    gerar = str(ci_get(vinculo, 'gerar_cobranca') or '').strip().upper()
    if gerar not in ('S', 'N', ''):
        issues.append(f"gerar_cobranca com valor inesperado: '{gerar}'")

    if ci_get(vinculo, 'acrescimo') or ci_get(vinculo, 'desconto'):
        issues.append('Vínculo tem acréscimo/desconto que o MasterSat não modela por contrato')

    return {
        'external_id': ci_get(vinculo, 'cod_vinculo'),
        'vehicle_plate': normalize_plate(ci_get(veic_vinc, 'placa_vinculo') or '') or None,
        'plan_external_id': str(grupo) if grupo else None,
        'billing_day': billing_day,
        'start_date': start.isoformat() if start else None,
        'installation_fee': _to_decimal_br(ci_get(vinculo, 'valor_instalacao')),
        'status': 'inativo' if gerar == 'N' else 'ativo',
        'interveniente_cpf': only_digits(ci_get(interveniente, 'cpf')) or None,
        'billing_modality': 'boleto',
    }, issues


# ---------------------------------------------------------------------------
# Histórico de cobrança — /buscar_boletos_cliente → Billing
#
# No SGR o boleto é CONSOLIDADO por cliente (formato_boleto_cliente = 'U'):
# um documento cobre vários veículos, e `discriminacao[]` detalha o valor de
# cada placa. No MasterSat a divisão é a mesma, só que ao contrário: `billings`
# guarda a cobrança item a item e o boleto bancário (ailos_boletos) é emitido
# depois, 1 para 1 com a cobrança. Por isso cada LINHA da discriminação vira
# um Billing, e não o boleto inteiro — é o que preserva o valor por veículo e
# mantém a soma igual à do documento original.
# ---------------------------------------------------------------------------

BILLING_FIELD_MAP: list[FieldMapping] = [
    FieldMapping('boleto.cod_boleto', 'billing.external_id', 'nao_existe',
                 'sem external_id no MasterSat — dedup por (cliente, nosso_numero, veículo, competência)'),
    FieldMapping('discriminacao[].valor', 'billing.amount', 'transformacao', "'49,99' → decimal"),
    FieldMapping('discriminacao[].placa', 'billing.vehicle_id', 'transformacao',
                 'placa → id do veículo migrado; sem discriminação o Billing fica sem veículo'),
    FieldMapping('discriminacao[].mes_referente', 'billing.period_label', 'compativel', "ex.: '08/2026'"),
    FieldMapping('boleto.data_vencimento', 'billing.due_date', 'transformacao', 'dd/mm/aaaa → date'),
    FieldMapping('boleto.data_pagamento', 'billing.payment_date', 'transformacao', 'dd/mm/aaaa → date'),
    FieldMapping('boleto.forma_pagamento', 'billing.payment_method', 'compativel'),
    FieldMapping('boleto.nosso_numero', 'billing.receipt_number', 'compativel'),
    FieldMapping('boleto.parcela', 'billing.installment_number/_total', 'transformacao', "'1 de 10' → (1, 10)"),
    FieldMapping('boleto.situacao.descricao', 'billing.status', 'transformacao',
                 'as 8 situações do SGR → os 4 status do MasterSat (ver _BILLING_STATUS_MAP)'),
    FieldMapping('boleto.valor_pagamento', 'billing.paid_amount', 'transformacao',
                 'é do documento inteiro; numa cobrança consolidada não dá para atribuir por '
                 'placa, então só é preenchido quando o boleto tem uma linha só'),
    FieldMapping('boleto.linha_digitavel / cod_barras', '-', 'nao_existe',
                 'no MasterSat isso vive em ailos_boletos, que é da emissão via Ailos — '
                 'boleto histórico do SGR não tem contrapartida lá'),
    FieldMapping('boleto.numero_nf', '-', 'nao_existe', 'MasterSat não guarda NF na cobrança'),
]

# O SGR tem 8 situações observadas na base real; o MasterSat tem 4.
#
# ATENÇÃO: estas situações dizem o que ACONTECEU com o documento, não se ele
# continua devido. 'APROVADO' é só "registrado no banco", e a base real tem
# boletos de 2019 e 2023 parados nesse status. Quem decide o que ainda é
# dívida é /buscar_boletos_abertos_cliente (parâmetro `em_aberto` de
# map_boleto); PENDING aqui é apenas um candidato, que vira CANCELED se o
# SGR não listar o boleto como aberto.
_BILLING_STATUS_MAP = {
    'BAIXADO': BillingStatus.PAID,
    'BAIXADO COM PENDÊNCIA': BillingStatus.PAID,
    'BAIXADO COM PENDENCIA': BillingStatus.PAID,
    'PAGO': BillingStatus.PAID,
    'ABERTO': BillingStatus.PENDING,
    # APROVADO é só "registrado no banco", NÃO dívida: aparece 33-34 vezes por
    # ano em 2019 e 2023, enquanto ABERTO aparece 2 vezes nos mesmos anos.
    # Confirmado cruzando 6 boletos ABERTO de 09/2026 com
    # /buscar_boletos_abertos_cliente: 6 de 6 conferem, nenhum APROVADO entra.
    'APROVADO': BillingStatus.CANCELED,
    'CANCELADO': BillingStatus.CANCELED,
    'REMOVIDO': BillingStatus.CANCELED,
    'NEGADO': BillingStatus.CANCELED,
    # NEGOCIADO = a dívida virou outro boleto (parcelamento). Cancelar aqui
    # evita contar a mesma dívida duas vezes; o boleto novo entra sozinho.
    'NEGOCIADO': BillingStatus.CANCELED,
}


# `produto` só vem na discriminação do endpoint GERAL (/buscar_boletos) — o
# /buscar_boletos_cliente não devolve esse campo. É o que permite separar
# mensalidade de taxa e de avulso; sem ele tudo entrava como 'recorrente'.
# Produtos observados na base real (jun-set/2026, 2.700+ itens).
_PRODUTO_BILLING_TYPE = (
    ('DESCONTO PRORATA', 'prorata'),
    ('PRORATA', 'prorata'),
    ('DESINSTALA', 'taxa_desinstalacao'),
    ('INSTALA', 'taxa_instalacao'),  # depois de DESINSTALA: 'INSTALA' é prefixo dele
    # ANTES de 'MENSALIDADE': 'MENSALIDADE EM ABERTO 15/01/2025 EM 5X' é uma
    # dívida antiga renegociada em parcelas — não a mensalidade do mês
    # corrente, mesmo vindo com mes_referente do mês atual. Confirmado com
    # dado real: cliente ABR EXPRESS, boleto 1738, tinha essa linha (R$
    # 90,98) E a mensalidade normal (R$ 64,99) da MESMA placa no MESMO mês —
    # as duas caindo em 'recorrente' duplicava a cobrança do contrato.
    ('MENSALIDADE EM ABERTO', 'avulsa'),
    ('MENSALIDADE', 'recorrente'),
)


def map_billing_type(produto) -> str:
    """Produto do SGR → billing_type do MasterSat.

    O que não é mensalidade, taxa nem prorata (tarifa bancária, acordo,
    fechamento, avulso) entra como 'avulsa' — é cobrança pontual, e tratar
    como recorrente distorceria qualquer leitura de receita recorrente.
    """
    texto = str(produto or '').strip().upper()
    if not texto:
        return 'recorrente'
    for marca, tipo in _PRODUTO_BILLING_TYPE:
        if marca in texto:
            return tipo
    return 'avulsa'


def _parse_parcela(valor) -> tuple[int | None, int | None]:
    """'1 de 10' → (1, 10). Formato observado na base real.

    O SGR também manda "0 de 1", "2 de 1", "0 de 0" em boleto de fechamento,
    que não é parcelamento. Número fora de 1..total vira (None, None) — o
    texto original fica em ``sgr_payload['parcela']`` — em vez de gravar uma
    parcela que a constraint ck_billings_parcela_no_intervalo recusa.
    """
    texto = str(valor or '').strip().lower()
    if ' de ' not in texto:
        return None, None
    inicio, _, fim = texto.partition(' de ')
    numero, total = _to_int(inicio), _to_int(fim)
    if numero is None or total is None or numero < 1 or total < 1 or numero > total:
        return None, None
    return numero, total


def map_boleto(
    boleto: dict, item: dict | None = None, em_aberto: bool | None = None,
) -> tuple[dict, list[str]]:
    """Um boleto do SGR (ou uma linha da sua discriminação) → Billing.

    `item` é uma entrada de `discriminacao[]`. Quando vem, o valor, a placa e
    a competência saem dela; o resto (datas, situação, nosso número) é sempre
    do documento, que é o que o cliente efetivamente recebeu.

    `em_aberto` vem de /buscar_boletos_abertos_cliente e é o que decide se a
    cobrança continua devida — a `situacao` do boleto não serve para isso
    (ver _BILLING_STATUS_MAP).
    """
    issues: list[str] = []

    if item is not None:
        valor_linha = ci_get(item, 'valor')
        amount = _to_decimal_br(valor_linha)
        placa = normalize_plate(ci_get(item, 'placa') or '') or None
        periodo = ci_get(item, 'mes_referente') or ci_get(boleto, 'mes_referente')
        produto = ci_get(item, 'produto')
    else:
        valor_linha = ci_get(boleto, 'valor')
        amount = _to_decimal_br(valor_linha)
        placa = None
        periodo = ci_get(boleto, 'mes_referente')
        produto = ci_get(boleto, 'tipo_boleto')

    situacao = ci_get(ci_get(boleto, 'situacao') or {}, 'descricao')
    status, status_issues = _map_status(situacao, _BILLING_STATUS_MAP, BillingStatus.CANCELED, 'boleto')
    issues.extend(status_issues)
    if em_aberto is not None:
        # Só o fluxo por cliente precisa desse override, porque lá a lista de
        # abertos vem de outro endpoint. Vindo do /buscar_boletos geral, a
        # própria `situacao` já distingue ABERTO de APROVADO, e em_aberto=None
        # deixa o mapa decidir.
        status = BillingStatus.PENDING if em_aberto else (
            BillingStatus.CANCELED if status == BillingStatus.PENDING else status
        )

    vencimento = parse_br_date(ci_get(boleto, 'data_vencimento'))
    if not vencimento:
        issues.append(f"Boleto #{ci_get(boleto, 'cod_boleto')} sem data de vencimento utilizável")
    pagamento = parse_br_date(ci_get(boleto, 'data_pagamento'))

    numero, total_parcelas = _parse_parcela(ci_get(boleto, 'parcela'))

    # valor_pagamento é do documento todo. Só dá para atribuir a uma cobrança
    # quando ela É o documento (boleto de uma linha só) — ratear por placa
    # inventaria um dado que o SGR não fornece.
    linhas = ci_get(boleto, 'discriminacao') or []
    pago = _to_decimal_br(ci_get(boleto, 'valor_pagamento')) if len(linhas) <= 1 else None
    # No SGR, boleto não pago vem com valor_pagamento '0,00'. No MasterSat
    # isso é ausência de pagamento (None): o schema BillingOut exige
    # paid_amount > 0, e gravar 0.0 faz a LISTAGEM INTEIRA de cobranças
    # responder 500 — não só o registro ruim.
    if not pago:
        pago = None
    pago_centavos = parse_centavos(ci_get(boleto, 'valor_pagamento')) if pago else None

    amount_cents = parse_centavos(valor_linha)
    if valor_linha not in (None, '') and amount_cents is None:
        issues.append(f"Boleto #{ci_get(boleto, 'cod_boleto')}: valor de linha malformado")

    return {
        'external_id': ci_get(boleto, 'cod_boleto'),
        # Decisões monetárias do importador usam só estes (centavos, int).
        # `amount`/`paid_amount` em float ficam por compatibilidade de leitura.
        'amount_cents': amount_cents,
        'paid_amount_cents': pago_centavos,
        'document_total_cents': parse_centavos(ci_get(boleto, 'valor')),
        'client_external_id': ci_get(boleto, 'cod_cliente'),
        'amount': amount,
        'vehicle_plate': placa,
        'period_label': periodo,
        'produto': produto,
        'billing_type': map_billing_type(produto),
        # Documento do boleto: o SGR só devolve isto enquanto a situação é
        # ABERTO. Depois de BAIXADO/REMOVIDO os três campos vêm vazios — o que
        # comprova a quitação é a baixa (data_pagamento/valor_pagamento), não
        # o documento. Exige linha_digitavel='S' na consulta.
        # Registro do boleto de origem, para a tela de detalhes. Guarda o
        # documento inteiro (não só os campos que viraram coluna): é o que
        # sobra de "boleto" depois que o SGR baixa e deixa de servir o PDF.
        'sgr_payload': {
            'cod_boleto': ci_get(boleto, 'cod_boleto'),
            'nosso_numero': ci_get(boleto, 'nosso_numero'),
            'tipo_boleto': ci_get(boleto, 'tipo_boleto'),
            'parcela': ci_get(boleto, 'parcela'),
            'mes_referente': ci_get(boleto, 'mes_referente'),
            'valor': ci_get(boleto, 'valor'),
            'valor_pagamento': ci_get(boleto, 'valor_pagamento'),
            'forma_pagamento': ci_get(boleto, 'forma_pagamento'),
            'data_emissao': ci_get(boleto, 'data_emissao'),
            'data_vencimento': ci_get(boleto, 'data_vencimento'),
            'data_vencimento_original': ci_get(boleto, 'data_vencimento_original'),
            'data_pagamento': ci_get(boleto, 'data_pagamento'),
            'data_credito_banco': ci_get(boleto, 'data_credito_banco'),
            'formato_geracao_boleto': ci_get(boleto, 'formato_geracao_boleto'),
            'numero_nf': ci_get(boleto, 'numero_nf'),
            'matriz_filial': ci_get(boleto, 'matriz_filial'),
            'interveniente': ci_get(boleto, 'interveniente'),
            'situacao': ci_get(ci_get(boleto, 'situacao') or {}, 'descricao'),
            'placas': ci_get(boleto, 'placas'),
            'discriminacao': ci_get(boleto, 'discriminacao'),
            'produto': produto,
            'codigo_banco': ci_get(boleto, 'codigo_banco'),
            'linha_digitavel': ci_get(boleto, 'linha_digitavel'),
            'cod_barras': ci_get(boleto, 'cod_barras'),
            'pix_copia_cola': ci_get(boleto, 'pix_copia_cola'),
        },
        'linha_digitavel': ci_get(boleto, 'linha_digitavel'),
        'cod_barras': ci_get(boleto, 'cod_barras'),
        'pix_copia_cola': ci_get(boleto, 'pix_copia_cola'),
        'link_boleto': ci_get(boleto, 'link'),
        'due_date': vencimento.isoformat() if vencimento else None,
        'payment_date': pagamento.isoformat() if pagamento else None,
        'paid_amount': pago,
        'payment_method': ci_get(boleto, 'forma_pagamento'),
        'receipt_number': ci_get(boleto, 'nosso_numero'),
        'installment_number': numero,
        'installment_total': total_parcelas,
        'status': status.value,
        # A placa entra no título porque é o que distingue as linhas de um
        # boleto consolidado. Sem ela, duas placas que não foram migradas
        # (máquina sem placa no padrão) viram cobranças indistinguíveis e a
        # deduplicação do importador colapsaria valores que são reais.
        'title': (
            f"Boleto SGR {ci_get(boleto, 'nosso_numero') or ci_get(boleto, 'cod_boleto')}"
            + (f' - {ci_get(item, "placa")}' if item is not None and ci_get(item, 'placa') else '')
        ),
    }, issues
