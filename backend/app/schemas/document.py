from datetime import datetime

from pydantic import BaseModel

from app.models.enums import DocumentReviewStatus


class DocumentOut(BaseModel):
    id: int
    file_name: str
    category: str
    content_type: str
    size_bytes: int
    review_status: DocumentReviewStatus
    review_notes: str | None = None
    url: str
    download_url: str
    # Metadados do anexo: quando foi enviado e por quem.
    created_at: datetime | None = None
    uploaded_by: str | None = None


class DocumentReviewUpdate(BaseModel):
    review_status: DocumentReviewStatus
    review_notes: str | None = None


class DocumentDeleteOut(BaseModel):
    message: str
