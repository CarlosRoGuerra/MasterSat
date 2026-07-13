"""
Serviço de boleto bancário — Banco Ailos (085-0)

Dados do beneficiário (MASTERSAT):
  Banco:              085-0 (Ailos Cooperativa)
  Agência:            0102-3
  Carteira:           01
  Variação carteira:  001
  Convênio:           102004
  CNPJ:               14.228.344/0001-67
  Código cedente:     0045470-2

Referência técnica:
  - Manual CNAB 400/240 Ailos (pasta api/)
  - FEBRABAN: Padrão de Arquivos de Intercâmbio
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

# ---------------------------------------------------------------------------
# Configuração estática do beneficiário
# ---------------------------------------------------------------------------

BANCO_CODIGO    = "085"
BANCO_NOME      = "AILOS"
MOEDA           = "9"           # 9 = Real (BRL)

AGENCIA         = "0102"        # sem dígito
AGENCIA_DIG     = "3"

CARTEIRA        = "01"
VARIACAO        = "001"
CONVENIO        = "102004"

CEDENTE_CNPJ    = "14228344000167"
CEDENTE_NOME    = "MASTERSAT RASTREAMENTO"
CEDENTE_COD     = "0045470"     # sem dígito
CEDENTE_COD_DIG = "2"

# Data-base para cálculo do fator de vencimento (07/10/1997)
DATA_BASE_FATOR = date(1997, 10, 7)
FATOR_MAX_DIAS  = 9999          # cicla depois de ~27 anos


# ---------------------------------------------------------------------------
# Algoritmos de verificação
# ---------------------------------------------------------------------------

def modulo10(numero: str) -> str:
    """Dígito verificador Módulo 10 (usado nos 3 campos da linha digitável)."""
    total = 0
    mult = 2
    for d in reversed(numero):
        resultado = int(d) * mult
        if resultado > 9:
            resultado = resultado // 10 + resultado % 10
        total += resultado
        mult = 1 if mult == 2 else 2
    resto = total % 10
    return "0" if resto == 0 else str(10 - resto)


def modulo11_barcode(codigo: str) -> str:
    """
    Dígito verificador Módulo 11 do código de barras (posição 5).
    Multiplica da direita para esquerda com pesos 2..9 ciclando.
    Se resto = 0 ou 1 → dígito = 1.
    """
    pesos = [2, 3, 4, 5, 6, 7, 8, 9]
    total = 0
    for i, d in enumerate(reversed(codigo)):
        total += int(d) * pesos[i % 8]
    resto = total % 11
    return "1" if resto in (0, 1) else str(11 - resto)


def modulo11_nosso_numero(numero: str) -> str:
    """
    Dígito verificador do Nosso Número (Módulo 11, pesos 2..7).
    Se resto = 0 → dígito = 0; resto = 1 → dígito = 1.
    """
    pesos = [2, 3, 4, 5, 6, 7]
    total = 0
    for i, d in enumerate(reversed(numero)):
        total += int(d) * pesos[i % 6]
    resto = total % 11
    if resto in (0, 1):
        return "0"
    return str(11 - resto)


# ---------------------------------------------------------------------------
# Geração de campos
# ---------------------------------------------------------------------------

def fator_vencimento(vencimento: date | None) -> str:
    """
    Fator de vencimento (4 dígitos), conforme regra Ailos/FEBRABAN:

    Ciclo antigo  (até 21/02/2025): dias desde 07/10/1997 (max 9999)
    Ciclo novo    (a partir de 22/02/2025): 1000 + dias desde 22/02/2025

    Boleto sem vencimento → '0000' (à vista).
    """
    if vencimento is None:
        return "0000"

    # Nova base: 22/02/2025, fator recomeça em 1000 (manual Ailos)
    NOVA_BASE        = date(2025, 2, 22)
    FATOR_NOVA_BASE  = 1000

    if vencimento >= NOVA_BASE:
        # Novo ciclo: 1000 + dias corridos desde 22/02/2025
        fator = FATOR_NOVA_BASE + (vencimento - NOVA_BASE).days
    else:
        # Ciclo antigo: dias desde 07/10/1997
        delta = (vencimento - DATA_BASE_FATOR).days
        if delta < 1:
            return "0000"
        fator = delta % FATOR_MAX_DIAS
        if fator == 0:
            fator = FATOR_MAX_DIAS

    return str(fator).zfill(4)


def formatar_valor(valor: float | Decimal) -> str:
    """Valor sem vírgula/ponto, 10 dígitos (centavos)."""
    centavos = round(float(valor) * 100)
    return str(centavos).zfill(10)


def gerar_nosso_numero(billing_id: int) -> tuple[str, str]:
    """
    Nosso Número = conta_corrente(8 dígitos) + sequencial(9 dígitos) = 17 dígitos.
    Conta corrente = CEDENTE_COD (7) + CEDENTE_COD_DIG (1) = "00454702" (8 dígitos).
    Check digit = Módulo 11 aplicado aos 17 dígitos completos.

    Exemplo: conta=00454702, seq=000000003 → nn="00454702000000003"
    """
    conta = (CEDENTE_COD + CEDENTE_COD_DIG).zfill(8)   # "00454702"
    seq   = str(billing_id).zfill(9)                    # "000000003"
    nn    = conta + seq                                  # "00454702000000003" (17 dígitos)
    dv    = modulo11_nosso_numero(nn)
    return nn, dv


def gerar_campo_livre(nosso_numero: str) -> str:
    """
    Campo livre Ailos (25 dígitos) — estrutura confirmada pelo manual:

      Convênio        : 6 dígitos  → "102004"
      Conta corrente  : 8 dígitos  → cedente(7) + dígito(1) = "00454702"
      Nosso número    : 9 dígitos  → parte sequencial do nosso número
      Carteira        : 2 dígitos  → "01"
      Total: 6 + 8 + 9 + 2 = 25

    Linha digitável esperada para billing_id=3, venc=28/06/2026:
      08591.02006 40045.470206 00000.003012 5 14910000010000
    """
    convenio = CONVENIO.zfill(6)                         # "102004"
    conta    = (CEDENTE_COD + CEDENTE_COD_DIG).zfill(8) # "00454702"
    # Extrai só o sequencial (últimos 9 dígitos do nosso número de 17)
    seq      = nosso_numero[-9:].zfill(9) if len(nosso_numero) > 9 else nosso_numero.zfill(9)
    carteira = CARTEIRA.zfill(2)                         # "01"

    campo = convenio + conta + seq + carteira
    assert len(campo) == 25, f"Campo livre inválido: {len(campo)} chars"
    return campo


def gerar_codigo_barras(
    nosso_numero: str,
    vencimento: date | None,
    valor: float | Decimal,
) -> str:
    """
    Código de barras completo com 44 posições.

    Estrutura:
      [1-3]  Código do banco        085
      [4]    Moeda                   9
      [5]    Dígito verificador      (calculado)
      [6-9]  Fator de vencimento    FFFF
      [10-19] Valor                  VVVVVVVVVV
      [20-44] Campo livre            25 dígitos
    """
    campo_livre = gerar_campo_livre(nosso_numero)
    fator       = fator_vencimento(vencimento)
    val_fmt     = formatar_valor(valor)

    # Barcode sem o dígito verificador (posição 5)
    barcode_sem_dv = (
        BANCO_CODIGO   # 085
        + MOEDA        # 9
        + fator        # FFFF
        + val_fmt      # VVVVVVVVVV
        + campo_livre  # 25 chars
    )
    assert len(barcode_sem_dv) == 43

    dv = modulo11_barcode(barcode_sem_dv)

    # Insere o DV na posição 5 (índice 4)
    codigo = barcode_sem_dv[:4] + dv + barcode_sem_dv[4:]
    assert len(codigo) == 44
    return codigo


def gerar_linha_digitavel(codigo_barras: str) -> str:
    """
    Linha digitável (47 dígitos + separadores) a partir do código de barras.

    Formato: BBBMC.CCCCCD  DDDDD.DDDDDD  EEEEE.EEEEEE  K  GGGGGGGGGGGGGG
    Onde:
      B = banco (3), M = moeda (1), C = campo_livre[0:5], D = mod10 check
      campo_livre segmentos restantes
      K = dígito verificador do código de barras
      G = fator vencimento (4) + valor (10) = 14 dígitos
    """
    banco       = codigo_barras[0:3]   # 085
    moeda       = codigo_barras[3]     # 9
    dv_barcode  = codigo_barras[4]     # dígito verificador
    fator_val   = codigo_barras[5:19]  # 4 (fator) + 10 (valor) = 14 chars
    campo_livre = codigo_barras[19:]   # 25 chars

    # Bloco 1: banco(3) + moeda(1) + campo_livre[0:5](5) + mod10
    # Padrão Ailos: ponto após posição 5 → "BBBMC.CCCCD"
    #   Ex: "08591.01008"  (banco=085, moeda=9, CL[0]=1, CL[1:5]=0100, DV=8)
    b1_digits = banco + moeda + campo_livre[0:5]
    b1_dv     = modulo10(b1_digits)
    bloco1    = b1_digits[:5] + "." + b1_digits[5:] + b1_dv
    # → "08590.XXXXD" (11 chars: 5 + ponto + 4 + dv)

    # Bloco 2: campo_livre[5:15](10) + mod10
    b2_digits = campo_livre[5:15]
    b2_dv     = modulo10(b2_digits)
    bloco2    = b2_digits[:5] + "." + b2_digits[5:] + b2_dv
    # → "XXXXX.XXXXXD" (12 chars)

    # Bloco 3: campo_livre[15:25](10) + mod10
    b3_digits = campo_livre[15:25]
    b3_dv     = modulo10(b3_digits)
    bloco3    = b3_digits[:5] + "." + b3_digits[5:] + b3_dv
    # → "XXXXX.XXXXXD" (12 chars)

    return f"{bloco1}  {bloco2}  {bloco3}  {dv_barcode}  {fator_val}"


# ---------------------------------------------------------------------------
# Dataclass de saída
# ---------------------------------------------------------------------------

@dataclass
class DadosBoleto:
    """Todos os dados necessários para renderizar o boleto."""
    # Identificadores
    billing_id: int
    nosso_numero: str           # 9 dígitos
    nosso_numero_dv: str        # 1 dígito
    nosso_numero_display: str   # "NNNNNNNN-D"

    # Datas e valores
    data_emissao: date
    data_vencimento: date | None
    valor: Decimal

    # Strings calculadas
    codigo_barras: str          # 44 dígitos
    linha_digitavel: str        # formato com pontos e espaços

    # Dados do sacado (pagador)
    sacado_nome: str
    sacado_cpf_cnpj: str
    sacado_endereco: str

    # Dados do beneficiário (cedente)
    cedente_nome: str = CEDENTE_NOME
    cedente_cnpj: str = CEDENTE_CNPJ
    cedente_agencia: str = f"{AGENCIA}-{AGENCIA_DIG}"
    cedente_codigo: str = f"{CEDENTE_COD}-{CEDENTE_COD_DIG}"
    cedente_convenio: str = CONVENIO
    carteira: str = f"{CARTEIRA}/{VARIACAO}"
    banco_codigo: str = BANCO_CODIGO
    banco_nome: str = BANCO_NOME
    especie: str = "R$"
    aceite: str = "N"
    instrucoes: list[str] | None = None
    pix_emv: str | None = None         # EMV/BR Code do Pix → gera QR vetorial nítido
    pix_qr_base64: str | None = None   # imagem PNG/JPEG do QR (fallback se não houver EMV)
    # Dados extras do sacado exibidos no Recibo de Pagamento (parte superior)
    sacado_cidade: str = ""
    sacado_cep: str = ""
    sacado_uf: str = ""
    sacado_ie: str = ""
    # Itens da tabela do recibo: [(descrição, valor)]; None → 1 item padrão
    itens: list[tuple[str, float]] | None = None


def gerar_dados_boleto(
    billing_id: int,
    valor: float | Decimal,
    vencimento: date | None,
    sacado_nome: str,
    sacado_cpf_cnpj: str,
    sacado_endereco: str = "",
    data_emissao: date | None = None,
    instrucoes: list[str] | None = None,
    sacado_cidade: str = "",
    sacado_cep: str = "",
    sacado_uf: str = "",
    sacado_ie: str = "",
    itens: list[tuple[str, float]] | None = None,
) -> DadosBoleto:
    """
    Gera todos os dados calculados do boleto a partir das informações do billing.
    """
    nn, nn_dv = gerar_nosso_numero(billing_id)
    barcode   = gerar_codigo_barras(nn, vencimento, valor)
    linha_dig = gerar_linha_digitavel(barcode)

    return DadosBoleto(
        billing_id=billing_id,
        nosso_numero=nn,
        nosso_numero_dv=nn_dv,
        # Display: 17 dígitos limpos (sem DV visual) conforme manual Ailos
        # DV calculado internamente mas não exibido no PDF/campo visual
        nosso_numero_display=nn,
        data_emissao=data_emissao or date.today(),
        data_vencimento=vencimento,
        valor=Decimal(str(valor)),
        codigo_barras=barcode,
        linha_digitavel=linha_dig,
        sacado_nome=sacado_nome,
        sacado_cpf_cnpj=sacado_cpf_cnpj,
        sacado_endereco=sacado_endereco,
        sacado_cidade=sacado_cidade,
        sacado_cep=sacado_cep,
        sacado_uf=sacado_uf,
        sacado_ie=sacado_ie,
        itens=itens,
        instrucoes=instrucoes or [
            "Não receber após vencimento.",
            "Em caso de dúvidas entre em contato com MASTERSAT.",
        ],
    )
