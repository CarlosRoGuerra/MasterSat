"""Testes da DPS do Emissor Nacional, validada contra o XSD oficial v1.01."""
from __future__ import annotations

import base64
import gzip
from decimal import Decimal
from types import SimpleNamespace

import pytest
from lxml import etree

from app.core.config import settings
from app.services.nfse_nacional import (
    NS_NFSE,
    NfseError,
    _compactar,
    _descompactar,
    id_dps,
    montar_dps,
    validar_dps,
)


def _client(**kw):
    base = dict(
        name='CLIENTE TESTE LTDA', cpf_cnpj='00000000000191', type='pj',
        email='a@b.com', phone='4730000000', zip_code='89201100',
        address_line='RUA DO PRINCIPE', address_number='100', address_complement='',
        neighborhood='CENTRO', city='JOINVILLE', state='SC', city_ibge_code='4209102',
        optante_simples=None, iss_retido=None, issue_invoice=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _billing(**kw):
    base = dict(id=1, title='MENSALIDADE MONITORAMENTO', amount=Decimal('120.00'))
    base.update(kw)
    return SimpleNamespace(**base)


def _q(no, *tags):
    """Busca por caminho de tags no namespace nacional."""
    caminho = '/'.join(f'{{{NS_NFSE}}}{t}' for t in tags)
    return no.find(caminho)


# --------------------------------------------------------------------------
# Conformidade com o esquema oficial
# --------------------------------------------------------------------------

def test_dps_valida_contra_xsd_oficial():
    """O teste que importa: a DPS bate com o DPS_v1.01.xsd publicado pelo governo."""
    validar_dps(montar_dps(_billing(), _client(), '1'))


def test_dps_valida_para_tomador_pessoa_fisica():
    validar_dps(montar_dps(_billing(), _client(type='pf', cpf_cnpj='11144477735'), '2'))


def test_dps_valida_com_complemento_de_endereco():
    validar_dps(montar_dps(_billing(), _client(address_complement='SALA 3'), '3'))


def test_dps_valida_sem_telefone_e_sem_email():
    validar_dps(montar_dps(_billing(), _client(phone='', email=''), '4'))


def test_dps_valida_com_percentual_do_simples(monkeypatch):
    monkeypatch.setattr(settings, 'nfse_nac_perc_trib_simples', '6.00')
    dps = montar_dps(_billing(), _client(), '5')
    validar_dps(dps)
    tot = _q(dps, 'infDPS', 'valores', 'trib', 'totTrib')
    assert _q(tot, 'pTotTribSN') is not None
    assert _q(tot, 'indTotTrib') is None, 'totTrib é um choice — só um dos dois'


# --------------------------------------------------------------------------
# Regras do leiaute
# --------------------------------------------------------------------------

def test_id_dps_tem_45_caracteres_e_estrutura_correta():
    """Id = 'DPS' + cLocEmi(7) + tpInsc(1) + inscrição(14) + série(5) + nDPS(15)."""
    ident = id_dps('42')
    assert len(ident) == 45
    assert ident.startswith('DPS')
    assert ident[3:10] == '4209102'          # código IBGE de Joinville
    assert ident[10] == '2'                  # 2 = CNPJ
    assert ident[11:25] == settings.nfse_cnpj.zfill(14)
    assert ident[25:30] == settings.nfse_nac_serie.zfill(5)
    assert ident[30:] == '42'.zfill(15)


def test_serie_e_a_faixa_de_integracao_via_api():
    """
    Joinville padronizou: 40000 = aplicativo próprio integrando via API com a
    Sefin Nacional. 60000/70000/80000 são móvel/web/transcrição manual.
    """
    assert settings.nfse_nac_serie == '40000'
    assert _q(montar_dps(_billing(), _client(), '6'), 'infDPS', 'serie').text == '40000'


def test_infdps_carrega_o_id_como_atributo():
    dps = montar_dps(_billing(), _client(), '7')
    assert _q(dps, 'infDPS').get('Id') == id_dps('7')


def test_ambiente_de_teste_marca_tpamb_2():
    """producao_restrita → tpAmb=2. Errar isso emite nota real por engano."""
    assert settings.nfse_nac_ambiente == 'producao_restrita'
    assert _q(montar_dps(_billing(), _client(), '8'), 'infDPS', 'tpAmb').text == '2'


def test_valor_do_servico_vem_do_billing():
    dps = montar_dps(_billing(amount=Decimal('89.90')), _client(), '9')
    assert _q(dps, 'infDPS', 'valores', 'vServPrest', 'vServ').text == '89.90'


def test_descricao_usa_o_titulo_do_billing():
    dps = montar_dps(_billing(title='INSTALACAO DE RASTREADOR'), _client(), '10')
    assert _q(dps, 'infDPS', 'serv', 'cServ', 'xDescServ').text == 'INSTALACAO DE RASTREADOR'


def test_prestador_vai_com_cnpj_e_regime_sem_im_por_padrao():
    """Joinville rejeita a IM do prestador (E0120): por padrão não a enviamos."""
    prest = _q(montar_dps(_billing(), _client(), '11'), 'infDPS', 'prest')
    assert _q(prest, 'CNPJ').text == settings.nfse_cnpj
    assert _q(prest, 'IM') is None
    assert _q(prest, 'regTrib', 'opSimpNac').text == settings.nfse_nac_op_simples_nacional


def test_prestador_envia_im_se_ligado(monkeypatch):
    monkeypatch.setattr(settings, 'nfse_nac_enviar_im', True)
    prest = _q(montar_dps(_billing(), _client(), '11'), 'infDPS', 'prest')
    assert _q(prest, 'IM').text == settings.nfse_inscricao_municipal


def test_simples_usa_ptottribsn_e_nao_indtottrib():
    """ME/EPP (opSimpNac=3): totTrib deve trazer pTotTribSN (E0712)."""
    tot = _q(montar_dps(_billing(), _client(), '11'), 'infDPS', 'valores', 'trib', 'totTrib')
    assert _q(tot, 'pTotTribSN') is not None
    assert _q(tot, 'indTotTrib') is None


def test_nao_optante_usa_indtottrib(monkeypatch):
    monkeypatch.setattr(settings, 'nfse_nac_op_simples_nacional', '1')
    tot = _q(montar_dps(_billing(), _client(), '11'), 'infDPS', 'valores', 'trib', 'totTrib')
    assert _q(tot, 'indTotTrib') is not None
    assert _q(tot, 'pTotTribSN') is None


def test_assinatura_usa_canonicalizacao_exclusiva():
    """C14N exclusiva — comprovado na produção restrita (inclusiva dá E0714)."""
    assert settings.nfse_nac_c14n == 'http://www.w3.org/2001/10/xml-exc-c14n#'


def test_tomador_pj_usa_cnpj_e_pf_usa_cpf():
    pj = _q(montar_dps(_billing(), _client(), '12'), 'infDPS', 'toma')
    assert _q(pj, 'CNPJ') is not None and _q(pj, 'CPF') is None

    pf = _q(montar_dps(_billing(), _client(type='pf', cpf_cnpj='11144477735'), '13'),
            'infDPS', 'toma')
    assert _q(pf, 'CPF') is not None and _q(pf, 'CNPJ') is None


def test_codigo_ibge_do_tomador_vem_do_cadastro_sem_consultar_viacep():
    """Com city_ibge_code preenchido não pode haver chamada HTTP ao ViaCEP."""
    dps = montar_dps(_billing(), _client(city_ibge_code='3550308'), '14')
    assert _q(dps, 'infDPS', 'toma', 'end', 'endNac', 'cMun').text == '3550308'


def test_erro_claro_quando_nao_da_para_resolver_o_municipio(monkeypatch):
    monkeypatch.setattr('app.services.nfse_joinville._ibge_por_cep', lambda _cep: None)
    with pytest.raises(NfseError, match='código IBGE'):
        montar_dps(_billing(), _client(city_ibge_code='', zip_code='00000000'), '15')


# --------------------------------------------------------------------------
# Transporte
# --------------------------------------------------------------------------

def test_compactar_produz_gzip_em_base64():
    xml = '<DPS>teste</DPS>'
    empacotado = _compactar(xml)
    assert gzip.decompress(base64.b64decode(empacotado)).decode() == xml
    assert _descompactar(empacotado) == xml


def test_endereco_sem_numero_vai_como_sn_e_continua_valido():
    """<nro> é 1-1 no leiaute; cadastro sem número não pode travar a emissão."""
    dps = montar_dps(_billing(), _client(address_number=''), '20')
    validar_dps(dps)
    assert _q(dps, 'infDPS', 'toma', 'end', 'nro').text == 'S/N'


@pytest.mark.parametrize('campo,rotulo', [
    ('name', 'nome'),
    ('cpf_cnpj', 'CPF/CNPJ'),
    ('address_line', 'logradouro'),
    ('neighborhood', 'bairro'),
    ('zip_code', 'CEP'),
])
def test_cadastro_incompleto_da_mensagem_util_em_vez_de_erro_de_xsd(campo, rotulo):
    with pytest.raises(NfseError, match=rotulo):
        montar_dps(_billing(), _client(**{campo: ''}), '21')


def test_cep_malformado_e_recusado_antes_do_xsd():
    with pytest.raises(NfseError, match='CEP'):
        montar_dps(_billing(), _client(zip_code='892'), '22')


def test_codigo_de_tributacao_default_e_de_6_digitos():
    """cTribNac tem 6 dígitos (E0310 se não existir na lista nacional)."""
    codigo = settings.nfse_nac_cod_trib_nacional
    assert len(codigo) == 6 and codigo.isdigit()
    dps = montar_dps(_billing(), _client(), '17')
    assert _q(dps, 'infDPS', 'serv', 'cServ', 'cTribNac').text == codigo


@pytest.mark.parametrize('entrada,esperado', [
    ('140101', '140101'),        # manutenção
    ('150307', '150307'),        # locação
    ('11.02.01', '110201'),      # aceita formato pontuado e normaliza
])
def test_codigo_de_tributacao_selecionavel_por_emissao(entrada, esperado):
    dps = montar_dps(_billing(), _client(), '17', cod_trib_nacional=entrada)
    assert _q(dps, 'infDPS', 'serv', 'cServ', 'cTribNac').text == esperado


def test_codigo_vazio_cai_no_default():
    dps = montar_dps(_billing(), _client(), '17', cod_trib_nacional='')
    assert _q(dps, 'infDPS', 'serv', 'cServ', 'cTribNac').text == settings.nfse_nac_cod_trib_nacional


def test_dps_nao_usa_prefixo_de_namespace():
    """RN de recepção E1228: prefixo de namespace na área de dados é rejeitado."""
    dps = montar_dps(_billing(), _client(), '18')
    assert all(el.prefix is None for el in dps.iter() if isinstance(el.tag, str))
    assert b'xmlns=' in etree.tostring(dps), 'namespace deve ser o default, não prefixado'


def test_data_de_competencia_nao_passa_da_emissao():
    """E0015: dCompet deve ser anterior ou igual a dhEmi."""
    inf = _q(montar_dps(_billing(), _client(), '19'), 'infDPS')
    assert _q(inf, 'dCompet').text <= _q(inf, 'dhEmi').text[:10]


def test_ambiente_invalido_da_erro_de_configuracao(monkeypatch):
    monkeypatch.setattr(settings, 'nfse_nac_ambiente', 'homologacao')
    with pytest.raises(NfseError, match='NFSE_NAC_AMBIENTE'):
        montar_dps(_billing(), _client(), '16')


# --------------------------------------------------------------------------
# Mensagens de erro do DANFSE
# --------------------------------------------------------------------------

class _RespFake:
    def __init__(self, status, text='', content_type='text/html'):
        self.status_code = status
        self.text = text
        self.headers = {'Content-Type': content_type}


def test_erro_danfse_404_na_restrita_diz_que_o_servico_nao_existe_la():
    """Sondagem de 05/08/2026: o ADN restrita devolve 404 em TODA rota /danfse,
    inclusive na página de documentação. O serviço não está implantado lá — não
    é documento apagado, e a nota continua íntegra."""
    from app.services.nfse_nacional import _erro_danfse

    msg = _erro_danfse(_RespFake(404), 'chave')
    assert 'produção restrita' in msg
    assert 'emitida' in msg


def test_erro_danfse_503_diz_que_e_do_governo_e_oferece_a_saida():
    from app.services.nfse_nacional import _erro_danfse

    msg = _erro_danfse(_RespFake(
        503, '<html><body><h1>503 Service Unavailable</h1></body></html>'), 'chave')
    assert 'sem servidor' in msg
    # o HTML cru NÃO pode vazar para a tela
    assert '<html>' not in msg and '<h1>' not in msg
    # e o operador precisa sair dali sabendo onde pegar o PDF
    assert 'consultapublica/?chave=chave' in msg


def test_url_de_consulta_publica_segue_o_ambiente(monkeypatch):
    """Link fixo no portal de produção quebrava para nota emitida em teste."""
    from app.services import nfse_nacional as nf

    monkeypatch.setattr(settings, 'nfse_nac_ambiente', 'producao')
    assert nf.url_consulta_publica('K') == 'https://www.nfse.gov.br/consultapublica/?chave=K'

    monkeypatch.setattr(settings, 'nfse_nac_ambiente', 'producao_restrita')
    assert nf.url_consulta_publica('K') == (
        'https://www.producaorestrita.nfse.gov.br/consultapublica/?chave=K'
    )


def test_erro_danfse_403_aponta_o_certificado():
    from app.services.nfse_nacional import _erro_danfse

    assert 'certificado' in _erro_danfse(_RespFake(403), 'chave').lower()


def test_erro_danfse_desconhecido_limpa_o_html():
    from app.services.nfse_nacional import _erro_danfse

    msg = _erro_danfse(_RespFake(418, '<html><body>bule de chá</body></html>'), 'chave')
    assert '<' not in msg and 'bule de chá' in msg
