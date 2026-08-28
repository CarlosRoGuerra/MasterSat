"""
Fixtures e testes de caracterização para os geradores CNAB 240/400 (Ailos).

IMPORTANTE — isto não é um parser: `cnab240.py` e `cnab400.py` só GERAM arquivo
de remessa (o que o MasterSat manda pro banco). Não existe, em lugar nenhum do
backend, código que LEIA um arquivo CNAB de retorno — `ailos_retorno.py` baixa
o retorno da Ailos como ZIP via API REST e só armazena no MinIO, sem interpretar
o conteúdo (a reconciliação de pagamentos usa outra via: consulta REST por
boleto, `conciliar_boletos_abertos`). Por isso não há "código de ocorrência"
para testar — esse conceito é de arquivo de RETORNO, que não é parseado aqui.

Os casos pedidos (registro inválido / valor inválido / data inválida /
ocorrência desconhecida / arquivo truncado) foram adaptados para o que
realmente existe — um GERADOR — e documentam o comportamento ATUAL, incluindo
lacunas de validação que valem correção futura (ver comentários em cada teste).
Nenhuma alteração foi feita em cnab240.py/cnab400.py.
"""
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
) | {"sacado_cpf_cnpj": "14.228.344/0001-67"}  # 14 dígitos → tipo inscrição = CNPJ

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
    # Identificadores: nosso número = billing_id (9) + DV (1) na posição 39-48 (1-based)
    assert linhas[1][38:48] == str(ITEM_VALIDO_PF["billing_id"]).zfill(9) + cnab400.modulo11_nosso_numero(
        str(ITEM_VALIDO_PF["billing_id"]).zfill(9)
    )
    # Data vencimento DDMMAAAA na posição 63-70
    assert linhas[1][62:70] == "10092026"
    # Valor em centavos (13 chars) na posição 71-83: R$150,75 = 15075 centavos
    assert linhas[1][70:83] == "0000000015075"


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
    assert int(trailer[1:6]) == len(MULTIPLOS_REGISTROS)


def test_cnab240_multiplos_registros_totaliza_valor_do_lote():
    arquivo = cnab240.gerar_arquivo_cnab240(MULTIPLOS_REGISTROS)
    linhas = [l for l in arquivo.decode("latin-1").split(cnab240.CRLF) if l]
    trailer_lote = next(l for l in linhas if l[7] == "5")
    valor_total_centavos = int(trailer_lote[29:44])
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


def test_cnab240_valor_ausente_e_tratado_como_zero_sem_erro():
    """Achado: diferente de `billing_id` (acesso por colchete → KeyError), o
    CNAB240 lê `valor` com `.get("valor", 0)` (cnab240.py:430) — um item sem
    valor não falha, gera um título de R$ 0,00 silenciosamente. Documenta o
    comportamento atual; não foi alterado."""
    item_sem_valor = {k: v for k, v in ITEM_VALIDO_PF.items() if k != "valor"}
    arquivo = cnab240.gerar_arquivo_cnab240([item_sem_valor])
    linhas = [l for l in arquivo.decode("latin-1").split(cnab240.CRLF) if l]
    seg_p = next(l for l in linhas if l[13] == "P")
    assert seg_p[85:100] == "0" * 15  # campo valor do título (86-100) zerado


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


def test_cnab400_valor_negativo_produz_campo_numerico_corrompido():
    """Achado: valor negativo não é rejeitado. `_fmt_valor_cnab` faz
    `str(round(v * 100)).zfill(13)` — para negativo, o sinal '-' entra no meio
    do campo (que o banco espera 100% numérico), gerando uma linha que o banco
    provavelmente rejeitaria ou interpretaria errado. Documenta o bug atual;
    não foi corrigido aqui."""
    item = {**ITEM_VALIDO_PF, "valor": -50.0}
    arquivo = cnab400.gerar_arquivo_cnab400([item])
    linhas = [l for l in arquivo.decode("latin-1").split(cnab400.CRLF) if l]
    campo_valor = linhas[1][70:83]
    assert not campo_valor.isdigit(), (
        f"Campo valor {campo_valor!r} deveria ser puramente numérico (13 dígitos) "
        "para o banco aceitar — o sinal negativo o corrompe."
    )
    assert "-" in campo_valor


# ---------------------------------------------------------------------------
# 5) Data inválida
# ---------------------------------------------------------------------------

def test_cnab400_vencimento_none_vira_zeros():
    item = {**ITEM_VALIDO_PF, "vencimento": None}
    arquivo = cnab400.gerar_arquivo_cnab400([item])
    linhas = [l for l in arquivo.decode("latin-1").split(cnab400.CRLF) if l]
    assert linhas[1][62:70] == "00000000"


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
    assert seg_p[128:136] == date.today().strftime("%d%m%Y")


# ---------------------------------------------------------------------------
# 6) "Ocorrência desconhecida" — não existe no domínio de remessa; o análogo
#    mais próximo são os códigos de instrução do CNAB400, que não são
#    validados contra uma lista fechada.
# ---------------------------------------------------------------------------

def test_cnab400_codigo_instrucao_desconhecido_e_aceito_sem_validacao():
    """Achado: `instrucao1`/`instrucao2` (posições 101-104) aceitam qualquer
    string de 2 chars, sem checagem contra a tabela de instruções do manual
    Ailos. Um código inválido (ex.: '99', que não consta no manual) é
    silenciosamente aceito e zero-preenchido — não há como um "código de
    ocorrência desconhecido" ser rejeitado hoje."""
    linha = cnab400.detalhe(
        billing_id=ITEM_VALIDO_PF["billing_id"],
        valor=ITEM_VALIDO_PF["valor"],
        vencimento=ITEM_VALIDO_PF["vencimento"],
        sacado_nome=ITEM_VALIDO_PF["sacado_nome"],
        sacado_cpf_cnpj=ITEM_VALIDO_PF["sacado_cpf_cnpj"],
        instrucao1="99",  # não existe no manual — aceito do mesmo jeito
        instrucao2="ZZ",  # nem é numérico — também aceito
    )
    assert len(linha) == 400
    assert linha[100:102] == "99"
    assert linha[102:104] == "ZZ"


# ---------------------------------------------------------------------------
# 7) Arquivo vazio
# ---------------------------------------------------------------------------

def test_cnab400_arquivo_vazio_gera_so_header_e_trailer():
    arquivo = cnab400.gerar_arquivo_cnab400([])
    linhas = [l for l in arquivo.decode("latin-1").split(cnab400.CRLF) if l]
    assert len(linhas) == 2
    assert linhas[0][0] == "0"
    assert linhas[1][0] == "9"
    assert int(linhas[1][1:6]) == 0  # quantidade de registros de detalhe = 0


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
    assert linhas[1][172:212] == "A" * 40  # campo nome (40 chars) truncado corretamente


def test_cnab240_validar_rejeita_linha_fora_do_tamanho_contratual():
    """`_validar` é a única guarda contra uma linha sair truncada/maior que o
    contratado — reproduz o disparo direto dessa guarda."""
    with pytest.raises(ValueError, match=r"240"):
        cnab240._validar("linha muito curta", 1)


def test_cnab400_validar_rejeita_linha_fora_do_tamanho_contratual():
    with pytest.raises(ValueError, match=r"400"):
        cnab400._validar("x" * 399, 1)
