from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.enums import UserRole
from app.schemas.audit_log import AuditLogOut, AuditLogPage

router = APIRouter()
ADMIN_ONLY = (UserRole.ADMIN,)


def _filtered_query(
    db: Session, user_id: int | None, user_role: str | None,
    entity_type: str | None, entity_id: int | None, method: str | None,
    date_from: date | None, date_to: date | None, search: str | None,
):
    query = db.query(AuditLog)
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)
    if user_role:
        query = query.filter(AuditLog.user_role == user_role)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.filter(AuditLog.entity_id == entity_id)
    if method:
        query = query.filter(AuditLog.method == method.upper())
    if date_from:
        query = query.filter(AuditLog.created_at >= date_from)
    if date_to:
        query = query.filter(AuditLog.created_at <= datetime.combine(date_to, time.max))
    if search:
        term = f'%{search.strip()}%'
        query = query.filter(
            AuditLog.description.ilike(term) |
            AuditLog.user_name.ilike(term) |
            AuditLog.path.ilike(term),
        )
    return query


@router.get('/paged', response_model=AuditLogPage)
def list_logs_paged(
    user_id: int | None = None,
    user_role: str | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    method: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ADMIN_ONLY)),
):
    query = _filtered_query(db, user_id, user_role, entity_type, entity_id,
                            method, date_from, date_to, search)
    total, today_count, error_count, unique_users = query.with_entities(
        func.count(AuditLog.id),
        func.count(AuditLog.id).filter(AuditLog.created_at >= date.today()),
        func.count(AuditLog.id).filter(AuditLog.status_code >= 400),
        func.count(func.distinct(AuditLog.user_id)),
    ).one()
    items = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset(skip).limit(limit).all()
    entity_types = [item for (item,) in db.query(AuditLog.entity_type).filter(
        AuditLog.entity_type.isnot(None)
    ).distinct().order_by(AuditLog.entity_type).all()]
    return {
        'items': items,
        'total': total,
        'today_count': today_count,
        'error_count': error_count,
        'unique_users': unique_users,
        'entity_types': entity_types,
    }


@router.get('/', response_model=list[AuditLogOut])
def list_logs(
    user_id: int | None = None,
    user_role: str | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    method: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(*ADMIN_ONLY)),
):
    # Legacy array response remains available to existing API consumers.
    return _filtered_query(db, user_id, user_role, entity_type, entity_id,
                           method, date_from, date_to, search).order_by(
        AuditLog.created_at.desc(), AuditLog.id.desc()
    ).limit(limit).all()
