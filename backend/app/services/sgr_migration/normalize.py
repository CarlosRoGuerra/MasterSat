"""
Normalizações e validações puras (sem I/O) usadas para avaliar a qualidade
dos dados vindos do SGR antes do mapeamento (ETAPA 11/12 da POC de migração).

Nada aqui modifica a origem (SGR) — os dados normalizados só existem em
memória / no relatório de diagnóstico local.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_ONLY_DIGITS = re.compile(r'\D')


def only_digits(value: str | None) -> str:
    return _ONLY_DIGITS.sub('', value or '')


def normalize_cpf_cnpj(value: str | None) -> tuple[str, str | None]:
    """Retorna (dígitos, tipo) — tipo é 'pf' (11 díg.), 'pj' (14 díg.) ou None se não reconhecido."""
    digits = only_digits(value)
    if len(digits) == 11:
        return digits, 'pf'
    if len(digits) == 14:
        return digits, 'pj'
    return digits, None


def is_valid_cpf(digits: str) -> bool:
    if len(digits) != 11 or digits == digits[0] * 11:
        return False

    def _check_digit(base: str) -> int:
        total = sum(int(d) * w for d, w in zip(base, range(len(base) + 1, 1, -1)))
        rest = (total * 10) % 11
        return 0 if rest == 10 else rest

    d1 = _check_digit(digits[:9])
    d2 = _check_digit(digits[:9] + str(d1))
    return digits[-2:] == f'{d1}{d2}'


def is_valid_cnpj(digits: str) -> bool:
    if len(digits) != 14 or digits == digits[0] * 14:
        return False

    def _check_digit(base: str, weights: list[int]) -> int:
        total = sum(int(d) * w for d, w in zip(base, weights))
        rest = total % 11
        return 0 if rest < 2 else 11 - rest

    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    w2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = _check_digit(digits[:12], w1)
    d2 = _check_digit(digits[:12] + str(d1), w2)
    return digits[-2:] == f'{d1}{d2}'


def is_valid_cpf_cnpj(digits: str, tipo: str | None) -> bool:
    if tipo == 'pf':
        return is_valid_cpf(digits)
    if tipo == 'pj':
        return is_valid_cnpj(digits)
    return False


def normalize_phone(value: str | None) -> str:
    """Extrai só os dígitos; remove DDI 55 quando o número já tem DDD+número."""
    digits = only_digits(value)
    if digits.startswith('55') and len(digits) > 11:
        digits = digits[2:]
    return digits


def normalize_plate(value: str | None) -> str:
    """Remove hífen/espaços e coloca em caixa alta — placas Mercosul e antigas têm 7 chars."""
    if not value:
        return ''
    return value.strip().upper().replace('-', '').replace(' ', '')


_PLATE_OFICIAL = re.compile(r'[A-Z]{3}[0-9][A-Z0-9][0-9]{2}')  # Mercosul/antiga, sempre 7


def is_valid_plate(plate: str) -> bool:
    """7 caracteres: formato oficial (Mercosul ou antigo), validado por regex.

    5 ou 6 caracteres: o SGR também usa o campo como identificador livre em
    máquinas pesadas sem placa oficial — casos reais na base: 'VIO17' (5),
    'MAQ002' e 'GIGA01' (6). Para esse tamanho aceitamos qualquer combinação
    alfanumérica que tenha PELO MENOS uma letra e um dígito (como todos os
    exemplos reais têm) — isso deixa esses códigos passarem sem abrir mão da
    guarda contra erro de cadastro: 'GEISON' (nome de pessoa, só letras) e
    'ALESSANDRO'/'CASE580H'/'VOLVO220' (fora da faixa de tamanho) continuam
    de fora.
    """
    if len(plate) == 7:
        return bool(_PLATE_OFICIAL.fullmatch(plate))
    if len(plate) in (5, 6):
        return (
            bool(re.fullmatch(r'[A-Z0-9]+', plate))
            and any(c.isalpha() for c in plate)
            and any(c.isdigit() for c in plate)
        )
    return False


_VALOR_BR = re.compile(r'-?\d{1,3}(\.\d{3})*(,\d{1,2})?|-?\d+(,\d{1,2})?')
_VALOR_PONTO = re.compile(r'-?\d+(\.\d{1,2})?')


def parse_centavos(value) -> int | None:
    """Valor monetário do SGR → centavos (int), sem passar por float.

    Aceita o formato da API ('1.234,56', '-41,65', '0,00'), número já
    decimal ('49.99', 49.99, Decimal) e inteiro. Qualquer outra coisa —
    texto, três casas decimais, separador ambíguo — devolve None: valor
    malformado não pode ser arredondado em silêncio numa decisão financeira.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value * 100
    if isinstance(value, (float, Decimal)):
        try:
            numero = Decimal(str(value))
        except InvalidOperation:
            return None
    else:
        texto = str(value).strip().replace(' ', '')
        if not texto:
            return None
        if ',' in texto:
            if not _VALOR_BR.fullmatch(texto):
                return None
            texto = texto.replace('.', '').replace(',', '.')
        elif not _VALOR_PONTO.fullmatch(texto):
            return None
        numero = Decimal(texto)
    if not numero.is_finite():
        return None
    centavos = numero * 100
    if centavos != centavos.to_integral_value():
        return None
    return int(centavos)


def normalize_email(value: str | None) -> str:
    if not value:
        return ''
    return value.strip().lower()


def is_valid_email(value: str) -> bool:
    return bool(re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', value))


def parse_br_date(value: str | None) -> date | None:
    """Datas do SGR aparecem tanto em 'dd/mm/aaaa' quanto em 'aaaa-mm-dd' (ISO)."""
    if not value:
        return None
    for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None
