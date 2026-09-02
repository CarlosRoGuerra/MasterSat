from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.security import decode_file_access_token
from app.core.uploads import serving_content_type
from app.db.session import SessionLocal
from app.models.document import Document
from app.services.storage import get_object_stream

router = APIRouter()


def _object_iterator(obj, chunk_size: int = 32 * 1024):
    try:
        for chunk in obj.stream(chunk_size):
            yield chunk
    finally:
        try:
            obj.close()
            obj.release_conn()
        except Exception:
            pass


@router.get('/{document_id}/view')
def view_document(document_id: int, token: str = Query(...), download: bool = False):
    try:
        token_document_id = decode_file_access_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail='Link de documento inválido ou expirado') from exc

    if token_document_id != document_id:
        raise HTTPException(status_code=401, detail='Link de documento inválido ou expirado')

    db: Session = SessionLocal()
    try:
        # active=False cobre dois casos distintos: documento EXCLUÍDO (deve
        # continuar escondido) e documento SUBSTITUÍDO por uma versão mais
        # nova (deve continuar acessível — é a estratégia de reimpressão da
        # OS: histórico completo, não só a última versão). Distingue os dois
        # checando se algum outro Document aponta pra este via
        # supersedes_document_id — só quem foi versionado tem essa referência.
        document = db.scalar(
            select(Document).where(
                Document.id == document_id,
                or_(
                    Document.active.is_(True),
                    select(Document.id)
                    .where(Document.supersedes_document_id == document_id)
                    .exists(),
                ),
            )
        )
        if not document:
            raise HTTPException(status_code=404, detail='Documento não encontrado')

        obj = get_object_stream(document.object_key)
        filename = quote(document.file_name)
        # Rede de seguranca para documentos salvos ANTES da allowlist existir:
        # so PDF/imagem sao servidos "inline"; qualquer outro tipo vira
        # attachment com octet-stream, para o navegador BAIXAR em vez de
        # interpretar. Sem isso um .html declarado text/html executava nesta
        # origem (api.mastersat.com.br) — e o nosniff nao ajuda, porque o tipo
        # estava declarado, nao sendo inferido.
        media_type, disposition_padrao = serving_content_type(document.content_type)
        disposition = 'attachment' if download else disposition_padrao
        headers = {
            'Content-Disposition': f"{disposition}; filename*=UTF-8''{filename}",
            'Cache-Control': 'private, max-age=300',
            # Defesa em profundidade caso o header global do nginx nao chegue.
            'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': "default-src 'none'; sandbox",
        }
        return StreamingResponse(
            _object_iterator(obj),
            media_type=media_type,
            headers=headers,
        )
    finally:
        db.close()
