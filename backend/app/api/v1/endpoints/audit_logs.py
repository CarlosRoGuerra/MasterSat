from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.enums import UserRole
from app.schemas.audit_log import AuditLogOut

router = APIRouter()

ADMIN_ONLY = (UserRole.ADMIN,)


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
        from datetime import datetime, time
        query = query.filter(AuditLog.created_at <= datetime.combine(date_to, time.max))
    if search:
        term = f'%{search.strip()}%'
        query = query.filter(
            AuditLog.description.ilike(term) |
            AuditLog.user_name.ilike(term) |
            AuditLog.path.ilike(term),
        )

    return query.order_by(AuditLog.created_at.desc()).limit(limit).all()
