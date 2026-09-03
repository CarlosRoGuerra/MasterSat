from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.user import User
from app.schemas.search import GlobalSearchOut
from app.services.global_search import STAFF_ROLES, run_global_search

router = APIRouter()


@router.get('/', response_model=GlobalSearchOut)
def global_search(
    q: str = Query(default='', max_length=200),
    limit: int = Query(default=6, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*STAFF_ROLES)),
):
    return run_global_search(db, current_user.role, q, limit)
