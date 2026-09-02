from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin
from app.models.enums import DocumentReviewStatus


class Document(Base, TimestampMixin):
    __tablename__ = 'documents'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    object_key: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    reference_type: Mapped[str] = mapped_column(String(50), index=True)
    reference_id: Mapped[int] = mapped_column(Integer, index=True)
    category: Mapped[str] = mapped_column(String(50), default='geral', index=True)
    review_status: Mapped[DocumentReviewStatus] = mapped_column(Enum(DocumentReviewStatus), default=DocumentReviewStatus.SUBMITTED)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Quem enviou o documento (data de envio vem do created_at do TimestampMixin).
    uploaded_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Versionamento (usado hoje só pelo fluxo de geração de documento da OS —
    # client/vehicle nunca reemitem o "mesmo kind", então ficam sempre 1/NULL).
    # Reemitir um documento marca o anterior `active=False` e aponta pra ele
    # aqui, em vez de apagar — histórico completo, sem duplicar linha "viva".
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')
    supersedes_document_id: Mapped[int | None] = mapped_column(ForeignKey('documents.id'), nullable=True)
