"""Campos de remessa com posições explícitas dos manuais Ailos fornecidos."""
from decimal import Decimal, InvalidOperation
import unicodedata


def numero(valor, tamanho: int) -> str:
    texto = str(valor)
    if not texto.isascii() or not texto.isdigit() or len(texto) > tamanho:
        raise ValueError(f'Campo numérico CNAB inválido para {tamanho} posições.')
    return texto.zfill(tamanho)


def texto(valor, tamanho: int) -> str:
    valor = unicodedata.normalize('NFKD', str(valor or ''))
    valor = ''.join(c for c in valor if not unicodedata.combining(c))
    valor = ''.join(c if 32 <= ord(c) < 127 else ' ' for c in valor).upper()
    return valor[:tamanho].ljust(tamanho)


def dinheiro(valor, tamanho: int, *, positivo: bool = False) -> str:
    try:
        quantia = Decimal(str(valor))
        if (not quantia.is_finite() or quantia < 0 or (positivo and quantia == 0)
                or quantia != quantia.quantize(Decimal('0.01'))):
            raise ValueError
        return numero(int(quantia * 100), tamanho)
    except (ValueError, InvalidOperation, OverflowError) as exc:
        raise ValueError('Valor CNAB inválido: informe centavos exatos e valor positivo para o título.') from exc


def registro(tamanho: int, campos: list[tuple[int, int, str]]) -> str:
    resultado = [' '] * tamanho
    usados = set()
    for inicio, fim, valor in campos:
        if len(valor) != fim - inicio + 1 or inicio < 1 or fim > tamanho:
            raise ValueError(f'Campo CNAB {inicio}–{fim} fora do tamanho esperado.')
        posicoes = set(range(inicio - 1, fim))
        if usados & posicoes:
            raise ValueError(f'Campos CNAB sobrepostos em {inicio}–{fim}.')
        usados |= posicoes
        resultado[inicio - 1:fim] = valor
    return ''.join(resultado)
