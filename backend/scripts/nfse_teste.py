"""
Emissão de NFS-e de TESTE — Joinville / NF-em (padrão Nota Nacional, fornecedor Pública).

Integração via WebService SOAP: monta o RPS, assina digitalmente (certificado
A1 e-CNPJ), envia em lote (RecepcionarLoteRps) e acompanha o protocolo até a
NFS-e ser gerada.

COMPROVADO (17/06/2026, contra a homologação de Joinville): o sistema ACEITA o
RPS SEM assinatura digital e processa as regras de negócio normalmente — o
certificado NÃO é exigido na prática, apesar do manual (4.2.3/4.2.4) dizer
``Signature`` 1-1. Assinamos só se o .pfx for informado; sem ele, enviamos sem
assinatura (que funciona).

Modos de uso (dentro de backend/):

  # 1) Só MONTA e salva o XML (sem certificado) — para revisar/validar a estrutura
  python scripts/nfse_teste.py

  # 2) Assina o XML com o certificado A1 (gera o XML assinado, NÃO envia)
  python scripts/nfse_teste.py --cert /caminho/cert.pfx --senha "SENHA_DO_PFX"

  # 3) Assina e ENVIA para a homologação de Joinville, e acompanha o protocolo
  python scripts/nfse_teste.py --cert /caminho/cert.pfx --senha "SENHA_DO_PFX" --enviar

Pré-requisitos:
  pip install signxml lxml requests cryptography

Observação: os pontos marcados com «CONFIRMAR» dependem do XSD/exemplo do portal
(seção Downloads de https://nfsehomologacao.joinville.sc.gov.br/) e do contador
(PIS/COFINS no Simples). Ajustamos juntos quando o certificado chegar.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import time
from pathlib import Path

import requests
from lxml import etree

# Console UTF-8 no Windows (evita UnicodeEncodeError com emojis/acentos)
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# =============================================================================
# Endpoints (manual cap. "LISTA DE ENDPOINT" + comunicados de Joinville)
# =============================================================================
ENDPOINTS = {
    'homologacao': 'https://nfsehomologacao.joinville.sc.gov.br/nfse_integracao/Services',
    'producao': 'https://nfem.joinville.sc.gov.br/nfse_integracao/Services',
}
ENDPOINTS_CONSULTAS = {
    'homologacao': 'https://nfsehomologacao.joinville.sc.gov.br/nfse_integracao/Consultas',
    'producao': 'https://nfem.joinville.sc.gov.br/nfse_integracao/Consultas',
}
NS_PUBLICA = 'http://www.publica.inf.br'                      # namespace do XML interno
NS_SERVICE = 'http://service.nfse.integracao.ws.publica/'    # namespace do envelope SOAP

# =============================================================================
# Dados do PRESTADOR (MasterSat) — confirmados no cadastro do portal
# =============================================================================
PRESTADOR_CNPJ = '14228344000167'
PRESTADOR_IM = '109545'                       # Inscrição Municipal Joinville
PRESTADOR_RAZAO = 'MASTERSAT COMERCIO E SERVICOS DE RASTREAMENTO LTDA'
OPTANTE_SIMPLES = 1                            # 1=Sim, 2=Não  (cadastro: SIM)
INCENTIVADOR_CULTURAL = 2                      # 1=Sim, 2=Não

# Serviço padrão
ITEM_LISTA_SERVICO = '1102'                    # 11.02 → "1102" (formato 0000)
CODIGO_MUNICIPIO_SERVICO = '4209102'           # «CONFIRMAR» IBGE de Joinville/SC = 4209102
ALIQUOTA_ISS = '2.00'                          # 2%
CNAE = '8020002'                               # «CONFIRMAR» formato exigido (com/sem pontuação)
NATUREZA_OPERACAO = '101'                      # «CONFIRMAR» código municipal (manual usa 101 no exemplo; 1 retornou E144)

# =============================================================================
# Endereço do PRESTADOR (cadastro do portal). OBS: o RPS NÃO envia o endereço do
# prestador — a prefeitura o preenche na NFS-e a partir do cadastro. Mantido aqui
# apenas para registro.
# =============================================================================
PRESTADOR_ENDERECO = {
    'logradouro': 'MARITIMA',
    'numero': '424',
    'bairro': 'COMASA',
    'codigo_municipio': '4209102',   # Joinville
    'uf': 'SC',
    'cep': '89228450',
}

# =============================================================================
# «CONFIRMAR» — TOMADOR de teste (PJ: endereço/numero/bairro/cidade/UF obrigatórios)
# =============================================================================
TOMADOR = {
    'cnpj': '00000000000191',        # CNPJ de teste — trocar por um tomador real de homologação
    'razao_social': 'CLIENTE TESTE LTDA',
    'email': 'teste@exemplo.com.br',
    'logradouro': 'RUA TESTE',
    'numero': '100',
    'bairro': 'CENTRO',
    'codigo_municipio': '4209102',
    'uf': 'SC',
    'cep': '89200000',
}

# Valor do serviço de teste
VALOR_SERVICOS = '1.00'
DESCRICAO_SERVICO = 'SERVICO DE TESTE - MONITORAMENTO E RASTREAMENTO DE VEICULOS'

# Série/Tipo do RPS — REGRA CRÍTICA de Joinville: WebService usa SÉRIE 3000
SERIE_RPS = '3000'
TIPO_RPS = 1                                   # 1 = RPS


def _fmt_data(d: dt.date) -> str:
    return d.strftime('%Y-%m-%d')


def _fmt_datahora(d: dt.datetime) -> str:
    return d.strftime('%Y-%m-%dT%H:%M:%S')


def montar_inf_rps(numero_rps: int) -> etree._Element:
    """Monta o elemento <InfRps id="..."> conforme o manual (tipo InfRps)."""
    agora = dt.datetime.now()
    rps_id = f'rps{numero_rps}'

    inf = etree.Element('InfRps', id=rps_id)

    ident = etree.SubElement(inf, 'IdentificacaoRps')
    etree.SubElement(ident, 'Numero').text = str(numero_rps)
    etree.SubElement(ident, 'Serie').text = SERIE_RPS
    etree.SubElement(ident, 'Tipo').text = str(TIPO_RPS)

    etree.SubElement(inf, 'DataEmissao').text = _fmt_datahora(agora)
    etree.SubElement(inf, 'NaturezaOperacao').text = NATUREZA_OPERACAO
    etree.SubElement(inf, 'OptanteSimplesNacional').text = str(OPTANTE_SIMPLES)
    etree.SubElement(inf, 'IncentivadorCultural').text = str(INCENTIVADOR_CULTURAL)
    etree.SubElement(inf, 'Status').text = '1'   # 1 = Normal

    # ---- Serviço ----
    servico = etree.SubElement(inf, 'Servico')
    valores = etree.SubElement(servico, 'Valores')
    etree.SubElement(valores, 'ValorServicos').text = VALOR_SERVICOS
    etree.SubElement(valores, 'IssRetido').text = '2'          # 2 = ISS não retido «CONFIRMAR»
    etree.SubElement(valores, 'ValorIss').text = '0.00'
    etree.SubElement(valores, 'BaseCalculo').text = VALOR_SERVICOS
    etree.SubElement(valores, 'Aliquota').text = ALIQUOTA_ISS
    etree.SubElement(valores, 'ValorLiquidoNfse').text = VALOR_SERVICOS
    # «CONFIRMAR» com o contador: no Simples, grupo TribFed/PisCofins pode ser exigido
    # (tags antigas ValorPis/ValorCofins foram descontinuadas — erro E923).
    etree.SubElement(servico, 'ItemListaServico').text = ITEM_LISTA_SERVICO
    etree.SubElement(servico, 'Discriminacao').text = DESCRICAO_SERVICO
    etree.SubElement(servico, 'CodigoMunicipio').text = CODIGO_MUNICIPIO_SERVICO

    # ---- Prestador ----
    prestador = etree.SubElement(inf, 'Prestador')
    etree.SubElement(prestador, 'Cnpj').text = PRESTADOR_CNPJ
    etree.SubElement(prestador, 'InscricaoMunicipal').text = PRESTADOR_IM

    # ---- Tomador (PJ: endereço completo obrigatório) ----
    tomador = etree.SubElement(inf, 'Tomador')
    ident_tom = etree.SubElement(tomador, 'IdentificacaoTomador')
    cpfcnpj = etree.SubElement(ident_tom, 'CpfCnpj')
    etree.SubElement(cpfcnpj, 'Cnpj').text = TOMADOR['cnpj']
    etree.SubElement(tomador, 'RazaoSocial').text = TOMADOR['razao_social']
    end = etree.SubElement(tomador, 'Endereco')
    etree.SubElement(end, 'Endereco').text = TOMADOR['logradouro']
    etree.SubElement(end, 'Numero').text = TOMADOR['numero']
    etree.SubElement(end, 'Bairro').text = TOMADOR['bairro']
    etree.SubElement(end, 'CodigoMunicipio').text = TOMADOR['codigo_municipio']
    etree.SubElement(end, 'Uf').text = TOMADOR['uf']
    etree.SubElement(end, 'Cep').text = TOMADOR['cep']
    contato = etree.SubElement(tomador, 'Contato')
    etree.SubElement(contato, 'Email').text = TOMADOR['email']

    return inf, rps_id


def montar_envio_lote(numero_lote: int, numero_rps: int):
    """Monta <EnviarLoteRpsEnvio> com 1 RPS (ainda SEM assinatura)."""
    nsmap = {None: NS_PUBLICA}
    envio = etree.Element('EnviarLoteRpsEnvio', nsmap=nsmap)
    lote = etree.SubElement(envio, 'LoteRps', versao='1.00', id=f'lote{numero_lote}')
    etree.SubElement(lote, 'NumeroLote').text = str(numero_lote)
    etree.SubElement(lote, 'Cnpj').text = PRESTADOR_CNPJ
    etree.SubElement(lote, 'InscricaoMunicipal').text = PRESTADOR_IM
    etree.SubElement(lote, 'QuantidadeRps').text = '1'
    lista = etree.SubElement(lote, 'ListaRps')

    rps = etree.SubElement(lista, 'Rps')
    inf, rps_id = montar_inf_rps(numero_rps)
    rps.append(inf)

    return envio, rps, inf, rps_id, f'lote{numero_lote}'


def carregar_pfx(cert_path: str, senha: str):
    """Extrai chave privada e certificado (PEM) de um arquivo .pfx/.p12."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12

    data = Path(cert_path).read_bytes()
    chave, cert, _ = pkcs12.load_key_and_certificates(data, senha.encode())
    if chave is None or cert is None:
        raise SystemExit('[ERRO] Não foi possível ler a chave/certificado do .pfx (senha incorreta?)')
    key_pem = chave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    return key_pem, cert_pem


def assinar(elemento: etree._Element, ref_id: str, key_pem: bytes, cert_pem: bytes) -> etree._Element:
    """
    Assina ``elemento`` (enveloped) referenciando ``ref_id``, no perfil ABRASF/Pública:
    C14N 2001, RSA-SHA1, digest SHA1, com X509Certificate no KeyInfo.
    """
    from signxml import XMLSigner, methods

    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm='rsa-sha1',
        digest_algorithm='sha1',
        c14n_algorithm='http://www.w3.org/TR/2001/REC-xml-c14n-20010315',
    )
    # remove o namespace ds: do default? signxml insere ds: — Pública costuma aceitar.
    return signer.sign(elemento, key=key_pem, cert=cert_pem, reference_uri=ref_id)


def montar_envelope_soap(xml_interno: str, metodo: str) -> str:
    """Envelope SOAP no padrão Pública: <ser:{metodo}><XML><![CDATA[ ... ]]></XML>."""
    return (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:ser="{NS_SERVICE}">'
        '<soapenv:Header/>'
        '<soapenv:Body>'
        f'<ser:{metodo}>'
        f'<XML><![CDATA[{xml_interno}]]></XML>'
        f'</ser:{metodo}>'
        '</soapenv:Body>'
        '</soapenv:Envelope>'
    )


def enviar(url: str, soap: str, soapaction: str) -> str:
    headers = {
        'Content-Type': 'text/xml; charset=utf-8',
        'SOAPAction': soapaction,
    }
    resp = requests.post(url, data=soap.encode('utf-8'), headers=headers, timeout=60)
    print(f'  HTTP {resp.status_code}')
    return resp.text


def montar_consultar_situacao(protocolo: str) -> str:
    """ConsultarSituacaoLoteRpsEnvio — retorna o código de situação (1 a 7)."""
    envio = etree.Element('ConsultarSituacaoLoteRpsEnvio', nsmap={None: NS_PUBLICA})
    prest = etree.SubElement(envio, 'Prestador')
    etree.SubElement(prest, 'Cnpj').text = PRESTADOR_CNPJ
    etree.SubElement(prest, 'InscricaoMunicipal').text = PRESTADOR_IM
    etree.SubElement(envio, 'Protocolo').text = protocolo
    return etree.tostring(envio, encoding='unicode')


def montar_consultar_lote(protocolo: str) -> str:
    """ConsultarLoteRpsEnvio — retorna as NFS-e geradas OU a lista de erros."""
    envio = etree.Element('ConsultarLoteRpsEnvio', nsmap={None: NS_PUBLICA})
    prest = etree.SubElement(envio, 'Prestador')
    etree.SubElement(prest, 'Cnpj').text = PRESTADOR_CNPJ
    etree.SubElement(prest, 'InscricaoMunicipal').text = PRESTADOR_IM
    etree.SubElement(envio, 'Protocolo').text = protocolo
    return etree.tostring(envio, encoding='unicode')


def consultar_protocolo(ambiente: str, protocolo: str) -> None:
    """Consulta situação do lote + ConsultarLoteRps (NFS-e gerada ou erros)."""
    print(f'Consultando protocolo {protocolo}...')
    soap_sit = montar_envelope_soap(montar_consultar_situacao(protocolo), 'ConsultarSituacaoLoteRps')
    print(enviar(ENDPOINTS_CONSULTAS[ambiente], soap_sit, 'ConsultarSituacaoLoteRps')[:4000])
    print('\n--- ConsultarLoteRps (NFS-e gerada OU lista de erros) ---')
    soap_lote = montar_envelope_soap(montar_consultar_lote(protocolo), 'ConsultarLoteRps')
    print(enviar(ENDPOINTS_CONSULTAS[ambiente], soap_lote, 'ConsultarLoteRps')[:8000])


def main() -> None:
    parser = argparse.ArgumentParser(description='Emite uma NFS-e de teste em Joinville (NF-em / Nota Nacional)')
    parser.add_argument('--cert', help='Caminho do certificado A1 (.pfx/.p12)')
    parser.add_argument('--senha', help='Senha do certificado')
    parser.add_argument('--enviar', action='store_true', help='Envia para a homologação (requer --cert)')
    parser.add_argument('--provar-sem-certificado', action='store_true',
                        help='Envia o XML SEM assinatura à homologação para comprovar que o certificado é exigido (esperado: erro E1)')
    parser.add_argument('--ambiente', default='homologacao', choices=list(ENDPOINTS))
    parser.add_argument('--numero-rps', type=int, default=1)
    parser.add_argument('--numero-lote', type=int, default=1)
    parser.add_argument('--consultar-lote', metavar='PROTOCOLO',
                        help='Consulta um protocolo já enviado (situação + NFS-e/erros) e sai')
    parser.add_argument('--natureza', help='Sobrescreve a NaturezaOperacao (use a que o cadastro do prestador autoriza)')
    parser.add_argument('--municipio', help='Sobrescreve o CodigoMunicipio (IBGE) de incidência do serviço')
    args = parser.parse_args()

    if args.natureza:
        globals()['NATUREZA_OPERACAO'] = args.natureza
    if args.municipio:
        globals()['CODIGO_MUNICIPIO_SERVICO'] = args.municipio

    # --- Modo consulta: não monta RPS, só consulta o protocolo informado ---
    if args.consultar_lote:
        consultar_protocolo(args.ambiente, args.consultar_lote)
        return

    print('Montando o RPS de teste (MasterSat)...\n')
    envio, rps, inf, rps_id, lote_id = montar_envio_lote(args.numero_lote, args.numero_rps)

    xml_sem_assinatura = etree.tostring(envio, encoding='unicode')
    Path('nfse_rps_teste.xml').write_text(xml_sem_assinatura, encoding='utf-8')
    print('XML (sem assinatura) salvo em ./nfse_rps_teste.xml — revise a estrutura.\n')

    if args.provar_sem_certificado:
        print(f'Enviando SEM assinatura para {args.ambiente} (Joinville aceita sem certificado)...')
        soap = montar_envelope_soap(xml_sem_assinatura, 'RecepcionarLoteRps')
        retorno = enviar(ENDPOINTS[args.ambiente], soap, 'RecepcionarLoteRps')
        print('\n--- Resposta da recepção ---')
        print(retorno[:2000])
        m = re.search(r'Protocolo&gt;\s*([0-9a-fA-F]+)\s*&lt;', retorno)
        if not m:
            print('\n⚠️  Sem protocolo na resposta. Veja o erro/HTTP acima '
                  '(ex.: E163 lote duplicado → use --numero-lote único; 502 → servidor fora do ar).')
            return
        protocolo = m.group(1)
        print(f'\n✅ Lote recebido SEM assinatura. Protocolo: {protocolo}')
        print('Aguardando o processamento assíncrono...')
        time.sleep(6)
        consultar_protocolo(args.ambiente, protocolo)
        return

    if not args.cert:
        print('⚠️  Sem --cert: parando aqui (o WebService exige assinatura digital A1).')
        print('   Quando o certificado .pfx chegar, rode:')
        print('     python scripts/nfse_teste.py --cert cert.pfx --senha SENHA --enviar')
        return

    if not args.senha:
        raise SystemExit('[ERRO] Informe a senha do certificado com --senha')

    print('Carregando certificado e assinando...')
    key_pem, cert_pem = carregar_pfx(args.cert, args.senha)

    # Assina o RPS (referência ao InfRps) e depois o lote (EnviarLoteRpsEnvio).
    inf_assinado = assinar(inf, rps_id, key_pem, cert_pem)
    rps.replace(inf, inf_assinado)
    envio_assinado = assinar(envio, lote_id, key_pem, cert_pem)

    xml_assinado = etree.tostring(envio_assinado, encoding='unicode')
    Path('nfse_rps_teste_assinado.xml').write_text(xml_assinado, encoding='utf-8')
    print('XML assinado salvo em ./nfse_rps_teste_assinado.xml\n')

    if not args.enviar:
        print('✅ XML assinado gerado. Para enviar à homologação, adicione --enviar.')
        return

    print(f'Enviando para {args.ambiente}...')
    soap = montar_envelope_soap(xml_assinado, 'RecepcionarLoteRps')
    retorno = enviar(ENDPOINTS[args.ambiente], soap, 'RecepcionarLoteRps')

    print('\n--- Resposta da prefeitura ---')
    print(retorno[:4000])

    # Extrai protocolo, se houver
    m = re.search(r'<Protocolo>(.*?)</Protocolo>', retorno)
    if m:
        protocolo = m.group(1)
        print(f'\n✅ Lote recebido. Protocolo: {protocolo}')
        print('   Próximo passo: ConsultarSituacaoLoteRps + ConsultarLoteRps (implementamos em seguida).')
    else:
        print('\n⚠️ Não veio protocolo — verifique as mensagens de erro acima (tabela E1..E923 do manual).')


if __name__ == '__main__':
    main()
