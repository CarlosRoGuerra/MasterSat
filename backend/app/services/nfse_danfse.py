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

_MARGEM = 4 * mm            # margem enxuta (NT 008 2.2.2 pede 1,5–2mm; 4mm é seguro p/ impressora)
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


def gerar_danfse_pdf(xml: str, consulta_url: str | None = None,
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
    hist: list = [_cabecalho(d), Spacer(1, 0.4 * mm)]
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
