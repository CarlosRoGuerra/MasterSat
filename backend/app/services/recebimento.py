"""Recebimento manual, diferença de valor e estorno (Fase 03, FIN-06).

Antes: receber R$ 1,00 num título de R$ 100,00 marcava a cobrança como paga e
os R$ 99,00 sumiam da carteira sem registro nenhum. Agora a diferença entre o
valor do título e o recebido tem de ser classificada pelo operador, e cada
classificação vira um ``BillingAdjustment`` com justificativa e responsável:

========================  ============================================
Recebido menor            ``desconto`` (quita com abatimento concedido)
                          ``parcial`` (quita esta; o saldo vira nova
                          cobrança avulsa com vencimento informado)
Recebido maior            ``encargos`` (multa/juros de atraso)
                          ``credito`` (a favor do cliente; sem
                          compensação automática — decisão pendente)
========================  ============================================

Sem classificação, recebimento divergente é recusado (409). Recebimento igual
ao título segue exatamente como antes. Nenhuma política comercial muda: o
sistema não concede desconto nem cobra encargo sozinho — só registra o que o
operador decidiu.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timezone import hoje
from app.models.ailos_boleto import AilosBoleto
from app.models.billing import Billing
from app.models.billing_adjustment import BillingAdjustment
from app.models.billing_change_log import BillingChangeLog
from app.models.billing_charge_item import BillingChargeItem
from app.models.enums import BillingStatus
from app.models.nfse_nota import NfseNota
from app.services import titulo_bancario
from app.services.financial import marcar_billing_pago, refresh_charge_items_for_billing

TRATAMENTOS_A_MENOR = ('desconto', 'parcial')
TRATAMENTOS_A_MAIOR = ('encargos', 'credito')
_CENTAVO = Decimal('0.01')
# Pendências da conciliação que o recebimento manual resolve: são sobre a
# própria cobrança aberta (valor divergente no banco, título baixado lá).
_PENDENCIAS_RESOLVIDAS_NO_RECEBIMENTO = ('pagamento_divergente', 'baixado_com_cobranca_aberta')


class RecebimentoError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 409, extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.extra = extra or {}

    def detail(self) -> dict:
        return {'code': self.code, 'message': self.message, **self.extra}


def centavos(valor) -> Decimal:
    return Decimal(str(valor)).quantize(_CENTAVO)


def _brl(valor: Decimal) -> str:
    return f'R$ {valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _tem_servico_vinculado(db: Session, billing: Billing) -> bool:
    if billing.item_id:
        return True
    return db.scalar(
        select(BillingChargeItem.id).where(BillingChargeItem.billing_id == billing.id).limit(1)
    ) is not None


def registrar_recebimento(
    db: Session,
    billing: Billing,
    *,
    paid_amount,
    payment_date: date,
    payment_method: str,
    notes: str | None,
    tratamento: str | None,
    justificativa: str | None,
    saldo_vencimento: date | None,
    user_id: int | None,
) -> dict:
    """Quita a cobrança (já travada e validada em aberto pelo chamador).

    Devolve ``{'billing': Billing, 'saldo_billing_id': int | None,
    'ajustes': [BillingAdjustment]}``. Um único commit grava cobrança,
    ajustes e cobrança de saldo.
    """
    titulo = centavos(billing.amount)
    pago = centavos(paid_amount) if paid_amount is not None else titulo
    if pago <= 0:
        raise RecebimentoError('valor_invalido', 'Valor recebido deve ser maior que zero.', status_code=422)
    diferenca = pago - titulo
    justificativa = (justificativa or '').strip() or None
    extra = {'valor_titulo': float(titulo), 'valor_recebido': float(pago), 'diferenca': float(diferenca)}

    ajustes: list[BillingAdjustment] = []
    saldo: Billing | None = None
    if diferenca < 0:
        falta = -diferenca
        if tratamento not in TRATAMENTOS_A_MENOR:
            raise RecebimentoError(
                'recebimento_divergente',
                f'Recebido {_brl(pago)} para título de {_brl(titulo)} (faltam {_brl(falta)}). '
                'Informe o que fazer com a diferença: "desconto" (quita com abatimento '
                'concedido) ou "parcial" (quita esta e o saldo vira nova cobrança).',
                extra={**extra, 'tratamentos': list(TRATAMENTOS_A_MENOR)},
            )
        if not justificativa:
            raise RecebimentoError('justificativa_obrigatoria',
                                   'Justifique o desconto ou o recebimento parcial.', status_code=422)
        if tratamento == 'desconto':
            ajustes.append(BillingAdjustment(
                billing_id=billing.id, kind='desconto', amount=falta,
                justification=justificativa, created_by_user_id=user_id,
            ))
        else:
            if _tem_servico_vinculado(db, billing):
                raise RecebimentoError(
                    'parcial_indisponivel',
                    'Recebimento parcial não está disponível para cobrança com serviço avulso '
                    'vinculado (o serviço seria dado como pago pela metade). Use desconto ou '
                    'receba o valor integral.',
                    extra=extra,
                )
            if saldo_vencimento is None:
                raise RecebimentoError('saldo_sem_vencimento',
                                       'Informe o vencimento da cobrança do saldo.', status_code=422)
            saldo = Billing(
                contract_id=billing.contract_id,
                client_id=billing.client_id,
                payer_client_id=billing.payer_client_id or billing.client_id,
                vehicle_id=billing.vehicle_id,
                tracker_id=billing.tracker_id,
                title=f'Saldo da cobrança #{billing.id}',
                billing_type='avulsa',
                amount=falta,
                due_date=saldo_vencimento,
                status=BillingStatus.PENDING if saldo_vencimento >= hoje() else BillingStatus.OVERDUE,
                period_label=billing.period_label,
                notes=(f'Saldo remanescente da cobrança #{billing.id}: recebido {_brl(pago)} de '
                       f'{_brl(titulo)}. {justificativa}'),
            )
            db.add(saldo)
            db.flush()
            ajustes.append(BillingAdjustment(
                billing_id=billing.id, kind='saldo_transferido', amount=falta,
                justification=justificativa, created_by_user_id=user_id, target_billing_id=saldo.id,
            ))
    elif diferenca > 0:
        if tratamento not in TRATAMENTOS_A_MAIOR:
            raise RecebimentoError(
                'recebimento_divergente',
                f'Recebido {_brl(pago)} para título de {_brl(titulo)} ({_brl(diferenca)} a mais). '
                'Informe o que é a diferença: "encargos" (multa/juros de atraso) ou "credito" '
                '(a favor do cliente).',
                extra={**extra, 'tratamentos': list(TRATAMENTOS_A_MAIOR)},
            )
        if tratamento == 'credito' and not justificativa:
            raise RecebimentoError('justificativa_obrigatoria',
                                   'Justifique o crédito a favor do cliente.', status_code=422)
        ajustes.append(BillingAdjustment(
            billing_id=billing.id, kind=tratamento, amount=diferenca,
            justification=justificativa or 'Encargos por atraso (multa/juros).',
            created_by_user_id=user_id,
        ))

    for ajuste in ajustes:
        db.add(ajuste)
    _pos_recebimento_bancario(db, billing, user_id=user_id)
    marcar_billing_pago(
        db, billing,
        payment_date=payment_date,
        paid_amount=pago,
        payment_method=payment_method,
        notes=notes,
        lock=False,
    )
    return {'billing': billing, 'saldo_billing_id': saldo.id if saldo else None, 'ajustes': ajustes}


def _pos_recebimento_bancario(db: Session, billing: Billing, *, user_id: int | None) -> None:
    """Título ativo no banco + recebimento por fora: o boleto continua
    pagável, então a baixa fica pendente e a conciliação segue acompanhando.
    Se o recebimento está registrando o pagamento divergente que o próprio
    banco informou, o título já foi liquidado lá: baixa confirmada."""
    boleto = db.scalar(select(AilosBoleto).where(AilosBoleto.billing_id == billing.id))
    if boleto is None:
        return
    pendencia = boleto.pendencia
    if pendencia in _PENDENCIAS_RESOLVIDAS_NO_RECEBIMENTO:
        db.add(BillingChangeLog(
            billing_id=billing.id, changed_by_user_id=user_id, field_name='pendencia_conciliacao',
            previous_value=pendencia, new_value=None,
            justification='Resolvida pelo recebimento manual com a diferença classificada.',
        ))
        boleto.pendencia = None
        boleto.pendencia_detalhe = None
        boleto.pendencia_desde = None
    if pendencia == 'pagamento_divergente':
        titulo_bancario.confirmar_baixa(boleto, origem='liquidado no banco (pagamento divergente)',
                                        user_id=user_id)
    else:
        titulo_bancario.marcar_baixa_pendente(db, [billing.id], 'recebida fora do boleto')


def _pagamento_confirmado_pelo_banco(boleto: AilosBoleto | None) -> bool:
    if boleto is None or not isinstance(boleto.payload_response, dict):
        return False
    dados = boleto.payload_response.get('boleto')
    if not isinstance(dados, dict):
        dados = boleto.payload_response
    if str(dados.get('indicadorSituacaoBoleto')) == '5':
        return True
    try:
        return Decimal(str((dados.get('valorBoleto') or {}).get('valorPago') or 0)) > 0
    except Exception:  # noqa: BLE001 — valor ilegível não é prova de pagamento
        return False


def estornar_recebimento(db: Session, billing: Billing, *, user_id: int | None, justificativa: str) -> Billing:
    """Desfaz um recebimento manual (cobrança já travada pelo chamador).

    Não apaga nada: o pagamento desfeito fica num ajuste ``estorno`` (data,
    forma, valor, recibo) e os ajustes daquele pagamento ficam marcados como
    revertidos. Recusa pagamento confirmado pelo banco (o dinheiro entrou pelo
    boleto; estorno de verdade é devolução, fora do sistema), cobrança com
    NFS-e e cobrança cujo saldo virou outra cobrança ainda válida.
    """
    justificativa = (justificativa or '').strip()
    if not justificativa:
        raise RecebimentoError('justificativa_obrigatoria', 'Justifique o estorno.', status_code=422)
    if billing.status != BillingStatus.PAID:
        raise RecebimentoError('cobranca_nao_paga', 'Só cobrança paga pode ter o recebimento estornado.')
    boleto = db.scalar(select(AilosBoleto).where(AilosBoleto.billing_id == billing.id))
    if _pagamento_confirmado_pelo_banco(boleto):
        raise RecebimentoError(
            'pagamento_bancario_confirmado',
            'O pagamento desta cobrança foi confirmado pelo banco (boleto liquidado). Não há '
            'estorno local: devolução ao cliente é tratada fora do sistema.',
        )
    nota = db.scalar(select(NfseNota).where(NfseNota.billing_id == billing.id))
    if nota is not None and nota.status in ('pending', 'processing', 'emitida'):
        raise RecebimentoError(
            'nfse_vinculada',
            'Há NFS-e emitida ou em emissão para esta cobrança. Cancele a nota antes de estornar.',
        )
    ativos = list(db.scalars(
        select(BillingAdjustment).where(
            BillingAdjustment.billing_id == billing.id,
            BillingAdjustment.reversed_at.is_(None),
            BillingAdjustment.kind != 'estorno',
        ).order_by(BillingAdjustment.id)
    ).all())
    for ajuste in ativos:
        if ajuste.kind != 'saldo_transferido':
            continue
        destino = db.get(Billing, ajuste.target_billing_id)
        if destino is not None and not destino.is_deleted and destino.status != BillingStatus.CANCELED:
            raise RecebimentoError(
                'saldo_em_aberto',
                f'O saldo deste recebimento virou a cobrança #{destino.id}. Cancele-a antes de '
                'estornar, para o saldo não ser cobrado duas vezes.',
                extra={'billing_ids': [destino.id]},
            )

    momento = titulo_bancario.agora()
    db.add(BillingAdjustment(
        billing_id=billing.id,
        kind='estorno',
        amount=centavos(billing.paid_amount or billing.amount),
        justification=justificativa,
        created_by_user_id=user_id,
        details={
            'payment_date': billing.payment_date.isoformat() if billing.payment_date else None,
            'payment_method': billing.payment_method,
            'paid_amount': str(billing.paid_amount) if billing.paid_amount is not None else None,
            'receipt_number': billing.receipt_number,
            'ajustes_revertidos': [ajuste.id for ajuste in ativos],
        },
    ))
    for ajuste in ativos:
        ajuste.reversed_at = momento
    novo_status = BillingStatus.PENDING if billing.due_date >= hoje() else BillingStatus.OVERDUE
    db.add(BillingChangeLog(
        billing_id=billing.id, changed_by_user_id=user_id, field_name='status',
        previous_value=BillingStatus.PAID.value, new_value=novo_status.value,
        justification=f'Estorno do recebimento: {justificativa}',
    ))
    billing.status = novo_status
    billing.paid_amount = None
    billing.payment_date = None
    billing.payment_method = None
    billing.receipt_number = None
    marcador = f'Recebimento estornado: {justificativa}'
    billing.notes = f'{billing.notes} | {marcador}' if billing.notes else marcador
    if boleto is not None and boleto.baixa_status == 'pendente':
        # O título volta a ser a cobrança válida: não há mais o que baixar.
        boleto.baixa_status = None
        boleto.baixa_observacao = f'{boleto.baixa_observacao or ""} | baixa desnecessária: recebimento estornado'.strip(' |')
    refresh_charge_items_for_billing(db, billing, commit=False)
    db.commit()
    db.refresh(billing)
    return billing


def ajustes_da_cobranca(db: Session, billing_id: int) -> list[BillingAdjustment]:
    return list(db.scalars(
        select(BillingAdjustment)
        .where(BillingAdjustment.billing_id == billing_id)
        .order_by(BillingAdjustment.id)
    ).all())
