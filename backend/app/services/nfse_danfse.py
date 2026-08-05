"""
DANFSE gerado por nós, a partir do XML da própria nota.

Por que existe: o PDF oficial vem de um serviço do governo (``/danfse`` no ADN)
que não está implantado na produção restrita e que, em produção, já ficou horas
com o pool de servidores vazio devolvendo 503. O XML autenticado, esse, está
sempre no banco — e ele contém tudo que a nota precisa mostrar. Então montamos
a representação visual aqui e o PDF deixa de depender de terceiro.

O documento fiscal continua sendo o XML; isto é a *representação* dele, como o
DANFE é da NF-e. O rodapé diz isso e leva à consulta pública, onde qualquer um
confere a autenticidade pela chave.

Entende os dois formatos que existem no banco:
  • nacional  — <NFSe><infNFSe> (Sefin Nacional, padrão desde 20/07/2026)
  • Joinville — <ConsultarLoteRpsResposta> do webservice municipal legado, que
    ficou em notas antigas e cujo link de visualização morreu junto com o
    sistema da prefeitura.
"""
from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass, field

from lxml import etree
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NS_NFSE = 'http://www.sped.fazenda.gov.br/nfse'
NS_JOINVILLE = 'http://www.publica.inf.br'

_CINZA_TITULO = colors.HexColor('#E8EDF3')
_BORDA = colors.HexColor('#9AA7B4')
_TEXTO = colors.HexColor('#1F2933')
_ROTULO = colors.HexColor('#5B6B7B')


class DanfseError(Exception):
    """XML ausente ou em formato que não sabemos desenhar."""


# ---------------------------------------------------------------------------
# Modelo intermediário — os dois formatos caem aqui e o desenho só conhece isto
# ---------------------------------------------------------------------------

@dataclass
class Pessoa:
    nome: str = ''
    documento: str = ''
    inscricao_municipal: str = ''
    endereco: str = ''
    municipio: str = ''
    cep: str = ''
    fone: str = ''
    email: str = ''


@dataclass
class Danfse:
    formato: str = 'nacional'   # 'nacional' | 'joinville'
    numero: str = ''
    serie: str = ''
    chave: str = ''
    codigo_verificacao: str = ''
    data_emissao: str = ''
    competencia: str = ''
    municipio_emissao: str = ''
    municipio_prestacao: str = ''
    teste: bool = False
    prestador: Pessoa = field(default_factory=Pessoa)
    tomador: Pessoa = field(default_factory=Pessoa)
    descricao_servico: str = ''
    codigo_servico: str = ''
    descricao_tributacao: str = ''
    valores: list[tuple[str, str]] = field(default_factory=list)
    valor_liquido: str = ''
    outras_informacoes: str = ''
    origem: str = ''
    consulta_url: str = ''


# ---------------------------------------------------------------------------
# Leitura do XML
# ---------------------------------------------------------------------------

def _filho(no, ns: str, *tags: str):
    """Desce por filhos diretos. Evita o `.//` global, que confundiria tags de
    mesmo nome em blocos diferentes (CNPJ existe em emit, prest e toma)."""
    atual = no
    for tag in tags:
        if atual is None:
            return None
        atual = atual.find(f'{{{ns}}}{tag}')
    return atual


def _txt(no, ns: str, *tags: str) -> str:
    el = _filho(no, ns, *tags)
    return (el.text or '').strip() if el is not None and el.text else ''


def _doc_formatado(valor: str) -> str:
    d = ''.join(c for c in valor if c.isdigit())
    if len(d) == 14:
        return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
    if len(d) == 11:
        return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'
    return valor


def _cep_formatado(valor: str) -> str:
    d = ''.join(c for c in valor if c.isdigit())
    return f'{d[:5]}-{d[5:]}' if len(d) == 8 else valor


def _fone_formatado(valor: str) -> str:
    d = ''.join(c for c in valor if c.isdigit())
    if len(d) == 11:
        return f'({d[:2]}) {d[2:7]}-{d[7:]}'
    if len(d) == 10:
        return f'({d[:2]}) {d[2:6]}-{d[6:]}'
    return valor


def _brl(valor: str) -> str:
    """Os valores vêm como '99.90' / '651.61000'. Sai 'R$ 651,61'."""
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return valor or '—'
    inteiro, _, dec = f'{n:,.2f}'.partition('.')
    return f'R$ {inteiro.replace(",", ".")},{dec}'


def _pct(valor: str) -> str:
    try:
        return f'{float(valor):.2f}'.replace('.', ',') + ' %'
    except (TypeError, ValueError):
        return valor or '—'


def _data_hora(valor: str) -> str:
    """'2026-08-05T09:18:26-03:00' → '05/08/2026 09:18:26'."""
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?', valor or '')
    if m:
        a, mes, d, h, mi, s = m.groups()
        return f'{d}/{mes}/{a} {h}:{mi}' + (f':{s}' if s else '')
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})$', valor or '')
    if m:
        a, mes, d = m.groups()
        return f'{d}/{mes}/{a}'
    m = re.match(r'(\d{4})-(\d{2})$', valor or '')
    if m:
        return f'{m.group(2)}/{m.group(1)}'
    return valor or ''


def _endereco_nacional(end, municipios: dict[str, str]) -> tuple[str, str, str]:
    """
    (logradouro completo, município/UF, CEP) de um bloco de endereço.

    Os dois lados da nota usam tipos diferentes: o emitente é TCEnderecoEmitente,
    com cMun/UF/CEP soltos; o tomador é TCEndereco, que aninha cMun e CEP dentro
    de <endNac>. Aqui aceita as duas formas.
    """
    if end is None:
        return '', '', ''
    partes = [
        _txt(end, NS_NFSE, 'xLgr'),
        _txt(end, NS_NFSE, 'nro'),
        _txt(end, NS_NFSE, 'xCpl'),
        _txt(end, NS_NFSE, 'xBairro'),
    ]
    cmun = _txt(end, NS_NFSE, 'endNac', 'cMun') or _txt(end, NS_NFSE, 'cMun')
    cep = _txt(end, NS_NFSE, 'endNac', 'CEP') or _txt(end, NS_NFSE, 'CEP')
    uf = _txt(end, NS_NFSE, 'UF')
    municipio = municipios.get(cmun, cmun)
    if uf and municipio:
        municipio = f'{municipio}/{uf}'
    return ', '.join(p for p in partes if p), municipio, _cep_formatado(cep)


def _ler_nacional(raiz) -> Danfse:
    inf = _filho(raiz, NS_NFSE, 'infNFSe')
    if inf is None:
        raise DanfseError('XML da NFS-e sem o bloco infNFSe')
    dps = _filho(inf, NS_NFSE, 'DPS', 'infDPS')

    d = Danfse()
    d.numero = _txt(inf, NS_NFSE, 'nNFSe')
    d.chave = (inf.get('Id') or '').removeprefix('NFS')
    d.data_emissao = _data_hora(_txt(inf, NS_NFSE, 'dhProc'))
    d.municipio_emissao = _txt(inf, NS_NFSE, 'xLocEmi')
    d.municipio_prestacao = _txt(inf, NS_NFSE, 'xLocPrestacao')
    d.descricao_tributacao = _txt(inf, NS_NFSE, 'xTribNac')
    d.outras_informacoes = _txt(inf, NS_NFSE, 'xOutInf')

    # Só os municípios que o próprio XML nomeia — dá para traduzir o código do
    # endereço do tomador sem carregar a tabela do IBGE inteira.
    municipios: dict[str, str] = {}
    if dps is not None:
        if _txt(dps, NS_NFSE, 'cLocEmi'):
            municipios[_txt(dps, NS_NFSE, 'cLocEmi')] = d.municipio_emissao
        cloc = _txt(dps, NS_NFSE, 'serv', 'locPrest', 'cLocPrestacao')
        if cloc:
            municipios[cloc] = d.municipio_prestacao

    emit = _filho(inf, NS_NFSE, 'emit')
    if emit is not None:
        end, mun, cep = _endereco_nacional(_filho(emit, NS_NFSE, 'enderNac'), municipios)
        d.prestador = Pessoa(
            nome=_txt(emit, NS_NFSE, 'xNome'),
            documento=_doc_formatado(_txt(emit, NS_NFSE, 'CNPJ') or _txt(emit, NS_NFSE, 'CPF')),
            inscricao_municipal=_txt(emit, NS_NFSE, 'IM'),
            endereco=end, municipio=mun or d.municipio_emissao, cep=cep,
            fone=_fone_formatado(_txt(emit, NS_NFSE, 'fone')),
            email=_txt(emit, NS_NFSE, 'email'),
        )

    if dps is not None:
        d.serie = _txt(dps, NS_NFSE, 'serie')
        d.competencia = _data_hora(_txt(dps, NS_NFSE, 'dCompet'))
        d.teste = _txt(dps, NS_NFSE, 'tpAmb') == '2'
        d.origem = f'DPS nº {_txt(dps, NS_NFSE, "nDPS")} série {d.serie}'
        if not d.data_emissao:
            d.data_emissao = _data_hora(_txt(dps, NS_NFSE, 'dhEmi'))

        toma = _filho(dps, NS_NFSE, 'toma')
        if toma is not None:
            end, mun, cep = _endereco_nacional(_filho(toma, NS_NFSE, 'end'), municipios)
            d.tomador = Pessoa(
                nome=_txt(toma, NS_NFSE, 'xNome'),
                documento=_doc_formatado(
                    _txt(toma, NS_NFSE, 'CNPJ') or _txt(toma, NS_NFSE, 'CPF')
                    or _txt(toma, NS_NFSE, 'NIF')),
                inscricao_municipal=_txt(toma, NS_NFSE, 'IM'),
                endereco=end, municipio=mun, cep=cep,
                fone=_fone_formatado(_txt(toma, NS_NFSE, 'fone')),
                email=_txt(toma, NS_NFSE, 'email'),
            )

        serv = _filho(dps, NS_NFSE, 'serv')
        if serv is not None:
            d.descricao_servico = _txt(serv, NS_NFSE, 'cServ', 'xDescServ')
            d.codigo_servico = _txt(serv, NS_NFSE, 'cServ', 'cTribNac')

    val = _filho(inf, NS_NFSE, 'valores')
    dps_val = _filho(dps, NS_NFSE, 'valores') if dps is not None else None
    bruto = _txt(dps_val, NS_NFSE, 'vServPrest', 'vServ') if dps_val is not None else ''
    aliq = _txt(val, NS_NFSE, 'pAliqAplic') if val is not None else ''
    d.valores = [
        ('Valor do serviço', _brl(bruto)),
        ('Base de cálculo', _brl(_txt(val, NS_NFSE, 'vBC')) if val is not None else '—'),
        ('Alíquota', _pct(aliq) if aliq else '—'),
        ('ISSQN', _brl(_txt(val, NS_NFSE, 'vISSQN')) if val is not None else '—'),
        ('Retenções', _brl(_txt(val, NS_NFSE, 'vTotalRet')) if val is not None else '—'),
    ]
    d.valor_liquido = _brl(_txt(val, NS_NFSE, 'vLiq')) if val is not None else _brl(bruto)
    return d


def _ler_joinville(raiz) -> Danfse:
    """Formato do webservice municipal legado (notas anteriores a 20/07/2026)."""
    inf = raiz.find(f'.//{{{NS_JOINVILLE}}}InfNfse')
    if inf is None:
        raise DanfseError('XML da NFS-e sem o bloco InfNfse')
    ns = NS_JOINVILLE

    d = Danfse(formato='joinville')
    d.numero = _txt(inf, ns, 'Numero')
    d.serie = _txt(inf, ns, 'Serie')
    d.chave = _txt(inf, ns, 'ChaveAcesso')
    d.codigo_verificacao = _txt(inf, ns, 'CodigoVerificacao')
    d.data_emissao = _data_hora(_txt(inf, ns, 'DataEmissao'))
    d.competencia = _data_hora(_txt(inf, ns, 'Competencia'))
    d.municipio_emissao = _txt(inf, ns, 'CodLocPrestDesc')
    d.municipio_prestacao = d.municipio_emissao
    d.outras_informacoes = _txt(inf, ns, 'OutrasInformacoes')
    d.origem = f'RPS nº {_txt(inf, ns, "IdentificacaoRps", "Numero")} ' \
               f'série {_txt(inf, ns, "IdentificacaoRps", "Serie")}'
    # A prefeitura carimbava "SEM VALOR LEGAL" nas notas de homologação.
    d.teste = 'SEM VALOR LEGAL' in d.outras_informacoes.upper()

    # O endereço legado só traz o código IBGE; o nome do município aparece uma
    # única vez na nota, em CodLocPrestDesc.
    municipios = {_txt(inf, ns, 'CodLocPrestCod'): d.municipio_emissao}

    def _pessoa(bloco, tag_doc: str) -> Pessoa:
        if bloco is None:
            return Pessoa()
        ident = _filho(bloco, ns, tag_doc)
        doc = ''
        if ident is not None:
            doc = (_txt(ident, ns, 'Cnpj') or _txt(ident, ns, 'Cpf')
                   or _txt(ident, ns, 'CpfCnpj', 'Cnpj') or _txt(ident, ns, 'CpfCnpj', 'Cpf'))
        end = _filho(bloco, ns, 'Endereco')
        partes = [_txt(end, ns, 'Endereco'), _txt(end, ns, 'Numero'),
                  _txt(end, ns, 'Complemento'), _txt(end, ns, 'Bairro')] if end is not None else []
        municipio = ''
        if end is not None:
            cidade = municipios.get(_txt(end, ns, 'CodigoMunicipio'), '')
            uf = _txt(end, ns, 'Uf')
            municipio = '/'.join(x for x in (cidade, uf) if x)
        return Pessoa(
            nome=_txt(bloco, ns, 'RazaoSocial'),
            documento=_doc_formatado(doc),
            inscricao_municipal=_txt(ident, ns, 'InscricaoMunicipal') if ident is not None else '',
            endereco=', '.join(p for p in partes if p),
            municipio=municipio,
            cep=_cep_formatado(_txt(end, ns, 'Cep')) if end is not None else '',
            fone=_fone_formatado(_txt(bloco, ns, 'Contato', 'Telefone')),
            email=_txt(bloco, ns, 'Contato', 'Email'),
        )

    d.prestador = _pessoa(_filho(inf, ns, 'PrestadorServico'), 'IdentificacaoPrestador')
    d.tomador = _pessoa(_filho(inf, ns, 'TomadorServico'), 'IdentificacaoTomador')

    serv = _filho(inf, ns, 'Servico')
    if serv is not None:
        d.descricao_servico = _txt(serv, ns, 'Discriminacao')
        d.codigo_servico = _txt(serv, ns, 'ItemListaServico')
        v = _filho(serv, ns, 'Valores')
        if v is not None:
            d.valores = [
                ('Valor do serviço', _brl(_txt(v, ns, 'ValorServicos'))),
                ('Deduções', _brl(_txt(v, ns, 'ValorDeducoes'))),
                ('Base de cálculo', _brl(_txt(v, ns, 'BaseCalculo'))),
                ('Alíquota', _pct(_txt(v, ns, 'Aliquota'))),
                ('ISSQN', _brl(_txt(v, ns, 'ValorIss'))),
                ('ISS retido', _brl(_txt(v, ns, 'ValorIssRetido'))),
            ]
            d.valor_liquido = _brl(_txt(v, ns, 'ValorLiquidoNfse'))
    return d


def ler_xml(xml: str) -> Danfse:
    """Normaliza o XML da nota (nacional ou Joinville) no modelo do desenho."""
    if not (xml or '').strip():
        raise DanfseError('Nota sem XML guardado')

    texto = xml.strip()
    # A resposta do webservice de Joinville vem com o XML da nota escapado
    # dentro de um envelope SOAP; sem desembrulhar não há o que ler.
    if 'ConsultarLoteRpsResponse' in texto or '&lt;' in texto[:400]:
        m = re.search(r'<return>(.*?)</return>', texto, re.S)
        if m:
            texto = html.unescape(m.group(1)).strip()

    try:
        raiz = etree.fromstring(texto.encode('utf-8'))
    except etree.XMLSyntaxError as exc:
        raise DanfseError(f'XML da nota ilegível: {exc}') from exc

    if NS_NFSE in (raiz.tag or '') or raiz.find(f'.//{{{NS_NFSE}}}infNFSe') is not None:
        return _ler_nacional(raiz)
    if raiz.find(f'.//{{{NS_JOINVILLE}}}InfNfse') is not None:
        return _ler_joinville(raiz)
    raise DanfseError('Formato de XML de NFS-e não reconhecido')


# ---------------------------------------------------------------------------
# Desenho
# ---------------------------------------------------------------------------

_P_ROTULO = ParagraphStyle('rotulo', fontName='Helvetica', fontSize=6.5,
                           textColor=_ROTULO, leading=8, spaceAfter=1)
_P_VALOR = ParagraphStyle('valor', fontName='Helvetica-Bold', fontSize=8.5,
                          textColor=_TEXTO, leading=11)
_P_TEXTO = ParagraphStyle('texto', fontName='Helvetica', fontSize=8.5,
                          textColor=_TEXTO, leading=12)
_P_SECAO = ParagraphStyle('secao', fontName='Helvetica-Bold', fontSize=8,
                          textColor=_TEXTO, leading=10)
_P_RODAPE = ParagraphStyle('rodape', fontName='Helvetica', fontSize=7,
                           textColor=_ROTULO, leading=9.5)
_P_TITULO = ParagraphStyle('titulo', fontName='Helvetica-Bold', fontSize=12,
                           textColor=_TEXTO, leading=15)
_P_CHAVE = ParagraphStyle('chave', fontName='Courier-Bold', fontSize=8.6,
                          textColor=_TEXTO, leading=11)

_LARGURA = A4[0] - 24 * mm


def _campo(rotulo: str, valor: str):
    """Célula rótulo em cima, valor embaixo — como nos quadros do DANFSE."""
    return [Paragraph(rotulo.upper(), _P_ROTULO),
            Paragraph(_esc(valor or '—'), _P_VALOR)]


def _esc(texto: str) -> str:
    return html.escape(texto or '', quote=False)


def _grade(linhas: list[list], larguras: list[float], fundo_titulo=False,
           extra: list | None = None) -> Table:
    t = Table(linhas, colWidths=larguras, hAlign='LEFT')
    estilo = [
        ('GRID', (0, 0), (-1, -1), 0.5, _BORDA),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    if fundo_titulo:
        estilo.append(('BACKGROUND', (0, 0), (-1, 0), _CINZA_TITULO))
    t.setStyle(TableStyle(estilo + (extra or [])))
    return t


def _secao(titulo: str) -> Table:
    t = Table([[Paragraph(titulo.upper(), _P_SECAO)]], colWidths=[_LARGURA], hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), _CINZA_TITULO),
        ('BOX', (0, 0), (-1, -1), 0.5, _BORDA),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    return t


def _bloco_pessoa(p: Pessoa) -> Table:
    meia = _LARGURA / 2
    municipio_cep = ' — '.join(x for x in (p.municipio, p.cep) if x)
    linhas = [
        [_campo('Nome / Razão social', p.nome), _campo('CPF / CNPJ', p.documento)],
        [_campo('Inscrição municipal', p.inscricao_municipal), _campo('Telefone', p.fone)],
        [_campo('Endereço', p.endereco), _campo('Município / CEP', municipio_cep)],
    ]
    corpo = [[_celula(a), _celula(b)] for a, b in linhas]
    # O e-mail ocupa a linha inteira: sem o span sobrava uma célula "—" à direita.
    corpo.append([_celula(_campo('E-mail', p.email)), ''])
    return _grade(corpo, [meia, meia],
                  extra=[('SPAN', (0, len(corpo) - 1), (1, len(corpo) - 1))])


def _celula(par_rotulo_valor: list) -> Table:
    """Empilha rótulo + valor dentro de uma célula da grade."""
    t = Table([[par_rotulo_valor[0]], [par_rotulo_valor[1]]], hAlign='LEFT')
    t.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return t


def gerar_danfse_pdf(xml: str, consulta_url: str | None = None) -> bytes:
    """Monta o PDF da nota a partir do XML. Levanta DanfseError se não der."""
    d = ler_xml(xml)
    # Nota antiga não ganha link de conferência: o sistema municipal saiu do ar
    # em 20/07/2026 e levou junto as URLs de verificação dele. Nessas o que vale
    # é o código de verificação, que já sai no cabeçalho.
    d.consulta_url = (consulta_url or '') if d.formato == 'nacional' else ''

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=10 * mm, bottomMargin=10 * mm,
        title=f'NFS-e {d.numero}'.strip(), author='MasterSat',
    )
    hist: list = []

    # ── Cabeçalho: identificação + QR da consulta pública ──
    ident = _grade([
        [_celula(_campo('Número da NFS-e', d.numero)),
         _celula(_campo('Série', d.serie)),
         _celula(_campo('Competência', d.competencia))],
        [_celula(_campo('Data e hora de emissão', d.data_emissao)),
         _celula(_campo('Município de emissão', d.municipio_emissao)),
         _celula(_campo('Local da prestação', d.municipio_prestacao))],
    ], [_LARGURA * 0.38, _LARGURA * 0.20, _LARGURA * 0.42])

    titulo = [
        Paragraph('NOTA FISCAL DE SERVIÇO ELETRÔNICA', _P_TITULO),
        Paragraph('DANFS-e · Documento Auxiliar da NFS-e', _P_TEXTO),
    ]
    if d.codigo_verificacao:
        titulo.append(Paragraph(f'Código de verificação: <b>{_esc(d.codigo_verificacao)}</b>',
                                _P_TEXTO))

    cabecalho = Table(
        [[titulo, _qr(d.consulta_url or d.chave)]],
        colWidths=[_LARGURA - 30 * mm, 30 * mm], hAlign='LEFT',
    )
    cabecalho.setStyle(TableStyle([
        ('VALIGN', (0, 0), (0, 0), 'MIDDLE'), ('VALIGN', (1, 0), (1, 0), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    hist += [cabecalho, Spacer(1, 4 * mm)]

    if d.teste:
        hist += [_faixa_teste(), Spacer(1, 3 * mm)]

    hist += [ident, Spacer(1, 1.5 * mm)]
    if d.chave:
        hist += [_grade([[_celula([Paragraph('CHAVE DE ACESSO', _P_ROTULO),
                                   Paragraph(_esc(d.chave), _P_CHAVE)])]], [_LARGURA]),
                 Spacer(1, 3.5 * mm)]

    hist += [_secao('Prestador de serviços'), _bloco_pessoa(d.prestador), Spacer(1, 3 * mm)]
    hist += [_secao('Tomador de serviços'), _bloco_pessoa(d.tomador), Spacer(1, 3 * mm)]

    # ── Serviço ──
    servico_linhas = [[_celula(_campo('Código de tributação', d.codigo_servico)),
                       _celula(_campo('Descrição da tributação', d.descricao_tributacao))]]
    hist += [
        _secao('Discriminação dos serviços'),
        _grade(servico_linhas, [_LARGURA * 0.28, _LARGURA * 0.72]),
        _grade([[Paragraph(_esc(d.descricao_servico) or '—', _P_TEXTO)]], [_LARGURA]),
        Spacer(1, 3 * mm),
    ]

    # ── Valores ──
    cabec = [Paragraph(r.upper(), _P_ROTULO) for r, _ in d.valores]
    corpo = [Paragraph(_esc(v), _P_VALOR) for _, v in d.valores]
    col = _LARGURA / max(len(d.valores), 1)
    total = _grade([[_celula([Paragraph('VALOR LÍQUIDO DA NFS-E', _P_ROTULO),
                              Paragraph(_esc(d.valor_liquido), _P_TITULO)])]], [_LARGURA])
    hist += [
        _secao('Valores'),
        _grade([cabec, corpo], [col] * len(d.valores), fundo_titulo=True),
        total,
        Spacer(1, 3 * mm),
    ]

    if d.outras_informacoes:
        hist += [_secao('Outras informações'),
                 _grade([[Paragraph(_esc(d.outras_informacoes), _P_TEXTO)]], [_LARGURA]),
                 Spacer(1, 3 * mm)]

    hist.append(_rodape(d))
    doc.build(hist)
    return buf.getvalue()


def _qr(conteudo: str) -> Drawing:
    lado = 28 * mm
    widget = qr.QrCodeWidget(conteudo or ' ', barLevel='M')
    x1, y1, x2, y2 = widget.getBounds()
    dsg = Drawing(lado, lado, transform=[lado / (x2 - x1), 0, 0, lado / (y2 - y1), 0, 0])
    dsg.add(widget)
    return dsg


def _faixa_teste() -> Table:
    t = Table([[Paragraph(
        '<b>AMBIENTE DE TESTE — DOCUMENTO SEM VALOR FISCAL.</b> '
        'Esta nota foi emitida em ambiente de teste e não serve como documento '
        'fiscal nem deve ser enviada ao tomador.',
        ParagraphStyle('aviso', fontName='Helvetica', fontSize=8.5, leading=11,
                       textColor=colors.HexColor('#8A4B00')))]],
        colWidths=[_LARGURA], hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFF3D6')),
        ('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor('#E0A030')),
        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return t


def _rodape(d: Danfse) -> Table:
    linhas = [
        'Documento auxiliar gerado pelo sistema MasterSat a partir do XML autenticado da NFS-e. '
        'O documento fiscal é o XML; este PDF é a sua representação visual.',
    ]
    if d.origem:
        linhas.append(f'Origem: {d.origem}.')
    if d.consulta_url:
        linhas.append(f'Confira a autenticidade pela chave de acesso em {d.consulta_url}')
    t = Table([[Paragraph('<br/>'.join(_esc(x) for x in linhas), _P_RODAPE)]],
              colWidths=[_LARGURA], hAlign='LEFT')
    t.setStyle(TableStyle([
        ('LINEABOVE', (0, 0), (-1, 0), 0.5, _BORDA),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
    ]))
    return t
