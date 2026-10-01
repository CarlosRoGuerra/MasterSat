from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class AilosBoleto(Base, TimestampMixin):
    """Boleto gerado via API de Cobrança Ailos, vinculado 1:1 a um Billing.

    ``status_ailos`` guarda o estado do REGISTRO (Fase 03, FIN-03):

    * ``REGISTRANDO``/``PROCESSANDO`` — pedido enviado (individual/lote);
    * ``DESFECHO_DESCONHECIDO`` — o pedido pode ter chegado ao banco, mas não
      houve resposta conclusiva (timeout, 5xx, falha local depois do envio).
      Bloqueia edição, exclusão e nova emissão até a consulta resolver;
    * ``ERRO_REGISTRO`` — rejeição definitiva ou pedido que não saiu daqui;
    * depois do registro, o que a Ailos devolve em ``indicadorSituacaoBoleto``
      (0 em aberto, 3 baixado, 5 liquidado — Manual v1.0).
    """

    __tablename__ = 'ailos_boletos'
    __table_args__ = (
        CheckConstraint(
            "baixa_status IS NULL OR baixa_status IN ('pendente', 'confirmada')",
            name='ck_ailos_boletos_baixa_status',
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    billing_id: Mapped[int] = mapped_column(ForeignKey('billings.id'), unique=True, index=True)
    lote_id: Mapped[int | None] = mapped_column(ForeignKey('ailos_lotes.id'), nullable=True)
    numero_convenio: Mapped[str] = mapped_column(String(20))
    numero_documento: Mapped[str | None] = mapped_column(String(40), nullable=True)
    nosso_numero: Mapped[str | None] = mapped_column(String(40), nullable=True)
    identificador_unico_titulo: Mapped[str | None] = mapped_column(String(60), nullable=True)
    linha_digitavel: Mapped[str | None] = mapped_column(String(60), nullable=True)
    codigo_barras: Mapped[str | None] = mapped_column(String(60), nullable=True)
    valor_nominal: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    data_vencimento: Mapped[date | None] = mapped_column(Date, nullable=True)
    status_ailos: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # "Copia e cola" (EMV/BR Code) do Pix — começa com "000201". Quando
    # presente, gera um QR vetorial nítido. Pode ser None se a Ailos só mandar
    # a imagem (pix_qr_base64).
    pix_emv: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Imagem PNG/JPEG do QR Pix em base64 (fallback quando não há o EMV).
    pix_qr_base64: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_request: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    payload_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Quando o pedido de registro foi reservado. Reserva REGISTRANDO/
    # PROCESSANDO mais velha que o prazo é tratada como desfecho desconhecido
    # (processo caiu entre o envio e a resposta).
    registro_iniciado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Checkpoint da conciliação (FIN-05): o worker consulta primeiro quem está
    # há mais tempo sem consulta. Gravado também quando a consulta falha, para
    # um título com erro não travar a fila.
    ultima_consulta_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
    )
    ultima_consulta_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    falhas_consulta: Mapped[int] = mapped_column(Integer, default=0, server_default='0', nullable=False)

    # Baixa do título no banco (FIN-02). O convênio não tem instrução de baixa
    # pela API: o operador baixa no internet banking da Ailos. ``pendente`` =
    # a cobrança local deixou de valer (cancelada, recebida por fora, removida)
    # e o título ainda pode ser pago; ``confirmada`` = a consulta devolveu
    # situação 3 (baixado) ou um administrador confirmou com justificativa.
    baixa_status: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    baixa_solicitada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    baixa_confirmada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    baixa_confirmada_por_user_id: Mapped[int | None] = mapped_column(
        ForeignKey('users.id', name='fk_ailos_boletos_baixa_user'), nullable=True,
    )
    baixa_observacao: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Divergência que a conciliação não resolve sozinha: pagamento com valor
    # diferente do título, título pago depois de cancelado/removido, título
    # baixado no banco com cobrança aberta aqui. Fica até alguém tratar.
    pendencia: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    pendencia_detalhe: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pendencia_desde: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
