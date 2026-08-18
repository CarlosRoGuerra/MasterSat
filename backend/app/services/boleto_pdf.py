"""
PDF de boleto bancário Ailos 085-0 — Canvas puro (ReportLab).

Logo: copiada para tempdir sem caracteres especiais no path.
Barcode: I2of5, barWidth=0.88, ratio=2.2, bearers=0.
"""
from __future__ import annotations

import io
import os
import shutil
import tempfile
from datetime import date

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdfcanvas

from app.services.boleto_ailos import DadosBoleto

# ── Paths ──────────────────────────────────────────────────────────────────
# Logo dentro de backend/app/static/ — sempre disponível dentro do container Docker
# (volume: ./backend:/app → /app/app/static/ailos_logo.png)
_HERE = os.path.abspath(os.path.dirname(__file__))

# Caminho primário: backend/app/static/ (funciona em Docker e localmente)
# _HERE = .../backend/app/services/ → sobe um nível para .../backend/app/static/
_AILOS_PRIMARY = os.path.normpath(os.path.join(_HERE, "..", "static", "ailos_logo.png"))

# Caminho secundário: raiz do projeto (funciona apenas localmente, não em Docker)
_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
_AILOS_FALLBACK = os.path.join(_ROOT, "api", "Logos", "Logo_SistemaAilos_Colorido.png")

# Usa o primeiro que existir
_AILOS_SRC = _AILOS_PRIMARY if os.path.exists(_AILOS_PRIMARY) else _AILOS_FALLBACK

# Copia para tempdir apenas se o path tiver caracteres especiais
_TMP_DIR = tempfile.mkdtemp(prefix="mastersat_boleto_")

def _tmp_copy(src: str, name: str) -> str | None:
    if not src or not os.path.exists(src):
        return None
    # Path limpo (sem acentos) — só copia se necessário
    try:
        src.encode("ascii")
        return src   # path já é ASCII, não precisa copiar
    except UnicodeEncodeError:
        pass
    dst = os.path.join(_TMP_DIR, name)
    try:
        shutil.copy2(src, dst)
        return dst
    except Exception:
        return None

_AILOS_PATH = _tmp_copy(_AILOS_SRC, "ailos.png")

# Logo da MASTERSAT (beneficiário) — aparece no RECIBO DO PAGADOR. Na FICHA DE
# COMPENSAÇÃO mantém-se o logo do banco (Ailos 085), exigência FEBRABAN.
_MASTERSAT_SRC = os.path.normpath(os.path.join(_HERE, "..", "static", "mastersat_logo.png"))
_MASTERSAT_PATH = _tmp_copy(_MASTERSAT_SRC, "mastersat.png")

# ── Dimensões A4 ─────────────────────────────────────────────────────────────
W, H = A4
def _mm(v: float) -> float: return v * 72 / 25.4
def _ft(mm: float) -> float: return H - _mm(mm)

LM = _mm(10)
RM = W - _mm(10)
CW = RM - LM

# ── Formatters ───────────────────────────────────────────────────────────────
def _fd(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "À VISTA"

def _fv(v) -> str:
    return f"{float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def _fmt_cnpj(s: str) -> str:
    c = "".join(ch for ch in (s or "") if ch.isdigit())
    if len(c) == 14: return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"
    if len(c) == 11: return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"
    return s

# ── Primitivas ────────────────────────────────────────────────────────────────
def _box(c, x, y_top, w, h, lw=0.3):
    c.setStrokeColorRGB(0, 0, 0); c.setLineWidth(lw)
    c.rect(x, y_top - h, w, h)

def _cell(c, x, y_top, w, h, label, value, align="left", vsize=7.5, box=True):
    if box: _box(c, x, y_top, w, h)
    c.setFillColorRGB(0.5, 0.5, 0.5); c.setFont("Helvetica", 5.5)
    c.drawString(x + _mm(1), y_top - _mm(2.8), label)
    c.setFillColorRGB(0, 0, 0); c.setFont("Helvetica-Bold", vsize)
    vbase = y_top - h + _mm(2)
    if align == "right": c.drawRightString(x + w - _mm(1), vbase, value)
    else: c.drawString(x + _mm(1), vbase, value)

def _hline(c, x1, y, x2, lw=0.3, dash=None):
    c.setLineWidth(lw); c.setStrokeColorRGB(0, 0, 0)
    if dash: c.setDash(*dash)
    c.line(x1, y, x2, y)
    if dash: c.setDash()

def _vline(c, x, y1, y2, lw=0.3):
    c.setLineWidth(lw); c.setStrokeColorRGB(0, 0, 0)
    c.line(x, y1, x, y2)

# ── Logo Ailos — pré-converte PNG→JPEG em memória (PIL) ────────────────────
# Converte uma única vez na importação do módulo para eliminar
# qualquer problema de path, RGBA, ImageReader com dims None, etc.
def _prepare_ailos_jpeg() -> bytes | None:
    """
    CORRIGIDO: usa _AILOS_PATH (tempdir, sem acentos) em vez de _AILOS_SRC.
    Fallback: lê o PNG direto via open() binário se PIL não estiver disponível.
    """
    # Usa o path temporário (sem caracteres especiais) — não o original
    src = _AILOS_PATH or _AILOS_SRC
    if not src or not os.path.exists(src):
        return None
    try:
        from PIL import Image as PILImg
        with open(src, "rb") as f:
            png_data = f.read()
        pil = PILImg.open(io.BytesIO(png_data))
        bg = PILImg.new("RGB", pil.size, (255, 255, 255))
        if pil.mode == "RGBA":
            bg.paste(pil, mask=pil.split()[3])
        else:
            bg.paste(pil.convert("RGB"))
        buf = io.BytesIO()
        bg.save(buf, format="JPEG", quality=92)
        buf.seek(0)
        return buf.read()
    except Exception:
        return None

_AILOS_JPEG = _prepare_ailos_jpeg()


def _prepare_mastersat_jpeg() -> bytes | None:
    """Converte o logo MasterSat (PNG/RGBA) para JPEG em memória (mesma técnica do Ailos)."""
    src = _MASTERSAT_PATH or _MASTERSAT_SRC
    if not src or not os.path.exists(src):
        return None
    try:
        from PIL import Image as PILImg
        with open(src, "rb") as f:
            png_data = f.read()
        pil = PILImg.open(io.BytesIO(png_data))
        bg = PILImg.new("RGB", pil.size, (255, 255, 255))
        if pil.mode == "RGBA":
            bg.paste(pil, mask=pil.split()[3])
        else:
            bg.paste(pil.convert("RGB"))
        buf = io.BytesIO()
        bg.save(buf, format="JPEG", quality=92)
        buf.seek(0)
        return buf.read()
    except Exception:
        return None

_MASTERSAT_JPEG = _prepare_mastersat_jpeg()


def _draw_mastersat(c, x, y_top, h_mm=11.0, max_w_mm=34.0):
    """Logo da MasterSat (beneficiário) no recibo. preserveAspectRatio evita distorção."""
    from reportlab.lib.utils import ImageReader
    h_pt = _mm(h_mm)
    box_w = _mm(max_w_mm)
    if _MASTERSAT_JPEG:
        try:
            c.drawImage(ImageReader(io.BytesIO(_MASTERSAT_JPEG)), x, y_top - h_pt,
                        width=box_w, height=h_pt, preserveAspectRatio=True, anchor='sw')
            return
        except Exception:
            pass
    if _MASTERSAT_PATH and os.path.exists(_MASTERSAT_PATH):
        try:
            c.drawImage(_MASTERSAT_PATH, x, y_top - h_pt, width=box_w, height=h_pt,
                        mask='auto', preserveAspectRatio=True, anchor='sw')
            return
        except Exception:
            pass
    c.setFont("Helvetica-Bold", 12); c.setFillColorRGB(0.85, 0.45, 0.0)
    c.drawString(x + _mm(1), y_top - h_pt / 2 - _mm(1), "MASTERSAT")
    c.setFillColorRGB(0, 0, 0)


def _draw_ailos(c, x, y_top, h_mm=14.0):
    """
    CORRIGIDO:
    - Tenta JPEG pré-convertido (PIL) → mais confiável
    - Fallback: PNG via path temporário com mask='auto' (suporta RGBA)
    - Fallback final: texto estilizado
    """
    from reportlab.lib.utils import ImageReader
    h_pt = _mm(h_mm)
    w_pt = h_pt * (448.0 / 220.0)

    # 1ª tentativa: JPEG em memória (PIL já resolveu RGBA e path)
    if _AILOS_JPEG:
        try:
            reader = ImageReader(io.BytesIO(_AILOS_JPEG))
            c.drawImage(reader, x, y_top - h_pt, width=w_pt, height=h_pt,
                        mask='auto')
            return w_pt
        except Exception:
            pass

    # 2ª tentativa: PNG via path temporário (sem acentos) com mask='auto'
    if _AILOS_PATH and os.path.exists(_AILOS_PATH):
        try:
            c.drawImage(_AILOS_PATH, x, y_top - h_pt, width=w_pt, height=h_pt,
                        mask='auto',
                        preserveAspectRatio=True)
            return w_pt
        except Exception:
            pass

    # Fallback texto estilizado (só chega aqui se arquivo não existir)
    c.setFont("Helvetica-Bold", 11); c.setFillColorRGB(0.0, 0.45, 0.7)
    c.drawString(x + _mm(1), y_top - h_pt / 2 - _mm(2), "AILOS")
    c.setFont("Helvetica", 6); c.setFillColorRGB(0.3, 0.3, 0.3)
    c.drawString(x + _mm(1), y_top - h_pt / 2 + _mm(2.5), "Sistema de Cooperativas")
    c.setFillColorRGB(0, 0, 0)
    return _mm(28)

# ── Cabeçalho (logo | 085-0 | título/linha) ──────────────────────────────
def _draw_header(c, y_top, is_ficha, linha_dig, h):
    _box(c, LM, y_top, CW, h, lw=0.5)
    sep1 = LM + _mm(37)
    sep2 = sep1 + _mm(20)
    _vline(c, sep1, y_top - h, y_top, lw=0.6)
    _vline(c, sep2, y_top - h, y_top, lw=0.6)

    # Logo na célula esquerda: MasterSat (beneficiário) no recibo; Ailos (banco)
    # na ficha de compensação — exigência FEBRABAN de exibir o logo do banco.
    if is_ficha:
        _draw_ailos(c, LM + _mm(2), y_top - _mm(1.5), h_mm=(h - _mm(3)) / _mm(1))
    else:
        _draw_mastersat(c, LM + _mm(2), y_top - _mm(1.5), h_mm=(h - _mm(3)) / _mm(1))

    # "085-0"
    mid_x = (sep1 + sep2) / 2
    c.setFont("Helvetica-Bold", 13); c.setFillColorRGB(0, 0, 0)
    c.drawCentredString(mid_x, y_top - h / 2 - _mm(2.5), "085-0")

    # Título ou linha digitável
    c.setFont("Helvetica-Bold", 8 if is_ficha else 9)
    c.setFillColorRGB(0, 0, 0)
    if is_ficha:
        c.drawRightString(RM - _mm(1), y_top - h / 2 - _mm(2.5), linha_dig)
    else:
        c.drawRightString(RM - _mm(1), y_top - h / 2 - _mm(2.5), "RECIBO DO PAGADOR")

# ─────────────────────────────────────────────────────────────────────────────
# RECIBO DE PAGAMENTO (layout aprovado pelo cliente — logo + dados da empresa,
# box do cliente, tabela de itens com cabeçalho azul, data e assinatura)
# ─────────────────────────────────────────────────────────────────────────────
# Razão social oficial (mesma do contrato / CNPJ 14.228.344/0001-67)
_EMPRESA_RAZAO = "MASTERSAT COMERCIO E SERVIÇOS DE RASTREAMENTO LTDA"
_EMPRESA_LINHAS = [
    "RUA MARITIMA, n 424, COMASA",
    "JOINVILLE - SC  CEP: 89228-450",
    "TEL: (47) 3085-3796   WHATS: (47) 98877-9273",
]

_DIAS_PT = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
_MESES_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
             "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def _fmt_cep(s: str) -> str:
    d = "".join(ch for ch in (s or "") if ch.isdigit())
    return f"{d[:5]}-{d[5:]}" if len(d) == 8 else (s or "")


def _data_recibo(dt: date) -> str:
    return f"JOINVILLE, {_DIAS_PT[dt.weekday()]}, {dt.day:02d} de {_MESES_PT[dt.month - 1]} de {dt.year}"


def _draw_recibo(c, d: DadosBoleto, y_top: float) -> float:
    """Recibo de pagamento: quita a cobrança, com data por extenso e assinatura.
    Só sai depois de o pagamento ser compensado e baixado."""
    return _draw_bloco_cobranca(
        c, d, y_top,
        titulo="RECIBO DE PAGAMENTO",
        rotulo_total="Valor Total da Fatura:",
        com_assinatura=True,
    )


def _draw_bloco_cobranca(c, d: DadosBoleto, y_top: float, *, titulo: str,
                         rotulo_total: str, com_assinatura: bool) -> float:
    y = y_top

    # ── Cabeçalho: logo + endereço (esq) · CNPJ (centro) · título (dir) ─────
    _draw_mastersat(c, LM, y, h_mm=11.0, max_w_mm=38.0)
    c.setFont("Helvetica-Bold", 10); c.setFillColorRGB(0, 0, 0)
    c.drawRightString(RM, y - _mm(5), titulo)
    ty = y - _mm(13.5)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(LM, ty, _EMPRESA_RAZAO); ty -= _mm(3.2)
    # CNPJ logo abaixo do nome da MasterSat
    c.drawString(LM, ty, f"CNPJ: {_fmt_cnpj(d.cedente_cnpj)}"); ty -= _mm(3.2)
    c.setFont("Helvetica", 7)
    for ln in _EMPRESA_LINHAS:
        c.drawString(LM, ty, ln); ty -= _mm(3.2)
    y = ty - _mm(1)

    # ── Box de dados do cliente (2 colunas) ─────────────────────────────────
    H_CLI = _mm(21)
    _box(c, LM, y, CW, H_CLI, lw=0.4)
    lx = LM + _mm(2); vx = LM + _mm(26)
    rx = LM + CW * 0.58; rvx = rx + _mm(36)
    rows_l = [
        ("NOME:", d.sacado_nome),
        ("ENDEREÇO:", d.sacado_endereco),
        ("CIDADE:", d.sacado_cidade),
        ("CEP:", _fmt_cep(d.sacado_cep)),
        ("CPF/CNPJ:", _fmt_cnpj(d.sacado_cpf_cnpj)),
    ]
    rows_r = [
        ("DATA DE EMISSÃO:", _fd(d.data_emissao)),
        ("DATA DE VENCIMENTO:", _fd(d.data_vencimento)),
        ("ESTADO:", d.sacado_uf),
        ("IE:", d.sacado_ie),
    ]
    line_h = _mm(3.9); ty = y - _mm(4.4)
    for lbl, val in rows_l:
        c.setFont("Helvetica-Bold", 7); c.drawString(lx, ty, lbl)
        c.setFont("Helvetica", 7); c.drawString(vx, ty, str(val or "")[:70])
        ty -= line_h
    ty = y - _mm(4.6)
    for lbl, val in rows_r:
        c.setFont("Helvetica-Bold", 7); c.drawString(rx, ty, lbl)
        c.setFont("Helvetica", 7); c.drawString(rvx, ty, str(val or ""))
        ty -= line_h
    y -= H_CLI + _mm(2)

    # ── Tabela de itens (cabeçalho azul) ────────────────────────────────────
    H_TH = _mm(5.5); H_TR = _mm(5.5)
    cQ = CW * 0.08; cVU = CW * 0.14; cVT = CW * 0.14
    cDESC = CW - cQ - cVU - cVT
    c.setFillColorRGB(0.24, 0.52, 0.78)
    c.rect(LM, y - H_TH, CW, H_TH, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1); c.setFont("Helvetica-Bold", 7.5)
    base_th = y - H_TH + _mm(2)
    c.drawCentredString(LM + cQ / 2, base_th, "Quant.")
    c.drawCentredString(LM + cQ + cDESC / 2, base_th, "Descrição dos Produtos/Serviços")
    c.drawCentredString(LM + cQ + cDESC + cVU / 2, base_th, "Vlr. Unitário")
    c.drawCentredString(LM + cQ + cDESC + cVU + cVT / 2, base_th, "Vlr. Total")
    y -= H_TH

    itens = d.itens or [(f"SERVIÇO DE RASTREAMENTO — COBRANÇA #{d.billing_id}", float(d.valor))]
    c.setFillColorRGB(0, 0, 0)
    for desc, val in itens[:3]:  # até 3 itens (mantém o boleto em 1 página)
        for bx, bw in ((LM, cQ), (LM + cQ, cDESC), (LM + cQ + cDESC, cVU), (LM + cQ + cDESC + cVU, cVT)):
            _box(c, bx, y, bw, H_TR, lw=0.3)
        base = y - H_TR + _mm(2)
        c.setFont("Helvetica", 7.5)
        c.drawCentredString(LM + cQ / 2, base, "1")
        c.drawString(LM + cQ + _mm(1.5), base, str(desc or "")[:80].upper())
        c.drawRightString(LM + cQ + cDESC + cVU - _mm(1.5), base, f"R$ {_fv(val)}")
        c.drawRightString(LM + cQ + cDESC + cVU + cVT - _mm(1.5), base, f"R$ {_fv(val)}")
        y -= H_TR

    total = sum(float(v) for _, v in itens[:3])
    c.setFont("Helvetica-Bold", 8)
    c.drawRightString(LM + cQ + cDESC + cVU - _mm(1.5), y - H_TR + _mm(2), rotulo_total)
    _box(c, LM + cQ + cDESC + cVU, y, cVT, H_TR, lw=0.3)
    c.drawRightString(LM + cQ + cDESC + cVU + cVT - _mm(1.5), y - H_TR + _mm(2), f"R$ {_fv(total)}")
    y -= H_TR + _mm(2.5)

    if not com_assinatura:
        return y

    # ── Data por extenso + assinatura automática ────────────────────────────
    c.setFont("Helvetica", 7.5)
    c.drawString(LM, y - _mm(3), _data_recibo(d.data_emissao or date.today()))
    y -= _mm(8)
    sig_w = _mm(80); sx = LM + (CW - sig_w) / 2
    # Assinatura automática: razão social em itálico sobre a linha
    c.setFont("Helvetica-Oblique", 8)
    c.drawCentredString(LM + CW / 2, y + _mm(1), _EMPRESA_RAZAO)
    _hline(c, sx, y, sx + sig_w, lw=0.5)
    c.setFont("Helvetica", 7)
    c.drawCentredString(LM + CW / 2, y - _mm(3.5), "Assinatura")
    y -= _mm(4.5)
    return y

# ─────────────────────────────────────────────────────────────────────────────
# FICHA DE COMPENSAÇÃO
# ─────────────────────────────────────────────────────────────────────────────
def _draw_ficha(c, d: DadosBoleto, y_top: float) -> float:
    H_HDR = _mm(14); H_R = _mm(12); H_FIN = _mm(8.5); H_PAG = _mm(14)
    y = y_top

    _draw_header(c, y, True, d.linha_digitavel, H_HDR); y -= H_HDR

    cLP = CW * 0.72
    _cell(c, LM,     y, cLP,     H_R, "Local de Pagamento",
          "Pagar preferencialmente nas cooperativas do Sistema AILOS.")
    _cell(c, LM+cLP, y, CW-cLP, H_R, "Vencimento",
          _fd(d.data_vencimento), align="right", vsize=8.5)
    y -= H_R

    cBN=CW*.44; cCP=CW*.24
    _cell(c, LM,          y, cBN,        H_R, "Beneficiário",                      d.cedente_nome)
    _cell(c, LM+cBN,      y, cCP,        H_R, "CPF/CNPJ",                          _fmt_cnpj(d.cedente_cnpj))
    _cell(c, LM+cBN+cCP,  y, CW-cBN-cCP, H_R, "Agência / Código do Beneficiário",
          f"{d.cedente_agencia} / {d.cedente_codigo}")
    y -= H_R

    cDD=CW*.12; cND=CW*.18; cES=CW*.07; cAC=CW*.05; cDP=CW*.12; cNN=CW-cDD-cND-cES-cAC-cDP
    _cell(c, LM,                      y, cDD, H_R, "Data do Documento",        _fd(d.data_emissao))
    _cell(c, LM+cDD,                  y, cND, H_R, "Nº do Documento",          f"BOLETO-{d.billing_id}")
    _cell(c, LM+cDD+cND,              y, cES, H_R, "Espécie Doc.",             "DS")
    _cell(c, LM+cDD+cND+cES,          y, cAC, H_R, "Aceite",                   d.aceite)
    _cell(c, LM+cDD+cND+cES+cAC,      y, cDP, H_R, "Data de Processamento",    _fd(d.data_emissao))
    _cell(c, LM+cDD+cND+cES+cAC+cDP,  y, cNN, H_R, "Nosso Número / Cód. do Documento",
          d.nosso_numero_display)
    y -= H_R

    cUB=CW*.10; cCA=CW*.10; cEM=CW*.12; cQM=CW*.10; cVM=CW*.12; cVD=CW-cUB-cCA-cEM-cQM-cVM
    _cell(c, LM,                      y, cUB, H_R, "Uso do Banco",          "")
    _cell(c, LM+cUB,                  y, cCA, H_R, "Carteira",              d.carteira.split("/")[0].strip())
    _cell(c, LM+cUB+cCA,              y, cEM, H_R, "Espécie Moeda",         "")
    _cell(c, LM+cUB+cCA+cEM,          y, cQM, H_R, "Quantidade Moeda",      "")
    _cell(c, LM+cUB+cCA+cEM+cQM,      y, cVM, H_R, "Valor Moeda",           "")
    _cell(c, LM+cUB+cCA+cEM+cQM+cVM,  y, cVD, H_R, "(=) Valor do Documento",
          _fv(d.valor), align="right", vsize=9)
    y -= H_R

    C_INSTR = CW * 0.62; C_FIN = CW - C_INSTR; H_BLOCK = H_FIN * 5
    _box(c, LM, y, C_INSTR, H_BLOCK)
    c.setFont("Helvetica", 5.5); c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawString(LM + _mm(1), y - _mm(3.5), "Instruções")
    c.setFont("Helvetica", 7); c.setFillColorRGB(0, 0, 0)
    txt_y = y - _mm(6.5)
    for line in (d.instrucoes or []):
        if txt_y > y - H_BLOCK + _mm(2):
            c.drawString(LM + _mm(1.5), txt_y, f"• {line}"); txt_y -= _mm(5)

    fx = LM + C_INSTR
    for i, lbl in enumerate([
        "(-) Desconto / Abatimento", "(-) Outras Deduções",
        "(+) Mora / Multa", "(+) Outros Acréscimos", "(=) Valor Cobrado",
    ]):
        _cell(c, fx, y - i * H_FIN, C_FIN, H_FIN, lbl, "", align="right")
    y -= H_BLOCK

    cPAG=CW*.55; cCOD=CW*.20; cFICHA=CW-cPAG-cCOD
    _box(c, LM, y, cPAG, H_PAG)
    c.setFont("Helvetica", 5.5); c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawString(LM + _mm(1), y - _mm(3.5), "Pagador")
    c.setFont("Helvetica", 7); c.setFillColorRGB(0, 0, 0)
    c.drawString(LM + _mm(1), y - _mm(7),  f"{d.sacado_nome}  -  {_fmt_cnpj(d.sacado_cpf_cnpj)}")
    c.setFont("Helvetica", 6.5)
    c.drawString(LM + _mm(1), y - _mm(11), d.sacado_endereco)
    _cell(c, LM+cPAG, y, cCOD, H_PAG, "Código de Baixa", "")
    _box(c, LM+cPAG+cCOD, y, cFICHA, H_PAG)
    c.setFont("Helvetica-Bold", 7); c.setFillColorRGB(0, 0, 0)
    c.drawRightString(RM - _mm(1), y - H_PAG + _mm(3), "FICHA DE COMPENSAÇÃO")
    y -= H_PAG

    H_BENEF = _mm(9); _box(c, LM, y, CW, H_BENEF)
    c.setFont("Helvetica", 7); c.setFillColorRGB(0, 0, 0)
    c.drawString(LM + _mm(1), y - H_BENEF + _mm(2.5), d.cedente_nome)
    c.setFont("Helvetica", 6); c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawRightString(RM - _mm(1), y - H_BENEF + _mm(2.5), "Autenticação Mecânica")
    y -= H_BENEF
    return y

# ─────────────────────────────────────────────────────────────────────────────
# BARCODE I2of5
# Parâmetros calibrados conforme referência visual Ailos:
#   barWidth=0.88pt (~0.31mm), ratio=2.2, bearers=0, quiet=0
# ─────────────────────────────────────────────────────────────────────────────
def _draw_barcode(c, codigo: str, x: float, y_top: float, height_mm: float = 15.0):
    from reportlab.graphics.barcode.common import I2of5
    h = _mm(height_mm)

    # Garante que o barcode não caia fora da página
    if y_top - h < _mm(8):
        c.showPage(); y_top = _ft(10)

    # Cor explicitamente preta antes de desenhar
    c.setFillColorRGB(0, 0, 0); c.setStrokeColorRGB(0, 0, 0)

    try:
        bc = I2of5(
            codigo,
            barWidth=0.88,       # ~0.31mm — tamanho correto conforme referência
            ratio=2.2,           # padrão FEBRABAN
            checksum=0,
            humanReadable=False,
            bearers=0,           # SEM barras horizontais cinza
            quiet=0,             # SEM quiet zone automática
        )
        bc.barHeight = h
        bc.drawOn(c, x, y_top - h)
        # Restaura cor preta após o barcode
        c.setFillColorRGB(0, 0, 0); c.setStrokeColorRGB(0, 0, 0)
        return True
    except Exception as e:
        c.setFont("Courier", 7); c.setFillColorRGB(0, 0, 0)
        c.drawString(x, y_top - h + _mm(3), codigo)
        return False

# ─────────────────────────────────────────────────────────────────────────────
# QR CODE PIX (BolePix) — usa o gerador nativo do reportlab (sem dependência nova)
# ─────────────────────────────────────────────────────────────────────────────
def _draw_pix_qr(c, emv: str, x: float, y_top: float, size_mm: float = 26.0) -> bool:
    try:
        from reportlab.graphics.barcode.qr import QrCodeWidget
        from reportlab.graphics.shapes import Drawing
        from reportlab.graphics import renderPDF

        size = _mm(size_mm)
        # barLevel='M' (recomendado p/ boleto). Vetorial → nítido em qualquer
        # tamanho. O conteúdo é o EMV (BR Code), não a imagem base64.
        qr = QrCodeWidget(emv, barLevel='M')
        b = qr.getBounds()
        w, h = b[2] - b[0], b[3] - b[1]
        d = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
        d.add(qr)
        renderPDF.draw(d, c, x, y_top - size)
        return True
    except Exception:
        return False


def _draw_pix_image(c, b64: str, x: float, y_top: float, size_mm: float = 28.0) -> bool:
    """Desenha a imagem do QR (PNG/JPEG base64) que a própria Ailos devolveu —
    usado só quando não há o EMV para gerar um QR vetorial."""
    try:
        import base64 as _b64
        from reportlab.lib.utils import ImageReader
        raw = b64.split(',', 1)[-1]  # remove prefixo data URI, se houver
        img = ImageReader(io.BytesIO(_b64.b64decode(raw)))
        size = _mm(size_mm)
        c.drawImage(img, x, y_top - size, width=size, height=size, mask='auto')
        return True
    except Exception:
        return False

# ─────────────────────────────────────────────────────────────────────────────
# FUNÇÃO PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────
def gerar_recibo_pdf(dados: DadosBoleto) -> bytes:
    """
    Recibo de pagamento AVULSO — o mesmo layout aprovado que sai no topo do
    boleto (logo, CNPJ, dados da empresa, box do cliente, tabela de itens,
    data por extenso e assinatura), só que sozinho na página.

    Usado pelo botão "Recibo" das cobranças pagas. Antes existia um segundo
    desenho, cru (só texto solto), que era o que de fato saía para o cliente.
    """
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Recibo MASTERSAT - {dados.billing_id}")
    _draw_recibo(c, dados, _ft(12))
    c.showPage()
    c.save()
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# BOLETO NO PADRÃO ITAÚ/HINOVA (modelo aprovado): canhoto (recibo do pagador)
# à esquerda + ficha de compensação à direita, com o QR do Pix nas instruções.
# Logo do banco é a Ailos (085-0). Reaproveita _cell/_box/_vline/_draw_*.
# ─────────────────────────────────────────────────────────────────────────────
_BOLETO_ALTURA = _mm(130)   # espaço por boleto (cabeçalho + corpo + barras)


def _draw_pix_no_quadro(c, d: DadosBoleto, cx: float, cy: float, lado_mm: float) -> bool:
    """QR do Pix (EMV vetorial ou imagem da Ailos). None se o Pix não estiver
    habilitado na conta — aí o quadro fica vazio, sem quebrar o layout."""
    if d.pix_emv:
        return _draw_pix_qr(c, d.pix_emv, cx, cy, size_mm=lado_mm)
    if d.pix_qr_base64:
        return _draw_pix_image(c, d.pix_qr_base64, cx, cy, size_mm=lado_mm)
    return False


def _draw_boleto_itau_style(c, d: DadosBoleto, y_top: float, parcela: tuple[int, int] | None = None) -> float:
    servico = str(d.itens[0][0]) if d.itens else "SERVIÇO DE RASTREAMENTO"
    ag_cod = f"{d.cedente_agencia} / {d.cedente_codigo}"
    y = y_top

    # ── Cabeçalho (full width): MasterSat | Ailos | 085-0 | linha digitável ──
    H_HDR = _mm(12)
    _box(c, LM, y, CW, H_HDR, lw=0.7)
    _draw_mastersat(c, LM + _mm(2), y - _mm(1.2), h_mm=9.0, max_w_mm=34.0)
    s1 = LM + _mm(40)
    _vline(c, s1, y - H_HDR, y, lw=0.7)
    _draw_ailos(c, s1 + _mm(2), y - _mm(1.0), h_mm=9.5)
    s2 = s1 + _mm(36)
    _vline(c, s2, y - H_HDR, y, lw=0.7)
    c.setFillColorRGB(0, 0, 0); c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(s2 + _mm(9), y - H_HDR / 2 - _mm(2.3), "085-0")
    s3 = s2 + _mm(18)
    _vline(c, s3, y - H_HDR, y, lw=0.7)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(s3 + _mm(2), y - H_HDR / 2 - _mm(2.3), d.linha_digitavel)
    y -= H_HDR

    body_top = y
    cCAN = _mm(42); gap = _mm(3)
    fx = LM + cCAN + gap
    cF = RM - fx

    # ── Canhoto (recibo do pagador) — coluna esquerda ──
    H = _mm(6.9)
    yc = y
    canhoto = [
        ("Vencimento", _fd(d.data_vencimento), "left"),
        ("Agência/Código do Beneficiário", ag_cod, "left"),
        ("Nosso número", d.nosso_numero_display, "left"),
        ("Nº documento", str(d.billing_id), "left"),
        ("Espécie", "REAL", "left"),
        ("Quantidade", "", "left"),
        ("(=) Valor Documento", _fv(d.valor), "right"),
        ("(-) Descontos/Abatimentos", "", "left"),
        ("(-) Outras deduções", "", "left"),
        ("(+) Mora / Multa", "", "left"),
        ("(+) Outros acréscimos", "", "left"),
        ("(=) Valor cobrado", "", "left"),
    ]
    for label, value, al in canhoto:
        _cell(c, LM, yc, cCAN, H, label, value, align=al, vsize=7)
        yc -= H
    _box(c, LM, yc, cCAN, _mm(10))
    c.setFillColorRGB(0.5, 0.5, 0.5); c.setFont("Helvetica", 5.5)
    c.drawString(LM + _mm(1), yc - _mm(2.8), "Pagador")
    c.setFillColorRGB(0, 0, 0); c.setFont("Helvetica", 6.5)
    c.drawString(LM + _mm(1), yc - _mm(6), (d.sacado_nome or "")[:34])
    yc -= _mm(10)
    _cell(c, LM, yc, cCAN, _mm(8), "", servico[:38], vsize=6.5)
    yc -= _mm(8)

    # ── Ficha de compensação — coluna direita ──
    Hr = _mm(9); yf = y
    _cell(c, fx, yf, cF * 0.80, Hr, "Local de pagamento",
          "Até o vencimento, pague nas cooperativas do Sistema AILOS.")
    _cell(c, fx + cF * 0.80, yf, cF * 0.20, Hr, "Vencimento",
          _fd(d.data_vencimento), align="right", vsize=8.5)
    yf -= Hr

    _cell(c, fx, yf, cF * 0.62, Hr, "Beneficiário", d.cedente_nome)
    _cell(c, fx + cF * 0.62, yf, cF * 0.18, Hr, "CPF/CNPJ", _fmt_cnpj(d.cedente_cnpj))
    _cell(c, fx + cF * 0.80, yf, cF * 0.20, Hr, "Agência/Código", ag_cod, align="right")
    yf -= Hr

    w = cF
    cDD = w*.13; cND = w*.17; cES = w*.08; cAC = w*.06; cDP = w*.14; cNN = w - cDD-cND-cES-cAC-cDP
    _cell(c, fx,                       yf, cDD, Hr, "Data do documento", _fd(d.data_emissao))
    _cell(c, fx+cDD,                   yf, cND, Hr, "Nº documento", str(d.billing_id))
    _cell(c, fx+cDD+cND,               yf, cES, Hr, "Espécie", "DS")
    _cell(c, fx+cDD+cND+cES,           yf, cAC, Hr, "Aceite", d.aceite)
    _cell(c, fx+cDD+cND+cES+cAC,       yf, cDP, Hr, "Data process.", _fd(d.data_emissao))
    _cell(c, fx+cDD+cND+cES+cAC+cDP,   yf, cNN, Hr, "Nosso número", d.nosso_numero_display, align="right")
    yf -= Hr

    cUB = w*.13; cCA = w*.10; cEM = w*.10; cQM = w*.12; cVL = w*.13; cVD = w - cUB-cCA-cEM-cQM-cVL
    _cell(c, fx,                     yf, cUB, Hr, "Uso do banco", "")
    _cell(c, fx+cUB,                 yf, cCA, Hr, "Carteira", d.carteira.split("/")[0].strip())
    _cell(c, fx+cUB+cCA,             yf, cEM, Hr, "Espécie", "REAL")
    _cell(c, fx+cUB+cCA+cEM,         yf, cQM, Hr, "Quantidade", "")
    _cell(c, fx+cUB+cCA+cEM+cQM,     yf, cVL, Hr, "Valor", "")
    _cell(c, fx+cUB+cCA+cEM+cQM+cVL, yf, cVD, Hr, "(=) Valor do Documento", _fv(d.valor),
          align="right", vsize=9)
    yf -= Hr

    # Instruções (com QR Pix à direita) + coluna financeira
    cINS = cF * 0.72; cFIN = cF - cINS; H_INS = _mm(38)
    _box(c, fx, yf, cINS, H_INS)
    c.setFillColorRGB(0.5, 0.5, 0.5); c.setFont("Helvetica", 5.5)
    c.drawString(fx + _mm(1), yf - _mm(2.8), "Instruções (texto de responsabilidade do beneficiário)")
    c.setFillColorRGB(0, 0, 0); c.setFont("Helvetica", 7)
    ty = yf - _mm(6.5)
    for line in (d.instrucoes or [])[:4]:
        c.drawString(fx + _mm(1.5), ty, f"• {line}"); ty -= _mm(4.2)
    qr = 30
    qx = fx + cINS - _mm(qr) - _mm(2); qy = yf - _mm(4)
    if _draw_pix_no_quadro(c, d, qx, qy, qr):
        c.setFont("Helvetica-Bold", 6.5); c.setFillColorRGB(0, 0, 0)
        c.drawCentredString(qx + _mm(qr) / 2, qy - _mm(qr) - _mm(2), "Pague com Pix")
    hf = H_INS / 5
    for i, lbl in enumerate([
        "(-) Descontos / Abatimentos", "(-) Outras deduções",
        "(+) Mora / Multa", "(+) Outros acréscimos", "(=) Valor cobrado",
    ]):
        _cell(c, fx + cINS, yf - i * hf, cFIN, hf, lbl, "", align="right")
    yf -= H_INS

    _box(c, fx, yf, cF, _mm(11))
    c.setFillColorRGB(0.5, 0.5, 0.5); c.setFont("Helvetica", 5.5)
    c.drawString(fx + _mm(1), yf - _mm(2.8), "Pagador")
    c.setFillColorRGB(0, 0, 0); c.setFont("Helvetica", 7)
    c.drawString(fx + _mm(1), yf - _mm(6), f"{d.sacado_nome} - CPF/CNPJ: {_fmt_cnpj(d.sacado_cpf_cnpj)}")
    c.setFont("Helvetica", 6.5)
    c.drawString(fx + _mm(1), yf - _mm(9.5), (d.sacado_endereco or "")[:92])
    yf -= _mm(11)

    _box(c, fx, yf, cF, _mm(7))
    c.setFont("Helvetica-Bold", 7); c.setFillColorRGB(0, 0, 0)
    c.drawString(fx + _mm(1), yf - _mm(4.5),
                 f"Beneficiário: {d.cedente_nome} - {_fmt_cnpj(d.cedente_cnpj)}")
    c.drawRightString(RM - _mm(1), yf - _mm(4.5), "Autenticação mecânica - Ficha de Compensação")
    yf -= _mm(7)

    # Linha vertical tracejada entre o canhoto e a ficha
    c.setDash(2, 3)
    _vline(c, LM + cCAN + gap / 2, min(yc, yf), body_top, lw=0.5)
    c.setDash()

    # Nº da parcela (carnê) no topo direito do cabeçalho
    if parcela is not None:
        c.setFont("Helvetica-Bold", 8); c.setFillColorRGB(0, 0, 0)
        c.drawRightString(RM - _mm(2), body_top + _mm(2), f"Parcela {parcela[0]}/{parcela[1]}")

    # Código de barras sob a ficha
    ybar = min(yc, yf) - _mm(3)
    _draw_barcode(c, d.codigo_barras, fx, ybar, height_mm=13)
    ybar -= _mm(15)
    return ybar


# ─────────────────────────────────────────────────────────────────────────────
# RECIBO DO PAGADOR / FATURA (topo do boleto, no modelo Hinova aprovado):
# cabeçalho da empresa, QR Pix + instruções, "Referente a", lista de itens por
# placa em colunas e a faixa-recibo. Abaixo, a ficha de compensação full-width.
# ─────────────────────────────────────────────────────────────────────────────
_ENDERECO_RECIBO = ["RUA MARITIMA, 424 - COMASA", "89228-450 - JOINVILLE - SC"]

_PIX_TEXTO = [
    "Efetue o pagamento deste boleto através do Pix pelo aplicativo do seu banco.",
    "Selecione a opção de pagamento através do QrCode e aponte a câmera do seu",
    "celular para a imagem ao lado.",
]


def _referente(d: DadosBoleto) -> str:
    """Linha "Referente a:" — vem das instruções, senão do primeiro item."""
    for ln in (d.instrucoes or []):
        if ln.strip().lower().startswith("referente a"):
            return ln.strip().rstrip(".")
    base = str(d.itens[0][0]) if d.itens else "Serviço de rastreamento"
    return f"Referente a: {base}"


def _draw_itens_recibo(c, itens: list[tuple[str, float]], y_top: float) -> float:
    """Itens cobrados, em até 3 colunas (placa/descrição - valor), como no modelo."""
    n_col = 3
    col_w = CW / n_col
    row_h = _mm(3.8)
    c.setFillColorRGB(0, 0, 0); c.setFont("Helvetica", 6.3)
    y = y_top
    linhas = (len(itens) + n_col - 1) // n_col
    for r in range(linhas):
        yy = y - _mm(2.9)
        for col in range(n_col):
            idx = r * n_col + col
            if idx >= len(itens):
                break
            desc, val = itens[idx]
            texto = f"{str(desc)[:36]} - R$ {_fv(val)}"
            c.drawString(LM + col * col_w + _mm(0.5), yy, texto[:54])
        y -= row_h
    return y


def _draw_recibo_pagador(c, d: DadosBoleto, y_top: float) -> float:
    y = y_top

    # ── Cabeçalho: logo + empresa (esq) · "Recibo do pagador" (dir) ──────────
    _draw_mastersat(c, LM, y, h_mm=11.0, max_w_mm=40.0)
    ex = LM + _mm(44)
    c.setFillColorRGB(0, 0, 0); c.setFont("Helvetica-Bold", 8)
    c.drawString(ex, y - _mm(3.2), "MASTERSAT")
    c.setFont("Helvetica", 7)
    ty = y - _mm(6.6)
    for ln in _ENDERECO_RECIBO:
        c.drawString(ex, ty, ln); ty -= _mm(3.1)
    c.drawString(ex, ty, f"CNPJ: {_fmt_cnpj(d.cedente_cnpj)}")
    # Nunca "Recibo" na emissão: recibo/quitação só sai depois do boleto pago
    # (regra da reunião). Aqui é a fatura/demonstrativo do que está sendo cobrado.
    c.setFont("Helvetica-Oblique", 7); c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawRightString(RM, y - _mm(2.5), "Demonstrativo de Serviços")
    _hline(c, LM, y - _mm(15.5), RM, lw=0.4)
    y -= _mm(18)

    # ── Pix (só quando habilitado na conta): QR + instruções ─────────────────
    if d.pix_emv or d.pix_qr_base64:
        lado = 24
        _draw_pix_no_quadro(c, d, LM, y, lado)
        tx = LM + _mm(lado) + _mm(4)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 8); c.drawString(tx, y - _mm(4), _PIX_TEXTO[0])
        c.setFont("Helvetica", 8)
        c.drawString(tx, y - _mm(8.5), _PIX_TEXTO[1])
        c.drawString(tx, y - _mm(12.5), _PIX_TEXTO[2])
        y -= _mm(lado) + _mm(3)

    # ── Referente a ──────────────────────────────────────────────────────────
    c.setFillColorRGB(0, 0, 0); c.setFont("Helvetica", 8)
    c.drawString(LM, y - _mm(3.5), _referente(d)[:120])
    y -= _mm(7)

    # ── Itens cobrados ───────────────────────────────────────────────────────
    itens = d.itens or [("SERVIÇO DE RASTREAMENTO", float(d.valor))]
    y = _draw_itens_recibo(c, itens, y) - _mm(2)

    # ── Faixa-recibo: nº doc · nosso número · datas · agência · carteira · valor
    H = _mm(9)
    cols = [
        ("Nº documento", str(d.billing_id), 0.12, "left"),
        ("Nosso número", d.nosso_numero_display, 0.20, "left"),
        ("Data emissão", _fd(d.data_emissao), 0.14, "left"),
        ("Vencimento", _fd(d.data_vencimento), 0.14, "left"),
        ("Agência/Código", f"{d.cedente_agencia} / {d.cedente_codigo}", 0.18, "left"),
        ("Carteira", d.carteira.split("/")[0].strip(), 0.10, "left"),
        ("Valor", _fv(d.valor), 0.12, "right"),
    ]
    x = LM
    for label, value, frac, al in cols:
        cw = CW * frac
        _cell(c, x, y, cw, H, label, value, align=al, vsize=8.5 if label == "Valor" else 7)
        x += cw
    y -= H

    # ── Pagador · Beneficiário Final · Nº nota fiscal ────────────────────────
    cPag = CW * 0.45; cBenef = CW * 0.40; cNota = CW - cPag - cBenef
    _cell(c, LM, y, cPag, H, "Pagador",
          f"{d.sacado_nome} - {_fmt_cnpj(d.sacado_cpf_cnpj)}"[:58], vsize=7)
    _cell(c, LM + cPag, y, cBenef, H, "Beneficiário Final",
          f"{d.cedente_nome} - {_fmt_cnpj(d.cedente_cnpj)}"[:56], vsize=7)
    _cell(c, LM + cPag + cBenef, y, cNota, H, "Nº nota fiscal", "")
    y -= H
    return y


def gerar_boleto_pdf(dados: DadosBoleto) -> bytes:
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Boleto MASTERSAT - {dados.billing_id}")

    # 1. Recibo do pagador / fatura no topo (QR Pix, itens, faixa-recibo).
    y = _draw_recibo_pagador(c, dados, _ft(10))

    # 2. Corte entre o recibo e a ficha de compensação.
    y -= _mm(2)
    _hline(c, LM, y, RM, lw=0.5, dash=(2, 4))
    c.setFont("Helvetica", 5.5); c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawString(RM - _mm(40), y - _mm(3), "Corte na linha pontilhada")

    # 3. Ficha de compensação full-width (Ailos 085-0) + código de barras.
    y = _draw_ficha(c, dados, y - _mm(6))
    _draw_barcode(c, dados.codigo_barras, LM, y - _mm(3), height_mm=13)

    c.save()
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
# CARNÊ — cada parcela é um boleto no mesmo padrão do avulso (canhoto do recibo
# do pagador + ficha de compensação + QR Pix), 2 por página A4, com o número da
# parcela no cabeçalho.
# ─────────────────────────────────────────────────────────────────────────────
_CARNE_ALTURA = _mm(132)   # espaço reservado por parcela (boleto + corte)


def _draw_carne_parcela(c, d: DadosBoleto, y_top: float, num: int, total: int) -> float:
    y = _draw_boleto_itau_style(c, d, y_top, parcela=(num, total))
    _hline(c, LM, y, RM, lw=0.5, dash=(2, 4))
    c.setFont("Helvetica", 5.5); c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawString(LM, y - _mm(3), "✂  Corte na linha pontilhada")
    return y - _mm(5)


def gerar_carne_pdf(parcelas: list[DadosBoleto]) -> bytes:
    """
    PDF do carnê: cada parcela é uma ficha de compensação completa e pagável,
    duas por página A4.

    ``parcelas`` já vêm com os dados oficiais da Ailos aplicados (linha
    digitável e código de barras reais) — sem isso a parcela não é pagável.
    """
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    ref = parcelas[0].billing_id if parcelas else ""
    c.setTitle(f"Carnê MASTERSAT - {ref}")

    total = len(parcelas)
    y = _ft(12)
    for i, d in enumerate(parcelas, start=1):
        if y - _CARNE_ALTURA < _mm(12):
            c.showPage()
            y = _ft(12)
        y = _draw_carne_parcela(c, d, y, i, total)
        y -= _mm(3)

    c.save()
    buf.seek(0)
    return buf.read()
