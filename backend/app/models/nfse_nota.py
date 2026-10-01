from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text
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
      processing — tentativa reservada ou lote recebido, aguardando resultado
      emitida    — NFS-e gerada com sucesso
      erro       — falha local ou rejeição fiscal comprovada (erro_tipo)
      desconhecido — resultado incerto; somente consulta, nunca reenvio automático
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

    # Reserva persistida; tentativa_id é também o fencing token de toda escrita.
    tentativa_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tentativa_numero: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    emissor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    envio_iniciado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    erro_tipo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    provedor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ambiente: Mapped[str | None] = mapped_column(String(30), nullable=True)
    prestador_cnpj: Mapped[str | None] = mapped_column(String(14), nullable=True)
    prestador_im: Mapped[str | None] = mapped_column(String(30), nullable=True)
    codigo_municipio: Mapped[str | None] = mapped_column(String(7), nullable=True)
    dps_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    competencia: Mapped[date | None] = mapped_column(Date, nullable=True)
    discriminacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    codigo_servico: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tentativas_anteriores: Mapped[list] = mapped_column(JSON, default=list, server_default='[]')
