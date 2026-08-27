"""Validação de uploads de documentos.

Dois problemas que este módulo fecha:

1. XSS armazenado. O ``content_type`` de um upload vem do CLIENTE e era salvo
   verbatim; o ``GET /documents/{id}/view`` devolvia esse mesmo valor como
   ``media_type`` com ``Content-Disposition: inline``. Um usuário com perfil
   CLIENT subia um arquivo declarado ``text/html`` e ganhava um link que
   EXECUTAVA em api.mastersat.com.br. O ``X-Content-Type-Options: nosniff`` não
   protege: o tipo estava declarado, não sendo inferido.

2. Leitura sem teto. Os uploads administrativos faziam ``await file.read()``
   sem limite (o portal do cliente já limitava). O MaxBodySizeMiddleware só
   olha o Content-Length, então não cobre requisição sem esse header.
"""
from __future__ import annotations

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings

# Tipos aceitos no upload. Um documento é comprovante, contrato ou foto —
# nada aqui precisa ser executável ou ativo (HTML, SVG, XML).
ALLOWED_UPLOAD_TYPES: frozenset[str] = frozenset({
    'application/pdf',
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/heic',
    'image/heif',
})

# Subconjunto que pode ser servido com "inline" (renderiza no navegador).
# O que estiver fora vai como "attachment", que o navegador baixa em vez de
# interpretar — a rede de segurança para documentos legados salvos antes
# desta validação existir.
SAFE_INLINE_TYPES: frozenset[str] = frozenset({
    'application/pdf',
    'image/jpeg',
    'image/png',
    'image/webp',
})

_FALLBACK_TYPE = 'application/octet-stream'


def validate_content_type(file: UploadFile) -> str:
    """Content-type normalizado do upload, ou 415 se não for aceito."""
    bruto = (file.content_type or '').split(';')[0].strip().lower()
    if bruto not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                'Tipo de arquivo não permitido. Aceitos: PDF, JPEG, PNG, WEBP e HEIC.'
            ),
        )
    return bruto


async def read_limited(file: UploadFile) -> bytes:
    """Lê o upload com teto de memória (413 se estourar).

    Lê ``max_upload_bytes + 1`` para conseguir DETECTAR o estouro sem carregar
    o arquivo inteiro — por isso o +1.
    """
    conteudo = await file.read(settings.max_upload_bytes + 1)
    if len(conteudo) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail='Arquivo excede o tamanho máximo permitido.',
        )
    return conteudo


def safe_object_name(filename: str | None) -> str:
    """Nome de arquivo sem separador de caminho, para compor a chave do objeto."""
    nome = (filename or 'arquivo').replace('/', '_').replace('\\', '_').strip()
    # ".." isolado viraria travessia ao compor a chave no MinIO.
    nome = nome.replace('..', '_')
    return nome or 'arquivo'


def serving_content_type(content_type: str | None) -> tuple[str, str]:
    """(media_type, disposition) para servir um documento já armazenado."""
    tipo = (content_type or '').split(';')[0].strip().lower()
    if tipo in SAFE_INLINE_TYPES:
        return tipo, 'inline'
    return _FALLBACK_TYPE, 'attachment'
