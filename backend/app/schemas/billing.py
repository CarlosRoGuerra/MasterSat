from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.billing import BILLING_TYPES, RECURRING_BILLING_TYPES
from app.models.enums import BillingStatus
from app.services.competencia import competencia_do_rotulo, rotulo_canonico


class BillingBase(BaseModel):
    contract_id: int | None = None
    client_id: int
    payer_client_id: int | None = None
    item_id: int | None = None
    vehicle_id: int | None = None
    tracker_id: int | None = None
    title: str | None = None
    billing_type: str = 'recorrente'
    installment_number: int | None = None
    installment_total: int | None = None
    amount: float = Field(gt=0)
    due_date: date
    status: BillingStatus = BillingStatus.PENDING
    payment_date: date | None = None
    payment_method: str | None = None
    notes: str | None = None
    paid_amount: float | None = Field(default=None, gt=0)
    receipt_number: str | None = None
    period_label: str | None = None


class BillingCreate(BillingBase):
    @model_validator(mode='after')
    def _tipo_e_competencia(self):
        if self.billing_type not in BILLING_TYPES:
            raise ValueError(
                'billing_type inválido. Use um de: ' + ', '.join(BILLING_TYPES) + '.'
            )
        if self.period_label is not None:
            canonico = rotulo_canonico(self.period_label)
            if canonico is not None:
                # '9/2026' e '09/2026' são o mesmo mês: grava do jeito que o
                # sistema gera, para listagem e filtros baterem (FIN-04).
                self.period_label = canonico
            elif self.billing_type in RECURRING_BILLING_TYPES:
                raise ValueError(
                    'period_label de mensalidade precisa indicar o período: MM/AAAA '
                    '(ex.: 09/2026), AAAA-MM, "AAAA • T1".."T4", "AAAA • S1"/"S2" ou AAAA.'
                )
        return self


class BillingUpdate(BaseModel):
    contract_id: int | None = None
    client_id: int | None = None
    payer_client_id: int | None = None
    item_id: int | None = None
    vehicle_id: int | None = None
    tracker_id: int | None = None
    title: str | None = None
    billing_type: str | None = None
    installment_number: int | None = None
    installment_total: int | None = None
    amount: float | None = Field(default=None, gt=0)
    due_date: date | None = None
    status: BillingStatus | None = None
    payment_date: date | None = None
    payment_method: str | None = None
    notes: str | None = None
    paid_amount: float | None = Field(default=None, gt=0)
    justification: str | None = None


class BillingBatchStatusIn(BaseModel):
    """Alterar situação de boletos em lote: receber ou cancelar vários de uma vez."""

    billing_ids: list[int] = Field(min_length=1)
    action: str  # 'receber' | 'cancelar'
    # para action='receber'
    payment_date: date | None = None
    payment_method: str | None = None
    # para action='cancelar'
    reason: str | None = None


class BillingBatchMaintIn(BaseModel):
    """Manutenção de título em lote: novo vencimento e/ou valor, com justificativa."""

    billing_ids: list[int] = Field(min_length=1)
    due_date: date | None = None
    amount: float | None = Field(default=None, gt=0)
    justification: str


class BillingUnify(BaseModel):
    """Unifica cobranças em aberto em um único boleto avulso (negociação)."""

    billing_ids: list[int] = Field(min_length=2)
    due_date: date
    # Valor negociado; se ausente, usa a soma simples das cobranças
    amount: float | None = Field(default=None, gt=0)
    notes: str | None = None


class BillingReceive(BaseModel):
    paid_amount: float | None = Field(default=None, gt=0)
    payment_date: date
    payment_method: str
    notes: str | None = None
    # Obrigatório quando paid_amount difere do valor do título (FIN-06):
    #   menor → 'desconto' | 'parcial' (saldo vira nova cobrança)
    #   maior → 'encargos' | 'credito'
    # Sem isto, recebimento divergente é recusado (409 recebimento_divergente).
    tratamento_diferenca: Literal['desconto', 'parcial', 'encargos', 'credito'] | None = None
    justificativa_diferenca: str | None = Field(default=None, max_length=500)
    # Vencimento da cobrança do saldo (tratamento 'parcial').
    saldo_vencimento: date | None = None


class BillingRefund(BaseModel):
    """Estorno de recebimento manual."""

    justificativa: str = Field(min_length=3, max_length=500)


class BillingAdjustmentOut(BaseModel):
    id: int
    billing_id: int
    kind: str
    amount: float
    justification: str
    created_by_user_id: int | None = None
    target_billing_id: int | None = None
    details: dict | None = None
    reversed_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {'from_attributes': True}


class TituloBancarioOut(BaseModel):
    """Situação do título no banco (app/services/titulo_bancario.py)."""

    estado: str
    canal: str | None = None
    nosso_numero: str | None = None
    baixa_status: str | None = None
    pendencia: str | None = None


class BillingCancel(BaseModel):
    reason: str
    # Confirma o cancelamento mesmo havendo boleto registrado na Ailos (que
    # continua ativo no banco — o convênio não oferece baixa automática).
    confirmar_boleto_ailos: bool = False
    # Mensalidade cancelada continua ocupando o mês do contrato (o fechamento
    # não recobra). True devolve o mês para ser cobrado de novo — carnê ou
    # fechamento. Fica registrado no histórico da cobrança.
    liberar_competencia: bool = False
    # Obrigatório para cancelar um boleto único/negociação que substituiu
    # outras cobranças: as originais são reabertas (a dívida volta para elas).
    reverter_substituicao: bool = False


class BillingReleasePeriod(BaseModel):
    """Libera o mês de uma mensalidade já cancelada para nova cobrança."""

    justificativa: str = Field(min_length=3, max_length=500)


class BillingOut(BillingBase):
    id: int
    client_name: str | None = None
    payer_name: str | None = None
    vehicle_plate: str | None = None
    tracker_identifier: str | None = None
    plan_name: str | None = None
    contract_status: str | None = None
    overdue_days: int = 0
    # Valor atualizado (multa 2% + juros 1% a.m.) — calculado no backend,
    # fonte única para tela/mensagens/integrações. None se não vencida.
    valor_com_juros: float | None = None
    # Há boleto REGISTRADO na Ailos? Só então existe PDF para baixar/enviar —
    # sem registro o título não é pagável no banco.
    boleto_ailos: bool = False
    # Dados do boleto de origem, para cobrança migrada do SGR. O boleto
    # bancário some do SGR depois de baixado, então é isto que a tela de
    # detalhes tem para mostrar.
    sgr_payload: dict | None = None
    # Competência canônica (1º dia do mês em que o período começa). None em
    # rótulo legado fora de formato.
    competencia: date | None = None
    competencia_liberada: bool = False
    # Cobrança que assumiu esta dívida (boleto único ou negociação).
    substituted_by_id: int | None = None
    # Situação do título no banco; None = nunca foi ao banco (Fase 03).
    titulo_bancario: TituloBancarioOut | None = None
    # Cadastros ligados à cobrança que foram removidos depois (FIN-10):
    # 'cliente', 'responsavel_financeiro', 'contrato', 'plano', 'veiculo',
    # 'rastreador'. A cobrança continua visível como histórico.
    relacoes_removidas: list[str] = []

    model_config = {'from_attributes': True}


class BillingChangeLogOut(BaseModel):
    id: int
    billing_id: int
    changed_by_user_id: int | None
    field_name: str
    previous_value: str | None
    new_value: str | None
    justification: str
    created_at: datetime | None = None

    model_config = {'from_attributes': True}


class RevenueReportItem(BaseModel):
    """Bases (PROD-01): emitido/aberto/recebido_by_due por VENCIMENTO,
    total_received por data de PAGAMENTO (caixa). Canceladas fora."""

    label: str
    total_received: float
    total_billed: float
    total_outstanding: float
    total_received_by_due: float = 0.0


class DelinquentClientItem(BaseModel):
    client_id: int
    client_name: str
    total_open: float
    overdue_count: int


class FinancialSummary(BaseModel):
    active_plans: int
    active_contracts: int
    pending_billings: int
    overdue_billings: int
    pending_amount: float
    overdue_amount: float
    paid_this_month: float
