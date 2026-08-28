"""
Validação leve do contrato assinado no momento do upload.

O contrato é gerado como PDF de texto e o cliente costuma imprimir, assinar à
mão e escanear/fotografar. Não dá para extrair de forma confiável os campos que
ele preenche à caneta — mas dá para conferir, quando o arquivo TEM camada de
texto, se ele realmente parece o contrato DESTE cliente. Quando não há texto
(escaneamento/foto ou imagem), avisamos que não foi possível conferir.

Retorno: {'level': 'ok'|'unreadable'|'mismatch', 'message': str}.
  ok         → tem texto e bate com o cliente (nome/CPF + marcadores do contrato)
  unreadable → não deu para ler o conteúdo (imagem/escaneamento) — só avisa
  mismatch   → tem texto, mas não parece o contrato deste cliente — pede atenção
"""
from __future__ import annotations

import io
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

try:  # pypdf é pure-Python; se faltar (imagem sem rebuild), a validação se cala.
    from pypdf import PdfReader
    _PYPDF_OK = True
except Exception:  # pragma: no cover - depende do ambiente
    _PYPDF_OK = False

# Marcadores do nosso contrato (ver contract_pdf.py).
_MARCADORES = ('TERMO DE ADESAO', 'MASTERSAT', 'CONTRATANTE')


def _norm(texto: str) -> str:
    """Maiúsculas, sem acentos — para comparar nome/marcadores sem tropeçar."""
    texto = unicodedata.normalize('NFKD', texto or '')
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return texto.upper()


def _extrair_texto_pdf(data: bytes) -> str:
    if not _PYPDF_OK:
        return ''
    try:
        reader = PdfReader(io.BytesIO(data))
        partes = [(page.extract_text() or '') for page in reader.pages[:6]]
        return '\n'.join(partes)
    except Exception:  # noqa: BLE001 — PDF ilegível vira 'unreadable' pro chamador, não é fatal
        logger.warning('Falha ao extrair texto do PDF do contrato', exc_info=True)
        return ''


def _nome_no_texto(nome_norm: str, texto_norm: str) -> bool:
    """Verdadeiro se pelo menos dois tokens relevantes do nome estão no texto."""
    tokens = [t for t in nome_norm.split() if len(t) >= 3]
    if not tokens:
        return False
    achados = sum(1 for t in tokens if t in texto_norm)
    return achados >= min(2, len(tokens))


def verificar_contrato_assinado(data: bytes, content_type: str, client) -> dict:
    # Sem a lib de leitura, não bloqueia nem gera aviso falso.
    if not _PYPDF_OK:
        return {'level': 'ok', 'message': ''}

    is_pdf = (content_type or '').lower() == 'application/pdf' or data[:5] == b'%PDF-'
    if not is_pdf:
        return {
            'level': 'unreadable',
            'message': 'O arquivo é uma imagem — não dá para conferir o conteúdo '
                       'automaticamente. Confira se está legível e é o contrato certo.',
        }

    texto = _extrair_texto_pdf(data)
    texto_norm = _norm(texto)
    # Pouco texto = provável escaneamento/foto dentro de um PDF.
    if len(re.sub(r'\s+', '', texto_norm)) < 60:
        return {
            'level': 'unreadable',
            'message': 'Não consegui ler o conteúdo (provável escaneamento ou foto). '
                       'Confira se o contrato está legível e é o arquivo certo.',
        }

    if not any(m in texto_norm for m in _MARCADORES):
        return {
            'level': 'mismatch',
            'message': 'O arquivo não parece ser um contrato MasterSat. '
                       'Confira se enviou o arquivo certo.',
        }

    nome_ok = _nome_no_texto(_norm(getattr(client, 'name', '') or ''), texto_norm)
    cpf_digits = re.sub(r'\D', '', getattr(client, 'cpf_cnpj', '') or '')
    cpf_ok = bool(cpf_digits) and cpf_digits in re.sub(r'\D', '', texto)
    if not (nome_ok or cpf_ok):
        # Tem os marcadores do nosso contrato, mas nenhum dado do cliente: é o
        # modelo AINDA EM BRANCO (o cliente não preencheu a parte dele).
        return {
            'level': 'blank',
            'message': 'O contrato parece estar EM BRANCO — não localizei os dados do cliente '
                       '(nome/CPF) preenchidos. Confira se o cliente preencheu e assinou antes de salvar.',
        }

    return {'level': 'ok', 'message': 'Arquivo confere com o contrato deste cliente.'}
