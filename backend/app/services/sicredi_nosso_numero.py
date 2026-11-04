"""Nosso número Sicredi gerado pelo beneficiário (manual "Geração do Nosso Número").

Estrutura ``AABnnnnnD`` (9 dígitos): ano (2), byte indicador de geração (2 a
9 = gerado pelo beneficiário), sequencial livre (5) e dígito verificador.

Gerar o número aqui, e não deixar o Sicredi gerar, é o que torna o registro
recuperável: o número fica reservado no banco de dados ANTES do POST, então
depois de um timeout a consulta pelo nosso número diz se o título existe — e
uma nova tentativa reenvia o mesmo número, que o Sicredi recusa se já estiver
cadastrado. Sem isso, a única pista seria ``/boletos/cadastrados``, que só
enxerga títulos emitidos há menos de 48 horas e não devolve o nosso número.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.sicredi_boleto import SicrediNossoNumeroSequencia

SEQUENCIAL_MAXIMO = 99999
_PESOS = (2, 3, 4, 5, 6, 7, 8, 9)


class NossoNumeroError(Exception):
    """Faixa de nosso número esgotada ou dado de convênio inválido."""


def _digitos(valor: str, tamanho: int, campo: str) -> str:
    texto = str(valor or '').strip()
    if not texto.isdigit() or len(texto) > tamanho:
        raise NossoNumeroError(f'{campo} inválido para o nosso número: "{valor}" (esperado {tamanho} dígitos).')
    return texto.zfill(tamanho)


def digito_verificador(
    cooperativa: str, posto: str, codigo_beneficiario: str, ano: int, byte: int, sequencial: int,
) -> int:
    """Módulo 11 sobre cooperativa+posto+beneficiário+ano+byte+sequencial.

    Pesos 2 a 9 da direita para a esquerda; DV = 11 − resto, e 10 ou 11 viram 0.
    Posto alfanumérico entra como ``00`` no cálculo (nota do manual).
    """
    posto_txt = str(posto or '').strip()
    posto_calc = posto_txt.zfill(2) if posto_txt.isdigit() else '00'
    base = (
        _digitos(cooperativa, 4, 'Cooperativa')
        + _digitos(posto_calc, 2, 'Posto')
        + _digitos(codigo_beneficiario, 5, 'Código do beneficiário')
        + f'{int(ano) % 100:02d}'
        + str(int(byte))
        + f'{int(sequencial):05d}'
    )
    soma = sum(int(d) * _PESOS[i % len(_PESOS)] for i, d in enumerate(reversed(base)))
    dv = 11 - (soma % 11)
    return 0 if dv in (10, 11) else dv


def montar(
    cooperativa: str, posto: str, codigo_beneficiario: str, ano: int, byte: int, sequencial: int,
) -> str:
    """Nosso número de 9 dígitos já com o DV."""
    if not 2 <= int(byte) <= 9:
        raise NossoNumeroError(f'Byte de geração {byte} fora da faixa do beneficiário (2 a 9).')
    if not 0 <= int(sequencial) <= SEQUENCIAL_MAXIMO:
        raise NossoNumeroError(f'Sequencial {sequencial} fora da faixa (0 a {SEQUENCIAL_MAXIMO}).')
    dv = digito_verificador(cooperativa, posto, codigo_beneficiario, ano, byte, sequencial)
    return f'{int(ano) % 100:02d}{int(byte)}{int(sequencial):05d}{dv}'


def proximo(
    db: Session,
    *,
    ambiente: str,
    cooperativa: str,
    posto: str,
    codigo_beneficiario: str,
    ano: int,
    byte: int,
) -> str:
    """Aloca o próximo nosso número do convênio no ano (trava de linha).

    Não faz commit: o número só vale junto com a reserva do boleto que o usa,
    na mesma transação.
    """
    chave = dict(
        ambiente=ambiente, cooperativa=cooperativa, posto=posto,
        codigo_beneficiario=codigo_beneficiario, ano=int(ano) % 100, byte=int(byte),
    )
    filtro = [getattr(SicrediNossoNumeroSequencia, k) == v for k, v in chave.items()]
    seq = db.scalars(select(SicrediNossoNumeroSequencia).where(*filtro).with_for_update()).first()
    if seq is None:
        try:
            with db.begin_nested():
                seq = SicrediNossoNumeroSequencia(**chave, ultimo=0)
                db.add(seq)
                db.flush()
        except IntegrityError:
            # Outro processo criou a linha ao mesmo tempo: trava a dele.
            seq = db.scalars(select(SicrediNossoNumeroSequencia).where(*filtro).with_for_update()).one()
    if seq.ultimo >= SEQUENCIAL_MAXIMO:
        raise NossoNumeroError(
            f'Faixa de nosso número esgotada para o byte {byte} em {ano}. '
            'Configure outro SICREDI_NOSSO_NUMERO_BYTE (2 a 9).'
        )
    seq.ultimo += 1
    db.flush()
    return montar(cooperativa, posto, codigo_beneficiario, ano, byte, seq.ultimo)
