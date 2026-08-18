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
from pathlib import Path

from lxml import etree
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NS_NFSE = 'http://www.sped.fazenda.gov.br/nfse'
NS_JOINVILLE = 'http://www.publica.inf.br'

# Paleta da marca: o logo é preto + amarelo, e brand-500 (#FFB800) é o mesmo
# tom usado como acento no app (tailwind.config).
_OURO = colors.HexColor('#FFB800')
_OURO_CLARO = colors.HexColor('#FFF6DB')
_TINTA = colors.HexColor('#14181F')      # preto do logotipo
_BORDA = colors.HexColor('#D6DDE5')
_TEXTO = colors.HexColor('#1F2933')
_ROTULO = colors.HexColor('#6B7A8A')
# NT 008 (2.2.3): sombreamento cinza claro (5%) no cabeçalho, títulos de bloco e
# nos campos "Emitente" e "Valor Líquido"; "SEM VALIDADE JURÍDICA" em vermelho.
_CINZA = colors.HexColor('#E7E9EC')
_VERMELHO = colors.HexColor('#D00000')
_MARCA_DAGUA = colors.HexColor('#A6A6A6')   # cinza K35 do carimbo CANCELADA/SUBSTITUÍDA


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
    numero_dps: str = ''
    numero_dfe: str = ''
    situacao: str = ''
    chave: str = ''
    codigo_verificacao: str = ''
    data_emissao: str = ''
    competencia: str = ''
    municipio_emissao: str = ''
    municipio_prestacao: str = ''
    municipio_incidencia: str = ''
    teste: bool = False
    tipo_ambiente: str = ''
    ambiente_gerador: str = ''
    prestador: Pessoa = field(default_factory=Pessoa)
    tomador: Pessoa = field(default_factory=Pessoa)
    descricao_servico: str = ''
    codigo_servico: str = ''
    descricao_tributacao: str = ''
    # Tributação — o que a consulta pública mostra e faltava no papel
    regime_simples: str = ''
    regime_apuracao: str = ''
    regime_especial: str = ''
    tributacao_issqn: str = ''
    retencao_issqn: str = ''
    tributos_aprox: str = ''
    valores: list[tuple[str, str]] = field(default_factory=list)
    valor_liquido: str = ''
    outras_informacoes: str = ''
    origem: str = ''
    consulta_url: str = ''
    # ── Campos exigidos pela NT 008 (todos os blocos do Anexo I) ──
    finalidade: str = ''
    emitente_tipo: str = ''
    data_emissao_dps: str = ''
    codigo_nbs: str = ''
    local_prestacao_completo: str = ''
    destinatario: Pessoa = field(default_factory=Pessoa)
    destinatario_proprio_tomador: bool = True
    intermediario: Pessoa = field(default_factory=Pessoa)
    issqn_campos: list[tuple[str, str]] = field(default_factory=list)
    federal_campos: list[tuple[str, str]] = field(default_factory=list)
    ibscbs_campos: list[tuple[str, str]] = field(default_factory=list)
    valor_total_campos: list[tuple[str, str]] = field(default_factory=list)
    valor_total_nf: str = ''
    tributos_aprox_texto: str = ''


# Tabelas do leiaute (tiposSimples_v1.01.xsd) — traduzem os códigos crus.
_OP_SIMPLES = {
    '1': 'Não optante',
    '2': 'Optante — Microempreendedor Individual (MEI)',
    '3': 'Optante — Microempresa ou EPP (ME/EPP)',
}
_REG_APURACAO = {
    '1': 'Tributos federais e municipal pelo Simples Nacional',
    '2': 'Federais pelo Simples Nacional; ISSQN fora do SN',
    '3': 'Tributos federais e municipal fora do Simples Nacional',
}
_REG_ESPECIAL = {
    '0': 'Nenhum', '1': 'Ato cooperado (cooperativa)', '2': 'Estimativa',
    '3': 'Microempresa municipal', '4': 'Notário ou registrador',
    '5': 'Profissional autônomo', '6': 'Sociedade de profissionais', '9': 'Outros',
}
_TRIB_ISSQN = {
    '1': 'Operação tributável', '2': 'Imunidade',
    '3': 'Exportação de serviço', '4': 'Não incidência',
}
_RET_ISSQN = {'1': 'Não retido', '2': 'Retido pelo tomador', '3': 'Retido pelo intermediário'}
_SITUACAO = {
    '100': 'NFS-e gerada', '102': 'NFS-e de decisão judicial',
    '103': 'NFS-e avulsa', '107': 'NFS-e MEI',
}
_AMB_GERADOR = {'1': 'Prefeitura', '2': 'Sistema Nacional NFS-e'}
_TIPO_AMBIENTE = {'1': 'Produção', '2': 'Produção Restrita'}
_FINALIDADE = {
    '1': 'NFS-e regular', '2': 'NFS-e complementar',
    '3': 'NFS-e extemporânea', '4': 'NFS-e de substituição',
}
_TP_EMIT = {'1': 'Prestador', '2': 'Tomador', '3': 'Intermediário'}
_RET_PISCOFINS = {
    '1': 'PIS/COFINS Retido', '2': 'PIS/COFINS Não Retido',
    '3': 'PIS/COFINS/CSLL Não Retido', '4': 'PIS/COFINS Retido / CSLL Não Retido',
}


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


def _endereco_nacional(end, municipios: dict[str, str],
                       por_cep: dict[str, str]) -> tuple[str, str, str]:
    """
    (logradouro completo, município/UF, CEP) de um bloco de endereço.

    Os dois lados da nota usam tipos diferentes: o emitente é TCEnderecoEmitente,
    com cMun/UF/CEP soltos; o tomador é TCEndereco, que aninha cMun e CEP dentro
    de <endNac>. Aqui aceita as duas formas.

    O nome do município sai, em ordem: dos nomes que o próprio XML traz para
    aquele código IBGE; do mapa por CEP que o chamador passou; e, em último
    caso, do código cru — melhor um número do que um campo vazio.
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
    municipio = municipios.get(cmun) or por_cep.get(_so_digitos(cep)) or cmun
    if uf and municipio and not municipio.endswith(f'/{uf}'):
        municipio = f'{municipio}/{uf}'
    return ', '.join(p for p in partes if p), municipio, _cep_formatado(cep)


def _so_digitos(valor: str) -> str:
    return ''.join(c for c in (valor or '') if c.isdigit())


def _pessoa_nacional(bloco, municipios: dict[str, str], por_cep: dict[str, str]) -> Pessoa:
    """Lê um bloco de pessoa (destinatário/intermediário) do XML nacional."""
    if bloco is None:
        return Pessoa()
    end, mun, cep = _endereco_nacional(_filho(bloco, NS_NFSE, 'end'), municipios, por_cep)
    return Pessoa(
        nome=_txt(bloco, NS_NFSE, 'xNome'),
        documento=_doc_formatado(_txt(bloco, NS_NFSE, 'CNPJ') or _txt(bloco, NS_NFSE, 'CPF')
                                 or _txt(bloco, NS_NFSE, 'NIF')),
        inscricao_municipal=_txt(bloco, NS_NFSE, 'IM'),
        endereco=end, municipio=mun, cep=cep,
        fone=_fone_formatado(_txt(bloco, NS_NFSE, 'fone')),
        email=_txt(bloco, NS_NFSE, 'email'),
    )


def _totais_aprox_texto(tot, valor_servico: str, agregado: str) -> str:
    """
    Linha dos Totais Aproximados dos Tributos no formato exigido pela NT 008
    (Nota 10): "Federais / Estaduais / Municipais". Quando o XML só traz a carga
    agregada do Simples (pTotTribSN), mostra-a como total — é o que existe.
    """
    if tot is None:
        return ''

    def _esfera(v_tag: str, p_tag: str) -> str:
        v = _txt(tot, NS_NFSE, v_tag)
        if v:
            return _brl(v)
        p = _txt(tot, NS_NFSE, p_tag)
        if p:
            try:
                return _brl(str(float(valor_servico) * float(p) / 100))
            except (TypeError, ValueError):
                return _pct(p)
        return ''

    fed, est, mun = _esfera('vTotTribFed', 'pTotTribFed'), _esfera('vTotTribEst', 'pTotTribEst'), _esfera('vTotTribMu', 'pTotTribMu')
    if fed or est or mun:
        return ('Totais Aproximados dos Tributos cfe. Lei nº 12.741/2012: '
                f'Federais: {fed or "R$ 0,00"} ; Estaduais: {est or "R$ 0,00"} ; '
                f'Municipais: {mun or "R$ 0,00"}')
    if agregado:
        return ('Totais Aproximados dos Tributos cfe. Lei nº 12.741/2012 '
                f'(carga total do Simples Nacional): {agregado}')
    return ''


def _ler_nacional(raiz, por_cep: dict[str, str] | None = None) -> Danfse:
    inf = _filho(raiz, NS_NFSE, 'infNFSe')
    if inf is None:
        raise DanfseError('XML da NFS-e sem o bloco infNFSe')
    dps = _filho(inf, NS_NFSE, 'DPS', 'infDPS')

    d = Danfse()
    d.numero = _txt(inf, NS_NFSE, 'nNFSe')
    d.numero_dfe = _txt(inf, NS_NFSE, 'nDFSe')
    d.situacao = _SITUACAO.get(_txt(inf, NS_NFSE, 'cStat'), '')
    d.chave = (inf.get('Id') or '').removeprefix('NFS')
    d.data_emissao = _data_hora(_txt(inf, NS_NFSE, 'dhProc'))
    d.municipio_emissao = _txt(inf, NS_NFSE, 'xLocEmi')
    d.municipio_prestacao = _txt(inf, NS_NFSE, 'xLocPrestacao')
    d.municipio_incidencia = _txt(inf, NS_NFSE, 'xLocIncid')
    d.descricao_tributacao = _txt(inf, NS_NFSE, 'xTribNac')
    d.outras_informacoes = _txt(inf, NS_NFSE, 'xOutInf')
    d.ambiente_gerador = _AMB_GERADOR.get(_txt(inf, NS_NFSE, 'ambGer'), '')

    # Os endereços só trazem o código IBGE. O XML nomeia alguns municípios
    # (xLocEmi/xLocPrestacao/xLocIncid); fora esses, o tomador de outra cidade
    # sairia como "3304557" — daí o mapa por CEP vindo do cadastro.
    por_cep = {_so_digitos(k): v for k, v in (por_cep or {}).items()}
    municipios: dict[str, str] = {}
    if _txt(inf, NS_NFSE, 'cLocIncid'):
        municipios[_txt(inf, NS_NFSE, 'cLocIncid')] = d.municipio_incidencia
    if dps is not None:
        if _txt(dps, NS_NFSE, 'cLocEmi'):
            municipios[_txt(dps, NS_NFSE, 'cLocEmi')] = d.municipio_emissao
        cloc = _txt(dps, NS_NFSE, 'serv', 'locPrest', 'cLocPrestacao')
        if cloc:
            municipios[cloc] = d.municipio_prestacao

    emit = _filho(inf, NS_NFSE, 'emit')
    if emit is not None:
        end, mun, cep = _endereco_nacional(_filho(emit, NS_NFSE, 'enderNac'), municipios, por_cep)
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
        d.numero_dps = _txt(dps, NS_NFSE, 'nDPS')
        d.competencia = _data_hora(_txt(dps, NS_NFSE, 'dCompet'))
        _tpamb = _txt(dps, NS_NFSE, 'tpAmb')
        d.teste = _tpamb == '2'
        d.tipo_ambiente = _TIPO_AMBIENTE.get(_tpamb, '')
        d.origem = f'DPS nº {d.numero_dps} série {d.serie}'
        if not d.data_emissao:
            d.data_emissao = _data_hora(_txt(dps, NS_NFSE, 'dhEmi'))

        reg = _filho(dps, NS_NFSE, 'prest', 'regTrib')
        if reg is not None:
            op = _txt(reg, NS_NFSE, 'opSimpNac')
            d.regime_simples = _OP_SIMPLES.get(op, op)
            apur = _txt(reg, NS_NFSE, 'regApTribSN')
            d.regime_apuracao = _REG_APURACAO.get(apur, apur)
            esp = _txt(reg, NS_NFSE, 'regEspTrib')
            d.regime_especial = _REG_ESPECIAL.get(esp, esp)

        toma = _filho(dps, NS_NFSE, 'toma')
        if toma is not None:
            end, mun, cep = _endereco_nacional(_filho(toma, NS_NFSE, 'end'), municipios, por_cep)
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

    if dps_val is not None:
        mun = _filho(dps_val, NS_NFSE, 'trib', 'tribMun')
        if mun is not None:
            trib = _txt(mun, NS_NFSE, 'tribISSQN')
            d.tributacao_issqn = _TRIB_ISSQN.get(trib, trib)
            ret = _txt(mun, NS_NFSE, 'tpRetISSQN')
            d.retencao_issqn = _RET_ISSQN.get(ret, ret)
        d.tributos_aprox = _tributos_aproximados(
            _filho(dps_val, NS_NFSE, 'trib', 'totTrib'), bruto)

    # No Simples Nacional a Sefin devolve só vLiq: base, alíquota e ISSQN não
    # existem porque o imposto é recolhido no DAS. Mostrar cinco traços passa a
    # impressão de nota quebrada, então cada linha só entra se houver valor.
    linhas = [('Valor do serviço', _brl(bruto) if bruto else '')]
    if val is not None:
        linhas += [
            ('Base de cálculo', _brl(_txt(val, NS_NFSE, 'vBC')) if _txt(val, NS_NFSE, 'vBC') else ''),
            ('Alíquota', _pct(_txt(val, NS_NFSE, 'pAliqAplic'))
             if _txt(val, NS_NFSE, 'pAliqAplic') else ''),
            ('ISSQN', _brl(_txt(val, NS_NFSE, 'vISSQN')) if _txt(val, NS_NFSE, 'vISSQN') else ''),
            ('Retenções', _brl(_txt(val, NS_NFSE, 'vTotalRet'))
             if _txt(val, NS_NFSE, 'vTotalRet') else ''),
        ]
    if d.tributos_aprox:
        linhas.append(('Tributos aprox. (Lei 12.741/12)', d.tributos_aprox))
    d.valores = [(rotulo, valor) for rotulo, valor in linhas if valor]

    d.valor_liquido = _brl(_txt(val, NS_NFSE, 'vLiq')) if val is not None else _brl(bruto)

    # ── Demais blocos exigidos pelo Anexo I (NT 008) ───────────────────────
    def _vd(txt: str) -> str:          # valor monetário ou traço (Nota 12)
        return _brl(txt) if txt else '-'

    if dps is not None:
        d.finalidade = _FINALIDADE.get(_txt(dps, NS_NFSE, 'IBSCBS', 'finNFSe'), '')
        d.emitente_tipo = _TP_EMIT.get(_txt(dps, NS_NFSE, 'tpEmit'), '')
        d.data_emissao_dps = _data_hora(_txt(dps, NS_NFSE, 'dhEmi'))
        _serv = _filho(dps, NS_NFSE, 'serv')
        if _serv is not None:
            d.codigo_nbs = _txt(_serv, NS_NFSE, 'cServ', 'cNBS')
        d.destinatario = _pessoa_nacional(_filho(dps, NS_NFSE, 'IBSCBS', 'dest'), municipios, por_cep)
        d.destinatario_proprio_tomador = not (d.destinatario.nome or d.destinatario.documento)
        d.intermediario = _pessoa_nacional(_filho(dps, NS_NFSE, 'interm'), municipios, por_cep)
    d.local_prestacao_completo = f'{d.municipio_prestacao} / BR' if d.municipio_prestacao else ''

    _al = _txt(val, NS_NFSE, 'pAliqAplic') if val is not None else ''
    d.issqn_campos = [
        ('Tipo de Tributação do ISSQN', d.tributacao_issqn or '-'),
        ('Município de Incidência', d.municipio_incidencia or '-'),
        ('Regime Especial de Tributação', d.regime_especial or '-'),
        ('BC ISSQN', _vd(_txt(val, NS_NFSE, 'vBC')) if val is not None else '-'),
        ('Alíquota Aplicada', _pct(_al) if _al else '-'),
        ('Retenção do ISSQN', d.retencao_issqn or '-'),
        ('ISSQN Apurado', _vd(_txt(val, NS_NFSE, 'vISSQN')) if val is not None else '-'),
    ]

    _fed = _filho(dps_val, NS_NFSE, 'trib', 'tribFed') if dps_val is not None else None
    _pis = _filho(_fed, NS_NFSE, 'piscofins') if _fed is not None else None
    d.federal_campos = [
        ('IRRF', _vd(_txt(_fed, NS_NFSE, 'vRetIRRF')) if _fed is not None else '-'),
        ('Contrib. Previdenciária Retida', _vd(_txt(_fed, NS_NFSE, 'vRetCP')) if _fed is not None else '-'),
        ('Contrib. Sociais Retidas', _vd(_txt(_fed, NS_NFSE, 'vRetCSLL')) if _fed is not None else '-'),
        ('PIS', _vd(_txt(_pis, NS_NFSE, 'vPis')) if _pis is not None else '-'),
        ('COFINS', _vd(_txt(_pis, NS_NFSE, 'vCofins')) if _pis is not None else '-'),
        ('Descrição Contrib. Sociais', _RET_PISCOFINS.get(_txt(_pis, NS_NFSE, 'tpRetPisCofins'), '-') if _pis is not None else '-'),
    ]

    _tot = _filho(inf, NS_NFSE, 'IBSCBS', 'totCIBS')
    _gtrib = _filho(dps, NS_NFSE, 'IBSCBS', 'valores', 'trib', 'gIBSCBS') if dps is not None else None
    _cst = _txt(_gtrib, NS_NFSE, 'CST') if _gtrib is not None else ''
    d.ibscbs_campos = [
        ('CST / cClassTrib', f'{_cst} / {_txt(_gtrib, NS_NFSE, "cClassTrib")}' if _cst else '-'),
        ('Valor Total do IBS', _vd(_txt(_tot, NS_NFSE, 'gIBS', 'vIBSTot')) if _tot is not None else '-'),
        ('Valor Total da CBS', _vd(_txt(_tot, NS_NFSE, 'gCBS', 'vCBS')) if _tot is not None else '-'),
    ]

    _ibstot, _cbstot = (_txt(_tot, NS_NFSE, 'gIBS', 'vIBSTot'), _txt(_tot, NS_NFSE, 'gCBS', 'vCBS')) if _tot is not None else ('', '')
    _ibscbs_total = ''
    if _ibstot or _cbstot:
        try:
            _ibscbs_total = _brl(str(float(_ibstot or 0) + float(_cbstot or 0)))
        except ValueError:
            _ibscbs_total = ''
    _dci = _filho(dps_val, NS_NFSE, 'vDescCondIncond') if dps_val is not None else None
    d.valor_total_campos = [
        ('Valor da Operação / Serviço', _vd(bruto)),
        ('Desconto Incondicionado', _vd(_txt(_dci, NS_NFSE, 'vDescIncond')) if _dci is not None else '-'),
        ('Desconto Condicionado', _vd(_txt(_dci, NS_NFSE, 'vDescCond')) if _dci is not None else '-'),
        ('Total das Retenções (ISSQN/Federais)', _vd(_txt(val, NS_NFSE, 'vTotalRet')) if val is not None else '-'),
        ('Valor Líquido da NFS-e', d.valor_liquido or '-'),
        ('Total do IBS/CBS', _ibscbs_total or '-'),
    ]
    d.valor_total_nf = (_vd(_txt(_tot, NS_NFSE, 'vTotNF'))
                        if (_tot is not None and _txt(_tot, NS_NFSE, 'vTotNF')) else d.valor_liquido)

    d.tributos_aprox_texto = _totais_aprox_texto(
        _filho(dps_val, NS_NFSE, 'trib', 'totTrib') if dps_val is not None else None,
        bruto, d.tributos_aprox)
    return d


def _tributos_aproximados(tot, valor_servico: str) -> str:
    """
    totTrib é um choice: valor fechado, percentual, percentual do Simples ou o
    indicador de "sem informação". Converte tudo para algo legível.
    """
    if tot is None:
        return ''
    fechado = _txt(tot, NS_NFSE, 'vTotTrib')
    if fechado:
        return _brl(fechado)
    for tag in ('pTotTribSN', 'pTotTrib'):
        pct = _txt(tot, NS_NFSE, tag)
        if not pct:
            continue
        try:
            valor = _brl(str(float(valor_servico) * float(pct) / 100))
            return f'{valor} ({_pct(pct)})'
        except (TypeError, ValueError):
            return _pct(pct)
    return ''


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


def ler_xml(xml: str, municipio_por_cep: dict[str, str] | None = None) -> Danfse:
    """
    Normaliza o XML da nota (nacional ou Joinville) no modelo do desenho.

    ``municipio_por_cep`` mapeia CEP → "Cidade/UF", para nomear o município
    de endereços que o XML só identifica pelo código IBGE (ver _ler_nacional).
    """
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
        return _ler_nacional(raiz, municipio_por_cep)
    if raiz.find(f'.//{{{NS_JOINVILLE}}}InfNfse') is not None:
        return _ler_joinville(raiz)
    raise DanfseError('Formato de XML de NFS-e não reconhecido')


# ---------------------------------------------------------------------------
# Desenho
# ---------------------------------------------------------------------------

# Tamanhos no mínimo exigido pela NT 008 (item 2.4): rótulos 6pt, conteúdo 7pt,
# títulos de bloco 7pt — para caber tudo na página única obrigatória.
_P_ROTULO = ParagraphStyle('rotulo', fontName='Helvetica-Bold', fontSize=6,
                           textColor=_ROTULO, leading=6.6, spaceAfter=0.4)
_P_VALOR = ParagraphStyle('valor', fontName='Helvetica-Bold', fontSize=7.5,
                          textColor=_TEXTO, leading=8.6)
_P_TEXTO = ParagraphStyle('texto', fontName='Helvetica', fontSize=7.5,
                          textColor=_TEXTO, leading=9)
_P_SECAO = ParagraphStyle('secao', fontName='Helvetica-Bold', fontSize=7,
                          textColor=_TINTA, leading=8.5)
# Cabeçalho oficial do DANFSe (NT 008, item 2.4.3)
_P_DANFSE_TIT = ParagraphStyle('danfse_tit', fontName='Helvetica-Bold', fontSize=11,
                               textColor=_TINTA, alignment=1, leading=13)
_P_DANFSE_SUB = ParagraphStyle('danfse_sub', fontName='Helvetica-Bold', fontSize=9,
                               textColor=_TINTA, alignment=1, leading=11)
_P_SEM_VALIDADE = ParagraphStyle('sem_val', fontName='Helvetica-Bold', fontSize=9,
                                 textColor=_VERMELHO, alignment=1, leading=11, spaceBefore=2)
_P_AMB = ParagraphStyle('amb', fontName='Helvetica', fontSize=6.5,
                        textColor=_TEXTO, alignment=2, leading=8.5)
_P_AUTENT = ParagraphStyle('autent', fontName='Helvetica', fontSize=5.2,
                           textColor=_ROTULO, alignment=2, leading=6.4)
_P_NFSE_MARCA = ParagraphStyle('nfse_marca', fontName='Helvetica-Bold', fontSize=17,
                               textColor=_TINTA, leading=19)
_P_RODAPE = ParagraphStyle('rodape', fontName='Helvetica', fontSize=7,
                           textColor=_ROTULO, leading=9.5)
_P_TITULO = ParagraphStyle('titulo', fontName='Helvetica-Bold', fontSize=13,
                           textColor=_TINTA, leading=16)
_P_SUBTITULO = ParagraphStyle('subtitulo', fontName='Helvetica', fontSize=8,
                              textColor=_ROTULO, leading=11)
_P_CHAVE = ParagraphStyle('chave', fontName='Courier-Bold', fontSize=8.6,
                          textColor=_TEXTO, leading=11)
_P_TOTAL = ParagraphStyle('total', fontName='Helvetica-Bold', fontSize=15,
                          textColor=_TINTA, leading=18)
_P_TOTAL_ROTULO = ParagraphStyle('totalrot', fontName='Helvetica-Bold', fontSize=6.5,
                                 textColor=_TINTA, leading=8)

_MARGEM = 5 * mm            # margem enxuta (NT 008 2.2.2 pede 1,5–2mm; 5mm é seguro p/ impressora)
_LARGURA = A4[0] - 2 * _MARGEM
_RAIO = 3  # eco do rounded-2xl dos cards do app, na escala do papel

# Logo lido em memória: o repositório mora num caminho com acento e passar o
# path direto ao ReportLab já quebrou antes (ver boleto_pdf._tmp_copy).
_LOGO = Path(__file__).parent.parent / 'static' / 'mastersat_logo.png'


def _logo_flowable(altura: float):
    """Logo da MasterSat na proporção original, ou None se o arquivo sumir."""
    try:
        dados = _LOGO.read_bytes()
    except OSError:
        return None
    largura_px, altura_px = ImageReader(io.BytesIO(dados)).getSize()
    # Image quer caminho ou file-like — e o ImageReader acima já consumiu o seu.
    return Image(io.BytesIO(dados), width=altura * largura_px / altura_px, height=altura)


def _campo(rotulo: str, valor: str):
    """Célula rótulo em cima, valor embaixo — como nos quadros do DANFSE."""
    return [Paragraph(rotulo.upper(), _P_ROTULO),
            Paragraph(_esc(valor or '—'), _P_VALOR)]


def _esc(texto: str) -> str:
    return html.escape(texto or '', quote=False)


def _reticencias(texto: str, n: int) -> str:
    """Trunca com reticências acima de N caracteres, como a NT 008 exige em
    várias descrições (itens 2.1/2.4.5) e para garantir a página única."""
    texto = texto or ''
    return texto if len(texto) <= n else texto[:n - 1].rstrip() + '…'


def _grade(linhas: list[list], larguras: list[float], fundo_titulo=False,
           extra: list | None = None) -> Table:
    t = Table(linhas, colWidths=larguras, hAlign='LEFT')
    estilo = [
        ('GRID', (0, 0), (-1, -1), 0.5, _BORDA),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 1.2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1.3),
    ]
    if fundo_titulo:
        estilo.append(('BACKGROUND', (0, 0), (-1, 0), _OURO_CLARO))
    t.setStyle(TableStyle(estilo + (extra or [])))
    return t


def _secao(titulo: str) -> Table:
    """Título de bloco: 7pt bold em caixa alta, com sombreamento cinza claro e
    borda fina de 0,5pt — o padrão neutro exigido pela NT 008 (itens 2.2.3/2.4.1)."""
    t = Table([[Paragraph(titulo.upper(), _P_SECAO)]], colWidths=[_LARGURA], hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), _CINZA),
        ('BOX', (0, 0), (-1, -1), 0.5, _BORDA),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 1.8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1.8),
    ]))
    return t


def _bloco_pessoa(p: Pessoa, extra_rows: list | None = None) -> Table:
    """Bloco de pessoa compacto (vários campos por linha, como o Anexo I)."""
    c = _LARGURA / 3
    corpo = [
        [_celula(_campo('Nome / Razão social', p.nome)), '', _celula(_campo('CPF / CNPJ / NIF', p.documento))],
        [_celula(_campo('Inscrição municipal', p.inscricao_municipal)),
         _celula(_campo('Telefone', p.fone)), _celula(_campo('Município / UF', p.municipio))],
        [_celula(_campo('Endereço', p.endereco)), '', _celula(_campo('CEP', p.cep))],
        [_celula(_campo('E-mail', p.email)), '', ''],
    ]
    spans = [('SPAN', (0, 0), (1, 0)), ('SPAN', (0, 2), (1, 2)), ('SPAN', (0, 3), (2, 3))]
    for r in (extra_rows or []):
        corpo.append([_celula(_campo(r[0][0], r[0][1])), '', _celula(_campo(r[1][0], r[1][1]))])
        spans.append(('SPAN', (0, len(corpo) - 1), (1, len(corpo) - 1)))
    return _grade(corpo, [c, c, c], extra=spans)


def _bloco_texto(texto: str) -> Table:
    """Linha única em caixa — colapso dos blocos suprimíveis (NT 008 2.3)."""
    return _grade([[Paragraph(_esc(texto), _P_VALOR)]], [_LARGURA])


def _bloco_campos(campos: list[tuple[str, str]], cols: int) -> Table:
    """Grade genérica rótulo/valor com ``cols`` campos por linha."""
    largura = _LARGURA / cols
    linhas = []
    for i in range(0, len(campos), cols):
        grupo = list(campos[i:i + cols])
        while len(grupo) < cols:
            grupo.append(('', ''))
        linhas.append([_celula(_campo(r, v)) for r, v in grupo])
    return _grade(linhas, [largura] * cols)


def _bloco_servico(d: Danfse) -> list:
    cabec = _grade([[
        _celula(_campo('Cód. Tributação Nac./Mun.', d.codigo_servico)),
        _celula(_campo('Código NBS', d.codigo_nbs)),
        _celula(_campo('Local da Prestação / UF / País', d.local_prestacao_completo)),
    ]], [_LARGURA * 0.28, _LARGURA * 0.22, _LARGURA * 0.50])
    trib = _grade([[Paragraph('<b>Descrição da tributação:</b> '
                              + (_esc(_reticencias(d.descricao_tributacao, 167)) or '-'), _P_TEXTO)]], [_LARGURA])
    serv = _grade([[Paragraph('<b>Descrição do serviço:</b> '
                              + (_esc(_reticencias(d.descricao_servico, 500)) or '-'), _P_TEXTO)]], [_LARGURA])
    return [cabec, trib, serv]


def _bloco_valor_total(d: Danfse) -> list:
    campos = d.valor_total_campos or [(r, v) for r, v in d.valores]
    grade = _bloco_campos(campos, 3)
    total = Table(
        [[[Paragraph('VALOR LÍQUIDO DA NFS-E + IBS/CBS', _P_TOTAL_ROTULO),
           Paragraph(_esc(d.valor_total_nf or d.valor_liquido), _P_TOTAL)]]],
        colWidths=[_LARGURA], hAlign='LEFT')
    total.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), _CINZA),
        ('BOX', (0, 0), (-1, -1), 0.5, _BORDA),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return [grade, total]


def _bloco_info_compl(d: Danfse) -> Table:
    partes = []
    if d.outras_informacoes:
        partes.append(_esc(_reticencias(d.outras_informacoes, 500)))
    if d.tributos_aprox_texto:  # a linha dos Totais é fixa (Nota 10), nunca truncada
        partes.append(_esc(d.tributos_aprox_texto))
    return _grade([[Paragraph(' | '.join(partes) or '-', _P_TEXTO)]], [_LARGURA])


def _bloco_canhoto(d: Danfse) -> Table:
    return _grade([[
        _celula(_campo('Data de cientificação', '')),
        _celula(_campo('Identificação e assinatura', '')),
        _celula(_campo('Nº NFS-e / Chave da NFS-e', f'{d.numero} / {d.chave}' if d.chave else d.numero)),
    ]], [_LARGURA * 0.22, _LARGURA * 0.40, _LARGURA * 0.38])


def _celula(par_rotulo_valor: list) -> Table:
    """Empilha rótulo + valor dentro de uma célula da grade."""
    t = Table([[par_rotulo_valor[0]], [par_rotulo_valor[1]]], hAlign='LEFT')
    t.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return t


def _pagina(marca_dagua: str | None):
    """Callback de página: desenha a borda de 1pt ao redor (NT 008 2.2.3) e, se
    a nota já não vale, o carimbo diagonal CANCELADA/SUBSTITUÍDA (2.5.1/2.5.2)."""
    def _desenhar(canvas, _doc):
        canvas.saveState()
        canvas.setLineWidth(1)
        canvas.setStrokeColor(_TINTA)
        m = 3.5 * mm
        canvas.rect(m, m, A4[0] - 2 * m, A4[1] - 2 * m)
        if marca_dagua:
            canvas.setFont('Helvetica', 60)
            canvas.setFillColor(_MARCA_DAGUA)
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(45)
            canvas.drawCentredString(0, 0, marca_dagua.upper())
        canvas.restoreState()
    return _desenhar


def _gerar_danfse_pdf_legado(xml: str, consulta_url: str | None = None,
                     municipio_por_cep: dict[str, str] | None = None,
                     marca_dagua: str | None = None) -> bytes:
    """Monta o PDF da nota a partir do XML. Levanta DanfseError se não der.

    ``marca_dagua`` carimba a página ('CANCELADA' / 'SUBSTITUÍDA') quando a nota
    já não vale — exigência da NT 008 (2.5.1/2.5.2)."""
    d = ler_xml(xml, municipio_por_cep)
    # Nota antiga não ganha link de conferência: o sistema municipal saiu do ar
    # em 20/07/2026 e levou junto as URLs de verificação dele. Nessas o que vale
    # é o código de verificação, que já sai no cabeçalho.
    d.consulta_url = (consulta_url or '') if d.formato == 'nacional' else ''

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=_MARGEM, rightMargin=_MARGEM,
        topMargin=_MARGEM, bottomMargin=_MARGEM,
        title=f'NFS-e {d.numero}'.strip(), author='MasterSat',
    )
    hist: list = [_cabecalho(d), Spacer(1, 0.8 * mm)]
    hist += [_secao('Dados da NFS-e'), _identificacao(d)]
    if d.chave:
        hist.append(_grade([[_celula([Paragraph('CHAVE DE ACESSO DA NFS-E', _P_ROTULO),
                                      Paragraph(_esc(d.chave), _P_CHAVE)])]], [_LARGURA]))
    hist.append(Spacer(1, 0.3 * mm))

    if d.formato == 'nacional':
        # Todos os blocos do Anexo I (NT 008), na ordem exigida. Blocos
        # suprimíveis colapsam para uma linha (item 2.3); campos sem dado saem
        # com traço (Nota 12).
        hist += [_secao('Prestador / Fornecedor'),
                 _bloco_pessoa(d.prestador, extra_rows=[[
                     ('Simples Nacional na competência', d.regime_simples or '-'),
                     ('Regime de apuração pelo SN', d.regime_apuracao or '-')]]),
                 Spacer(1, 0.3 * mm)]

        hist.append(_secao('Tomador / Adquirente'))
        hist.append(_bloco_pessoa(d.tomador) if (d.tomador.nome or d.tomador.documento)
                    else _bloco_texto('TOMADOR/ADQUIRENTE DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e'))
        hist.append(Spacer(1, 0.3 * mm))

        hist.append(_secao('Destinatário da Operação'))
        hist.append(_bloco_texto('O DESTINATÁRIO É O PRÓPRIO TOMADOR/ADQUIRENTE DA OPERAÇÃO')
                    if d.destinatario_proprio_tomador else _bloco_pessoa(d.destinatario))
        hist.append(Spacer(1, 0.3 * mm))

        hist.append(_secao('Intermediário da Operação'))
        hist.append(_bloco_pessoa(d.intermediario) if (d.intermediario.nome or d.intermediario.documento)
                    else _bloco_texto('INTERMEDIÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e'))
        hist.append(Spacer(1, 0.3 * mm))

        hist += [_secao('Serviço Prestado'), *_bloco_servico(d), Spacer(1, 0.3 * mm)]
        hist += [_secao('Tributação Municipal (ISSQN)'), _bloco_campos(d.issqn_campos, 4), Spacer(1, 0.3 * mm)]
        hist += [_secao('Tributação Federal (Exceto CBS)'), _bloco_campos(d.federal_campos, 3), Spacer(1, 0.3 * mm)]
        hist += [_secao('Tributação IBS / CBS'), _bloco_campos(d.ibscbs_campos, 3), Spacer(1, 0.3 * mm)]
        hist += [_secao('Valor Total da NFS-e'), *_bloco_valor_total(d), Spacer(1, 0.3 * mm)]
        hist += [_secao('Informações Complementares'), _bloco_info_compl(d), Spacer(1, 0.3 * mm)]
        hist += [_secao('Canhoto'), _bloco_canhoto(d)]
    else:
        # Legado de Joinville (notas anteriores ao padrão nacional): leiaute simples.
        hist += [_secao('Prestador de serviços'), _bloco_pessoa(d.prestador), Spacer(1, 2 * mm)]
        hist += [_secao('Tomador de serviços'), _bloco_pessoa(d.tomador), Spacer(1, 2 * mm)]
        hist += [_secao('Discriminação dos serviços'),
                 _grade([[Paragraph(_esc(d.descricao_servico) or '—', _P_TEXTO)]], [_LARGURA]),
                 Spacer(1, 2 * mm)]
        trib = _bloco_tributacao(d)
        if trib is not None:
            hist += [_secao('Tributação'), trib, Spacer(1, 2 * mm)]
        hist += [_secao('Valores'), *_bloco_valores(d), Spacer(1, 2 * mm)]
        if d.outras_informacoes:
            hist += [_secao('Outras informações'),
                     _grade([[Paragraph(_esc(d.outras_informacoes), _P_TEXTO)]], [_LARGURA]),
                     Spacer(1, 2 * mm)]

    hist.append(_rodape(d))
    cb = _pagina(marca_dagua)
    doc.build(hist, onFirstPage=cb, onLaterPages=cb)
    return buf.getvalue()


def _cabecalho(d: Danfse) -> Table:
    """
    Cabeçalho no padrão da NT 008 (item 2.4.3): à esquerda a marca "NFS-e";
    ao centro "DANFSe v2.0" / "Documento Auxiliar da NFS-e"; à direita o
    município do emitente, o ambiente e o QR Code de consulta pública.

    Em produção restrita (tpAmb=2), abaixo do título entra, em vermelho, a
    expressão obrigatória "NFS-e SEM VALIDADE JURÍDICA".
    """
    centro = [Paragraph('DANFSe v2.0', _P_DANFSE_TIT),
              Paragraph('Documento Auxiliar da NFS-e', _P_DANFSE_SUB)]
    if d.teste:
        centro.append(Paragraph('NFS-e SEM VALIDADE JURÍDICA', _P_SEM_VALIDADE))

    amb = []
    if d.municipio_emissao:
        amb.append(f'Município: {_esc(d.municipio_emissao)}')
    if d.ambiente_gerador:
        amb.append(f'Ambiente Gerador: {_esc(d.ambiente_gerador)}')
    if d.tipo_ambiente:
        amb.append(f'Tipo de Ambiente: {_esc(d.tipo_ambiente)}')

    direita = Table([
        [Paragraph('<br/>'.join(amb) or ' ', _P_AMB)],
        [_qr(d.consulta_url or d.chave, lado_mm=17)],
        [Paragraph('A autenticidade desta NFS-e pode ser verificada pela leitura '
                   'deste código QR ou pela consulta da chave de acesso no portal '
                   'nacional da NFS-e', _P_AUTENT)],
    ], colWidths=[52 * mm], hAlign='RIGHT')
    direita.setStyle(TableStyle([
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 1), ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))

    t = Table([[Paragraph('NFS-e', _P_NFSE_MARCA), centro, direita]],
              colWidths=[26 * mm, _LARGURA - 26 * mm - 54 * mm, 54 * mm], hAlign='LEFT')
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, 0), 'TOP'),
        ('ALIGN', (1, 0), (1, 0), 'CENTER'),
        ('BACKGROUND', (0, 0), (-1, -1), _CINZA),   # cabeçalho sombreado (NT 008 2.2.3)
        ('BOX', (0, 0), (-1, -1), 0.5, _BORDA),
        ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    return t


def _identificacao(d: Danfse) -> Table:
    """Dados de Identificação da NFS-e (NT 008, item 2.1.2) — os 10 campos, com
    número da NFS-e e número/série da DPS bem separados (são coisas diferentes)."""
    return _grade([
        [_celula(_campo('Número da NFS-e', d.numero)),
         _celula(_campo('Competência', d.competencia)),
         _celula(_campo('Data e hora de emissão da NFS-e', d.data_emissao))],
        [_celula(_campo('Número da DPS', d.numero_dps)),
         _celula(_campo('Série da DPS', d.serie)),
         _celula(_campo('Data e hora de emissão da DPS', d.data_emissao_dps))],
        [_celula(_campo('Emitente da NFS-e', d.emitente_tipo)),
         _celula(_campo('Situação da NFS-e', d.situacao)),
         _celula(_campo('Finalidade', d.finalidade))],
    ], [_LARGURA * 0.34, _LARGURA * 0.28, _LARGURA * 0.38])


def _bloco_tributacao(d: Danfse) -> Table | None:
    """O que a consulta pública mostra em "Tributação Municipal" e faltava aqui."""
    linhas = [
        [('Tributação do ISSQN', d.tributacao_issqn), ('Retenção do ISSQN', d.retencao_issqn),
         ('Município de incidência', d.municipio_incidencia)],
        [('Situação no Simples Nacional', d.regime_simples),
         ('Regime de apuração', d.regime_apuracao),
         ('Regime especial de tributação', d.regime_especial)],
    ]
    presentes = [linha for linha in linhas if any(v for _, v in linha)]
    if not presentes:
        return None
    return _grade([[_celula(_campo(r, v)) for r, v in linha] for linha in presentes],
                  [_LARGURA / 3] * 3)


def _bloco_valores(d: Danfse) -> list:
    """Faixa de valores + o líquido em destaque dourado."""
    total = Table(
        [[[Paragraph('VALOR LÍQUIDO DA NFS-E + IBS/CBS', _P_TOTAL_ROTULO),
           Paragraph(_esc(d.valor_liquido), _P_TOTAL)]]],
        colWidths=[_LARGURA], hAlign='LEFT')
    total.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), _CINZA),   # campo sombreado (NT 008 2.2.3)
        ('BOX', (0, 0), (-1, -1), 0.5, _BORDA),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    if not d.valores:
        return [total]
    col = _LARGURA / len(d.valores)
    faixa = _grade(
        [[Paragraph(r.upper(), _P_ROTULO) for r, _ in d.valores],
         [Paragraph(_esc(v), _P_VALOR) for _, v in d.valores]],
        [col] * len(d.valores), fundo_titulo=True)
    return [faixa, total]


def _qr(conteudo: str, lado_mm: float = 28) -> Drawing:
    lado = lado_mm * mm
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
        ('ROUNDEDCORNERS', [_RAIO, _RAIO, _RAIO, _RAIO]),
    ]))
    return t


def _rodape(d: Danfse) -> Table:
    linhas = [
        'Documento auxiliar gerado pelo sistema MasterSat a partir do XML autenticado da NFS-e. '
        'O documento fiscal é o XML; este PDF é a sua representação visual.',
    ]
    procedencia = [d.origem]
    if d.numero_dfe:
        procedencia.append(f'DFe nº {d.numero_dfe}')
    if d.situacao:
        procedencia.append(f'situação: {d.situacao}')
    if any(procedencia):
        linhas.append(' · '.join(x for x in procedencia if x) + '.')
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


# ===========================================================================
# RENDERIZADOR OFICIAL DANFSe v2.0 - NT SE/CGNFS-e 008 v1.02 (14/07/2026)
# ===========================================================================
#
# Este renderizador substitui SOMENTE o desenho do formato nacional. A leitura
# do XML permanece acima. Para notas municipais legadas, o gerador antigo e
# mantido em _gerar_danfse_pdf_legado().
#
# Objetivos:
#   - reproduzir a disposicao do Anexo I da NT 008 v1.02;
#   - usar a logomarca oficial da NFS-e;
#   - usar Arial nos labels e Microsoft Sans Serif nos conteudos quando as
#     fontes existirem no sistema;
#   - usar o QR Code exatamente com a URL de Consulta Publica + chave;
#   - nao inserir rodape/marca do ERP, pois o DANFSe so deve representar dados
#     previstos no XML/modelo oficial;
#   - manter uma unica pagina A4 em modo retrato.
#
# IMPORTANTE: a validade fiscal decorre da NFS-e/XML autorizado no Sistema
# Nacional. Este PDF e o DANFSe (documento auxiliar/representacao impressa).

from reportlab.pdfgen import canvas as _canvas
from reportlab.pdfbase import pdfmetrics as _pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as _TTFont
from reportlab.graphics import renderPDF as _renderPDF

_NFSE_LOGO_OFICIAL = Path(__file__).with_name('nfse_logo_horizontal.png')
_PT_PER_CM = 72.0 / 2.54
_PAGE_W, _PAGE_H = A4

# Geometria medida no modelo oficial/Anexo I (A4 595x842 pt).
_X0, _X1, _X2, _X3, _X4 = 8.50, 153.07, 297.64, 442.20, 586.77
_TX = (_X0 + 3.41, _X1 + 3.40, _X2 + 3.40, _X3 + 3.41)
_GRAY_5 = colors.Color(0.949, 0.949, 0.949)
_BLACK = colors.black
_RED = colors.Color(1, 0, 0)


def _registrar_fontes_oficiais() -> tuple[str, str, str]:
    """Retorna (Arial normal, Arial bold, Microsoft Sans Serif).

    No Windows usa as fontes nativas. Em Linux tenta equivalentes metricos.
    Nao embute/distribui arquivos de fonte no projeto.
    """
    candidatos = {
        'DANFSE_ARIAL': [
            r'C:\\Windows\\Fonts\\arial.ttf',
            '/usr/share/fonts/truetype/msttcorefonts/Arial.ttf',
            '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
            '/usr/share/fonts/truetype/arimo/Arimo-Regular.ttf',
        ],
        'DANFSE_ARIAL_BOLD': [
            r'C:\\Windows\\Fonts\\arialbd.ttf',
            '/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf',
            '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
            '/usr/share/fonts/truetype/arimo/Arimo-Bold.ttf',
        ],
        'DANFSE_MS_SANS': [
            r'C:\\Windows\\Fonts\\micross.ttf',
            '/usr/share/fonts/truetype/msttcorefonts/Microsoft_Sans_Serif.ttf',
            '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
            '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
        ],
    }
    fallback = {
        'DANFSE_ARIAL': 'Helvetica',
        'DANFSE_ARIAL_BOLD': 'Helvetica-Bold',
        'DANFSE_MS_SANS': 'Helvetica',
    }
    result = {}
    for nome, paths in candidatos.items():
        if nome in _pdfmetrics.getRegisteredFontNames():
            result[nome] = nome
            continue
        achou = False
        for p in paths:
            if Path(p).exists():
                try:
                    _pdfmetrics.registerFont(_TTFont(nome, p))
                    result[nome] = nome
                    achou = True
                    break
                except Exception:
                    pass
        if not achou:
            result[nome] = fallback[nome]
    return result['DANFSE_ARIAL'], result['DANFSE_ARIAL_BOLD'], result['DANFSE_MS_SANS']


_F_ARIAL, _F_ARIAL_B, _F_MS = _registrar_fontes_oficiais()


def _y(top: float) -> float:
    """Converte coordenada medida a partir do topo para coordenada ReportLab."""
    return _PAGE_H - top


def _baseline(top: float, size: float, font: str) -> float:
    # Ajuste visual calibrado com o PDF oficial (Arial/MS Sans).
    fator = 0.91 if font == _F_ARIAL_B else 0.88
    return _PAGE_H - top - size * fator


def _txt_canvas(c, x: float, top: float, texto: str, *, font: str, size: float,
                max_width: float | None = None, ellipsis: bool = True,
                align: str = 'left') -> None:
    texto = str(texto if texto not in (None, '') else '-')
    if max_width is not None and ellipsis:
        sufixo = '...'
        if _pdfmetrics.stringWidth(texto, font, size) > max_width:
            base = texto
            while base and _pdfmetrics.stringWidth(base + sufixo, font, size) > max_width:
                base = base[:-1]
            texto = base.rstrip() + sufixo
    c.setFillColor(_BLACK)
    c.setFont(font, size)
    yy = _baseline(top, size, font)
    if align == 'center':
        c.drawCentredString(x, yy, texto)
    elif align == 'right':
        c.drawRightString(x, yy, texto)
    else:
        c.drawString(x, yy, texto)


def _label_value(c, col: int, top: float, label: str, value: str,
                 width: float | None = None, label_upper: bool = False) -> None:
    x = _TX[col]
    if width is None:
        width = (_X1 - _X0 if col == 0 else _X2 - _X1 if col == 1 else _X3 - _X2 if col == 2 else _X4 - _X3) - 7
    lab = label.upper() if label_upper else label
    _txt_canvas(c, x, top, lab, font=_F_ARIAL_B, size=6 if not label_upper else 7,
                max_width=width)
    _txt_canvas(c, x, top + 6.70, value or '-', font=_F_MS, size=7, max_width=width)


def _hline(c, top: float, x0: float = _X0, x1: float = _X4, width: float = 0.5) -> None:
    c.setStrokeColor(_BLACK)
    c.setLineWidth(width)
    c.line(x0, _y(top), x1, _y(top))


def _fill_rect_top(c, x0: float, top: float, x1: float, bottom: float, fill=_GRAY_5) -> None:
    c.setFillColor(fill)
    c.rect(x0, _y(bottom), x1 - x0, bottom - top, stroke=0, fill=1)


def _fmt_ibge(v: str) -> str:
    d = _so_digitos(v)
    return f'{d[:2]}.{d[2:]}' if len(d) == 7 else (v or '-')


def _fmt_cep_danfse(v: str) -> str:
    d = _so_digitos(v)
    return f'{d[:2]}.{d[2:5]}-{d[5:]}' if len(d) == 8 else (v or '-')


def _mun_uf(municipio: str, uf: str, sep: str = ' / ') -> str:
    municipio = (municipio or '').strip()
    uf = (uf or '').strip()
    if not municipio and not uf:
        return '-'
    if uf and municipio.endswith('/' + uf):
        municipio = municipio[:-(len(uf) + 1)].strip()
    if uf and municipio.endswith(' - ' + uf):
        municipio = municipio[:-(len(uf) + 3)].strip()
    return sep.join(x for x in (municipio, uf) if x) or '-'


def _xml_nacional_raiz(xml: str):
    texto = (xml or '').strip()
    if 'ConsultarLoteRpsResponse' in texto or '&lt;' in texto[:400]:
        m = re.search(r'<return>(.*?)</return>', texto, re.S)
        if m:
            texto = html.unescape(m.group(1)).strip()
    try:
        return etree.fromstring(texto.encode('utf-8'))
    except etree.XMLSyntaxError as exc:
        raise DanfseError(f'XML da nota ilegivel: {exc}') from exc


def _layout_raw_nacional(xml: str, d: Danfse, municipio_por_cep: dict[str, str] | None = None) -> dict:
    raiz = _xml_nacional_raiz(xml)
    inf = _filho(raiz, NS_NFSE, 'infNFSe')
    dps = _filho(inf, NS_NFSE, 'DPS', 'infDPS') if inf is not None else None
    if inf is None or dps is None:
        raise DanfseError('XML nacional sem infNFSe/DPS/infDPS')

    # Mapa de municipios semelhante ao parser principal.
    por_cep = {_so_digitos(k): v for k, v in (municipio_por_cep or {}).items()}
    municipios = {}
    for cod, nome in [
        (_txt(inf, NS_NFSE, 'cLocIncid'), _txt(inf, NS_NFSE, 'xLocIncid')),
        (_txt(dps, NS_NFSE, 'cLocEmi'), _txt(inf, NS_NFSE, 'xLocEmi')),
        (_txt(dps, NS_NFSE, 'serv', 'locPrest', 'cLocPrestacao'), _txt(inf, NS_NFSE, 'xLocPrestacao')),
    ]:
        if cod and nome:
            municipios[cod] = nome

    def pessoa(bloco):
        if bloco is None:
            return dict(nome='', doc='', im='', fone='', end='', mun='', uf='', ibge='', cep='', email='')
        end = _filho(bloco, NS_NFSE, 'end')
        if end is None:
            end = _filho(bloco, NS_NFSE, 'enderNac')
        e_txt, m_txt, cep = _endereco_nacional(end, municipios, por_cep)
        endn = _filho(end, NS_NFSE, 'endNac') if end is not None else None
        ibge = _txt(endn, NS_NFSE, 'cMun') if endn is not None else _txt(end, NS_NFSE, 'cMun')
        uf = _txt(end, NS_NFSE, 'UF') if end is not None else ''
        if not uf and m_txt and '/' in m_txt:
            uf = m_txt.rsplit('/', 1)[-1].strip()
        mun = m_txt.rsplit('/', 1)[0].strip() if m_txt and '/' in m_txt else m_txt
        return dict(
            nome=_txt(bloco, NS_NFSE, 'xNome'),
            doc=_doc_formatado(_txt(bloco, NS_NFSE, 'CNPJ') or _txt(bloco, NS_NFSE, 'CPF') or _txt(bloco, NS_NFSE, 'NIF')),
            im=_txt(bloco, NS_NFSE, 'IM'), fone=_fone_formatado(_txt(bloco, NS_NFSE, 'fone')),
            end=e_txt, mun=mun, uf=uf, ibge=ibge, cep=cep, email=_txt(bloco, NS_NFSE, 'email'),
        )

    prest = pessoa(_filho(dps, NS_NFSE, 'prest'))
    # Em algumas respostas nacionais os dados completos do emitente ficam em infNFSe/emit.
    emit = _filho(inf, NS_NFSE, 'emit')
    if emit is not None:
        pe = pessoa(emit)
        for k in prest:
            if not prest[k] and pe.get(k):
                prest[k] = pe[k]
    toma = pessoa(_filho(dps, NS_NFSE, 'toma'))
    dest = pessoa(_filho(dps, NS_NFSE, 'IBSCBS', 'dest'))
    interm = pessoa(_filho(dps, NS_NFSE, 'interm'))

    val_inf = _filho(inf, NS_NFSE, 'valores')
    dps_val = _filho(dps, NS_NFSE, 'valores')
    trib_mun = _filho(dps_val, NS_NFSE, 'trib', 'tribMun') if dps_val is not None else None
    trib_fed = _filho(dps_val, NS_NFSE, 'trib', 'tribFed') if dps_val is not None else None
    piscof = _filho(trib_fed, NS_NFSE, 'piscofins') if trib_fed is not None else None
    ibs_dps = _filho(dps, NS_NFSE, 'IBSCBS')
    ibs_inf = _filho(inf, NS_NFSE, 'IBSCBS')
    ibs_vals = _filho(ibs_inf, NS_NFSE, 'valores') if ibs_inf is not None else None
    ibs_dps_vals = _filho(ibs_dps, NS_NFSE, 'valores') if ibs_dps is not None else None
    gtrib = _filho(ibs_dps_vals, NS_NFSE, 'trib', 'gIBSCBS') if ibs_dps_vals is not None else None
    totcibs = _filho(ibs_inf, NS_NFSE, 'totCIBS') if ibs_inf is not None else None

    uf_emit = prest.get('uf', '')
    if not uf_emit and d.prestador.municipio and '/' in d.prestador.municipio:
        uf_emit = d.prestador.municipio.rsplit('/', 1)[-1].strip()

    ctribn = _txt(dps, NS_NFSE, 'serv', 'cServ', 'cTribNac')
    ctribm = _txt(dps, NS_NFSE, 'serv', 'cServ', 'cTribMun')
    xn = _txt(inf, NS_NFSE, 'xTribNac')
    xm = _txt(inf, NS_NFSE, 'xTribMun')
    nbs = _txt(dps, NS_NFSE, 'serv', 'cServ', 'cNBS')
    pais_prest = _txt(dps, NS_NFSE, 'serv', 'locPrest', 'cPaisPrestacao')

    def v(node, *tags):
        return _txt(node, NS_NFSE, *tags) if node is not None else ''

    def dinheiro(s, zero=False):
        if s not in ('', None):
            return _brl(s)
        return 'R$ 0,00' if zero else '-'

    def perc(s):
        return _pct(s) if s not in ('', None) else '-'

    # Contribuicoes sociais: regra da NT 008 v1.02.
    tp_pc = v(piscof, 'tpRetPisCofins')
    csll = v(trib_fed, 'vRetCSLL')
    pis = v(piscof, 'vPis')
    cof = v(piscof, 'vCofins')
    if tp_pc == '1':
        try:
            contrib_soc = _brl(str(float(csll or 0) + float(pis or 0) + float(cof or 0)))
            pis_deb, cof_deb = 'R$ 0,00', 'R$ 0,00'
        except ValueError:
            contrib_soc, pis_deb, cof_deb = dinheiro(csll), dinheiro(pis), dinheiro(cof)
    else:
        contrib_soc, pis_deb, cof_deb = dinheiro(csll), dinheiro(pis), dinheiro(cof)

    # Exclusoes/reducoes IBS/CBS: somatorio dos campos que existirem.
    exclusoes_paths = [
        (dps_val, ('vDescCondIncond', 'vDescIncond')),
        (ibs_vals, ('vDR',)), (ibs_vals, ('vCalcDR',)), (ibs_vals, ('vCalcReeRepRes',)),
        (val_inf, ('vISSQN',)), (piscof, ('vPis',)), (piscof, ('vCofins',)),
    ]
    soma = 0.0; achou = False
    for node, tags in exclusoes_paths:
        s = v(node, *tags)
        if s:
            try:
                soma += float(s); achou = True
            except ValueError:
                pass
    exclusoes = _brl(str(soma)) if achou else 'R$ 0,00'

    uf_vals = _filho(ibs_vals, NS_NFSE, 'uf') if ibs_vals is not None else None
    mun_vals = _filho(ibs_vals, NS_NFSE, 'mun') if ibs_vals is not None else None
    fed_vals = _filho(ibs_vals, NS_NFSE, 'fed') if ibs_vals is not None else None
    gibs = _filho(totcibs, NS_NFSE, 'gIBS') if totcibs is not None else None
    gibsm = _filho(gibs, NS_NFSE, 'gIBSMunTot') if gibs is not None else None
    gibsu = _filho(gibs, NS_NFSE, 'gIBSUFTot') if gibs is not None else None
    gcbs = _filho(totcibs, NS_NFSE, 'gCBS') if totcibs is not None else None

    vibs = v(gibs, 'vIBSTot')
    vcbs = v(gcbs, 'vCBS')
    total_ibscbs = '-'
    if vibs or vcbs:
        try:
            total_ibscbs = _brl(str(float(vibs or 0) + float(vcbs or 0)))
        except ValueError:
            pass

    # Totais aproximados, sempre no formato do modelo.
    tottrib = _filho(dps_val, NS_NFSE, 'trib', 'totTrib') if dps_val is not None else None
    bruto = v(dps_val, 'vServPrest', 'vServ')
    def esfera(vtag, ptag):
        vv = v(tottrib, vtag)
        if vv:
            return _brl(vv)
        pp = v(tottrib, ptag)
        if pp:
            try:
                return _brl(str(float(bruto or 0) * float(pp) / 100))
            except ValueError:
                return _pct(pp)
        return '-'
    trib_line = ('Totais aproximados dos Tributos cfe. Lei n° 12.741/2012: '
                 f'Federais: {esfera("vTotTribFed","pTotTribFed")}; '
                 f'Estaduais: {esfera("vTotTribEst","pTotTribEst")}; '
                 f'Municipais: {esfera("vTotTribMu","pTotTribMu")};')

    return dict(
        raiz=raiz, inf=inf, dps=dps, prest=prest, toma=toma, dest=dest, interm=interm,
        ambGer=v(inf, 'ambGer'), tpAmb=v(dps, 'tpAmb'), uf_emit=uf_emit,
        ctrib=(ctribn or '-') + ' / ' + (ctribm or '-'), nbs=nbs or '-',
        desc_trib=(xm or xn or '-'), desc_serv=v(dps, 'serv', 'cServ', 'xDescServ') or '-',
        local_prest=f'{d.municipio_prestacao or "-"} / {uf_emit or "-"} / {pais_prest or "-"}',
        iss_tipo=d.tributacao_issqn or '-',
        iss_local=f'{d.municipio_incidencia or "-"} / {uf_emit or "-"} / {v(trib_mun, "cPaisResult") or "-"}',
        iss_regesp=d.regime_especial or '',
        iss_imun=v(trib_mun, 'tpImunidade'), iss_susp=v(trib_mun, 'exigSusp', 'tpSusp'),
        iss_proc=v(trib_mun, 'exigSusp', 'nProcesso'),
        beneficio=v(val_inf, 'tpBM'), calculo_bm=v(trib_mun, 'BM', 'vCalcBM') or v(trib_mun, 'BM', 'vRedBCBM'),
        ded_red=v(dps_val, 'vDedRed'), desc_incond=v(dps_val, 'vDescCondIncond', 'vDescIncond'),
        bc_iss=dinheiro(v(val_inf, 'vBC')), aliq_iss=perc(v(val_inf, 'pAliqAplic')),
        ret_iss=d.retencao_issqn or '-', iss_apurado=dinheiro(v(val_inf, 'vISSQN')),
        irrf=dinheiro(v(trib_fed, 'vRetIRRF')), cp=dinheiro(v(trib_fed, 'vRetCP')),
        contrib_soc=contrib_soc, pis=pis_deb, cofins=cof_deb,
        desc_contrib=_RET_PISCOFINS.get(tp_pc, '-') if tp_pc else '-',
        cst_class=((v(gtrib, 'CST') or '-') + ' / ' + (v(gtrib, 'cClassTrib') or '-')),
        indop=((v(ibs_inf, 'cIndOp') or '-') + ' / ' + (v(ibs_inf, 'cLocalidadeIncid') or '-') +
               ' / ' + (v(ibs_inf, 'xLocalidadeIncid') or '-') + ' / ' + (uf_emit or '-')),
        exclusoes=exclusoes, bc_ibscbs=dinheiro(v(ibs_vals, 'vBC')),
        red_aliq=' / '.join([perc(v(uf_vals,'pRedAliqUF')), perc(v(mun_vals,'pRedAliqMun')), perc(v(fed_vals,'pRedAliqCBS'))]),
        aliq_ibs=' / '.join([perc(v(uf_vals,'pIBSUF')), perc(v(mun_vals,'pIBSMun'))]),
        ef_mun=perc(v(mun_vals,'pAliqEfetMun')), v_mun=dinheiro(v(gibsm,'vIBSMun')),
        ef_uf=perc(v(uf_vals,'pAliqEfetUF')), v_uf=dinheiro(v(gibsu,'vIBSUF')),
        v_ibs=dinheiro(vibs), aliq_cbs=perc(v(fed_vals,'pCBS')), ef_cbs=perc(v(fed_vals,'pAliqEfetCBS')),
        v_cbs=dinheiro(vcbs),
        bruto=dinheiro(bruto), desc_cond=dinheiro(v(dps_val, 'vDescCondIncond','vDescCond')),
        ret_total=dinheiro(v(val_inf, 'vTotalRet')), liquido=d.valor_liquido or '-', total_ibscbs=total_ibscbs,
        total_nf=dinheiro(v(totcibs, 'vTotNF')),
        info=(d.outras_informacoes or '').strip(), trib_line=trib_line,
    )


def _draw_qr_oficial(c, chave: str) -> None:
    url = f'https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave={chave}'
    widget = qr.QrCodeWidget(url, barLevel='M')
    x1, y1, x2, y2 = widget.getBounds()
    lado = 45.0  # exatamente o tamanho observado no PDF oficial (>= 1,52 cm)
    dsg = Drawing(lado, lado, transform=[lado/(x2-x1),0,0,lado/(y2-y1),0,0])
    dsg.add(widget)
    _renderPDF.draw(dsg, c, 491.988, _PAGE_H - 89.764)


def _draw_pessoa_bloco(c, top: float, titulo: str, p: dict, *, simples: str = '', apuracao: str = '',
                       tomador: bool = False) -> float:
    """Desenha Prestador/Tomador no estilo exato do modelo. Retorna o bottom."""
    row = 19.07
    rows = 3 if tomador else 4
    bottom = top + row * rows
    _hline(c, top - 0.25)
    _fill_rect_top(c, _X0, top, _X1, top + row)
    _txt_canvas(c, _TX[0], top + 0.23, titulo, font=_F_ARIAL_B, size=7, max_width=_X1-_X0-7)

    _label_value(c, 1, top + 0.20, 'CNPJ / CPF / NIF', p.get('doc','-'))
    _label_value(c, 2, top + 0.20, 'Indicador Municipal (Inscrição)', p.get('im','-'))
    _label_value(c, 3, top + 0.20, 'Telefone', p.get('fone','-'))

    _label_value(c, 0, top + row + 0.20, 'Nome / Nome Empresarial', p.get('nome','-'), width=282)
    _label_value(c, 2, top + row + 0.20, 'Município / Sigla UF', _mun_uf(p.get('mun',''),p.get('uf','')))
    codcep = f'{_fmt_ibge(p.get("ibge",""))} / {_fmt_cep_danfse(p.get("cep",""))}'
    _label_value(c, 3, top + row + 0.20, 'Código IBGE / CEP', codcep)

    _label_value(c, 0, top + row*2 + 0.20, 'Endereço', p.get('end','-'), width=282)
    _label_value(c, 2, top + row*2 + 0.20, 'E-mail', p.get('email','-'), width=282)

    if not tomador:
        _label_value(c, 0, top + row*3 + 0.20, 'Simples Nacional na Data de Competência', simples or '-', width=137)
        _label_value(c, 1, top + row*3 + 0.20, 'Regime de Apuração Tributária pelo SN', apuracao or '-', width=424)

    _hline(c, bottom - 0.25)
    return bottom


def _draw_reduzido(c, top: float, texto: str) -> float:
    h = 8.43
    _hline(c, top - 0.25)
    _txt_canvas(c, (_X0+_X4)/2, top + 0.25, texto, font=_F_MS, size=7, align='center', ellipsis=False)
    _hline(c, top + h - 0.25)
    return top + h


def _draw_servico(c, top: float, r: dict) -> float:
    row = 19.07
    _hline(c, top - 0.25)
    _fill_rect_top(c, _X0, top, _X1, top + row)
    _txt_canvas(c, _TX[0], top + 0.23, 'SERVIÇO PRESTADO', font=_F_ARIAL_B, size=7)
    _label_value(c, 1, top + 0.20, 'Código de Tributação Nacional/Municipal', r['ctrib'])
    _label_value(c, 2, top + 0.20, 'Código da NBS', r['nbs'])
    _label_value(c, 3, top + 0.20, 'Local da Prestação / Sigla UF / País', r['local_prest'])
    _txt_canvas(c, _TX[0], top + row + 0.20, r['desc_trib'], font=_F_MS, size=7, max_width=_X4-_X0-7)
    _txt_canvas(c, _TX[0], top + row + 12.58, 'Descrição do Serviço', font=_F_ARIAL_B, size=6)
    _txt_canvas(c, _TX[0], top + row + 19.28, r['desc_serv'], font=_F_MS, size=7, max_width=_X4-_X0-7)
    bottom = top + 50.82
    _hline(c, bottom - 0.25)
    return bottom


def _draw_municipal_compacto(c, top: float, r: dict) -> float:
    row = 19.32
    _hline(c, top - 0.25)
    _fill_rect_top(c, _X0, top, _X1, top + row)
    _txt_canvas(c, _TX[0], top + 0.23, 'TRIBUTAÇÃO MUNICIPAL (ISSQN)', font=_F_ARIAL_B, size=7)
    _label_value(c, 1, top + 0.20, 'Tipo de Tributação do ISSQN', r['iss_tipo'])
    # No modelo oficial este campo ocupa as colunas 3 e 4.
    _txt_canvas(c, _TX[2], top + 0.20, 'Município / Sigla UF / País de Incidência do ISSQN',
                font=_F_ARIAL_B, size=6, max_width=_X4-_X2-7)
    _txt_canvas(c, _TX[2], top + 6.90, r['iss_local'], font=_F_MS, size=7, max_width=_X4-_X2-7)
    _label_value(c, 0, top + row + 0.20, 'BC ISSQN', r['bc_iss'])
    _label_value(c, 1, top + row + 0.20, 'Alíquota Aplicada', r['aliq_iss'])
    _label_value(c, 2, top + row + 0.20, 'Retenção do ISSQN', r['ret_iss'])
    _label_value(c, 3, top + row + 0.20, 'ISSQN Apurado', r['iss_apurado'])
    bottom = top + row*2
    _hline(c, bottom - 0.25)
    return bottom


def _draw_section_2rows(c, top: float, titulo: str, row1: list[tuple[int,str,str]], row2: list[tuple[int,str,str]]) -> float:
    row = 19.32
    _hline(c, top - 0.25)
    _fill_rect_top(c, _X0, top, _X1, top + row)
    _txt_canvas(c, _TX[0], top + 0.23, titulo, font=_F_ARIAL_B, size=7, max_width=_X1-_X0-7)
    for col, lab, val in row1:
        _label_value(c, col, top + 0.20, lab, val, width=(_X4-_X3-7 if col==3 else _X3-_X2-7 if col==2 else _X2-_X1-7 if col==1 else _X1-_X0-7))
    for col, lab, val in row2:
        _label_value(c, col, top + row + 0.20, lab, val)
    bottom = top + row*2
    _hline(c, bottom - 0.25)
    return bottom


def _draw_ibscbs(c, top: float, r: dict) -> float:
    row = 19.20
    _hline(c, top - 0.25)
    _fill_rect_top(c, _X0, top, _X1, top + row)
    _txt_canvas(c, _TX[0], top + 0.23, 'TRIBUTAÇÃO IBS/CBS', font=_F_ARIAL_B, size=7)
    _label_value(c, 1, top + 0.20, 'CST / cClassTrib', r['cst_class'])
    # campo largo das colunas 2+3
    _txt_canvas(c, _TX[2], top + 0.20, 'Indicador de Operação / Código IBGE Incidência / Município Incidência / Sigla UF',
                font=_F_ARIAL_B, size=6, max_width=_X4-_X2-7)
    _txt_canvas(c, _TX[2], top + 6.90, r['indop'], font=_F_MS, size=7, max_width=_X4-_X2-7)

    rows = [
        [('Exclusões e Reduções da Base de Cálculo', r['exclusoes']), ('Base de Cálculo Após Exclusões e Reduções', r['bc_ibscbs']),
         ('Red. Alíquota IBS / Red. Alíquota CBS', r['red_aliq']), ('Alíquota - IBS UF / IBS Mun', r['aliq_ibs'])],
        [('Alíq. Efetiva Municipal - IBS', r['ef_mun']), ('Valor Apurado Municipal - IBS', r['v_mun']),
         ('Alíq. Efetiva Estadual - IBS', r['ef_uf']), ('Valor Apurado Estadual - IBS', r['v_uf'])],
        [('Valor Total Apurado - IBS', r['v_ibs']), ('Alíquota - CBS', r['aliq_cbs']),
         ('Alíquota Efetiva - CBS', r['ef_cbs']), ('Valor Total Apurado - CBS', r['v_cbs'])],
    ]
    for ri, campos in enumerate(rows, start=1):
        for col,(lab,val) in enumerate(campos):
            _label_value(c, col, top + row*ri + 0.20, lab, val)
    bottom = top + row*4
    _hline(c, bottom - 0.25)
    return bottom


def _draw_valores(c, top: float, r: dict) -> float:
    row = 19.32
    _hline(c, top - 0.25)
    _fill_rect_top(c, _X0, top, _X1, top + row)
    _txt_canvas(c, _TX[0], top + 0.23, 'VALOR TOTAL DA NFS-e', font=_F_ARIAL_B, size=7)
    _label_value(c, 1, top + 0.20, 'VALOR DA OPERAÇÃO / SERVIÇO', r['bruto'])
    _label_value(c, 2, top + 0.20, 'Desconto Incondicionado', r['desc_incond'] and _brl(r['desc_incond']) if r['desc_incond'] else '-')
    _label_value(c, 3, top + 0.20, 'Desconto Condicionado', r['desc_cond'])
    _label_value(c, 0, top + row + 0.20, 'Total das Retenções (ISSQN / Federais)', r['ret_total'])
    _label_value(c, 1, top + row + 0.20, 'VALOR LÍQUIDO DA NFS-e', r['liquido'])
    _label_value(c, 2, top + row + 0.20, 'Total do IBS/CBS', r['total_ibscbs'])
    _fill_rect_top(c, _X3, top + row, _X4, top + row*2)
    _label_value(c, 3, top + row + 0.20, 'VALOR LÍQUIDO DA NFS-e + IBS/CBS', r['total_nf'])
    bottom = top + row*2
    _hline(c, bottom - 0.25)
    return bottom


def _wrap_lines(text: str, font: str, size: float, max_width: float, max_lines: int) -> list[str]:
    words = (text or '').split()
    if not words:
        return []
    lines=[]; cur=''
    for w in words:
        cand = (cur + ' ' + w).strip()
        if _pdfmetrics.stringWidth(cand, font, size) <= max_width:
            cur=cand
        else:
            if cur: lines.append(cur)
            cur=w
            if len(lines) >= max_lines: break
    if cur and len(lines)<max_lines: lines.append(cur)
    if len(lines)==max_lines and words:
        # garante reticencias se houve corte
        joined=' '.join(lines)
        if len(joined)<len(text):
            while lines[-1] and _pdfmetrics.stringWidth(lines[-1]+'...',font,size)>max_width:
                lines[-1]=lines[-1][:-1]
            lines[-1]=lines[-1].rstrip()+'...'
    return lines


def _render_nacional_oficial(xml: str, d: Danfse, municipio_por_cep: dict[str,str] | None = None,
                             marca_dagua: str | None = None) -> bytes:
    r = _layout_raw_nacional(xml, d, municipio_por_cep)
    out = io.BytesIO()
    c = _canvas.Canvas(out, pagesize=A4, pageCompression=1)
    c.setTitle(f'NFS-e {d.numero}'.strip())

    # Borda externa: 1 pt, praticamente igual ao PDF oficial fornecido.
    c.setStrokeColor(_BLACK); c.setLineWidth(1.0)
    c.rect(5, 5, 585, 832, stroke=1, fill=0)

    # Cabeçalho sombreado - 5%.
    _fill_rect_top(c, _X0, 5.67, _X1, 39.68)
    _fill_rect_top(c, _X1, 5.67, _X3, 39.68)
    _fill_rect_top(c, _X3, 5.67, _X4, 39.68)
    _hline(c, 39.93)

    # Logo oficial.
    if _NFSE_LOGO_OFICIAL.exists():
        c.drawImage(str(_NFSE_LOGO_OFICIAL), 11.9055, _PAGE_H-33.2394,
                    width=115.6536, height=22.9165, preserveAspectRatio=True, mask='auto')
    else:
        _txt_canvas(c, 11.91, 10.5, 'NFS-e', font=_F_ARIAL_B, size=18)

    _txt_canvas(c, (153.07+442.20)/2, 12.62, 'DANFSe v2.0', font=_F_ARIAL_B, size=9, align='center')
    _txt_canvas(c, (153.07+442.20)/2, 22.97, 'Documento Auxiliar da NFS-e', font=_F_ARIAL_B, size=9, align='center')
    if r['tpAmb'] == '2':
        c.setFillColor(_RED); c.setFont(_F_ARIAL_B, 9)
        c.drawCentredString((153.07+442.20)/2, _baseline(32.5,9,_F_ARIAL_B), 'NFS-e SEM VALIDADE JURÍDICA')

    mun_head = _mun_uf(d.municipio_emissao, r['uf_emit'], sep=' - ')
    _txt_canvas(c, 445.61, 11.36, f'Município: {mun_head}', font=_F_MS, size=8, max_width=136)
    _txt_canvas(c, 445.61, 20.41, f'Ambiente Gerador: {r["ambGer"] or "-"}', font=_F_MS, size=6, max_width=136)
    _txt_canvas(c, 445.61, 27.20, f'Tipo de Ambiente: {r["tpAmb"] or "-"}', font=_F_MS, size=6, max_width=136)

    # Identificacao.
    _txt_canvas(c, 11.91, 44.67, 'CHAVE DE ACESSO DA NFS-e', font=_F_ARIAL_B, size=7)
    _txt_canvas(c, 11.91, 52.49, d.chave or '-', font=_F_MS, size=7, max_width=425)
    _draw_qr_oficial(c, d.chave)
    for i,linha in enumerate([
        'A autenticidade desta NFS-e pode ser verificada',
        'pela leitura deste código QR ou pela consulta da',
        'chave de acesso no portal nacional da NFS-e',
    ]):
        _txt_canvas(c, 445.61, 91.88 + i*6.79, linha, font=_F_MS, size=6, max_width=138, ellipsis=False)

    _label_value(c,0,64.89,'NÚMERO DA NFS-e',d.numero,label_upper=True)
    _label_value(c,1,64.89,'COMPETÊNCIA DA NFS-e',d.competencia,label_upper=True)
    _label_value(c,2,64.89,'DATA E HORA DA EMISSÃO DA NFS-e',d.data_emissao,label_upper=True)
    _label_value(c,0,85.12,'NÚMERO DA DPS',d.numero_dps,label_upper=True)
    _label_value(c,1,85.12,'SÉRIE DA DPS',d.serie,label_upper=True)
    _label_value(c,2,85.12,'DATA E HORA DA EMISSÃO DA DPS',d.data_emissao_dps,label_upper=True)
    _fill_rect_top(c,_X0,105.11,_X1,125.33)
    _label_value(c,0,105.34,'EMITENTE DA NFS-e',d.emitente_tipo or '-',label_upper=True)
    _label_value(c,1,105.34,'SITUAÇÃO DA NFS-e',d.situacao or '-',label_upper=True)
    _label_value(c,2,105.34,'FINALIDADE',d.finalidade or '-',label_upper=True)

    # Fluxo vertical a partir do prestador.
    top = 125.83
    simples_txt = d.regime_simples.replace('Optante — Microempresa ou EPP (ME/EPP)', 'Optante - Microempresa ou Empresa de Pequeno Porte (ME/EPP)').replace(' — ', ' - ')
    apur_txt = d.regime_apuracao
    if apur_txt == 'Tributos federais e municipal pelo Simples Nacional':
        apur_txt = 'Regime de apuração dos tributos federais e municipal pelo Simples Nacional'
    top = _draw_pessoa_bloco(c, top, 'PRESTADOR / FORNECEDOR', r['prest'], simples=simples_txt, apuracao=apur_txt)
    top = _draw_pessoa_bloco(c, top+0.25, 'TOMADOR / ADQUIRENTE', r['toma'], tomador=True)

    if any(r['dest'].get(k) for k in ('nome','doc','end','email')):
        # Layout completo permitido; 3 linhas de 19.07 pt.
        top = _draw_pessoa_bloco(c, top+0.25, 'DESTINATÁRIO DA OPERAÇÃO', r['dest'], tomador=True)
    else:
        top = _draw_reduzido(c, top+0.25, 'DESTINATÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e')

    if any(r['interm'].get(k) for k in ('nome','doc','end','email')):
        top = _draw_pessoa_bloco(c, top+0.25, 'INTERMEDIÁRIO DA OPERAÇÃO', r['interm'], tomador=True)
    else:
        top = _draw_reduzido(c, top+0.25, 'INTERMEDIÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e')

    top = _draw_servico(c, top+0.25, r)

    # Municipal: reproduz o modelo compacto quando os opcionais nao existem.
    opcionais_iss = any([r['iss_regesp'], r['iss_imun'], r['iss_susp'], r['iss_proc'], r['beneficio'], r['calculo_bm'], r['ded_red'], r['desc_incond']])
    if not opcionais_iss:
        top = _draw_municipal_compacto(c, top+0.25, r)
    else:
        # Versao expandida conforme campos opcionais da NT. Usa 4 linhas e reduz
        # automaticamente a area de Informacoes Complementares.
        row=19.07; start=top+0.25
        _hline(c,start-0.25); _fill_rect_top(c,_X0,start,_X1,start+row)
        _txt_canvas(c,_TX[0],start+0.23,'TRIBUTAÇÃO MUNICIPAL (ISSQN)',font=_F_ARIAL_B,size=7)
        _label_value(c,1,start+0.20,'Tipo de Tributação do ISSQN',r['iss_tipo'])
        _txt_canvas(c,_TX[2],start+0.20,'Município / Sigla UF / País de Incidência do ISSQN',font=_F_ARIAL_B,size=6,max_width=_X4-_X2-7)
        _txt_canvas(c,_TX[2],start+6.90,r['iss_local'],font=_F_MS,size=7,max_width=_X4-_X2-7)
        vals1=[('Regime Especial de Tributação do ISSQN',r['iss_regesp'] or '-'),('Tipo de Imunidade do ISSQN',r['iss_imun'] or '-'),('Suspensão da Exigibilidade do ISSQN',r['iss_susp'] or '-'),('Número Processo Suspensão',r['iss_proc'] or '-')]
        vals2=[('Benefício Municipal',r['beneficio'] or '-'),('Cálculo do BM',_brl(r['calculo_bm']) if r['calculo_bm'] else '-'),('Total Deduções/Reduções',_brl(r['ded_red']) if r['ded_red'] else '-'),('Desconto Incondicionado',_brl(r['desc_incond']) if r['desc_incond'] else '-')]
        vals3=[('BC ISSQN',r['bc_iss']),('Alíquota Aplicada',r['aliq_iss']),('Retenção do ISSQN',r['ret_iss']),('ISSQN Apurado',r['iss_apurado'])]
        for ri,vals in enumerate((vals1,vals2,vals3),1):
            for col,(lab,valx) in enumerate(vals): _label_value(c,col,start+row*ri+0.20,lab,valx)
        top=start+row*4; _hline(c,top-0.25)

    top = _draw_section_2rows(c, top+0.25, 'TRIBUTAÇÃO FEDERAL (EXCETO CBS)',
        [(1,'IRRF',r['irrf']), (2,'Contribuição Previdenciária - Retida',r['cp']), (3,'Contribuições Sociais - Retidas',r['contrib_soc'])],
        [(0,'PIS - Débito Apuração Própria',r['pis']), (1,'COFINS - Débito Apuração Própria',r['cofins']), (2,'Descrição Contrib. Sociais - Retidas',r['desc_contrib'])])

    top = _draw_ibscbs(c, top+0.25, r)
    top = _draw_valores(c, top+0.25, r)

    # Informacoes complementares ocupam todo o espaco restante ate o canhoto.
    _hline(c, top+0.25)
    _txt_canvas(c, _TX[0], top+0.75, 'INFORMAÇÕES COMPLEMENTARES', font=_F_ARIAL_B, size=7)
    info_top = top + 20.74
    canhoto_top = 795.80
    max_lines = max(1, int((canhoto_top - info_top - 8) / 8.0))
    info = ' | '.join(x for x in [r['info'], r['trib_line']] if x)
    for i,ln in enumerate(_wrap_lines(info, _F_MS, 7, _X4-_X0-7, max_lines)):
        _txt_canvas(c, _TX[0], info_top + i*8.0, ln, font=_F_MS, size=7, ellipsis=False)

    # Canhoto opcional, posicionado no rodape igual ao modelo fornecido.
    ct, cb = 795.80, 815.88
    c.setStrokeColor(_BLACK); c.setLineWidth(1)
    for xa,xb in [(_X0,_X1),(_X1,_X2),(_X2,_X4)]:
        c.rect(xa, _y(cb), xb-xa, cb-ct, stroke=1, fill=0)
    _txt_canvas(c, _TX[0], 796.50, 'DATA CIENTIFICAÇÃO:', font=_F_ARIAL_B, size=6)
    _txt_canvas(c, _TX[1], 796.50, 'IDENTIFICAÇÃO E ASSINATURA', font=_F_ARIAL_B, size=6)
    _txt_canvas(c, _TX[2], 796.50, 'N° NFS-e / CHAVE NFS-e', font=_F_ARIAL_B, size=6)
    _txt_canvas(c, _TX[2], 803.20, f'{d.numero} / {d.chave}', font=_F_MS, size=7, max_width=_X4-_X2-7)

    if marca_dagua:
        c.saveState(); c.setFillColor(colors.Color(.65,.65,.65)); c.setFont(_F_ARIAL,60)
        c.translate(_PAGE_W/2,_PAGE_H/2); c.rotate(45); c.drawCentredString(0,0,marca_dagua.upper()); c.restoreState()

    c.showPage(); c.save()
    return out.getvalue()


def gerar_danfse_pdf(xml: str, consulta_url: str | None = None,
                     municipio_por_cep: dict[str, str] | None = None,
                     marca_dagua: str | None = None) -> bytes:
    """Drop-in replacement.

    - NFS-e nacional: gera o DANFSe no modelo oficial NT 008 v1.02.
    - XML municipal legado de Joinville: preserva o gerador antigo.

    O parametro ``consulta_url`` e mantido por compatibilidade, mas no modelo
    nacional o QR Code e montado obrigatoriamente com a URL oficial definida na
    NT 008 + a chave de acesso da NFS-e.
    """
    d = ler_xml(xml, municipio_por_cep)
    if d.formato != 'nacional':
        return _gerar_danfse_pdf_legado(xml, consulta_url, municipio_por_cep, marca_dagua)
    return _render_nacional_oficial(xml, d, municipio_por_cep, marca_dagua)
