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

    # Logo Ailos (célula esquerda)
    _draw_ailos(c, LM + _mm(2), y_top - _mm(1.5), h_mm=(h - _mm(3)) / _mm(1))

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
# RECIBO DO PAGADOR
# ─────────────────────────────────────────────────────────────────────────────
def _draw_recibo(c, d: DadosBoleto, y_top: float) -> float:
    H_HDR  = _mm(14)
    H_ROW1 = _mm(13)
    H_ROW2 = _mm(12)
    H_ROW3 = _mm(11)
    H_ROW4 = _mm(14)
    H_ROW5 = _mm(9)
    y = y_top

    _draw_header(c, y, False, d.linha_digitavel, H_HDR); y -= H_HDR

    cB=CW*.43; cA=CW*.18; cE=CW*.07; cQ=CW*.08; cN=CW-cB-cA-cE-cQ
    _cell(c, LM,             y, cB, H_ROW1, "Nome do Beneficiário",               d.cedente_nome)
    _cell(c, LM+cB,          y, cA, H_ROW1, "Agência / Código do Beneficiário",   f"{d.cedente_agencia} / {d.cedente_codigo}")
    _cell(c, LM+cB+cA,       y, cE, H_ROW1, "Espécie",                            d.especie)
    _cell(c, LM+cB+cA+cE,    y, cQ, H_ROW1, "Quantidade",                         "")
    _cell(c, LM+cB+cA+cE+cQ, y, cN, H_ROW1, "Nosso Número",                       d.nosso_numero_display)
    y -= H_ROW1

    cD=CW*.17; cC=CW*.10; cCPF=CW*.22; cV=CW*.14; cVL=CW-cD-cC-cCPF-cV
    _cell(c, LM,                  y, cD,   H_ROW2, "Número do Documento", f"BOLETO-{d.billing_id}")
    _cell(c, LM+cD,               y, cC,   H_ROW2, "Contrato",            d.cedente_convenio)
    _cell(c, LM+cD+cC,            y, cCPF, H_ROW2, "CPF/CNPJ",            _fmt_cnpj(d.sacado_cpf_cnpj))
    _cell(c, LM+cD+cC+cCPF,       y, cV,   H_ROW2, "Vencimento",          _fd(d.data_vencimento))
    _cell(c, LM+cD+cC+cCPF+cV,    y, cVL,  H_ROW2, "Valor Documento",     _fv(d.valor), align="right")
    y -= H_ROW2

    _cell(c, LM, y, CW, H_ROW3, "Pagador", d.sacado_nome); y -= H_ROW3
    _cell(c, LM, y, CW, H_ROW4, "Informações", " | ".join(d.instrucoes or [])); y -= H_ROW4

    _box(c, LM, y, CW, H_ROW5)
    c.setFont("Helvetica", 7); c.setFillColorRGB(0, 0, 0)
    # Rodapé: agência-dígito / código cedente-dígito (padrão Ailos)
    c.drawString(LM + _mm(1), y - H_ROW5 + _mm(2.5),
                 f"{d.cedente_agencia} / {d.cedente_codigo}")
    c.setFont("Helvetica", 6); c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawRightString(RM - _mm(1), y - H_ROW5 + _mm(2.5), "Autenticação Mecânica")
    y -= H_ROW5
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
def gerar_boleto_pdf(dados: DadosBoleto) -> bytes:
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Boleto MASTERSAT - {dados.billing_id}")

    y = _ft(6)

    # Linha digitável (topo)
    c.setFont("Helvetica", 5.5); c.setFillColorRGB(0.2, 0.4, 0.7)
    c.drawString(LM, y, "Linha digitável para ser utilizada em seu Internet Banking")
    y -= _mm(5)
    c.setFont("Helvetica-Bold", 9); c.setFillColorRGB(0, 0, 0)
    c.drawString(LM, y, dados.linha_digitavel)
    y -= _mm(5)

    y = _draw_recibo(c, dados, y)
    y -= _mm(4)

    _hline(c, LM, y, RM, lw=0.5, dash=(2, 4))
    y -= _mm(1.5)
    c.setFont("Helvetica", 5.5); c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawString(LM, y, "✂  Corte aqui")
    y -= _mm(4)

    y = _draw_ficha(c, dados, y)
    y -= _mm(6)

    # Pix (BolePix): prefere o EMV (QR vetorial nítido); se só houver a imagem
    # da Ailos, desenha a imagem. Se nenhum, não desenha (pix não habilitado).
    pix_ok = False
    if dados.pix_emv:
        pix_ok = _draw_pix_qr(c, dados.pix_emv, LM, y, size_mm=28)
    elif dados.pix_qr_base64:
        pix_ok = _draw_pix_image(c, dados.pix_qr_base64, LM, y, size_mm=28)
    if pix_ok:
        c.setFont("Helvetica-Bold", 8); c.setFillColorRGB(0, 0, 0)
        c.drawString(LM + _mm(32), y - _mm(7), "Pague com Pix")
        c.setFont("Helvetica", 6.5); c.setFillColorRGB(0.35, 0.35, 0.35)
        c.drawString(LM + _mm(32), y - _mm(12), "Escaneie o QR Code no app do seu banco.")
        y -= _mm(32)

    _draw_barcode(c, dados.codigo_barras, LM, y, height_mm=13)  # manual Ailos: 13mm

    c.save()
    buf.seek(0)
    return buf.read()
