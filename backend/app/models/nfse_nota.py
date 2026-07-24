from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class NfseNota(Base, TimestampMixin):
    """
    NFS-e de Joinville (Pública/Nota Nacional) vinculada 1:1 a um Billing.

    Guarda o RPS enviado (numero/serie/lote/protocolo) e os dados oficiais
    retornados pela prefeitura (numero da NFS-e, codigo de verificacao, chave
    de acesso e link de visualizacao).

    status:
      pending    — registro criado, ainda não enviado
      processing — lote recebido (tem protocolo), aguardando processamento
      emitida    — NFS-e gerada com sucesso
      erro       — lote rejeitado (ver erro_mensagem)
    """

    __tablename__ = 'nfse_notas'

    id: Mapped[int] = mapped_column(primary_key=True)
    billing_id: Mapped[int] = mapped_column(ForeignKey('billings.id'), unique=True, index=True)
    # Lote de emissão em massa (NULL = emissão avulsa de uma cobrança só)
    lote_id: Mapped[int | None] = mapped_column(ForeignKey('nfse_lotes.id'), nullable=True, index=True)

    # RPS enviado
    numero_rps: Mapped[str | None] = mapped_column(String(15), nullable=True)
    serie_rps: Mapped[str | None] = mapped_column(String(5), nullable=True)
    numero_lote: Mapped[str | None] = mapped_column(String(20), nullable=True)
    protocolo: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    situacao: Mapped[str | None] = mapped_column(String(2), nullable=True)  # 1..7

    # NFS-e gerada (dados oficiais)
    numero_nfse: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    serie_nfse: Mapped[str | None] = mapped_column(String(5), nullable=True)
    codigo_verificacao: Mapped[str | None] = mapped_column(String(20), nullable=True)
    chave_acesso: Mapped[str | None] = mapped_column(String(60), nullable=True)
    link_visualizacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_emissao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    erro_codigo: Mapped[str | None] = mapped_column(String(10), nullable=True)
    erro_mensagem: Mapped[str | None] = mapped_column(Text, nullable=True)

    xml_envio: Mapped[str | None] = mapped_column(Text, nullable=True)
    xml_retorno: Mapped[str | None] = mapped_column(Text, nullable=True)
