from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_file_access_token
from app.db.session import SessionLocal
from app.models.document import Document
from app.services.storage import get_object_stream

router = APIRouter()


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
        document = db.scalar(select(Document).where(Document.id == document_id, Document.active.is_(True)))
        if not document:
            raise HTTPException(status_code=404, detail='Documento não encontrado')

        obj = get_object_stream(document.object_key)
        filename = quote(document.file_name)
        disposition = 'attachment' if download else 'inline'
        headers = {
            'Content-Disposition': f"{disposition}; filename*=UTF-8''{filename}",
            'Cache-Control': 'private, max-age=300',
        }
        return StreamingResponse(
            obj.stream(32 * 1024),
            media_type=document.content_type or 'application/octet-stream',
            headers=headers,
        )
    finally:
        db.close()
