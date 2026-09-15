"""
Normalizações e validações puras (sem I/O) usadas para avaliar a qualidade
dos dados vindos do SGR antes do mapeamento (ETAPA 11/12 da POC de migração).

Nada aqui modifica a origem (SGR) — os dados normalizados só existem em
memória / no relatório de diagnóstico local.
"""
from __future__ import annotations

import re
from datetime import date, datetime

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


def is_valid_plate(plate: str) -> bool:
    return bool(re.fullmatch(r'[A-Z]{3}[0-9][A-Z0-9][0-9]{2}', plate))


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
