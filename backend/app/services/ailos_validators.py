"""
Validações e normalizações puras (sem I/O) para os payloads trocados com a
API de Cobrança Ailos.
"""
from __future__ import annotations

import re
import unicodedata


class AilosValidationError(Exception):
    """Erro de validação de payload Ailos — acumula todas as violações encontradas."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__('; '.join(errors))


def strip_accents(value: str) -> str:
    """Remove acentos/diacríticos, mantendo os caracteres base (á → a)."""
    nfkd = unicodedata.normalize('NFKD', value)
    return ''.join(ch for ch in nfkd if not unicodedata.combining(ch))


_NORMALIZE_ALLOWED = re.compile(r'[^A-Za-z0-9 ]')
_COLLAPSE_SPACES = re.compile(r'\s+')


def normalize_text(value: str | None, max_length: int, upper: bool = True) -> str:
    """
    Remove acentos e caracteres especiais (substituindo por espaço), colapsa
    espaços repetidos e trunca em ``max_length``.

    Por padrão retorna em CAIXA ALTA — os exemplos da documentação Ailos
    mostram todos os campos de texto (nome, logradouro, bairro, cidade,
    descricaoDocumento, mensagemPagador) em maiúsculas.
    """
    if not value:
        return ''
    text = strip_accents(value)
    text = _NORMALIZE_ALLOWED.sub(' ', text)
    text = _COLLAPSE_SPACES.sub(' ', text).strip()
    if upper:
        text = text.upper()
    return text[:max_length]


def only_digits(value: str | None) -> str:
    """Extrai apenas os dígitos de uma string (CPF/CNPJ, telefone, CEP, etc.)."""
    return re.sub(r'\D', '', value or '')


def validar_cpf_cnpj(value: str | None) -> str:
    """Retorna o CPF/CNPJ apenas com dígitos; levanta AilosValidationError se ausente/inválido."""
    digits = only_digits(value)
    if not digits:
        raise AilosValidationError(['CPF/CNPJ do pagador não informado'])
    if len(digits) not in (11, 14):
        raise AilosValidationError([f'CPF/CNPJ do pagador inválido: "{value}" ({len(digits)} dígitos)'])
    return digits


def split_phone(phone: str | None) -> tuple[str, str, str]:
    """
    Divide um telefone em (ddi, ddd, numero).

    Assume DDI '55' (Brasil). Se o telefone vier com o prefixo 55 e dígitos
    suficientes para DDD+número, o prefixo é removido. Telefone vazio
    retorna ('55', '', '').
    """
    digits = only_digits(phone)
    if not digits:
        return '55', '', ''
    if digits.startswith('55') and len(digits) > 11:
        digits = digits[2:]
    return '55', digits[:2], digits[2:]


def _check_max_length(errors: list[str], value: str | None, max_length: int, field: str) -> None:
    if value and len(value) > max_length:
        errors.append(f'{field} excede {max_length} caracteres: "{value}" ({len(value)})')


def validate_boleto_payload(payload: dict) -> None:
    """
    Valida o dict já montado para os endpoints de geração de boleto Ailos
    contra as regras documentadas. Acumula TODAS as violações encontradas
    antes de levantar AilosValidationError (não para na primeira).
    """
    errors: list[str] = []

    pagador = payload.get('pagador') or {}
    entidade = pagador.get('entidadeLegal') or {}
    endereco = pagador.get('endereco') or {}
    documento = payload.get('documento') or {}
    emissao = payload.get('emissao') or {}
    vencimento = payload.get('vencimento') or {}
    valor_boleto = payload.get('valorBoleto') or {}

    cpf_cnpj = only_digits(entidade.get('identificadorReceitaFederal'))
    if not cpf_cnpj:
        errors.append('pagador.entidadeLegal.identificadorReceitaFederal não informado')
    elif len(cpf_cnpj) not in (11, 14):
        errors.append(
            f'pagador.entidadeLegal.identificadorReceitaFederal inválido: {len(cpf_cnpj)} dígitos'
        )

    cep = only_digits(endereco.get('cep'))
    if len(cep) != 8:
        errors.append(f'pagador.endereco.cep deve ter 8 dígitos: "{endereco.get("cep")}"')

    uf = endereco.get('uf') or ''
    if len(uf) != 2:
        errors.append(f'pagador.endereco.uf deve ter 2 letras: "{uf}"')

    _check_max_length(errors, entidade.get('nome'), 50, 'pagador.entidadeLegal.nome')
    _check_max_length(errors, endereco.get('logradouro'), 56, 'pagador.endereco.logradouro')
    _check_max_length(errors, endereco.get('bairro'), 30, 'pagador.endereco.bairro')
    _check_max_length(errors, endereco.get('cidade'), 30, 'pagador.endereco.cidade')

    for msg in pagador.get('mensagemPagador') or []:
        _check_max_length(errors, msg, 40, 'pagador.mensagemPagador')

    _check_max_length(errors, documento.get('descricaoDocumento'), 15, 'documento.descricaoDocumento')

    valor_nominal = valor_boleto.get('valorNominal')
    if valor_nominal is None or float(valor_nominal) <= 0:
        errors.append(f'valorBoleto.valorNominal deve ser maior que zero: {valor_nominal!r}')

    data_emissao = emissao.get('dataEmissaoDocumento')
    data_vencimento = vencimento.get('dataVencimento')
    if data_emissao and data_vencimento and data_vencimento < data_emissao:
        errors.append(
            f'vencimento.dataVencimento ({data_vencimento}) anterior a '
            f'emissao.dataEmissaoDocumento ({data_emissao})'
        )

    if errors:
        raise AilosValidationError(errors)
