"""Testes do DANFSE que geramos a partir do XML da nota."""
from __future__ import annotations

import pytest

from app.services.nfse_danfse import DanfseError, gerar_danfse_pdf, ler_xml

NS = 'http://www.sped.fazenda.gov.br/nfse'

# NFS-e nacional reduzida ao essencial, na ordem do TCInfNFSe (XSD v1.01).
XML_NACIONAL = f'''<?xml version="1.0" encoding="UTF-8"?>
<NFSe xmlns="{NS}" versao="1.00">
  <infNFSe Id="NFS42091022214228344000167400000000000172608978505873">
    <xLocEmi>Joinville</xLocEmi>
    <xLocPrestacao>Joinville</xLocPrestacao>
    <nNFSe>17</nNFSe>
    <xTribNac>Vigilancia, seguranca ou monitoramento de bens.</xTribNac>
    <verAplic>1.00</verAplic>
    <ambGer>1</ambGer>
    <tpEmis>1</tpEmis>
    <cStat>100</cStat>
    <dhProc>2026-08-05T09:18:26-03:00</dhProc>
    <nDFSe>17</nDFSe>
    <emit>
      <CNPJ>14228344000167</CNPJ>
      <xNome>MASTERSAT COMERCIO E SERVICO DE RASTREADORES LTDA</xNome>
      <enderNac>
        <xLgr>RUA MARITIMA</xLgr><nro>424</nro><xBairro>COMASA</xBairro>
        <cMun>4209102</cMun><UF>SC</UF><CEP>89228450</CEP>
      </enderNac>
      <email>contato@mastersat.com.br</email>
    </emit>
    <valores>
      <vBC>651.61</vBC><pAliqAplic>2.00</pAliqAplic>
      <vISSQN>13.03</vISSQN><vTotalRet>0.00</vTotalRet><vLiq>651.61</vLiq>
    </valores>
    <xOutInf>Contrato 2026/017.</xOutInf>
    <DPS versao="1.00">
      <infDPS Id="DPS420910222142283440001674000000000000000017">
        <tpAmb>1</tpAmb>
        <dhEmi>2026-08-05T09:18:25-03:00</dhEmi>
        <verAplic>1.00</verAplic>
        <serie>40000</serie>
        <nDPS>17</nDPS>
        <dCompet>2026-08-01</dCompet>
        <tpEmit>1</tpEmit>
        <cLocEmi>4209102</cLocEmi>
        <prest><CNPJ>14228344000167</CNPJ></prest>
        <toma>
          <CNPJ>11222333000181</CNPJ>
          <xNome>TRANSPORTES ACQUE LTDA</xNome>
          <end>
            <endNac><cMun>4209102</cMun><CEP>89201100</CEP></endNac>
            <xLgr>RUA DO PRINCIPE</xLgr><nro>330</nro><xBairro>CENTRO</xBairro>
          </end>
          <email>financeiro@acque.com.br</email>
        </toma>
        <serv>
          <locPrest><cLocPrestacao>4209102</cLocPrestacao></locPrest>
          <cServ>
            <cTribNac>110201</cTribNac>
            <xDescServ>MONITORAMENTO VEICULAR - AGOSTO/2026</xDescServ>
          </cServ>
        </serv>
        <valores><vServPrest><vServ>651.61</vServ></vServPrest></valores>
      </infDPS>
    </DPS>
  </infNFSe>
</NFSe>'''

# Formato do webservice municipal legado, do jeito que ficou guardado: o XML da
# nota escapado dentro do envelope SOAP.
XML_JOINVILLE = '''<?xml version="1.0"?>
<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>
<ns2:ConsultarLoteRpsResponse xmlns:ns2="http://service.nfse.integracao.ws.publica/">
<return>&lt;ConsultarLoteRpsResposta xmlns="http://www.publica.inf.br"&gt;
&lt;ListaNfse&gt;&lt;CompNfse&gt;&lt;Nfse&gt;&lt;InfNfse&gt;
&lt;Numero&gt;202600000002925&lt;/Numero&gt;
&lt;Serie&gt;A1&lt;/Serie&gt;
&lt;CodigoVerificacao&gt;C1PL-FKTH&lt;/CodigoVerificacao&gt;
&lt;DataEmissao&gt;2026-06-19T01:03:30&lt;/DataEmissao&gt;
&lt;CodLocPrestCod&gt;4209102&lt;/CodLocPrestCod&gt;
&lt;CodLocPrestDesc&gt;JOINVILLE&lt;/CodLocPrestDesc&gt;
&lt;IdentificacaoRps&gt;&lt;Numero&gt;5&lt;/Numero&gt;&lt;Serie&gt;3000&lt;/Serie&gt;&lt;/IdentificacaoRps&gt;
&lt;Competencia&gt;2026-06&lt;/Competencia&gt;
&lt;OutrasInformacoes&gt;SEM VALOR LEGAL&lt;/OutrasInformacoes&gt;
&lt;ChaveAcesso&gt;42091021214228344000167000000000292526060017339526&lt;/ChaveAcesso&gt;
&lt;Servico&gt;&lt;Valores&gt;
&lt;ValorServicos&gt;99.90000&lt;/ValorServicos&gt;&lt;ValorDeducoes&gt;0.00000&lt;/ValorDeducoes&gt;
&lt;ValorIss&gt;0.00000&lt;/ValorIss&gt;&lt;ValorIssRetido&gt;0.00000&lt;/ValorIssRetido&gt;
&lt;BaseCalculo&gt;99.90000&lt;/BaseCalculo&gt;&lt;Aliquota&gt;2.00000&lt;/Aliquota&gt;
&lt;ValorLiquidoNfse&gt;99.90000&lt;/ValorLiquidoNfse&gt;
&lt;/Valores&gt;&lt;ItemListaServico&gt;1102&lt;/ItemListaServico&gt;
&lt;Discriminacao&gt;MONITORAMENTO&lt;/Discriminacao&gt;&lt;/Servico&gt;
&lt;PrestadorServico&gt;
&lt;IdentificacaoPrestador&gt;&lt;Cnpj&gt;14228344000167&lt;/Cnpj&gt;
&lt;InscricaoMunicipal&gt;109545&lt;/InscricaoMunicipal&gt;&lt;/IdentificacaoPrestador&gt;
&lt;RazaoSocial&gt;MASTERSAT COMERCIO E SERVICO DE RASTREADORES LTDA&lt;/RazaoSocial&gt;
&lt;Endereco&gt;&lt;Endereco&gt;Maritima&lt;/Endereco&gt;&lt;Numero&gt;424&lt;/Numero&gt;
&lt;Bairro&gt;COMASA&lt;/Bairro&gt;&lt;CodigoMunicipio&gt;4209102&lt;/CodigoMunicipio&gt;
&lt;Uf&gt;SC&lt;/Uf&gt;&lt;Cep&gt;89228450&lt;/Cep&gt;&lt;/Endereco&gt;
&lt;/PrestadorServico&gt;
&lt;TomadorServico&gt;
&lt;IdentificacaoTomador&gt;&lt;CpfCnpj&gt;&lt;Cnpj&gt;11222333000181&lt;/Cnpj&gt;&lt;/CpfCnpj&gt;
&lt;/IdentificacaoTomador&gt;
&lt;RazaoSocial&gt;TRANSPORTES ACQUE LTDA&lt;/RazaoSocial&gt;
&lt;/TomadorServico&gt;
&lt;/InfNfse&gt;&lt;/Nfse&gt;&lt;/CompNfse&gt;&lt;/ListaNfse&gt;&lt;/ConsultarLoteRpsResposta&gt;</return>
</ns2:ConsultarLoteRpsResponse></S:Body></S:Envelope>'''


# --------------------------------------------------------------------------
# Leitura do XML nacional
# --------------------------------------------------------------------------

def test_le_identificacao_da_nota_nacional():
    d = ler_xml(XML_NACIONAL)
    assert d.numero == '17'
    assert d.serie == '40000'
    assert d.chave == '42091022214228344000167400000000000172608978505873'
    assert d.data_emissao == '05/08/2026 09:18:26'
    assert d.competencia == '01/08/2026'


def test_nao_confunde_o_cnpj_do_emitente_com_o_do_tomador():
    """Os dois blocos têm uma tag <CNPJ>; uma busca global pegaria a errada."""
    d = ler_xml(XML_NACIONAL)
    assert d.prestador.documento == '14.228.344/0001-67'
    assert d.tomador.documento == '11.222.333/0001-81'


def test_endereco_do_emitente_e_do_tomador_usam_layouts_diferentes():
    """emit/enderNac traz cMun e CEP soltos; toma/end aninha em <endNac>."""
    d = ler_xml(XML_NACIONAL)
    assert d.prestador.endereco == 'RUA MARITIMA, 424, COMASA'
    assert d.prestador.municipio == 'Joinville/SC'
    assert d.prestador.cep == '89228-450'
    assert d.tomador.endereco == 'RUA DO PRINCIPE, 330, CENTRO'
    assert d.tomador.municipio == 'Joinville'   # traduzido pelo cLocEmi do XML
    assert d.tomador.cep == '89201-100'


def test_le_servico_e_valores_da_nota_nacional():
    d = ler_xml(XML_NACIONAL)
    assert d.codigo_servico == '110201'
    assert d.descricao_servico == 'MONITORAMENTO VEICULAR - AGOSTO/2026'
    assert d.valor_liquido == 'R$ 651,61'
    assert ('Valor do serviço', 'R$ 651,61') in d.valores
    assert ('Alíquota', '2,00 %') in d.valores
    assert ('ISSQN', 'R$ 13,03') in d.valores


def test_producao_nao_leva_faixa_de_teste():
    assert ler_xml(XML_NACIONAL).teste is False


def test_producao_restrita_marca_a_nota_como_sem_valor_fiscal():
    """tpAmb=2 precisa aparecer no papel — senão alguém manda teste ao cliente."""
    assert ler_xml(XML_NACIONAL.replace('<tpAmb>1</tpAmb>', '<tpAmb>2</tpAmb>')).teste is True


# --------------------------------------------------------------------------
# Formato legado de Joinville
# --------------------------------------------------------------------------

def test_desembrulha_o_envelope_soap_de_joinville():
    d = ler_xml(XML_JOINVILLE)
    assert d.numero == '202600000002925'
    assert d.serie == 'A1'
    assert d.codigo_verificacao == 'C1PL-FKTH'
    assert d.prestador.documento == '14.228.344/0001-67'
    assert d.tomador.nome == 'TRANSPORTES ACQUE LTDA'
    assert d.valor_liquido == 'R$ 99,90'
    # o endereço legado só tem o código IBGE; o nome vem de CodLocPrestDesc
    assert d.prestador.municipio == 'JOINVILLE/SC'


def test_sem_valor_legal_da_prefeitura_vira_faixa_de_teste():
    assert ler_xml(XML_JOINVILLE).teste is True


def _url_no_rodape(monkeypatch, xml: str, url: str) -> str:
    """Qual URL o rodapé acabou recebendo."""
    from app.services import nfse_danfse as mod

    visto: list[str] = []
    original = mod._rodape
    monkeypatch.setattr(mod, '_rodape', lambda d: (visto.append(d.consulta_url), original(d))[1])
    mod.gerar_danfse_pdf(xml, url)
    return visto[0]


def test_nota_antiga_nao_ganha_link_de_conferencia(monkeypatch):
    """O sistema municipal saiu do ar em 20/07/2026; mandar o operador conferir
    numa URL morta é pior do que não oferecer link nenhum."""
    assert ler_xml(XML_JOINVILLE).formato == 'joinville'
    assert _url_no_rodape(monkeypatch, XML_JOINVILLE, 'https://sistema-morto.exemplo/nota') == ''


def test_nota_nacional_mantem_o_link_de_conferencia(monkeypatch):
    url = 'https://www.nfse.gov.br/consultapublica/?chave=X'
    assert _url_no_rodape(monkeypatch, XML_NACIONAL, url) == url


# --------------------------------------------------------------------------
# Erros
# --------------------------------------------------------------------------

@pytest.mark.parametrize('xml, trecho', [
    ('', 'sem XML'),
    ('   ', 'sem XML'),
    ('<nada/>', 'não reconhecido'),
    ('<<<quebrado', 'ilegível'),
])
def test_xml_impossivel_da_erro_explicito(xml, trecho):
    with pytest.raises(DanfseError) as exc:
        ler_xml(xml)
    assert trecho in str(exc.value)


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

@pytest.mark.parametrize('xml', [XML_NACIONAL, XML_JOINVILLE])
def test_gera_pdf_de_uma_pagina(xml):
    pdf = gerar_danfse_pdf(xml, 'https://www.nfse.gov.br/consultapublica/?chave=X')
    assert pdf[:4] == b'%PDF'
    assert pdf.count(b'/Type /Page\n') <= 1 or b'/Count 1' in pdf


def test_pdf_nao_quebra_sem_link_de_consulta():
    assert gerar_danfse_pdf(XML_NACIONAL)[:4] == b'%PDF'


def test_descricao_com_caractere_de_xml_nao_corrompe_o_pdf():
    """A descrição vai para um Paragraph, que interpreta marcação: '&' e '<'
    crus derrubariam a geração."""
    xml = XML_NACIONAL.replace('MONITORAMENTO VEICULAR - AGOSTO/2026',
                               'RASTREIO &amp; MONITORAMENTO &lt;24h&gt;')
    d = ler_xml(xml)
    assert d.descricao_servico == 'RASTREIO & MONITORAMENTO <24h>'
    assert gerar_danfse_pdf(xml)[:4] == b'%PDF'
