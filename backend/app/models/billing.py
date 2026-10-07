from datetime import date
from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Date, DDL, Enum, ForeignKey, Index, Integer, Numeric, String, Text,
    event, false, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import SoftDeleteMixin, TimestampMixin
from app.models.enums import BillingStatus
from app.db.session import Base
from app.services.competencia import (
    SQL_FUNCAO_COMPETENCIA,
    SQL_FUNCAO_TRIGGER,
    SQL_TRIGGER,
    competencia_do_rotulo,
)

# Tipos que "ocupam" o mês de um contrato (ver RECURRING_BILLING_TYPES em
# app/services/financial.py, que reexporta esta tupla).
RECURRING_BILLING_TYPES = ('recorrente', 'prorata', 'primeira_mensalidade', 'carne')
# Todos os tipos que o sistema grava hoje (fechamento, carnê, serviços,
# negociação, importação SGR). A criação manual pela API só aceita estes.
BILLING_TYPES = RECURRING_BILLING_TYPES + ('taxa_instalacao', 'taxa_desinstalacao', 'avulsa', 'item')
# Tipo interno do título que reúne mensalidades, taxas e serviços de um fechamento.
# Fica fora de BILLING_TYPES para não ser criado como cobrança manual.
CONSOLIDATED_BILLING_TYPE = 'boleto_unico'
_TIPOS_SQL = ', '.join(f"'{tipo}'" for tipo in RECURRING_BILLING_TYPES)


class Billing(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = 'billings'
    __table_args__ = (
        # Uma mensalidade por contrato e competência (FIN-04/FIN-01). Cancelada
        # continua ocupando o mês — é o que impede o fechamento de recobrar um
        # mês negociado ou consolidado. Só sai do índice quando o financeiro
        # libera a competência explicitamente (competencia_liberada) ou remove
        # a cobrança. Substitui uq_billings_contract_period_recurring, que
        # comparava o texto do rótulo.
        Index(
            'uq_billings_contract_competencia_recorrente',
            'contract_id', 'competencia',
            unique=True,
            postgresql_where=text(
                f'is_deleted = false AND competencia_liberada = false '
                f'AND billing_type IN ({_TIPOS_SQL})'
            ),
            sqlite_where=text(
                f'is_deleted = 0 AND competencia_liberada = 0 '
                f'AND billing_type IN ({_TIPOS_SQL})'
            ),
        ),
        # Uma parcela efetiva por serviço avulso (FIN-12). Parcela cancelada
        # sem substituto sai do índice: é o cancelamento que devolve o serviço
        # à fila. Parcela unificada numa negociação continua contando.
        Index(
            'uq_billings_item_parcela_efetiva',
            'item_id', 'installment_number',
            unique=True,
            postgresql_where=text(
                "is_deleted = false AND (status <> 'CANCELED' OR substituted_by_id IS NOT NULL)"
            ),
            sqlite_where=text(
                "is_deleted = 0 AND (status <> 'CANCELED' OR substituted_by_id IS NOT NULL)"
            ),
        ),
        # Removida não é validada: é histórico, e remover é o saneamento de
        # valor inválido legado (ver migration e5c2a9d71f04).
        CheckConstraint('is_deleted OR amount > 0', name='ck_billings_amount_positivo'),
        CheckConstraint('paid_amount IS NULL OR paid_amount > 0', name='ck_billings_paid_amount_positivo'),
        CheckConstraint(
            'installment_number IS NULL OR (installment_number >= 1 AND '
            '(installment_total IS NULL OR installment_number <= installment_total))',
            name='ck_billings_parcela_no_intervalo',
        ),
        CheckConstraint(
            "NOT competencia_liberada OR status = 'CANCELED'",
            name='ck_billings_liberada_so_cancelada',
        ),
        CheckConstraint(
            "substituted_by_id IS NULL OR (status = 'CANCELED' AND substituted_by_id <> id)",
            name='ck_billings_substituida_cancelada',
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey('contracts.id'), nullable=True, index=True)
    # Cliente atendido/dono do contrato. O responsável financeiro fica em
    # payer_client_id para não perder a origem operacional da cobrança.
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'), index=True)
    # Snapshot do pagador no momento da emissão. Sem este campo, alterar o
    # interveniente do contrato mudava retroativamente boleto e NFS-e antigos.
    payer_client_id: Mapped[int | None] = mapped_column(
        ForeignKey('clients.id'), nullable=True, index=True,
    )
    item_id: Mapped[int | None] = mapped_column(ForeignKey('client_charge_items.id'), nullable=True, index=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey('vehicles.id'), nullable=True, index=True)
    tracker_id: Mapped[int | None] = mapped_column(ForeignKey('trackers.id'), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    billing_type: Mapped[str] = mapped_column(String(30), default='recorrente', index=True)
    installment_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    installment_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    due_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[BillingStatus] = mapped_column(Enum(BillingStatus), default=BillingStatus.PENDING, index=True)
    payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    paid_amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    receipt_number: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    # Rótulo de exibição do período ("09/2026", "2026 • T3"). A identidade do
    # período é ``competencia``, derivada deste texto (app/services/competencia.py).
    period_label: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # Primeiro dia do mês em que o período começa. Preenchida a partir do
    # rótulo pela aplicação e, no PostgreSQL, também pelo trigger
    # trg_billings_competencia.
    competencia: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    # Cobrança cancelada cujo mês o financeiro devolveu para ser cobrado de
    # novo (carnê ou fechamento). Sem isto, cancelada ocupa o mês para sempre.
    competencia_liberada: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False,
    )
    # Cobrança que vive só no sistema (carnê simples): nunca vai ao banco. A
    # política de título bancário recusa emitir na Ailos ou em remessa CNAB.
    somente_sistema: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False,
    )
    # Título que substituiu este: boleto único do fechamento ou negociação
    # (/billings/unificar). A original fica cancelada e continua ocupando o
    # mês/parcela, porque a dívida passou para o substituto.
    substituted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey('billings.id', name='fk_billings_substituted_by_id'), nullable=True, index=True,
    )
    # Registro do boleto como o SGR devolve, para cobrança migrada. O boleto
    # bancário em si deixa de existir lá depois de baixado, então estes
    # campos são o que resta como documento de origem.
    sgr_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


@event.listens_for(Billing, 'before_insert')
@event.listens_for(Billing, 'before_update')
def _preencher_competencia(_mapper, _connection, target: Billing) -> None:
    # Mesma regra do trigger do PostgreSQL. No SQLite dos testes é a única.
    if target.period_label is not None:
        target.competencia = competencia_do_rotulo(target.period_label)


# create_all (testes em PostgreSQL) precisa do mesmo trigger que a migration
# cria; sem ele, o teste validaria um banco diferente do de produção.
for _sql in (SQL_FUNCAO_COMPETENCIA, SQL_FUNCAO_TRIGGER, SQL_TRIGGER):
    event.listen(Billing.__table__, 'after_create', DDL(_sql).execute_if(dialect='postgresql'))
