"""
Emissão de NFS-e de Joinville (Pública / Nota Nacional) via WebService SOAP.

Modelo: monta o RPS a partir de ``Billing``/``Client``, envia em lote
(``RecepcionarLoteRps``) e consulta o protocolo (``ConsultarSituacaoLoteRps`` +
``ConsultarLoteRps``) para obter a NFS-e gerada.

Comprovado (18/06/2026) em homologação: a prefeitura ACEITA o RPS SEM assinatura
digital. O certificado A1 é opcional — só assinamos se ``NFSE_CERT_PATH`` estiver
configurado.

Regras críticas embutidas:
  - Série do RPS = 3000 (exigência do WebService de Joinville; série errada = E97).
  - Natureza da operação = a autorizada do prestador (MasterSat: 107).
  - Tomador PJ exige endereço completo + CodigoMunicipio (IBGE) — resolvido via
    CEP (ViaCEP); tomador PF pode ir com endereço mínimo.
"""
from __future__ import annotations

import datetime as dt
import time
from decimal import Decimal

import requests
from lxml import etree
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.billing import Billing
from app.models.client import Client
from app.models.nfse_nota import NfseNota

# ---------------------------------------------------------------------------
# Endpoints e namespaces
# ---------------------------------------------------------------------------
_ENDPOINTS = {
    'homologacao': 'https://nfsehomologacao.joinville.sc.gov.br/nfse_integracao',
    'producao': 'https://nfem.joinville.sc.gov.br/nfse_integracao',
}
NS_PUBLICA = 'http://www.publica.inf.br'
NS_SERVICE = 'http://service.nfse.integracao.ws.publica/'

# Situações de lote (tsSituacaoLoteRps): 4=Sucesso, 7=Erro+Sucesso são terminais OK
_SITUACAO_PROCESSANDO = {'1', '2', '6'}  # Não recebido / Não processado / Em processamento

# Fuso de Brasília (UTC-3, sem horário de verão desde 2019). O backend roda em
# UTC no container, então a DataEmissao PRECISA ser convertida para Brasília —
# senão o ambiente nacional acusa "emissão no futuro" (erro E0008).
_TZ_BRASILIA = dt.timezone(dt.timedelta(hours=-3))


class NfseError(Exception):
    """Erro de configuração/estado — nenhuma chamada à prefeitura foi feita (ou inválida)."""


class NfseApiError(Exception):
    """Erro retornado pela prefeitura (rejeição do lote ou falha HTTP/SOAP)."""

    def __init__(self, message: str, codigo: str | None = None, status_code: int | None = None):
        self.codigo = codigo
        self.status_code = status_code
        super().__init__(message)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_url() -> str:
    base = _ENDPOINTS.get(settings.nfse_env)
    if not base:
        raise NfseError(f'NFSE_ENV inválido: {settings.nfse_env!r} (use homologacao|producao)')
    return base


def _only_digits(value: str | None) -> str:
    return ''.join(c for c in (value or '') if c.isdigit())


def _norm(value: str | None, max_len: int) -> str:
    return (value or '').strip()[:max_len]


def _fmt_valor(value) -> str:
    return f'{Decimal(str(value or 0)):.2f}'


_ibge_cache: dict[str, str] = {}


def _ibge_por_cep(cep: str | None) -> str | None:
    """Resolve o código IBGE do município pelo CEP (ViaCEP), com cache em memória."""
    cep_d = _only_digits(cep)
    if len(cep_d) != 8:
        return None
    if cep_d in _ibge_cache:
        return _ibge_cache[cep_d]
    try:
        resp = requests.get(f'https://viacep.com.br/ws/{cep_d}/json/', timeout=10)
        if resp.ok:
            ibge = (resp.json() or {}).get('ibge')
            if ibge:
                _ibge_cache[cep_d] = ibge
                return ibge
    except (requests.RequestException, ValueError):
        pass
    return None


# ---------------------------------------------------------------------------
# Montagem do XML (RPS)
# ---------------------------------------------------------------------------

def _montar_tomador(inf: etree._Element, client: Client) -> None:
    tomador = etree.SubElement(inf, 'Tomador')
    ident = etree.SubElement(tomador, 'IdentificacaoTomador')
    cpfcnpj = etree.SubElement(ident, 'CpfCnpj')
    doc = _only_digits(client.cpf_cnpj)
    if (client.type or 'pf').lower() == 'pf':
        etree.SubElement(cpfcnpj, 'Cpf').text = doc
    else:
        etree.SubElement(cpfcnpj, 'Cnpj').text = doc

    etree.SubElement(tomador, 'RazaoSocial').text = _norm(client.name, 115)

    if client.address_line:
        end = etree.SubElement(tomador, 'Endereco')
        etree.SubElement(end, 'Endereco').text = _norm(client.address_line, 125)
        if client.address_number:
            etree.SubElement(end, 'Numero').text = _norm(client.address_number, 10)
        if client.neighborhood:
            etree.SubElement(end, 'Bairro').text = _norm(client.neighborhood, 100)
        codigo_mun = _ibge_por_cep(client.zip_code)
        if codigo_mun:
            etree.SubElement(end, 'CodigoMunicipio').text = codigo_mun
        if client.state:
            etree.SubElement(end, 'Uf').text = client.state.upper()[:2]
        cep = _only_digits(client.zip_code)
        if cep:
            etree.SubElement(end, 'Cep').text = cep

    if client.email:
        contato = etree.SubElement(tomador, 'Contato')
        etree.SubElement(contato, 'Email').text = _norm(client.email, 100)


def _sim_nao_code(value: str | None, default: int) -> str:
    """Converte a preferência do cliente ('sim'/'nao') para o código ABRASF
    (1=Sim, 2=Não). None/vazio → usa o default global do .env."""
    if value == 'sim':
        return '1'
    if value == 'nao':
        return '2'
    return str(default)


def montar_inf_rps(billing: Billing, client: Client, numero_rps: str) -> tuple[etree._Element, str]:
    """Monta <InfRps id="..."> a partir de um Billing + Client."""
    agora = dt.datetime.now(_TZ_BRASILIA)
    rps_id = f'rps{numero_rps}'
    valor = _fmt_valor(billing.amount)

    inf = etree.Element('InfRps', id=rps_id)

    ident = etree.SubElement(inf, 'IdentificacaoRps')
    etree.SubElement(ident, 'Numero').text = str(numero_rps)
    etree.SubElement(ident, 'Serie').text = settings.nfse_serie_rps
    etree.SubElement(ident, 'Tipo').text = '1'  # 1 = RPS

    etree.SubElement(inf, 'DataEmissao').text = agora.strftime('%Y-%m-%dT%H:%M:%S')
    etree.SubElement(inf, 'NaturezaOperacao').text = settings.nfse_natureza_operacao
    etree.SubElement(inf, 'OptanteSimplesNacional').text = _sim_nao_code(
        client.optante_simples, settings.nfse_optante_simples)
    etree.SubElement(inf, 'IncentivadorCultural').text = str(settings.nfse_incentivador_cultural)
    etree.SubElement(inf, 'Status').text = '1'  # 1 = Normal

    servico = etree.SubElement(inf, 'Servico')
    valores = etree.SubElement(servico, 'Valores')
    etree.SubElement(valores, 'ValorServicos').text = valor
    etree.SubElement(valores, 'IssRetido').text = _sim_nao_code(
        client.iss_retido, settings.nfse_iss_retido)
    etree.SubElement(valores, 'ValorIss').text = '0.00'
    etree.SubElement(valores, 'BaseCalculo').text = valor
    etree.SubElement(valores, 'Aliquota').text = settings.nfse_aliquota_iss
    etree.SubElement(valores, 'ValorLiquidoNfse').text = valor
    etree.SubElement(servico, 'ItemListaServico').text = settings.nfse_item_lista_servico
    etree.SubElement(servico, 'Discriminacao').text = _norm(
        billing.title or settings.nfse_discriminacao_padrao, 2000
    )
    etree.SubElement(servico, 'CodigoMunicipio').text = settings.nfse_codigo_municipio

    prestador = etree.SubElement(inf, 'Prestador')
    etree.SubElement(prestador, 'Cnpj').text = settings.nfse_cnpj
    etree.SubElement(prestador, 'InscricaoMunicipal').text = settings.nfse_inscricao_municipal

    _montar_tomador(inf, client)
    return inf, rps_id


def montar_envio_lote(
    billing: Billing, client: Client, numero_lote: str, numero_rps: str
) -> tuple[etree._Element, etree._Element, etree._Element, str, str]:
    """Monta <EnviarLoteRpsEnvio> com 1 RPS (sem assinatura)."""
    envio = etree.Element('EnviarLoteRpsEnvio', nsmap={None: NS_PUBLICA})
    lote = etree.SubElement(envio, 'LoteRps', versao='1.00', id=f'lote{numero_lote}')
    etree.SubElement(lote, 'NumeroLote').text = str(numero_lote)
    etree.SubElement(lote, 'Cnpj').text = settings.nfse_cnpj
    etree.SubElement(lote, 'InscricaoMunicipal').text = settings.nfse_inscricao_municipal
    etree.SubElement(lote, 'QuantidadeRps').text = '1'
    lista = etree.SubElement(lote, 'ListaRps')
    rps = etree.SubElement(lista, 'Rps')
    inf, rps_id = montar_inf_rps(billing, client, numero_rps)
    rps.append(inf)
    return envio, rps, inf, rps_id, f'lote{numero_lote}'


def _montar_consulta(metodo_root: str, protocolo: str) -> str:
    envio = etree.Element(metodo_root, nsmap={None: NS_PUBLICA})
    prest = etree.SubElement(envio, 'Prestador')
    etree.SubElement(prest, 'Cnpj').text = settings.nfse_cnpj
    etree.SubElement(prest, 'InscricaoMunicipal').text = settings.nfse_inscricao_municipal
    etree.SubElement(envio, 'Protocolo').text = protocolo
    return etree.tostring(envio, encoding='unicode')


def _envelope_soap(xml_interno: str, metodo: str) -> str:
    return (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:ser="{NS_SERVICE}">'
        '<soapenv:Header/><soapenv:Body>'
        f'<ser:{metodo}><XML><![CDATA[{xml_interno}]]></XML></ser:{metodo}>'
        '</soapenv:Body></soapenv:Envelope>'
    )


# ---------------------------------------------------------------------------
# Assinatura opcional (A1)
# ---------------------------------------------------------------------------

def _assinar_lote(envio: etree._Element, inf: etree._Element, rps: etree._Element,
                  rps_id: str, lote_id: str) -> str:
    """Assina o InfRps e o lote com o certificado A1 (perfil ABRASF/Pública)."""
    from pathlib import Path

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12
    from signxml import XMLSigner, methods

    data = Path(settings.nfse_cert_path).read_bytes()
    chave, cert, _ = pkcs12.load_key_and_certificates(data, settings.nfse_cert_senha.encode())
    if chave is None or cert is None:
        raise NfseError('Não foi possível ler o certificado .pfx (senha incorreta?)')
    key_pem = chave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    def _sign(elem, ref):
        signer = XMLSigner(
            method=methods.enveloped,
            signature_algorithm='rsa-sha1',
            digest_algorithm='sha1',
            c14n_algorithm='http://www.w3.org/TR/2001/REC-xml-c14n-20010315',
        )
        return signer.sign(elem, key=key_pem, cert=cert_pem, reference_uri=ref)

    inf_assinado = _sign(inf, rps_id)
    rps.replace(inf, inf_assinado)
    envio_assinado = _sign(envio, lote_id)
    return etree.tostring(envio_assinado, encoding='unicode')


# ---------------------------------------------------------------------------
# Transporte + parse das respostas
# ---------------------------------------------------------------------------

def _post(servico: str, soap: str, soapaction: str) -> str:
    url = f'{_base_url()}/{servico}'
    try:
        resp = requests.post(
            url,
            data=soap.encode('utf-8'),
            headers={'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': soapaction},
            timeout=settings.nfse_timeout_seconds,
        )
    except requests.RequestException as exc:
        raise NfseApiError(f'Falha de conexão com a prefeitura de Joinville: {exc}') from exc

    if resp.status_code >= 400:
        raise NfseApiError(
            f'HTTP {resp.status_code} da prefeitura ({soapaction}). '
            f'{"Servidor de homologação fora do ar (502)." if resp.status_code == 502 else resp.text[:300]}',
            status_code=resp.status_code,
        )
    return resp.text


def _inner_root(soap_text: str) -> etree._Element | None:
    """Extrai e parseia o XML interno (conteúdo de <return>) da resposta SOAP."""
    try:
        outer = etree.fromstring(soap_text.encode('utf-8'))
    except etree.XMLSyntaxError:
        return None
    ret = outer.xpath('.//*[local-name()="return"]')
    if not ret or not (ret[0].text or '').strip():
        return None
    try:
        return etree.fromstring(ret[0].text.strip().encode('utf-8'))
    except etree.XMLSyntaxError:
        return None


def _txt(root: etree._Element, tag: str) -> str | None:
    found = root.xpath('.//*[local-name()=$n]', n=tag)
    return found[0].text if found and found[0].text else None


def _erros(root: etree._Element) -> list[dict]:
    erros = []
    for msg in root.xpath('.//*[local-name()="MensagemRetorno"]'):
        erros.append({
            'codigo': _txt(msg, 'Codigo'),
            'mensagem': _txt(msg, 'Mensagem'),
            'correcao': _txt(msg, 'Correcao'),
        })
    return erros


def _aplicar_erro(nota: NfseNota, erros: list[dict]) -> None:
    nota.status = 'erro'
    primeiro = erros[0] if erros else {}
    nota.erro_codigo = primeiro.get('codigo')
    nota.erro_mensagem = '; '.join(
        f"{e.get('codigo')}: {e.get('mensagem')} ({e.get('correcao')})" for e in erros
    )[:2000]


# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------

def emitir_nfse(db: Session, billing: Billing, client: Client) -> NfseNota:
    """
    Emite a NFS-e de um billing: monta o RPS, envia o lote e consulta o
    resultado. Idempotente: se já houver NFS-e emitida para o billing, retorna-a.
    """
    if not settings.nfse_enabled:
        raise NfseError('Integração NFS-e desabilitada (NFSE_ENABLED=false)')

    # Respeita a preferência do cliente: só emite se "Emitir Nota Fiscal" != 'nao'
    # (None/'sim' → emite; mantém compatibilidade com clientes antigos sem o campo).
    if (client.issue_invoice or 'sim') == 'nao':
        raise NfseError('Cliente configurado para NÃO emitir nota fiscal (campo "Emitir Nota Fiscal" = Não no cadastro).')

    nota = db.query(NfseNota).filter_by(billing_id=billing.id).first()
    if nota and nota.status == 'emitida':
        return nota
    if nota is None:
        nota = NfseNota(billing_id=billing.id)
        db.add(nota)

    numero_rps = str(billing.id)
    numero_lote = str(int(time.time()))
    envio, rps, inf, rps_id, lote_id = montar_envio_lote(billing, client, numero_lote, numero_rps)

    if settings.nfse_cert_path:
        xml = _assinar_lote(envio, inf, rps, rps_id, lote_id)
    else:
        xml = etree.tostring(envio, encoding='unicode')

    nota.numero_rps = numero_rps
    nota.serie_rps = settings.nfse_serie_rps
    nota.numero_lote = numero_lote
    nota.xml_envio = xml
    nota.status = 'pending'
    nota.erro_codigo = None
    nota.erro_mensagem = None
    db.commit()

    resp_text = _post('Services', _envelope_soap(xml, 'RecepcionarLoteRps'), 'RecepcionarLoteRps')
    root = _inner_root(resp_text)
    if root is None:
        _aplicar_erro(nota, [{'codigo': None, 'mensagem': resp_text[:500], 'correcao': None}])
        db.commit()
        raise NfseApiError('Resposta inesperada da prefeitura na recepção do lote')

    protocolo = _txt(root, 'Protocolo')
    erros = _erros(root)
    if not protocolo:
        _aplicar_erro(nota, erros or [{'codigo': None, 'mensagem': 'Lote sem protocolo', 'correcao': None}])
        db.commit()
        return nota

    nota.protocolo = protocolo
    nota.status = 'processing'
    db.commit()

    # Consulta imediata (o processamento costuma ser instantâneo). SEM sleep no
    # request: se ainda estiver processando, o front reconsulta via /consultar.
    return consultar(db, nota)


def consultar(db: Session, nota: NfseNota) -> NfseNota:
    """Consulta situação + lote de um protocolo e atualiza a nota (emitida/erro/processing)."""
    if not nota.protocolo:
        raise NfseError('Nota sem protocolo — emita primeiro')

    sit_text = _post(
        'Consultas',
        _envelope_soap(_montar_consulta('ConsultarSituacaoLoteRpsEnvio', nota.protocolo), 'ConsultarSituacaoLoteRps'),
        'ConsultarSituacaoLoteRps',
    )
    sit_root = _inner_root(sit_text)
    if sit_root is not None:
        nota.situacao = _txt(sit_root, 'Situacao')

    lote_text = _post(
        'Consultas',
        _envelope_soap(_montar_consulta('ConsultarLoteRpsEnvio', nota.protocolo), 'ConsultarLoteRps'),
        'ConsultarLoteRps',
    )
    nota.xml_retorno = lote_text
    root = _inner_root(lote_text)
    if root is None:
        nota.status = 'processing'
        db.commit()
        return nota

    inf_nfse = root.xpath('.//*[local-name()="InfNfse"]')
    if inf_nfse:
        node = inf_nfse[0]
        nota.numero_nfse = _txt(node, 'Numero')
        nota.serie_nfse = _txt(node, 'Serie')
        nota.codigo_verificacao = _txt(node, 'CodigoVerificacao')
        nota.chave_acesso = _txt(node, 'ChaveAcesso')
        nota.link_visualizacao = _txt(node, 'LinkVisualizacaoNfse')
        data_emissao = _txt(node, 'DataEmissao')
        if data_emissao:
            try:
                nota.data_emissao = dt.datetime.fromisoformat(data_emissao)
            except ValueError:
                pass
        nota.status = 'emitida'
        nota.erro_codigo = None
        nota.erro_mensagem = None
        db.commit()
        return nota

    erros = _erros(root)
    if erros:
        _aplicar_erro(nota, erros)
    elif (nota.situacao or '') in _SITUACAO_PROCESSANDO:
        nota.status = 'processing'
    else:
        nota.status = 'processing'
    db.commit()
    return nota
