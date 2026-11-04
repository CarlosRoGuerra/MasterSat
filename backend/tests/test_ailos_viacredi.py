"""Parametrização Viacredi e campos dos manuais CNAB 240/400 fornecidos."""
from datetime import date

from app.models.ailos_boleto import AilosBoleto
from app.services import boleto_ailos, cnab240, cnab400
from app.services.ailos_boletos import aplicar_dados_oficiais_ailos


ITEM = dict(billing_id=380, valor=10, vencimento=date(2026, 11, 13),
            data_emissao=date(2026, 11, 3), sacado_nome='PAGADOR DE HOMOLOGAÇÃO',
            sacado_cpf_cnpj='52998224725', sacado_endereco='RUA DE TESTE 100',
            sacado_bairro='CENTRO', sacado_cidade='JOINVILLE', sacado_estado='SC', sacado_cep='89200000')


def test_boleto_novo_usa_agencia_convenio_e_razao_social_da_viacredi():
    d = boleto_ailos.gerar_dados_boleto(380, 10, ITEM['vencimento'], 'PAGADOR', '52998224725')
    assert d.cedente_agencia == '0101-5'
    assert d.cedente_convenio == '101004'
    assert d.cedente_nome == 'MASTERSAT COMERCIO E SERVICO DE RASTREADORES LTDA'
    assert d.codigo_barras[19:25] == '101004'
    assert d.codigo_barras[25:33] == '00454702'
    assert d.codigo_barras[42:44] == '01'


def test_reimpressao_preserva_identidade_e_codigo_oficiais_do_titulo_antigo():
    d = boleto_ailos.gerar_dados_boleto(3, 100, date(2026, 6, 28), 'PAGADOR', '52998224725')
    # Vetor do boleto anterior à incorporação: campo livre contém o convênio 102004.
    codigo = '08595149100000100001020040045470200000000301'
    antigo = AilosBoleto(codigo_barras=codigo, linha_digitavel='LINHA OFICIAL ANTIGA', nosso_numero='00454702000000003')
    impresso = aplicar_dados_oficiais_ailos(d, antigo)
    assert impresso.codigo_barras == codigo
    assert impresso.linha_digitavel == 'LINHA OFICIAL ANTIGA'
    assert impresso.cedente_agencia == '0102-3'
    assert impresso.cedente_convenio == '102004'


def test_cnab400_posicoes_do_manual_pp16_a18_com_nova_parametrizacao():
    h, d, t = cnab400.gerar_arquivo_cnab400([ITEM], data_geracao=date(2026, 11, 3), seq_arquivo=12).decode().splitlines()
    assert [len(l) for l in (h, d, t)] == [400, 400, 400]
    assert h[26:31] == '01015'
    assert h[31:40] == '000454702'
    assert h[129:136] == '0101004'
    assert h[100:107] == '0000012'
    assert d[1:17] == '0214228344000167'  # Beneficiário, não o pagador.
    assert d[17:22] == '01015'
    assert d[22:31] == '000454702'
    assert d[31:38] == '0101004'
    assert d[63:80] == '00454702000000380'
    assert d[91:94] == '001' and d[106:110] == '0101'
    assert d[120:126] == '131126' and d[126:139] == '0000000001000'
    assert d[218:234] == '0100052998224725'
    assert d[234:271].rstrip() == 'PAGADOR DE HOMOLOGACAO'
    assert t[1:394] == ' ' * 393 and t[394:400] == '000003'


def test_cnab240_posicoes_do_manual_pp15_a23_e_totais_em_centavos():
    h, lote, p, q, t, fim = cnab240.gerar_arquivo_cnab240([ITEM], seq_arquivo=12, data_geracao=date(2026, 11, 3)).decode().splitlines()
    assert all(len(l) == 240 for l in (h, lote, p, q, t, fim))
    assert h[32:52].rstrip() == '101004'
    assert h[52:58] == '001015' and h[58:71] == '0000000454702'
    assert h[142:151] == '103112026'
    assert h[157:163] == '000012' and h[163:166] == '084'
    assert lote[13:16] == '043' and lote[33:53].rstrip() == '101004'
    assert lote[53:59] == '001015' and lote[59:72] == '0000000454702'
    assert lote[183:191] == '00000012'
    assert p[17:23] == '001015' and p[23:36] == '0000000454702'
    assert p[37:57] == '00454702000000380   '
    assert p[57:62] == '11122'
    assert p[77:85] == '13112026' and p[85:100] == '000000000001000'
    assert p[117] == '3' and p[220] == '3' and p[223] == '2'  # Isento; sem protesto; sem baixa automática própria.
    # Instruções particulares do PDF Dicas de homologação, p. 2.
    assert p[141] == '2' and p[224:227] == '000'
    assert q[18:33] == '000052998224725'
    assert q[33:73].rstrip() == 'PAGADOR DE HOMOLOGACAO'
    assert t[17:23] == '000004' and t[23:29] == '000001'
    assert t[29:46] == '00000000000001000'
    assert fim[17:29] == '000001000006'
