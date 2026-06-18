"""
Gerador de arquivo de remessa CNAB 240 — Banco Ailos (085-0)

Estrutura do arquivo (cada linha = 240 chars + CRLF):
  Header do arquivo  (tipo 0)
  Header do lote     (tipo 1)
    Segmento P       (tipo 3, seg P)
    Segmento Q       (tipo 3, seg Q)
    Segmento R       (tipo 3, seg R)  [opcional — juros/multa]
  Trailer do lote    (tipo 5)
  Trailer do arquivo (tipo 9)

Referência: Manual Técnico de Cobrança Bancária 240 posições — Ailos / FEBRABAN
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
    fator_vencimento,
    gerar_nosso_numero,
    formatar_valor,
)

CRLF = "\r\n"
LINHA_LEN = 240


def _num(v, size: int) -> str:
    return str(v or 0).zfill(size)[:size]


def _pad(text: str, size: int) -> str:
    return str(text or "").ljust(size)[:size]


def _fmt_date8(d: date | None) -> str:
    return d.strftime("%d%m%Y") if d else "00000000"


def _fmt_valor15(v: float | Decimal) -> str:
    """Valor com 13 inteiros e 2 decimais, sem separador = 15 chars."""
    centavos = round(float(v or 0) * 100)
    return str(centavos).zfill(15)[:15]


def _validar(linha: str, seq: int):
    if len(linha) != LINHA_LEN:
        raise ValueError(f"Linha {seq}: {len(linha)} chars (esperado {LINHA_LEN})")


def _tipo_insc(cpf_cnpj: str) -> str:
    limpo = "".join(c for c in (cpf_cnpj or "") if c.isdigit())
    return "2" if len(limpo) == 14 else "1"


def _cpf_cnpj_fmt(cpf_cnpj: str) -> str:
    return "".join(c for c in (cpf_cnpj or "") if c.isdigit()).zfill(14)[:14]


# ---------------------------------------------------------------------------
# Header do Arquivo (tipo 0)
# ---------------------------------------------------------------------------

def header_arquivo(data_geracao: date | None = None, seq_arquivo: int = 1) -> str:
    """
    CNAB240 Header Arquivo — 240 posições.
    Posições (1-based):
      1-3   : Código banco = 085
      4-7   : Lote = 0000 (header não tem lote)
      8     : Tipo registro = 0
      9-17  : Brancos
      18    : Tipo inscrição empresa (2=CNPJ)
      19-32 : CNPJ (14)
      33-52 : Código do convênio (20) — formatado como beneficiário
      53-57 : Agência (5) com dígito
      58-70 : Conta corrente (13) com dígito
      71-80 : Nome empresa (30) continua...

    Implementação baseada no modelo CNAB240 Ailos.
    """
    dg = data_geracao or date.today()
    hora_agora = "000000"  # HH:MM:SS

    agencia_5 = _num(AGENCIA, 4) + AGENCIA_DIG        # "01023"
    conta_13  = "0" * 5 + _num(CEDENTE_COD, 7) + CEDENTE_COD_DIG  # 13 chars

    linha = (
        BANCO_CODIGO                  # 1-3
        + "0000"                      # 4-7  lote = 0000
        + "0"                         # 8    tipo registro
        + " " * 9                     # 9-17 brancos
        + "2"                         # 18   tipo inscricao (2=CNPJ)
        + _num(CEDENTE_CNPJ, 14)      # 19-32
        + _pad(CONVENIO, 20)          # 33-52 convênio
        + agencia_5                   # 53-57
        + conta_13                    # 58-70
        + "0"                         # 71   dígito ag/conta
        + _pad(CEDENTE_NOME, 30)      # 72-101
        + _pad(BANCO_NOME, 30)        # 102-131
        + " " * 10                    # 132-141 endereço banco
        + _num(seq_arquivo, 6)        # 142-147 nro sequencial arquivo
        + dg.strftime("%d%m%Y")       # 148-155 data geração
        + hora_agora                  # 156-161 hora geração
        + "00000"                     # 162-166 densidade (brancos)
        + " " * 69                    # 167-235 reservado
        + " " * 5                     # 236-240 reservado banco
    )
    _validar(linha, 0)
    return linha


# ---------------------------------------------------------------------------
# Header do Lote (tipo 1)
# ---------------------------------------------------------------------------

def header_lote(lote: int = 1, data_geracao: date | None = None) -> str:
    """
    CNAB240 Header Lote (tipo 1, segmento R = remessa cobrança).
    """
    dg = data_geracao or date.today()
    agencia_5 = _num(AGENCIA, 4) + AGENCIA_DIG
    conta_13  = "0" * 5 + _num(CEDENTE_COD, 7) + CEDENTE_COD_DIG

    linha = (
        BANCO_CODIGO                  # 1-3
        + _num(lote, 4)               # 4-7  número do lote
        + "1"                         # 8    tipo registro (header lote)
        + "R"                         # 9    tipo operação (R=remessa)
        + "01"                        # 10-11 tipo serviço (01=cobrança)
        + "  "                        # 12-13 forma de lançamento
        + "045"                       # 14-16 versão layout
        + " "                         # 17   brancos
        + "2"                         # 18   tipo inscricao (2=CNPJ)
        + _num(CEDENTE_CNPJ, 14)      # 19-32
        + agencia_5                   # 33-37
        + " "                         # 38
        + conta_13                    # 39-51
        + " "                         # 52
        + "0"                         # 53   dígito ag/conta
        + _pad(CEDENTE_NOME, 30)      # 54-83
        + " " * 40                    # 84-123 endereço cedente (filler)
        + _num(0, 8)                  # 124-131 nro remessa/retorno
        + dg.strftime("%d%m%Y")       # 132-139 data gravação
        + "00000000"                  # 140-147 data crédito (zeros = não informado)
        + " " * 33                    # 148-180 reservado
        + " " * 60                    # 181-240 brancos
    )
    _validar(linha, 100)
    return linha


# ---------------------------------------------------------------------------
# Segmento P (dados do título)
# ---------------------------------------------------------------------------

def segmento_p(
    lote: int,
    seq: int,
    billing_id: int,
    valor: float | Decimal,
    vencimento: date | None,
    data_emissao: date | None = None,
    mora_valor: float = 0.0,
    desconto_valor: float = 0.0,
) -> str:
    """
    Segmento P — dados do título de cobrança (240 posições).
    """
    de = data_emissao or date.today()
    # gerar_nosso_numero retorna (17_dígitos, dv).
    # CNAB240 Seg. P campos 43-56 (14 chars) + campo 57 (DV).
    # Usamos os 17 dígitos completos: conta(8) + seq(9), truncado/padded a 14,
    # e DV calculado sobre o sequencial isolado para manter rastreabilidade.
    nn, nn_dv = gerar_nosso_numero(billing_id)
    nosso_num_14 = nn[-14:].zfill(14) if len(nn) >= 14 else nn.zfill(14)

    agencia_5 = _num(AGENCIA, 4) + AGENCIA_DIG
    conta_12  = "0" * 4 + _num(CEDENTE_COD, 7) + CEDENTE_COD_DIG  # 12 chars
    fator_venc = fator_vencimento(vencimento)
    dt_venc = _fmt_date8(vencimento)
    dt_emis = _fmt_date8(de)

    linha = (
        BANCO_CODIGO                   # 1-3
        + _num(lote, 4)                # 4-7
        + "3"                          # 8   tipo registro (detalhe)
        + _num(seq, 5)                 # 9-13 nro seq. registro lote
        + "P"                          # 14  código segmento
        + " "                          # 15  brancos
        + "01"                         # 16-17 código movimento remessa (01=entrada)
        + agencia_5                    # 18-22 agência cedente
        + " "                          # 23
        + conta_12                     # 24-35 conta cedente
        + " "                          # 36
        + "0"                          # 37  dígito ag/conta
        + CARTEIRA                     # 38-39
        + VARIACAO                     # 40-42 variação carteira
        + nosso_num_14                 # 43-56 nosso número (14 chars)
        + nn_dv                        # 57   dígito nosso número
        + "0" * 20                     # 58-77 número do documento (referência cedente)
        + dt_venc                      # 78-85 data vencimento DDMMAAAA
        + _fmt_valor15(valor)          # 86-100 valor título (15 chars)
        + BANCO_CODIGO                 # 101-103 banco cobrador
        + agencia_5                    # 104-108 agência cobradora
        + _num(billing_id, 20)         # 109-128 nro documento cedente
        + dt_emis                      # 129-136 data emissão
        + "00"                         # 137-138 instrução 1
        + "00"                         # 139-140 instrução 2
        + _fmt_valor15(mora_valor)     # 141-155 mora/juros por dia
        + _fmt_date8(None)             # 156-163 data desconto (zeros)
        + _fmt_valor15(desconto_valor) # 164-178 desconto
        + _fmt_valor15(0)              # 179-193 IOF
        + _fmt_valor15(0)              # 194-208 abatimento
        + "2"                          # 209   tipo inscricao sacado  ← preenchido depois
        + "00"                         # 210-211 dias para protesto
        + "3"                          # 212   código baixa (3=não baixar)
        + "000"                        # 213-215 dias para baixa
        + "09"                         # 216-217 código moeda (09=Real)
        + _num(0, 10)                  # 218-227 nro contrato cedente
        + "0"                          # 228   uso banco
        + " " * 12                     # 229-240 brancos
    )
    _validar(linha, seq)
    return linha


# ---------------------------------------------------------------------------
# Segmento Q (dados do sacado)
# ---------------------------------------------------------------------------

def segmento_q(
    lote: int,
    seq: int,
    sacado_nome: str,
    sacado_cpf_cnpj: str,
    sacado_endereco: str = "",
    sacado_bairro: str = "",
    sacado_cep: str = "",
    sacado_cidade: str = "",
    sacado_estado: str = "",
) -> str:
    """
    Segmento Q — dados do sacado (pagador) e sacador/avalista.
    """
    tipo = _tipo_insc(sacado_cpf_cnpj)
    cpf  = _cpf_cnpj_fmt(sacado_cpf_cnpj)
    cep_limpo = "".join(c for c in (sacado_cep or "") if c.isdigit()).zfill(8)[:8]

    linha = (
        BANCO_CODIGO                    # 1-3
        + _num(lote, 4)                 # 4-7
        + "3"                           # 8
        + _num(seq, 5)                  # 9-13
        + "Q"                           # 14 segmento
        + " "                           # 15
        + "01"                          # 16-17 mov. remessa
        + tipo                          # 18   tipo inscricao sacado
        + cpf                           # 19-32
        + _pad(sacado_nome, 40)         # 33-72
        + _pad(sacado_endereco, 40)     # 73-112
        + _pad(sacado_bairro, 15)       # 113-127
        + cep_limpo                     # 128-135
        + _pad(sacado_cidade, 15)       # 136-150
        + _pad(sacado_estado, 2)        # 151-152
        + "0"                           # 153   tipo inscricao sacador
        + "0" * 14                      # 154-167 CNPJ sacador
        + _pad("", 40)                  # 168-207 nome sacador
        + BANCO_CODIGO                  # 208-210 banco correspondente
        + " " * 20                      # 211-230 nro doc no banco correspondente
        + " " * 10                      # 231-240 brancos
    )
    _validar(linha, seq)
    return linha


# ---------------------------------------------------------------------------
# Segmento R (descontos, multa, juros — opcional)
# ---------------------------------------------------------------------------

def segmento_r(
    lote: int,
    seq: int,
    multa_pct: float = 0.0,
    data_multa: date | None = None,
) -> str:
    """
    Segmento R — informações complementares (multa, desconto 2).
    """
    cod_multa = "2" if multa_pct > 0 else "0"   # 2 = percentual
    dt_multa  = _fmt_date8(data_multa)

    linha = (
        BANCO_CODIGO                    # 1-3
        + _num(lote, 4)                 # 4-7
        + "3"                           # 8
        + _num(seq, 5)                  # 9-13
        + "R"                           # 14
        + " "                           # 15
        + "01"                          # 16-17 mov remessa
        + "0"                           # 18   código desconto 2
        + "00000000"                    # 19-26 data desconto 2
        + _fmt_valor15(0)               # 27-41 valor desconto 2
        + "0"                           # 42   código desconto 3
        + "00000000"                    # 43-50 data desconto 3
        + _fmt_valor15(0)               # 51-65 valor desconto 3
        + cod_multa                     # 66   código multa (2=%)
        + dt_multa                      # 67-74 data multa (ou 00000000)
        + _fmt_valor15(multa_pct)       # 75-89 valor/percentual multa
        + " " * 10                      # 90-99 informação sacado
        + " " * 40                      # 100-139 mensagem 3
        + " " * 40                      # 140-179 mensagem 4
        + "0" * 8                       # 180-187 nro do contrato
        + _fmt_valor15(0)               # 188-202 valor nominal do título
        + _fmt_valor15(0)               # 203-217 valor da multa
        + " " * 23                      # 218-240 brancos
    )
    _validar(linha, seq)
    return linha


# ---------------------------------------------------------------------------
# Trailer do Lote (tipo 5)
# ---------------------------------------------------------------------------

def trailer_lote(
    lote: int,
    qtd_registros: int,
    qtd_titulos: int,
    valor_total: float | Decimal,
) -> str:
    """CNAB240 Trailer Lote (tipo 5)."""
    linha = (
        BANCO_CODIGO                    # 1-3
        + _num(lote, 4)                 # 4-7
        + "5"                           # 8
        + " " * 9                       # 9-17 brancos
        + _num(qtd_registros, 6)        # 18-23 qtd registros no lote
        + _num(qtd_titulos, 6)          # 24-29 qtd títulos em cobrança simples
        + _fmt_valor15(valor_total)     # 30-44 valor cobrança simples
        + _num(0, 6)                    # 45-50 qtd títulos em vinculada
        + _fmt_valor15(0)               # 51-65 valor vinculada
        + _num(0, 6)                    # 66-71 qtd títulos descontada
        + _fmt_valor15(0)               # 72-86 valor descontada
        + _num(0, 6)                    # 87-92 qtd títulos vendor
        + _fmt_valor15(0)               # 93-107 valor vendor
        + "0" * 8                       # 108-115 aviso débito
        + " " * 117                     # 116-232 brancos
        + " " * 8                       # 233-240 brancos banco
    )
    _validar(linha, -1)
    return linha


# ---------------------------------------------------------------------------
# Trailer do Arquivo (tipo 9)
# ---------------------------------------------------------------------------

def trailer_arquivo(
    qtd_lotes: int,
    qtd_registros: int,
) -> str:
    """CNAB240 Trailer Arquivo (tipo 9)."""
    linha = (
        BANCO_CODIGO                    # 1-3
        + "9999"                        # 4-7
        + "9"                           # 8
        + " " * 9                       # 9-17
        + _num(qtd_lotes, 6)            # 18-23 qtd lotes
        + _num(qtd_registros, 6)        # 24-29 qtd registros total
        + _num(0, 6)                    # 30-35 qtd contas (para conciliação)
        + " " * 205                     # 36-240 brancos
    )
    _validar(linha, -2)
    return linha


# ---------------------------------------------------------------------------
# Gerador de arquivo completo
# ---------------------------------------------------------------------------

def gerar_arquivo_cnab240(
    boletos: Iterable[dict],
    seq_arquivo: int = 1,
    data_geracao: date | None = None,
) -> bytes:
    """
    Gera o arquivo CNAB240 completo.

    Cada item de `boletos` deve conter:
      billing_id, valor, vencimento (date|None),
      sacado_nome, sacado_cpf_cnpj,
      sacado_endereco?, sacado_bairro?, sacado_cep?,
      sacado_cidade?, sacado_estado?,
      mora_valor? (float), multa_pct? (float, percentual)
    """
    buf    = io.StringIO()
    lote   = 1
    seq    = 0        # sequencial global de registros

    lista_boletos = list(boletos)

    # Cabeçalho do arquivo
    buf.write(header_arquivo(data_geracao, seq_arquivo) + CRLF)
    seq += 1

    # Cabeçalho do lote
    buf.write(header_lote(lote, data_geracao) + CRLF)
    seq += 1
    seq_lote = 1          # sequencial dentro do lote
    registros_lote = 2    # header + trailer do lote
    valor_lote: float = 0.0
    qtd_titulos = 0

    for item in lista_boletos:
        valor_item = float(item.get("valor", 0))
        valor_lote += valor_item
        qtd_titulos += 1

        # Segmento P
        buf.write(segmento_p(
            lote=lote,
            seq=seq_lote,
            billing_id=item["billing_id"],
            valor=valor_item,
            vencimento=item.get("vencimento"),
            data_emissao=item.get("data_emissao"),
            mora_valor=item.get("mora_valor", 0.0),
        ) + CRLF)
        seq_lote += 1
        seq += 1
        registros_lote += 1

        # Segmento Q
        buf.write(segmento_q(
            lote=lote,
            seq=seq_lote,
            sacado_nome=item.get("sacado_nome", ""),
            sacado_cpf_cnpj=item.get("sacado_cpf_cnpj", ""),
            sacado_endereco=item.get("sacado_endereco", ""),
            sacado_bairro=item.get("sacado_bairro", ""),
            sacado_cep=item.get("sacado_cep", ""),
            sacado_cidade=item.get("sacado_cidade", ""),
            sacado_estado=item.get("sacado_estado", ""),
        ) + CRLF)
        seq_lote += 1
        seq += 1
        registros_lote += 1

        # Segmento R (apenas se houver multa)
        multa = item.get("multa_pct", 0.0)
        if multa and multa > 0:
            buf.write(segmento_r(lote=lote, seq=seq_lote, multa_pct=multa) + CRLF)
            seq_lote += 1
            seq += 1
            registros_lote += 1

    # Trailer do lote
    buf.write(trailer_lote(lote, registros_lote, qtd_titulos, valor_lote) + CRLF)
    seq += 1

    # Trailer do arquivo
    total_registros = seq + 1
    buf.write(trailer_arquivo(1, total_registros) + CRLF)

    return buf.getvalue().encode("latin-1", errors="replace")
