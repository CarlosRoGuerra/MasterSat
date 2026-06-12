from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.services.billing_closure import execute_closure, generate_closure_pdf, simulate_closure

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
    return simulate_closure(db, ref, filter_type, client_id)


@router.get('/simulate/pdf')
def simulate_pdf(
    reference_month: str = Query(..., description='Mês de referência no formato YYYY-MM'),
    filter_type: str = Query(default='all', pattern='^(all|pf|pj|client)$'),
    client_id: int | None = None,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ALLOWED_ROLES)),
):
    ref = _parse_reference_month(reference_month)
    simulation = simulate_closure(db, ref, filter_type, client_id)
    pdf_buffer = generate_closure_pdf(simulation)
    filename = f'fechamento-{reference_month}.pdf'
    return StreamingResponse(
        pdf_buffer,
        media_type='application/pdf',
        headers={'Content-Disposition': f'inline; filename={filename}'},
    )


@router.post('/generate')
def generate(
    reference_month: str = Query(..., description='Mês de referência no formato YYYY-MM'),
    filter_type: str = Query(default='all', pattern='^(all|pf|pj|client)$'),
    client_id: int | None = None,
    contract_ids: list[int] | None = Query(default=None, description='IDs de contratos específicos para gerar'),
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

    result = execute_closure(db, ref, filter_type, client_id, contract_ids or None)
    return {'status': 'completed', 'reference_month': reference_month, **result}
