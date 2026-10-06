from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.services.billing_closure import (
    execute_closure,
    generate_closure_pdf,
    generate_closure_xlsx,
    simulate_closure,
)
from app.services.financial import add_months
from app.services.closure_delivery import (
    conferir_lote, conferir_mes, enfileirar_lote, listar_lotes, listar_meses,
    recuperar_lotes_anteriores,
)

router = APIRouter()

ALLOWED_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL)


@router.get('/meses')
def closure_service_months(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    return listar_meses(db)


@router.get('/meses/{mes_servico}')
def closure_month_preview(
    mes_servico: str,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    return conferir_mes(db, mes_servico)


@router.get('/lotes')
def closure_batches(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    return listar_lotes(db)


@router.post('/lotes/recuperar')
def recover_older_closure_batches(
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    return recuperar_lotes_anteriores(db)


@router.get('/lotes/{lote_id}')
def closure_batch_preview(
    lote_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    return conferir_lote(db, lote_id)


class ClosureQueueIn(BaseModel):
    billing_ids: list[int] = Field(min_length=1, max_length=1000)


@router.post('/lotes/{lote_id}/enviar', status_code=202)
def closure_batch_enqueue(
    lote_id: int,
    payload: ClosureQueueIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    return enfileirar_lote(db, lote_id, payload.billing_ids)


@router.post('/lotes/{lote_id}/enviar/{billing_id}', status_code=202)
def closure_batch_send_title(
    lote_id: int,
    billing_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    # Clientes antigos também entram na fila; nunca contornam o intervalo.
    result = enfileirar_lote(db, lote_id, [billing_id])
    if result['enfileirados']:
        return {'estado': 'aguardando', 'billing_id': billing_id}
    item = result['ignorados'][0]
    if item['estado'] == 'enviado':
        return {'estado': 'ja_enviado', 'billing_id': billing_id}
    if item['estado'] in ('aguardando', 'processando'):
        return {'estado': item['estado'], 'billing_id': billing_id}
    raise HTTPException(status_code=409, detail=item['motivo'] or 'Envio indisponível.')


def _parse_reference_month(reference_month: str):
    try:
        year, month = reference_month.split('-')
        from datetime import date
        return date(int(year), int(month), 1)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=422, detail='Formato de mês inválido. Use YYYY-MM (ex: 2026-06).')


def _resolve_months(reference_month: str | None, service_month: str | None):
    if bool(reference_month) == bool(service_month):
        raise HTTPException(
            status_code=422,
            detail='Informe apenas reference_month ou service_month no formato YYYY-MM.',
        )
    if service_month:
        activity_month = _parse_reference_month(service_month)
        return add_months(activity_month, 1), activity_month
    return _parse_reference_month(reference_month), None


_FORMA_COBRANCA = Query(
    default=None, pattern='^(boleto_mensal|carne_ailos|carne_simples|cartao_credito|nao_informado)$',
    description='Forma de cobrança do responsável financeiro (cadastro do cliente)',
)


def _simulate_for_month(db, billing_month, activity_month, filter_type, client_id, forma_cobranca=None):
    options = {'activity_month': activity_month} if activity_month else {}
    simulation = simulate_closure(
        db, billing_month, filter_type, client_id, forma_cobranca=forma_cobranca, **options,
    )
    if activity_month:
        simulation['billing_month'] = simulation['reference_month']
        simulation['reference_month'] = activity_month.strftime('%m/%Y')
        for item in simulation['items']:
            item['service_period_label'] = simulation['reference_month']
    return simulation


@router.get('/simulate')
def simulate(
    reference_month: str | None = Query(default=None, description='Mês de vencimento legado no formato YYYY-MM'),
    service_month: str | None = Query(default=None, description='Mês do serviço no formato YYYY-MM; vencimento no mês seguinte'),
    filter_type: str = Query(default='all', pattern='^(all|pf|pj|client)$'),
    client_id: int | None = None,
    forma_cobranca: str | None = _FORMA_COBRANCA,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    ref, activity_month = _resolve_months(reference_month, service_month)
    if filter_type == 'client' and not client_id:
        raise HTTPException(status_code=422, detail='client_id obrigatório quando filter_type=client.')
    try:
        return _simulate_for_month(db, ref, activity_month, filter_type, client_id, forma_cobranca)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get('/simulate/pdf')
def simulate_pdf(
    reference_month: str | None = Query(default=None, description='Mês de vencimento legado no formato YYYY-MM'),
    service_month: str | None = Query(default=None, description='Mês do serviço no formato YYYY-MM; vencimento no mês seguinte'),
    filter_type: str = Query(default='all', pattern='^(all|pf|pj|client)$'),
    client_id: int | None = None,
    forma_cobranca: str | None = _FORMA_COBRANCA,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    ref, activity_month = _resolve_months(reference_month, service_month)
    if filter_type == 'client' and not client_id:
        raise HTTPException(status_code=422, detail='client_id obrigatório quando filter_type=client.')
    try:
        simulation = _simulate_for_month(db, ref, activity_month, filter_type, client_id, forma_cobranca)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    pdf_buffer = generate_closure_pdf(simulation)
    filename = f'fechamento-{service_month or reference_month}.pdf'
    return StreamingResponse(
        pdf_buffer,
        media_type='application/pdf',
        headers={'Content-Disposition': f'inline; filename={filename}'},
    )


@router.get('/simulate/xlsx')
def simulate_xlsx(
    reference_month: str | None = Query(default=None, description='Mês de vencimento legado no formato YYYY-MM'),
    service_month: str | None = Query(default=None, description='Mês do serviço no formato YYYY-MM; vencimento no mês seguinte'),
    filter_type: str = Query(default='all', pattern='^(all|pf|pj|client)$'),
    client_id: int | None = None,
    forma_cobranca: str | None = _FORMA_COBRANCA,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    ref, activity_month = _resolve_months(reference_month, service_month)
    if filter_type == 'client' and not client_id:
        raise HTTPException(status_code=422, detail='client_id obrigatório quando filter_type=client.')
    try:
        simulation = _simulate_for_month(db, ref, activity_month, filter_type, client_id, forma_cobranca)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    xlsx_buffer = generate_closure_xlsx(simulation)
    filename = f'fechamento-{service_month or reference_month}.xlsx'
    return StreamingResponse(
        xlsx_buffer,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )


@router.post('/generate')
def generate(
    reference_month: str | None = Query(default=None, description='Mês de vencimento legado no formato YYYY-MM'),
    service_month: str | None = Query(default=None, description='Mês do serviço no formato YYYY-MM; vencimento no mês seguinte'),
    filter_type: str = Query(default='all', pattern='^(all|pf|pj|client)$'),
    client_id: int | None = None,
    forma_cobranca: str | None = _FORMA_COBRANCA,
    contract_ids: list[int] | None = Query(default=None, description='Seleção exata de contratos recorrentes'),
    uninstall_event_ids: list[int] | None = Query(default=None, description='Seleção exata de eventos de desinstalação'),
    charge_item_ids: list[int] | None = Query(default=None, description='Seleção exata de serviços avulsos'),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    """
    Executa o fechamento de faturamento de forma síncrona.
    Retorna o resultado completo ao final do processamento.
    """
    ref, activity_month = _resolve_months(reference_month, service_month)
    if filter_type == 'client' and not client_id:
        raise HTTPException(status_code=422, detail='client_id obrigatório quando filter_type=client.')

    try:
        options = {'activity_month': activity_month} if activity_month else {}
        result = execute_closure(
            db, ref, filter_type, client_id,
            contract_ids=contract_ids,
            uninstall_event_ids=uninstall_event_ids,
            charge_item_ids=charge_item_ids,
            forma_cobranca=forma_cobranca,
            **options,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # `**result` traz um reference_month já formatado para exibição (MM/YYYY) e
    # vinha sobrescrevendo o eco do parâmetro logo acima. O efeito era um
    # contrato inconsistente: a API só aceita YYYY-MM, mas devolvia 05/2025 —
    # que ela própria rejeita com 422 se o cliente reenviar o valor recebido.
    # Agora o campo canônico ecoa a entrada e o formato de exibição fica num
    # campo próprio, sem colisão.
    return {
        'status': 'completed',
        **result,
        'reference_month': service_month or reference_month,
        'reference_month_label': activity_month.strftime('%m/%Y') if activity_month else result.get('reference_month'),
        'billing_month': result.get('reference_month'),
    }
