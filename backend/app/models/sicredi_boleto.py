from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin

# Estado do REGISTRO (o que este sistema sabe do pedido). A situação que o
# Sicredi devolve na consulta (EM CARTEIRA, LIQUIDADO PIX...) fica em
# ``situacao`` — separada de propósito: na Ailos as duas coisas dividem a
# mesma coluna e cada leitor precisa adivinhar qual é qual.
STATUS_REGISTRANDO = 'REGISTRANDO'
STATUS_NA_FILA = 'NA_FILA'
STATUS_DESFECHO_DESCONHECIDO = 'DESFECHO_DESCONHECIDO'
STATUS_ERRO_REGISTRO = 'ERRO_REGISTRO'
STATUS_REGISTRADO = 'REGISTRADO'


class SicrediBoleto(Base, TimestampMixin):
    """Boleto registrado pela API de Cobrança Sicredi, vinculado 1:1 a um Billing.

    ``status``:

    * ``REGISTRANDO`` — reserva gravada antes do POST (o nosso número já está
      reservado); reserva sem resposta além do prazo vira desfecho desconhecido;
    * ``DESFECHO_DESCONHECIDO`` — o pedido pode ter chegado ao banco e não
      houve resposta conclusiva (timeout, 5xx). Bloqueia tudo até a consulta
      pelo nosso número resolver;
    * ``ERRO_REGISTRO`` — rejeição definitiva ou pedido que não saiu daqui.
      Uma nova tentativa reaproveita o mesmo nosso número (ele não foi usado);
    * ``REGISTRADO`` — o Sicredi devolveu linha digitável e código de barras.

    ``baixa_status``: ``pendente`` (a cobrança local deixou de valer e o título
    ainda é pagável), ``solicitada`` (instrução de baixa aceita pela API — o
    processamento é assíncrono) e ``confirmada`` (a consulta devolveu
    BAIXADO POR SOLICITACAO ou o título foi liquidado).
    """

    __tablename__ = 'sicredi_boletos'
    __table_args__ = (
        CheckConstraint(
            "status IN ('NA_FILA', 'REGISTRANDO', 'DESFECHO_DESCONHECIDO', 'ERRO_REGISTRO', 'REGISTRADO')",
            name='ck_sicredi_boletos_status',
        ),
        CheckConstraint(
            "baixa_status IS NULL OR baixa_status IN ('pendente', 'solicitada', 'confirmada')",
            name='ck_sicredi_boletos_baixa_status',
        ),
        # O nosso número identifica o título no Sicredi: nunca dois registros
        # locais com o mesmo número no mesmo convênio e ambiente.
        UniqueConstraint(
            'ambiente', 'cooperativa', 'posto', 'codigo_beneficiario', 'nosso_numero',
            name='uq_sicredi_boletos_nosso_numero',
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    billing_id: Mapped[int] = mapped_column(ForeignKey('billings.id'), unique=True, index=True)
    lote_id: Mapped[int | None] = mapped_column(ForeignKey('sicredi_lotes.id'), nullable=True, index=True)
    ambiente: Mapped[str] = mapped_column(String(10))
    cooperativa: Mapped[str] = mapped_column(String(4))
    posto: Mapped[str] = mapped_column(String(2))
    codigo_beneficiario: Mapped[str] = mapped_column(String(5))
    nosso_numero: Mapped[str] = mapped_column(String(9))
    seu_numero: Mapped[str | None] = mapped_column(String(10), nullable=True)
    id_titulo_empresa: Mapped[str | None] = mapped_column(String(25), nullable=True)
    tipo_cobranca: Mapped[str] = mapped_column(String(10), default='NORMAL')
    status: Mapped[str] = mapped_column(String(30), default=STATUS_REGISTRANDO)
    situacao: Mapped[str | None] = mapped_column(String(40), nullable=True)
    linha_digitavel: Mapped[str | None] = mapped_column(String(60), nullable=True)
    codigo_barras: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Boleto híbrido: identificador e "copia e cola" do Pix.
    txid: Mapped[str | None] = mapped_column(String(60), nullable=True)
    qr_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor_nominal: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    data_vencimento: Mapped[date | None] = mapped_column(Date, nullable=True)
    payload_request: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    payload_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    registro_iniciado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Checkpoint da conciliação: quem está há mais tempo sem consulta vai primeiro.
    ultima_consulta_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
    )
    ultima_consulta_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    falhas_consulta: Mapped[int] = mapped_column(Integer, default=0, server_default='0', nullable=False)
    data_liquidacao: Mapped[date | None] = mapped_column(Date, nullable=True)
    valor_liquidado: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    baixa_status: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    baixa_solicitada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    baixa_enviada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    baixa_transaction_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    baixa_confirmada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    baixa_confirmada_por_user_id: Mapped[int | None] = mapped_column(
        ForeignKey('users.id', name='fk_sicredi_boletos_baixa_user'), nullable=True,
    )
    baixa_observacao: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Divergência que a conciliação não resolve sozinha (mesmos códigos da Ailos).
    pendencia: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    pendencia_detalhe: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pendencia_desde: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SicrediNossoNumeroSequencia(Base):
    """Último sequencial usado por (convênio, ano, byte). Alocado com trava de
    linha: dois boletos nunca recebem o mesmo nosso número."""

    __tablename__ = 'sicredi_nosso_numero_sequencias'
    __table_args__ = (
        UniqueConstraint(
            'ambiente', 'cooperativa', 'posto', 'codigo_beneficiario', 'ano', 'byte',
            name='uq_sicredi_nosso_numero_sequencias',
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ambiente: Mapped[str] = mapped_column(String(10))
    cooperativa: Mapped[str] = mapped_column(String(4))
    posto: Mapped[str] = mapped_column(String(2))
    codigo_beneficiario: Mapped[str] = mapped_column(String(5))
    ano: Mapped[int] = mapped_column(Integer)
    byte: Mapped[int] = mapped_column(Integer)
    ultimo: Mapped[int] = mapped_column(Integer, default=0)
