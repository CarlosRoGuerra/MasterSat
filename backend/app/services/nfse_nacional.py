"""
Emissão de NFS-e pelo **Emissor Nacional** (Sefin Nacional / SNNFSe), REST.

Substitui ``nfse_joinville.py``: em **20/07/2026** Joinville encerrou a emissão
pelo sistema municipal e passou a exigir o Emissor Nacional (antecipando a
Resolução CGSN nº 189/2026). O sintoma no webservice antigo é o erro **E930**
("Emissão bloqueada. A data de emissão ultrapassa o limite permitido"), que
ocorre com QUALQUER data — inclusive datas que já emitiram com sucesso. O portal
municipal segue ativo apenas para apuração do ISS.

Diferenças em relação ao módulo antigo:
  - Documento: **DPS** (Declaração de Prestação de Serviços), leiaute nacional
    v1.01, namespace ``http://www.sped.fazenda.gov.br/nfse``.
  - Transporte: REST. ``POST /nfse`` com o XML **assinado, gzipado e em base64**.
    Geração é SÍNCRONA — a resposta já traz a NFS-e ou a rejeição.
  - Autenticação: **mTLS** com certificado ICP-Brasil (e-CNPJ A1). Ao contrário
    do webservice municipal, aqui o certificado é OBRIGATÓRIO, tanto para a
    conexão quanto para assinar a DPS.

Referências (baixadas de gov.br/nfse em 22/07/2026):
  - Manual das APIs do Emissor Público Nacional v1.2
  - Esquemas XSD v1.01 (20260209) — versionados em ``schemas/nfse_nacional/``
"""
from __future__ import annotations

import base64
import datetime as dt
import gzip
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import requests
from lxml import etree
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.billing import Billing
from app.models.client import Client
from app.models.nfse_nota import NfseNota

# ---------------------------------------------------------------------------
# Constantes do padrão nacional
# ---------------------------------------------------------------------------
NS_NFSE = 'http://www.sped.fazenda.gov.br/nfse'
_NS = {'n': NS_NFSE}

# tpAmb: 1 = produção (nota com valor fiscal!), 2 = produção restrita (testes)
_AMBIENTES = {
    'producao': ('1', 'https://sefin.nfse.gov.br/SefinNacional'),
    'producao_restrita': ('2', 'https://sefin.producaorestrita.nfse.gov.br/API/SefinNacional'),
}

_SCHEMA_DIR = Path(__file__).parent / 'schemas' / 'nfse_nacional'

# Fuso de Brasília (UTC-3). O container roda em UTC; dhEmi é TSDateTimeUTC e
# precisa do offset explícito.
_TZ_BRASILIA = dt.timezone(dt.timedelta(hours=-3))


class NfseError(Exception):
    """Erro de configuração/estado — nada foi enviado à Sefin Nacional."""


class NfseApiError(Exception):
    """Rejeição da Sefin Nacional ou falha de transporte."""

    def __init__(self, message: str, codigo: str | None = None, status_code: int | None = None):
        self.codigo = codigo
        self.status_code = status_code
        super().__init__(message)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ambiente() -> tuple[str, str]:
    try:
        return _AMBIENTES[settings.nfse_nac_ambiente]
    except KeyError:
        raise NfseError(
            f'NFSE_NAC_AMBIENTE inválido: {settings.nfse_nac_ambiente!r} '
            '(use producao|producao_restrita)'
        ) from None


def _only_digits(value: str | None) -> str:
    return ''.join(c for c in (value or '') if c.isdigit())


def _norm(value: str | None, max_len: int) -> str:
    return (value or '').strip()[:max_len]


def _fmt_valor(value) -> str:
    return f'{Decimal(str(value or 0)):.2f}'


def _sub(pai: etree._Element, tag: str, texto: str | None = None) -> etree._Element:
    """SubElement no namespace da NFS-e nacional (todos os elementos são qualificados)."""
    el = etree.SubElement(pai, f'{{{NS_NFSE}}}{tag}')
    if texto is not None:
        el.text = texto
    return el


def id_dps(numero_dps: str) -> str:
    """
    Id da DPS = 'DPS' + cLocEmi(7) + tpInsc(1) + inscrição(14) + série(5) + nDPS(15).

    tpInsc: 1 = CPF (completar com 000 à esquerda), 2 = CNPJ.
    """
    cnpj = _only_digits(settings.nfse_cnpj)
    return (
        'DPS'
        + settings.nfse_codigo_municipio.zfill(7)
        + '2'
        + cnpj.zfill(14)
        + settings.nfse_nac_serie.zfill(5)
        + str(numero_dps).zfill(15)
    )


# ---------------------------------------------------------------------------
# Montagem da DPS
# ---------------------------------------------------------------------------

def _montar_endereco(pai: etree._Element, client: Client) -> None:
    """<end> do tomador. Só endereço nacional — não atendemos exterior."""
    end = _sub(pai, 'end')
    nac = _sub(end, 'endNac')
    _sub(nac, 'cMun', _codigo_municipio_tomador(client))
    _sub(nac, 'CEP', _only_digits(client.zip_code))
    _sub(end, 'xLgr', _norm(client.address_line, 255))
    # <nro> é 1-1 (1 a 60 caracteres) no leiaute — não aceita vazio. Cadastro sem
    # número usa "S/N", a convenção usual para logradouro sem numeração.
    _sub(end, 'nro', _norm(client.address_number, 60) or 'S/N')
    if (client.address_complement or '').strip():
        _sub(end, 'xCpl', _norm(client.address_complement, 156))
    _sub(end, 'xBairro', _norm(client.neighborhood, 60))


def _codigo_municipio_tomador(client: Client) -> str:
    """
    Código IBGE do município do tomador. Usa o campo do cadastro quando existir;
    senão resolve pelo CEP (ViaCEP). Sem código, a Sefin rejeita a DPS.
    """
    codigo = _only_digits(getattr(client, 'city_ibge_code', None))
    if len(codigo) == 7:
        return codigo
    from app.services.nfse_joinville import _ibge_por_cep  # cache compartilhado

    codigo = _ibge_por_cep(client.zip_code) or ''
    if len(_only_digits(codigo)) != 7:
        raise NfseError(
            f'Não foi possível determinar o código IBGE do município do tomador '
            f'"{client.name}" (CEP {client.zip_code!r}). Confira o CEP no cadastro.'
        )
    return _only_digits(codigo)


def _montar_prestador(inf: etree._Element) -> None:
    prest = _sub(inf, 'prest')
    _sub(prest, 'CNPJ', _only_digits(settings.nfse_cnpj))
    if settings.nfse_inscricao_municipal:
        _sub(prest, 'IM', _only_digits(settings.nfse_inscricao_municipal))
    reg = _sub(prest, 'regTrib')
    # opSimpNac: 1=Não optante, 2=Optante MEI, 3=Optante ME/EPP
    _sub(reg, 'opSimpNac', settings.nfse_nac_op_simples_nacional)
    if settings.nfse_nac_op_simples_nacional == '3' and settings.nfse_nac_reg_apur_simples:
        _sub(reg, 'regApTribSN', settings.nfse_nac_reg_apur_simples)
    # regEspTrib: 0=Nenhum
    _sub(reg, 'regEspTrib', settings.nfse_nac_regime_especial)


def _montar_tomador(inf: etree._Element, client: Client) -> None:
    toma = _sub(inf, 'toma')
    doc = _only_digits(client.cpf_cnpj)
    if (client.type or 'pf').lower() == 'pf':
        _sub(toma, 'CPF', doc.zfill(11))
    else:
        _sub(toma, 'CNPJ', doc.zfill(14))
    _sub(toma, 'xNome', _norm(client.name, 300))
    _montar_endereco(toma, client)
    if (client.phone or '').strip():
        _sub(toma, 'fone', _only_digits(client.phone))
    if (client.email or '').strip():
        _sub(toma, 'email', _norm(client.email, 80))


def _montar_servico(inf: etree._Element, billing: Billing) -> None:
    serv = _sub(inf, 'serv')
    loc = _sub(serv, 'locPrest')
    _sub(loc, 'cLocPrestacao', settings.nfse_codigo_municipio)
    cserv = _sub(serv, 'cServ')
    # cTribNac: código da lista de serviços NACIONAL (6 dígitos), não o da LC116
    # em 4 dígitos usado no padrão antigo. 11.02 (monitoramento) → 110200.
    _sub(cserv, 'cTribNac', settings.nfse_nac_cod_trib_nacional)
    _sub(cserv, 'xDescServ', _norm(
        billing.title or settings.nfse_discriminacao_padrao, 2000))
    if settings.nfse_nac_cod_nbs:
        _sub(cserv, 'cNBS', settings.nfse_nac_cod_nbs)


def _montar_valores(inf: etree._Element, billing: Billing) -> None:
    valores = _sub(inf, 'valores')
    vserv = _sub(valores, 'vServPrest')
    _sub(vserv, 'vServ', _fmt_valor(billing.amount))

    trib = _sub(valores, 'trib')
    mun = _sub(trib, 'tribMun')
    # tribISSQN: 1=Operação tributável, 2=Imunidade, 3=Exportação, 4=Não incidência
    _sub(mun, 'tribISSQN', settings.nfse_nac_trib_issqn)
    # tpRetISSQN: 1=Não retido, 2=Retido pelo tomador, 3=Retido pelo intermediário
    _sub(mun, 'tpRetISSQN', settings.nfse_nac_tipo_ret_issqn)

    # totTrib é um <choice>: informamos indTotTrib=0 (não informar valor estimado
    # de tributos, Decreto 8.264/2014) ou o percentual do Simples, nunca ambos.
    tot = _sub(trib, 'totTrib')
    if settings.nfse_nac_perc_trib_simples:
        _sub(tot, 'pTotTribSN', settings.nfse_nac_perc_trib_simples)
    else:
        _sub(tot, 'indTotTrib', '0')


def validar_cadastro_tomador(client: Client) -> None:
    """
    Confere o cadastro do cliente antes de montar a DPS. Sem isso, a falha vem
    como erro de esquema XSD ("facet 'minLength'..."), que não diz ao operador
    qual campo preencher. Só entram aqui os campos 1-1 que não têm default.
    """
    faltando: list[str] = []
    if not (client.name or '').strip():
        faltando.append('nome/razão social')
    if not _only_digits(client.cpf_cnpj):
        faltando.append('CPF/CNPJ')
    if not (client.address_line or '').strip():
        faltando.append('logradouro')
    if not (client.neighborhood or '').strip():
        faltando.append('bairro')
    if len(_only_digits(client.zip_code)) != 8:
        faltando.append('CEP (8 dígitos)')
    if faltando:
        raise NfseError(
            f'Cadastro do cliente "{client.name or getattr(client, "id", "?")}" está incompleto '
            f'para emitir NFS-e. Preencha: {", ".join(faltando)}. '
            '(Endereço sem número é aceito — vai como "S/N".)'
        )


def montar_dps(billing: Billing, client: Client, numero_dps: str) -> etree._Element:
    """Monta o <DPS> completo (sem assinatura) para um Billing/Client."""
    tp_amb, _ = _ambiente()
    validar_cadastro_tomador(client)
    agora = dt.datetime.now(_TZ_BRASILIA)

    dps = etree.Element(f'{{{NS_NFSE}}}DPS', nsmap={None: NS_NFSE})
    dps.set('versao', '1.01')
    inf = _sub(dps, 'infDPS')
    inf.set('Id', id_dps(numero_dps))

    _sub(inf, 'tpAmb', tp_amb)
    _sub(inf, 'dhEmi', agora.strftime('%Y-%m-%dT%H:%M:%S%z')[:-2] + ':' + agora.strftime('%z')[-2:])
    _sub(inf, 'verAplic', settings.nfse_nac_ver_aplic)
    _sub(inf, 'serie', settings.nfse_nac_serie)
    _sub(inf, 'nDPS', str(numero_dps))
    _sub(inf, 'dCompet', agora.strftime('%Y-%m-%d'))
    # tpEmit: 1=Prestador, 2=Tomador, 3=Intermediário
    _sub(inf, 'tpEmit', '1')
    _sub(inf, 'cLocEmi', settings.nfse_codigo_municipio)

    _montar_prestador(inf)
    _montar_tomador(inf, client)
    _montar_servico(inf, billing)
    _montar_valores(inf, billing)
    return dps


# ---------------------------------------------------------------------------
# Validação contra o XSD oficial
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _schema_dps() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(_SCHEMA_DIR / 'DPS_v1.01.xsd')))


# Defeito conhecido do XSD oficial v1.01: TSSerieDPS declara o pattern
# "^0{0,4}\d{1,5}$". Em XML Schema o pattern já é implicitamente ancorado, então
# ^ e $ valem como caracteres LITERAIS — nenhum valor normal passa. É o único
# tipo do esquema inteiro com âncoras, o que confirma ser engano de quem
# publicou. Ignoramos esse erro específico e validamos <serie> com a regra que
# o autor claramente quis, sem mexer no arquivo oficial (que fica idêntico ao
# baixado de gov.br e pode ser diferenciado a cada nova versão).
_SERIE_VALIDA = re.compile(r'^0{0,4}\d{1,5}$')


def _erro_do_defeito_conhecido(erro) -> bool:
    return f'{{{NS_NFSE}}}serie' in (erro.message or '') and "facet 'pattern'" in (erro.message or '')


def validar_dps(dps: etree._Element) -> None:
    """
    Valida a DPS contra o esquema oficial ANTES de enviar. Barato, e evita
    rejeição de layout na Sefin (que conta contra limites de uso).
    """
    serie = dps.findtext(f'{{{NS_NFSE}}}infDPS/{{{NS_NFSE}}}serie') or ''
    if not _SERIE_VALIDA.match(serie):
        raise NfseError(f'Série da DPS inválida: {serie!r} (esperado 1 a 5 dígitos)')

    schema = _schema_dps()
    if schema.validate(etree.ElementTree(dps)):
        return
    erros = [e for e in schema.error_log if not _erro_do_defeito_conhecido(e)]
    if erros:
        detalhe = '; '.join(f'linha {e.line}: {e.message}' for e in erros)
        raise NfseError(f'DPS inválida perante o esquema oficial v1.01 — {detalhe}')


# ---------------------------------------------------------------------------
# Certificado, assinatura e transporte
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _material_certificado() -> tuple[bytes, bytes]:
    """Carrega o .pfx e devolve (chave_pem, certificado_pem)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12

    if not settings.nfse_cert_path:
        raise NfseError(
            'Certificado digital obrigatório para o Emissor Nacional. '
            'Configure NFSE_CERT_PATH (e-CNPJ A1, .pfx/.p12) e NFSE_CERT_SENHA.'
        )
    caminho = Path(settings.nfse_cert_path)
    if not caminho.exists():
        raise NfseError(f'Certificado não encontrado em {caminho}')

    chave, cert, _ = pkcs12.load_key_and_certificates(
        caminho.read_bytes(), settings.nfse_cert_senha.encode()
    )
    if chave is None or cert is None:
        raise NfseError('Não foi possível ler o certificado .pfx (senha incorreta?)')
    return (
        chave.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        cert.public_bytes(serialization.Encoding.PEM),
    )


@lru_cache(maxsize=1)
def _par_pem_mtls() -> tuple[str, str]:
    """
    Escreve cert e chave em PEM temporários — o ``requests`` exige caminhos de
    arquivo para mTLS, não bytes em memória.
    """
    import tempfile

    key_pem, cert_pem = _material_certificado()
    tmp = Path(tempfile.mkdtemp(prefix='nfse_mtls_'))
    cert_file, key_file = tmp / 'cert.pem', tmp / 'key.pem'
    cert_file.write_bytes(cert_pem)
    key_file.write_bytes(key_pem)
    key_file.chmod(0o600)
    return str(cert_file), str(key_file)


NS_DSIG = 'http://www.w3.org/2000/09/xmldsig#'


def assinar_dps(dps: etree._Element) -> etree._Element:
    """Assina a DPS (XMLDSig enveloped) referenciando o Id de <infDPS>."""
    from signxml import XMLSigner, methods

    key_pem, cert_pem = _material_certificado()
    inf = dps.find(f'{{{NS_NFSE}}}infDPS')
    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm=settings.nfse_nac_alg_assinatura,
        digest_algorithm=settings.nfse_nac_alg_digest,
        c14n_algorithm='http://www.w3.org/TR/2001/REC-xml-c14n-20010315',
    )
    # RN de recepção E1228: "Uso de prefixo de namespace não permitido na área
    # de dados descompactada". O signxml assina com <ds:Signature> por padrão —
    # declaramos o namespace da assinatura como default para sair
    # <Signature xmlns="http://www.w3.org/2000/09/xmldsig#">, sem prefixo.
    signer.namespaces = {None: NS_DSIG}
    assinada = signer.sign(dps, key=key_pem, cert=cert_pem, reference_uri=inf.get('Id'))
    _garantir_sem_prefixo(assinada)
    return assinada


def _garantir_sem_prefixo(dps: etree._Element) -> None:
    """
    Rede de segurança para a E1228: rejeita localmente se sobrou qualquer
    prefixo de namespace no XML assinado. Melhor falhar aqui do que levar
    rejeição da Sefin.
    """
    xml = etree.tostring(dps, encoding='unicode')
    prefixos = {
        el.tag.split('}')[0][1:] if el.tag.startswith('{') else '': el.prefix
        for el in dps.iter() if isinstance(el.tag, str)
    }
    usados = sorted({p for p in prefixos.values() if p})
    if usados:
        raise NfseError(
            f'XML da DPS saiu com prefixo(s) de namespace {usados} — a Sefin '
            f'rejeita com E1228. Verifique a configuração da assinatura. XML: {xml[:300]}'
        )


def _compactar(xml: str) -> str:
    """XML → gzip → base64, que é como a API recebe a DPS."""
    return base64.b64encode(gzip.compress(xml.encode('utf-8'))).decode('ascii')


def _descompactar(b64: str) -> str:
    return gzip.decompress(base64.b64decode(b64)).decode('utf-8')


def _post(caminho: str, payload: dict) -> requests.Response:
    _, base = _ambiente()
    try:
        return requests.post(
            f'{base}{caminho}',
            json=payload,
            headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
            cert=_par_pem_mtls(),
            timeout=settings.nfse_timeout_seconds,
        )
    except requests.RequestException as exc:
        raise NfseApiError(f'Falha de conexão com a Sefin Nacional: {exc}') from exc


def _erro_da_resposta(resp: requests.Response) -> str:
    """Extrai a mensagem de rejeição do corpo JSON da Sefin."""
    try:
        corpo = resp.json()
    except ValueError:
        return resp.text[:500]
    if isinstance(corpo, dict):
        erros = corpo.get('erros') or corpo.get('Erros') or []
        if erros:
            return '; '.join(
                f"{e.get('Codigo') or e.get('codigo')}: "
                f"{e.get('Descricao') or e.get('descricao') or e.get('Mensagem')}"
                for e in erros if isinstance(e, dict)
            )[:2000]
        for chave in ('mensagem', 'Mensagem', 'message', 'title'):
            if corpo.get(chave):
                return str(corpo[chave])[:500]
    return str(corpo)[:500]


# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------

def emitir_nfse(db: Session, billing: Billing, client: Client) -> NfseNota:
    """
    Emite a NFS-e de um billing pelo Emissor Nacional. Idempotente: se já houver
    nota emitida para o billing, retorna-a sem reenviar.
    """
    if not settings.nfse_enabled:
        raise NfseError('Integração NFS-e desabilitada (NFSE_ENABLED=false)')
    if (client.issue_invoice or 'sim') == 'nao':
        raise NfseError(
            'Cliente configurado para NÃO emitir nota fiscal '
            '(campo "Emitir Nota Fiscal" = Não no cadastro).'
        )

    nota = db.query(NfseNota).filter_by(billing_id=billing.id).first()
    if nota and nota.status == 'emitida':
        return nota
    if nota is None:
        nota = NfseNota(billing_id=billing.id)
        db.add(nota)

    numero_dps = str(billing.id)
    dps = montar_dps(billing, client, numero_dps)
    validar_dps(dps)
    assinada = assinar_dps(dps)
    xml = etree.tostring(assinada, encoding='unicode')

    nota.numero_rps = numero_dps
    nota.serie_rps = settings.nfse_nac_serie
    nota.xml_envio = xml
    nota.status = 'pending'
    nota.erro_codigo = None
    nota.erro_mensagem = None
    db.commit()

    resp = _post('/nfse', {'dpsXmlGZipB64': _compactar(xml)})

    if resp.status_code not in (200, 201):
        mensagem = _erro_da_resposta(resp)
        nota.status = 'erro'
        nota.erro_codigo = str(resp.status_code)
        nota.erro_mensagem = mensagem[:2000]
        db.commit()
        raise NfseApiError(
            f'Sefin Nacional rejeitou a DPS (HTTP {resp.status_code}): {mensagem}',
            status_code=resp.status_code,
        )

    corpo = resp.json()
    nfse_b64 = corpo.get('nfseXmlGZipB64') or corpo.get('NfseXmlGZipB64')
    if not nfse_b64:
        nota.status = 'erro'
        nota.erro_mensagem = f'Resposta sem XML da NFS-e: {str(corpo)[:500]}'
        db.commit()
        raise NfseApiError('Sefin Nacional não devolveu o XML da NFS-e')

    xml_nfse = _descompactar(nfse_b64)
    nota.xml_retorno = xml_nfse
    _aplicar_nfse(nota, xml_nfse, corpo)
    db.commit()
    return nota


def _aplicar_nfse(nota: NfseNota, xml_nfse: str, corpo: dict) -> None:
    """Extrai os dados da NFS-e gerada e marca a nota como emitida."""
    raiz = etree.fromstring(xml_nfse.encode('utf-8'))
    inf = raiz.find(f'.//{{{NS_NFSE}}}infNFSe')

    chave = corpo.get('chaveAcesso') or corpo.get('ChaveAcesso')
    if inf is not None:
        chave = chave or inf.get('Id', '').removeprefix('NFS')
        nota.numero_nfse = _texto(inf, 'nNFSe')
        data = _texto(inf, 'dhProc') or _texto(inf, 'dhEmi')
        if data:
            try:
                nota.data_emissao = dt.datetime.fromisoformat(data)
            except ValueError:
                pass
    nota.chave_acesso = chave
    if chave:
        nota.link_visualizacao = f'https://www.nfse.gov.br/consultapublica/?chave={chave}'
    nota.status = 'emitida'
    nota.erro_codigo = None
    nota.erro_mensagem = None


def _texto(no: etree._Element, tag: str) -> str | None:
    achado = no.find(f'.//{{{NS_NFSE}}}{tag}')
    return achado.text if achado is not None else None


def consultar_por_chave(chave_acesso: str) -> str:
    """GET /nfse/{chaveAcesso} — devolve o XML da NFS-e."""
    _, base = _ambiente()
    try:
        resp = requests.get(
            f'{base}/nfse/{chave_acesso}',
            headers={'Accept': 'application/json'},
            cert=_par_pem_mtls(),
            timeout=settings.nfse_timeout_seconds,
        )
    except requests.RequestException as exc:
        raise NfseApiError(f'Falha de conexão com a Sefin Nacional: {exc}') from exc

    if resp.status_code != 200:
        raise NfseApiError(
            f'Consulta falhou (HTTP {resp.status_code}): {_erro_da_resposta(resp)}',
            status_code=resp.status_code,
        )
    corpo = resp.json()
    b64 = corpo.get('nfseXmlGZipB64') or corpo.get('NfseXmlGZipB64')
    return _descompactar(b64) if b64 else str(corpo)
