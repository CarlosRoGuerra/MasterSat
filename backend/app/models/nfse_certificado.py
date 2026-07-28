from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class NfseCertificado(Base, TimestampMixin):
    """
    Certificado digital A1 (ICP-Brasil) usado para assinar a DPS e autenticar
    o mTLS com a Sefin Nacional.

    O arquivo .pfx e a senha ficam **criptografados** (Fernet, mesma chave dos
    tokens Ailos). Substitui o par NFSE_CERT_PATH/NFSE_CERT_SENHA no .env, que
    exigia scp + edição de arquivo para trocar o certificado.

    Um certificado por vez fica ``ativo``; ao cadastrar um novo, o anterior é
    desativado (mantido para histórico/auditoria).
    """

    __tablename__ = 'nfse_certificados'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Extraídos do próprio .pfx na hora do upload (não são digitados)
    titular: Mapped[str] = mapped_column(String(300))
    cnpj: Mapped[str | None] = mapped_column(String(14), nullable=True, index=True)
    emissor: Mapped[str | None] = mapped_column(String(300), nullable=True)
    valido_de: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valido_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    nome_arquivo: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Segredos em repouso (Fernet)
    arquivo_cifrado: Mapped[bytes] = mapped_column(LargeBinary)
    senha_cifrada: Mapped[str] = mapped_column(Text)

    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    enviado_por: Mapped[int | None] = mapped_column(Integer, nullable=True)
