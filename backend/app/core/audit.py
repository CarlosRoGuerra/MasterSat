"""
Middleware de auditoria.

Intercepta todas as requisições autenticadas e registra na tabela audit_logs:
- Quem fez (user_id, name, role)
- O quê (method, path, entity_type, entity_id)
- Quando (created_at via TimestampMixin)
- De onde (ip_address)
- Resultado (status_code)
"""
from __future__ import annotations

import re

from jose import JWTError, jwt
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.audit_log import AuditLog

# Paths que não devem ser logados (logs sobre logs = loop, health check)
_SKIP_PATHS = re.compile(r'^(/api/v1/audit-logs|/|/docs|/openapi\.json|/redoc)')

# Mapeia segmento de URL → nome legível da entidade
_ENTITY_MAP: dict[str, str] = {
    'clients':         'cliente',
    'vehicles':        'veiculo',
    'trackers':        'rastreador',
    'contracts':       'contrato',
    'billings':        'cobranca',
    'service-orders':  'ordem_servico',
    'plans':           'plano',
    'users':           'usuario',
    'documents':       'documento',
    'integration':     'integracao',
    'auth':            'autenticacao',
    'dashboard':       'dashboard',
}

# Descrições legíveis por método
_METHOD_DESC: dict[str, str] = {
    'GET':    'Consultou',
    'POST':   'Criou',
    'PUT':    'Editou',
    'PATCH':  'Atualizou',
    'DELETE': 'Removeu',
}


def _extract_entity(path: str) -> tuple[str | None, int | None]:
    """Extrai (entity_type, entity_id) de um path como /api/v1/clients/42/documents."""
    segments = [s for s in path.split('/') if s and s not in ('api', 'v1')]
    entity_type: str | None = None
    entity_id: int | None = None

    for i, seg in enumerate(segments):
        mapped = _ENTITY_MAP.get(seg)
        if mapped:
            entity_type = mapped
            # Próximo segmento pode ser um ID numérico
            if i + 1 < len(segments):
                try:
                    entity_id = int(segments[i + 1])
                except ValueError:
                    pass
            break

    return entity_type, entity_id


def _build_description(method: str, entity_type: str | None, entity_id: int | None, path: str) -> str:
    action = _METHOD_DESC.get(method, method)
    if not entity_type:
        return f'{action} {path}'
    if entity_id:
        return f'{action} {entity_type} #{entity_id}'
    # Sub-operações (ex.: link-vehicle, generate-billings)
    extra = ''
    for keyword in ('link-vehicle', 'generate-billings', 'sync', 'review', 'timeline-pdf'):
        if keyword in path:
            extra = f' ({keyword})'
            break
    return f'{action} {entity_type}{extra}'


def _get_client_ip(request: Request) -> str | None:
    forwarded = request.headers.get('x-forwarded-for')
    if forwarded:
        return forwarded.split(',')[0].strip()
    if request.client:
        return request.client.host
    return None


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        path = request.url.path
        if _SKIP_PATHS.match(path):
            return response

        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return response

        token = auth_header[7:]
        user_id: int | None = None
        user_name: str | None = None
        user_role: str | None = None

        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
            raw_id = payload.get('sub')
            if raw_id:
                user_id = int(raw_id)
            user_name = payload.get('name')
            user_role = payload.get('role')
        except (JWTError, ValueError):
            return response

        # Se o token não tem name/role embutidos, tentamos buscar do DB
        if (user_name is None or user_role is None) and user_id:
            try:
                db: Session = SessionLocal()
                try:
                    from app.models.user import User
                    user = db.get(User, user_id)
                    if user:
                        user_name = user.name
                        user_role = user.role.value if hasattr(user.role, 'value') else str(user.role)
                finally:
                    db.close()
            except Exception:  # noqa: BLE001
                pass

        method = request.method.upper()
        entity_type, entity_id = _extract_entity(path)
        description = _build_description(method, entity_type, entity_id, path)
        ip_address = _get_client_ip(request)

        try:
            db = SessionLocal()
            try:
                log = AuditLog(
                    user_id=user_id,
                    user_name=user_name,
                    user_role=user_role,
                    method=method,
                    path=path,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    status_code=response.status_code,
                    ip_address=ip_address,
                    description=description,
                )
                db.add(log)
                db.commit()
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            pass  # Auditoria nunca pode derrubar a resposta principal

        return response
