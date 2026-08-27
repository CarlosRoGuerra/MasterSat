from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.services.billing_closure import (
    execute_closure,
    generate_closure_pdf,
    generate_closure_xlsx,
    simulate_closure,
)

router = APIRouter()

ALLOWED_ROLES = (UserRole.ADMIN, UserRole.FINANCIAL)


def _parse_reference_month(reference_month: str):
    try:
        year, month = reference_month.split('-')
        from datetime import date
        return date(int(year), int(month), 1)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=422, detail='Formato de mês inválido. Use YYYY-MM (ex: 2026-06).')


@router.get('/simulate')
def simulate(
    reference_month: str = Query(..., description='Mês de referência no formato YYYY-MM'),
    filter_type: str = Query(default='all', pattern='^(all|pf|pj|client)$'),
    client_id: int | None = None,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    ref = _parse_reference_month(reference_month)
    if filter_type == 'client' and not client_id:
        raise HTTPException(status_code=422, detail='client_id obrigatório quando filter_type=client.')
    try:
        return simulate_closure(db, ref, filter_type, client_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get('/simulate/pdf')
def simulate_pdf(
    reference_month: str = Query(..., description='Mês de referência no formato YYYY-MM'),
    filter_type: str = Query(default='all', pattern='^(all|pf|pj|client)$'),
    client_id: int | None = None,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    ref = _parse_reference_month(reference_month)
    if filter_type == 'client' and not client_id:
        raise HTTPException(status_code=422, detail='client_id obrigatório quando filter_type=client.')
    try:
        simulation = simulate_closure(db, ref, filter_type, client_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    pdf_buffer = generate_closure_pdf(simulation)
    filename = f'fechamento-{reference_month}.pdf'
    return StreamingResponse(
        pdf_buffer,
        media_type='application/pdf',
        headers={'Content-Disposition': f'inline; filename={filename}'},
    )


@router.get('/simulate/xlsx')
def simulate_xlsx(
    reference_month: str = Query(..., description='Mês de referência no formato YYYY-MM'),
    filter_type: str = Query(default='all', pattern='^(all|pf|pj|client)$'),
    client_id: int | None = None,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    ref = _parse_reference_month(reference_month)
    if filter_type == 'client' and not client_id:
        raise HTTPException(status_code=422, detail='client_id obrigatório quando filter_type=client.')
    try:
        simulation = simulate_closure(db, ref, filter_type, client_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    xlsx_buffer = generate_closure_xlsx(simulation)
    filename = f'fechamento-{reference_month}.xlsx'
    return StreamingResponse(
        xlsx_buffer,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )


@router.post('/generate')
def generate(
    reference_month: str = Query(..., description='Mês de referência no formato YYYY-MM'),
    filter_type: str = Query(default='all', pattern='^(all|pf|pj|client)$'),
    client_id: int | None = None,
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
    ref = _parse_reference_month(reference_month)
    if filter_type == 'client' and not client_id:
        raise HTTPException(status_code=422, detail='client_id obrigatório quando filter_type=client.')

    try:
        result = execute_closure(
            db, ref, filter_type, client_id,
            contract_ids=contract_ids,
            uninstall_event_ids=uninstall_event_ids,
            charge_item_ids=charge_item_ids,
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
        'reference_month': reference_month,
        'reference_month_label': result.get('reference_month'),
    }
