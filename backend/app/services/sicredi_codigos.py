"""Código de barras e linha digitável Sicredi (CNAB 400 2.9, pp. 43–49)."""
from datetime import date
from decimal import Decimal


def _digitos(valor: str, tamanho: int, campo: str) -> str:
    if not isinstance(valor, str) or len(valor) != tamanho or not valor.isascii() or not valor.isdigit():
        raise ValueError(f'{campo}: esperado {tamanho} dígitos.')
    return valor


def modulo_11(texto: str, *, geral: bool = False) -> str:
    soma = sum(int(d) * (2 + i % 8) for i, d in enumerate(reversed(texto)))
    dv = 11 - soma % 11
    return str((1 if geral else 0) if dv > 9 else dv)


def modulo_10(texto: str) -> str:
    produtos = [int(d) * (2 if i % 2 == 0 else 1) for i, d in enumerate(reversed(texto))]
    return str(-sum(p // 10 + p % 10 for p in produtos) % 10)


def fator_vencimento(vencimento: date) -> int:
    # Reinício previsto expressamente no CNAB 400 2.9, p. 46.
    base = date(2025, 2, 22)
    fator = 1000 + (vencimento - base).days if vencimento >= base else (vencimento - date(1997, 10, 7)).days
    if not 0 <= fator <= 9999:
        raise ValueError('Vencimento fora do intervalo documentado para o fator de quatro dígitos.')
    return fator


def codigo_barras(cooperativa: str, posto: str, beneficiario: str, nosso_numero: str,
                  vencimento: date, valor: Decimal) -> str:
    for dado, tamanho, campo in ((cooperativa, 4, 'Cooperativa'), (posto, 2, 'Posto'),
                                  (beneficiario, 5, 'Beneficiário'), (nosso_numero, 9, 'Nosso número')):
        _digitos(dado, tamanho, campo)
    if (not valor.is_finite() or not Decimal('0') < valor <= Decimal('99999999.99')
            or valor != valor.quantize(Decimal('0.01'))):
        raise ValueError('Valor incompatível com o campo de dez dígitos do código de barras.')
    livre = f'11{nosso_numero}{cooperativa}{posto}{beneficiario}10'
    livre += modulo_11(livre)
    sem_dv = f'7489{fator_vencimento(vencimento):04d}{int(valor * 100):010d}{livre}'
    return sem_dv[:4] + modulo_11(sem_dv, geral=True) + sem_dv[4:]


def linha_digitavel(codigo: str) -> str:
    _digitos(codigo, 44, 'Código de barras')
    if codigo[:4] != '7489' or codigo[4] != modulo_11(codigo[:4] + codigo[5:], geral=True):
        raise ValueError('Código de barras Sicredi com banco, moeda ou DV geral inválido.')
    livre = codigo[19:]
    if livre[-1] != modulo_11(livre[:-1]):
        raise ValueError('DV do campo livre inválido.')
    campos = [codigo[:4] + livre[:5], livre[5:15], livre[15:]]
    editados = [f'{campo[:5]}.{campo[5:]}{modulo_10(campo)}' for campo in campos]
    return ' '.join([*editados, codigo[4], codigo[5:19]])
