"""
Gerador de arquivo de remessa CNAB 400 — Banco Ailos (085-0)

Estrutura do arquivo:
  Linha 1   : Header (tipo 0)
  Linhas 2-N: Detalhe (tipo 7) — um por boleto
  Linha N+1 : Trailer (tipo 9)

Cada linha tem exatamente 400 caracteres + CRLF.

Referência: Manual Técnico de Cobrança Bancária 400 posições — Ailos
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from typing import Iterable

from app.services.boleto_ailos import (
    AGENCIA,
    AGENCIA_DIG,
    BANCO_CODIGO,
    BANCO_NOME,
    CARTEIRA,
    CEDENTE_CNPJ,
    CEDENTE_COD,
    CEDENTE_COD_DIG,
    CEDENTE_NOME,
    CONVENIO,
    VARIACAO,
    gerar_nosso_numero,
    modulo11_nosso_numero,
)

CRLF = "\r\n"
LINHA_LEN = 400


def _pad(text: str, size: int, fill: str = " ", align: str = "left") -> str:
    """Formata string com padding fixo."""
    t = str(text or "")
    if align == "right":
        return t.zfill(size)[:size] if fill == "0" else t.rjust(size)[:size]
    return t.ljust(size)[:size]


def _num(value: int | str, size: int) -> str:
    return str(value).zfill(size)[:size]


def _fmt_date_cnab(d: date | None) -> str:
    return d.strftime("%d%m%y") if d else "000000"


def _fmt_valor_cnab(v: float | Decimal, size: int = 13) -> str:
    centavos = round(float(v or 0) * 100)
    return str(centavos).zfill(size)[:size]


def _validar(linha: str, num: int):
    if len(linha) != LINHA_LEN:
        raise ValueError(f"Linha {num} tem {len(linha)} chars (esperado {LINHA_LEN})")


# ---------------------------------------------------------------------------
# Header (registro tipo 0)
# ---------------------------------------------------------------------------

def header(data_geracao: date | None = None, seq_arquivo: int = 1) -> str:
    """
    Posições do header CNAB400 Ailos:
      1       : Tipo registro = 0
      2       : Tipo operação = 1 (remessa)
      3-9     : Literal "REMESSA"
      10-11   : Código serviço = 01 (cobrança)
      12-26   : Literal "COBRANCA       "
      27-30   : Agência cedente
      31      : Dígito agência
      32      : Zero (filler)
      33-46   : CNPJ cedente (14)
      47-76   : Nome empresa (30)
      77-79   : Código banco
      80-94   : Nome banco (15)
      95-100  : Data geração (DDMMAA)
      101-107 : Sequencial arquivo (7)
      108-394 : Brancos
      395-400 : Sequencial registro = 000001
    """
    dg = data_geracao or date.today()
    linha = (
        "0"                                   # 1
        "1"                                   # 2
        "REMESSA"                             # 3-9
        "01"                                  # 10-11
        "COBRANCA       "                     # 12-26
        + _pad(AGENCIA, 4)                    # 27-30
        + AGENCIA_DIG                         # 31
        + "0"                                 # 32
        + _num(CEDENTE_CNPJ, 14)             # 33-46
        + _pad(CEDENTE_NOME, 30)              # 47-76
        + BANCO_CODIGO                        # 77-79
        + _pad(BANCO_NOME, 15)               # 80-94
        + dg.strftime("%d%m%y")              # 95-100
        + _num(seq_arquivo, 7)               # 101-107
        + " " * 287                           # 108-394
        + "000001"                            # 395-400
    )
    _validar(linha, 1)
    return linha


# ---------------------------------------------------------------------------
# Detalhe (registro tipo 7)
# ---------------------------------------------------------------------------

def detalhe(
    billing_id: int,
    valor: float | Decimal,
    vencimento: date | None,
    sacado_nome: str,
    sacado_cpf_cnpj: str,
    sacado_endereco: str = "",
    sacado_bairro: str = "",
    sacado_cep: str = "",
    sacado_cidade: str = "",
    sacado_estado: str = "",
    data_emissao: date | None = None,
    instrucao1: str = "00",
    instrucao2: str = "00",
    mora_diaria: float = 0.0,
    desconto: float = 0.0,
    seq_registro: int = 2,
) -> str:
    """
    Posições do detalhe CNAB400 Ailos:
      1       : Tipo registro = 7
      2-3     : Tipo inscrição sacado (01=CPF, 02=CNPJ)
      4-17    : CPF/CNPJ sacado (14)
      18-21   : Agência cedente (4)
      22      : Dígito agência
      23      : Zero filler
      24-31   : Código cedente/beneficiário (8): CCCCCCC + dígito
      32      : Zero filler
      33-37   : Carteira/variação: carteira(2)+variação(3)
      38      : Zero filler
      39-48   : Nosso número (9 dígitos + dígito verificador = 10)
      49      : Zero filler
      50-62   : Zeros (reservado)
      63-70   : Data vencimento (DDMMAAAA — 8 chars)
      71-83   : Valor do boleto (13 chars, em centavos)
      84-86   : Banco cobrador = "085"
      87-91   : Agência cobradora (5)
      92-93   : Espécie = "01" (duplicata)
      94      : Aceite (N)
      95-100  : Data emissão (DDMMAA)
      101-102 : Instrução 1
      103-104 : Instrução 2
      105-117 : Mora/juros diários (13, em centavos)
      118-130 : Desconto (13, em centavos)
      131-143 : IOF = zeros (13)
      144-156 : Abatimento = zeros (13)
      157-158 : Tipo inscrição sacado (mesmo pos 2-3)
      159-172 : CPF/CNPJ sacado (14)
      173-212 : Nome sacado (40)
      213-252 : Endereço sacado (40)
      253-264 : Bairro sacado (12)
      265-272 : CEP sacado (8)
      273-287 : Cidade sacado (15)
      288-289 : Estado sacado (2)
      290-319 : Sacador/avalista (30, brancos)
      320-344 : Nro documento cedente (25)
      345-394 : Brancos (50)
      395-400 : Sequencial registro (6)
    """
    # gerar_nosso_numero retorna (17_dígitos, dv).
    # Para CNAB400 usamos apenas o sequencial (9 dígitos) + DV do sequencial = 10 chars.
    seq     = str(billing_id).zfill(9)
    seq_dv  = modulo11_nosso_numero(seq)
    nosso_num = seq + seq_dv           # 9 + 1 = 10 chars (campo 39-48)

    de = data_emissao or date.today()

    # Determina tipo inscrição sacado
    cpf_cnpj_limpo = "".join(c for c in (sacado_cpf_cnpj or "") if c.isdigit())
    tipo_insc = "02" if len(cpf_cnpj_limpo) == 14 else "01"
    cpf_cnpj_fmt = _num(cpf_cnpj_limpo, 14)

    # Data vencimento formato DDMMAAAA (8 chars)
    dt_venc = vencimento.strftime("%d%m%Y") if vencimento else "00000000"

    cep_limpo = "".join(c for c in (sacado_cep or "") if c.isdigit()).ljust(8)[:8]

    linha = (
        "7"                                         # 1
        + tipo_insc                                 # 2-3
        + cpf_cnpj_fmt                              # 4-17
        + _pad(AGENCIA, 4)                          # 18-21
        + AGENCIA_DIG                               # 22
        + "0"                                       # 23
        + _pad(CEDENTE_COD, 7) + CEDENTE_COD_DIG   # 24-31
        + "0"                                       # 32
        + CARTEIRA + VARIACAO                       # 33-37
        + "0"                                       # 38
        + nosso_num                                 # 39-48
        + "0"                                       # 49
        + "0" * 13                                  # 50-62
        + dt_venc                                   # 63-70
        + _fmt_valor_cnab(valor, 13)                # 71-83
        + BANCO_CODIGO                              # 84-86
        + _num(AGENCIA + AGENCIA_DIG, 5)            # 87-91
        + "01"                                      # 92-93 espécie
        + "N"                                       # 94 aceite
        + de.strftime("%d%m%y")                     # 95-100
        + _num(instrucao1, 2)                       # 101-102
        + _num(instrucao2, 2)                       # 103-104
        + _fmt_valor_cnab(mora_diaria, 13)          # 105-117
        + _fmt_valor_cnab(desconto, 13)             # 118-130
        + "0" * 13                                  # 131-143 IOF
        + "0" * 13                                  # 144-156 abatimento
        + tipo_insc                                 # 157-158
        + cpf_cnpj_fmt                              # 159-172
        + _pad(sacado_nome, 40)                     # 173-212
        + _pad(sacado_endereco, 40)                 # 213-252
        + _pad(sacado_bairro, 12)                   # 253-264
        + cep_limpo                                 # 265-272
        + _pad(sacado_cidade, 15)                   # 273-287
        + _pad(sacado_estado, 2)                    # 288-289
        + " " * 30                                  # 290-319 sacador
        + _pad(f"BOLETO-{billing_id}", 25)          # 320-344 nro doc
        + " " * 50                                  # 345-394
        + _num(seq_registro, 6)                     # 395-400
    )
    _validar(linha, seq_registro)
    return linha


# ---------------------------------------------------------------------------
# Trailer (registro tipo 9)
# ---------------------------------------------------------------------------

def trailer(qtd_registros: int, seq_registro: int) -> str:
    """
    Trailer CNAB400 (tipo 9).
      1       : Tipo registro = 9
      2-6     : Quantidade de registros de detalhe
      7-394   : Brancos
      395-400 : Sequencial registro
    """
    linha = (
        "9"
        + _num(qtd_registros, 5)
        + " " * 388
        + _num(seq_registro, 6)
    )
    _validar(linha, seq_registro)
    return linha


# ---------------------------------------------------------------------------
# Gerador de arquivo completo
# ---------------------------------------------------------------------------

def gerar_arquivo_cnab400(
    boletos: Iterable[dict],
    seq_arquivo: int = 1,
    data_geracao: date | None = None,
) -> bytes:
    """
    Gera o arquivo CNAB400 completo.

    Cada item de `boletos` deve conter:
      billing_id, valor, vencimento (date|None),
      sacado_nome, sacado_cpf_cnpj,
      sacado_endereco?, sacado_bairro?, sacado_cep?,
      sacado_cidade?, sacado_estado?,
      data_emissao? (date)
    """
    buf = io.StringIO()
    seq = 1

    # Header
    buf.write(header(data_geracao, seq_arquivo) + CRLF)

    qtd_detalhes = 0
    for item in boletos:
        seq += 1
        qtd_detalhes += 1
        linha = detalhe(
            billing_id=item["billing_id"],
            valor=item["valor"],
            vencimento=item.get("vencimento"),
            sacado_nome=item.get("sacado_nome", ""),
            sacado_cpf_cnpj=item.get("sacado_cpf_cnpj", ""),
            sacado_endereco=item.get("sacado_endereco", ""),
            sacado_bairro=item.get("sacado_bairro", ""),
            sacado_cep=item.get("sacado_cep", ""),
            sacado_cidade=item.get("sacado_cidade", ""),
            sacado_estado=item.get("sacado_estado", ""),
            data_emissao=item.get("data_emissao"),
            seq_registro=seq,
        )
        buf.write(linha + CRLF)

    # Trailer
    seq += 1
    buf.write(trailer(qtd_detalhes, seq) + CRLF)

    # Encode em latin-1 (padrão CNAB)
    return buf.getvalue().encode("latin-1", errors="replace")
