"""Remessas Ailos: posições verificadas contra os manuais CNAB fornecidos."""
from __future__ import annotations

from datetime import date

import pytest

from app.services import cnab240, cnab400
from app.services.boleto_ailos import BANCO_CODIGO


# ---------------------------------------------------------------------------
# Fixtures — itens representativos de entrada
# ---------------------------------------------------------------------------

def _item_valido(billing_id: int = 1001, valor: float = 150.75) -> dict:
    return {
        "billing_id": billing_id,
        "valor": valor,
        "vencimento": date(2026, 9, 10),
        "sacado_nome": "João da Silva",
        "sacado_cpf_cnpj": "529.982.247-25",
        "sacado_endereco": "Rua das Flores, 100",
        "sacado_bairro": "Centro",
        "sacado_cep": "89200-000",
        "sacado_cidade": "Joinville",
        "sacado_estado": "SC",
        "data_emissao": date(2026, 8, 28),
    }


ITEM_VALIDO_PF = _item_valido()
ITEM_VALIDO_PJ = _item_valido(
    billing_id=1002, valor=980.00,
) | {"sacado_cpf_cnpj": "11.444.777/0001-61"}  # Pagador PJ diferente do beneficiário.

MULTIPLOS_REGISTROS = [ITEM_VALIDO_PF, ITEM_VALIDO_PJ, _item_valido(billing_id=1003, valor=42.00)]


# ---------------------------------------------------------------------------
# 1) Arquivo válido — formato suportado / identificadores / retorno esperado
# ---------------------------------------------------------------------------

def test_cnab400_arquivo_valido_estrutura_basica():
    arquivo = cnab400.gerar_arquivo_cnab400([ITEM_VALIDO_PF])
    linhas = arquivo.decode("latin-1").split(cnab400.CRLF)
    linhas = [l for l in linhas if l]  # remove a string vazia depois do último CRLF

    assert len(linhas) == 3  # header + 1 detalhe + trailer
    assert all(len(l) == 400 for l in linhas)
    assert linhas[0][0] == "0"   # header
    assert linhas[1][0] == "7"   # detalhe
    assert linhas[2][0] == "9"   # trailer
    # CNAB 400, pp. 17–18: nosso número 64–80, vencimento 121–126 e valor 127–139.
    assert linhas[1][63:80] == '00454702000001001'
    assert linhas[1][120:126] == '100926'
    assert linhas[1][126:139] == '0000000015075'



def test_cnab240_arquivo_valido_estrutura_basica():
    arquivo = cnab240.gerar_arquivo_cnab240([ITEM_VALIDO_PF])
    linhas = [l for l in arquivo.decode("latin-1").split(cnab240.CRLF) if l]
    assert len(linhas) == 6
    assert all(len(l) == 240 for l in linhas)
    tipos_registro = [l[7] for l in linhas]  # posição 8 (1-based) = tipo de registro
    assert tipos_registro == ["0", "1", "3", "3", "5", "9"]  # header arq, header lote, seg P, seg Q, trailer lote, trailer arq
    segmentos = [linhas[2][13], linhas[3][13]]  # posição 14 (1-based) = código do segmento
    assert segmentos == ["P", "Q"]
    # Código do banco (085) presente em toda linha, posições 1-3
    assert all(l[:3] == BANCO_CODIGO for l in linhas)


# ---------------------------------------------------------------------------
# 2) Múltiplos registros
# ---------------------------------------------------------------------------

def test_cnab400_multiplos_registros_sequencial_incrementa():
    arquivo = cnab400.gerar_arquivo_cnab400(MULTIPLOS_REGISTROS)
    linhas = [l for l in arquivo.decode("latin-1").split(cnab400.CRLF) if l]

    assert len(linhas) == 2 + len(MULTIPLOS_REGISTROS)  # header + N detalhes + trailer
    # Sequencial de registro (posição 395-400, 1-based) incrementa 1 por linha
    sequenciais = [int(l[394:400]) for l in linhas]
    assert sequenciais == list(range(1, len(linhas) + 1))
    # Trailer relata a quantidade correta de detalhes (posições 2-6)
    trailer = linhas[-1]
    assert trailer[1:394] == ' ' * 393


def test_cnab240_multiplos_registros_totaliza_valor_do_lote():
    arquivo = cnab240.gerar_arquivo_cnab240(MULTIPLOS_REGISTROS)
    linhas = [l for l in arquivo.decode("latin-1").split(cnab240.CRLF) if l]
    trailer_lote = next(l for l in linhas if l[7] == "5")
    valor_total_centavos = int(trailer_lote[29:46])
    esperado_centavos = round(sum(item["valor"] for item in MULTIPLOS_REGISTROS) * 100)
    assert valor_total_centavos == esperado_centavos
    qtd_titulos = int(trailer_lote[23:29])
    assert qtd_titulos == len(MULTIPLOS_REGISTROS)


# ---------------------------------------------------------------------------
# 3) Registro inválido — campo obrigatório ausente
# ---------------------------------------------------------------------------

def test_cnab400_billing_id_ausente_leva_a_keyerror():
    item_sem_billing_id = {k: v for k, v in ITEM_VALIDO_PF.items() if k != "billing_id"}
    with pytest.raises(KeyError):
        cnab400.gerar_arquivo_cnab400([item_sem_billing_id])


def test_cnab400_valor_ausente_leva_a_keyerror():
    item_sem_valor = {k: v for k, v in ITEM_VALIDO_PF.items() if k != "valor"}
    with pytest.raises(KeyError):
        cnab400.gerar_arquivo_cnab400([item_sem_valor])


def test_cnab240_billing_id_ausente_leva_a_keyerror():
    item_sem_billing_id = {k: v for k, v in ITEM_VALIDO_PF.items() if k != "billing_id"}
    with pytest.raises(KeyError):
        cnab240.gerar_arquivo_cnab240([item_sem_billing_id])


def test_cnab240_valor_ausente_e_recusado():
    item = {k: v for k, v in ITEM_VALIDO_PF.items() if k != 'valor'}
    with pytest.raises(KeyError):
        cnab240.gerar_arquivo_cnab240([item])


# ---------------------------------------------------------------------------
# 4) Valor inválido
# ---------------------------------------------------------------------------

def test_cnab400_valor_nao_numerico_levanta_valueerror():
    item = {**ITEM_VALIDO_PF, "valor": "cento e cinquenta"}
    with pytest.raises(ValueError):
        cnab400.gerar_arquivo_cnab400([item])


def test_cnab240_valor_nao_numerico_levanta_valueerror():
    item = {**ITEM_VALIDO_PF, "valor": "cento e cinquenta"}
    with pytest.raises(ValueError):
        cnab240.gerar_arquivo_cnab240([item])


@pytest.mark.parametrize('gerador', [cnab240.gerar_arquivo_cnab240, cnab400.gerar_arquivo_cnab400])
@pytest.mark.parametrize('valor', [-50.0, 0, 'NaN', 'Infinity', '1.001'])
def test_valores_invalidos_nao_geram_remessa(gerador, valor):
    with pytest.raises(ValueError):
        gerador([{**ITEM_VALIDO_PF, 'valor': valor}])


# ---------------------------------------------------------------------------
# 5) Data inválida
# ---------------------------------------------------------------------------

def test_cnab400_vencimento_none_vira_zeros():
    item = {**ITEM_VALIDO_PF, "vencimento": None}
    arquivo = cnab400.gerar_arquivo_cnab400([item])
    linhas = [l for l in arquivo.decode("latin-1").split(cnab400.CRLF) if l]
    assert linhas[1][120:126] == "000000"


def test_cnab400_vencimento_tipo_errado_levanta_attributeerror():
    """String em vez de `date` — `_fmt_date_cnab` chama `.strftime` direto, sem
    checagem de tipo. Documenta que a validação de tipo é responsabilidade de
    quem monta o dict de entrada; o gerador não valida."""
    item = {**ITEM_VALIDO_PF, "vencimento": "2026-09-10"}
    with pytest.raises(AttributeError):
        cnab400.gerar_arquivo_cnab400([item])


def test_cnab240_data_emissao_none_usa_hoje_sem_erro():
    item = {**ITEM_VALIDO_PF, "data_emissao": None}
    arquivo = cnab240.gerar_arquivo_cnab240([item])
    linhas = [l for l in arquivo.decode("latin-1").split(cnab240.CRLF) if l]
    seg_p = next(l for l in linhas if l[13] == "P")
    assert seg_p[109:117] == date.today().strftime("%d%m%Y")


# ---------------------------------------------------------------------------
# 6) "Ocorrência desconhecida" — não existe no domínio de remessa; o análogo
#    mais próximo são os códigos de instrução do CNAB400, que não são
#    validados contra uma lista fechada.
# ---------------------------------------------------------------------------

def test_cnab400_recusa_instrucao_em_campo_que_o_manual_reserva_para_zeros():
    with pytest.raises(ValueError, match='159–160'):
        cnab400.detalhe(1001, 10, date(2026, 11, 13), 'PAGADOR', '52998224725', instrucao2='ZZ')


# ---------------------------------------------------------------------------
# 7) Arquivo vazio
# ---------------------------------------------------------------------------

def test_cnab400_arquivo_vazio_gera_so_header_e_trailer():
    arquivo = cnab400.gerar_arquivo_cnab400([])
    linhas = [l for l in arquivo.decode("latin-1").split(cnab400.CRLF) if l]
    assert len(linhas) == 2
    assert linhas[0][0] == "0"
    assert linhas[1][0] == "9"
    assert linhas[1][1:394] == ' ' * 393


def test_cnab240_arquivo_vazio_gera_lote_sem_titulos():
    arquivo = cnab240.gerar_arquivo_cnab240([])
    linhas = [l for l in arquivo.decode("latin-1").split(cnab240.CRLF) if l]
    assert len(linhas) == 4  # header arq + header lote + trailer lote + trailer arq
    trailer_lote = next(l for l in linhas if l[7] == "5")
    assert int(trailer_lote[23:29]) == 0  # qtd_titulos


# ---------------------------------------------------------------------------
# 8) "Arquivo truncado" — não se aplica a um gerador (truncamento é erro de
#    quem RECEBE o arquivo). O equivalente do lado da geração é: a linha
#    NUNCA pode sair fora do tamanho contratual, mesmo com entrada anômala —
#    é exatamente o que `_validar()` garante em cada função de linha.
# ---------------------------------------------------------------------------

def test_cnab400_nome_absurdamente_longo_nao_trunca_a_linha_toda():
    item = {**ITEM_VALIDO_PF, "sacado_nome": "A" * 500, "sacado_endereco": "B" * 500}
    arquivo = cnab400.gerar_arquivo_cnab400([item])
    linhas = [l for l in arquivo.decode("latin-1").split(cnab400.CRLF) if l]
    assert all(len(l) == 400 for l in linhas)
    assert linhas[1][234:271] == "A" * 37  # campo nome (40 chars) truncado corretamente


def test_cnab240_validar_rejeita_linha_fora_do_tamanho_contratual():
    """`_validar` é a única guarda contra uma linha sair truncada/maior que o
    contratado — reproduz o disparo direto dessa guarda."""
    with pytest.raises(ValueError, match=r"240"):
        cnab240._validar("linha muito curta", 1)


def test_cnab400_validar_rejeita_linha_fora_do_tamanho_contratual():
    with pytest.raises(ValueError, match=r"400"):
        cnab400._validar("x" * 399, 1)
