"""
Middleware de auditoria.

Registra toda requisição autenticada na tabela audit_logs.

Correções aplicadas em relação à versão anterior:
- name e role agora são lidos diretamente do JWT (não há mais query extra ao banco)
- A escrita no banco ocorre via BackgroundTask, fora do event loop asyncio
  (evita deadlocks do connection pool com psycopg3 em contexto async)
- Erros de escrita são logados em stderr em vez de engolidos silenciosamente
"""
from __future__ import annotations

import logging
import re
from typing import Any

from jose import JWTError, jwt
from starlette.background import BackgroundTask
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.config import settings

logger = logging.getLogger(__name__)

_SKIP_PATHS = re.compile(
    r'^(/api/v1/audit-logs|/|/docs|/openapi\.json|/redoc|/favicon\.ico)'
)

_ENTITY_MAP: dict[str, str] = {
    'clients':        'cliente',
    'vehicles':       'veiculo',
    'trackers':       'rastreador',
    'contracts':      'contrato',
    'billings':       'cobranca',
    'service-orders': 'ordem_servico',
    'plans':          'plano',
    'users':          'usuario',
    'documents':      'documento',
    'integration':    'integracao',
    'integrations':   'integracao',
    'auth':           'autenticacao',
    'dashboard':      'dashboard',
}

_METHOD_DESC: dict[str, str] = {
    'GET':    'Consultou',
    'POST':   'Criou',
    'PUT':    'Editou',
    'PATCH':  'Atualizou',
    'DELETE': 'Removeu',
}


def _extract_entity(path: str) -> tuple[str | None, int | None]:
    segments = [s for s in path.split('/') if s and s not in ('api', 'v1')]
    for i, seg in enumerate(segments):
        mapped = _ENTITY_MAP.get(seg)
        if mapped:
            entity_id: int | None = None
            if i + 1 < len(segments):
                try:
                    entity_id = int(segments[i + 1])
                except ValueError:
                    pass
            return mapped, entity_id
    return None, None


def _build_description(method: str, entity_type: str | None, entity_id: int | None, path: str) -> str:
    action = _METHOD_DESC.get(method, method)
    if not entity_type:
        return f'{action} {path}'
    if entity_id:
        return f'{action} {entity_type} #{entity_id}'
    for kw in ('link-vehicle', 'generate-billings', 'sync-flow', 'sync-equipment',
               'sync-chip', 'review', 'timeline-pdf', 'uninstall'):
        if kw in path:
            return f'{action} {entity_type} ({kw})'
    return f'{action} {entity_type}'


def _get_ip(request: Request) -> str | None:
    forwarded = request.headers.get('x-forwarded-for')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.client.host if request.client else None


def _write_log(
    user_id: int | None,
    user_name: str | None,
    user_role: str | None,
    method: str,
    path: str,
    entity_type: str | None,
    entity_id: int | None,
    status_code: int | None,
    ip_address: str | None,
    description: str,
) -> None:
    """Executa em BackgroundTask (thread separada) — nunca bloqueia o event loop."""
    from app.db.session import SessionLocal
    from app.models.audit_log import AuditLog

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
                status_code=status_code,
                ip_address=ip_address,
                description=description,
            )
            db.add(log)
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception('Falha ao gravar registro de auditoria')


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

        # Decodifica o JWT — name e role estão embutidos desde a versão corrigida
        try:
            payload = jwt.decode(
                token,
                settings.secret_key,
                algorithms=[settings.algorithm],
            )
            if payload.get('type') != 'access':
                return response
        except JWTError:
            return response

        raw_id = payload.get('sub')
        user_id: int | None = int(raw_id) if raw_id else None
        user_name: str | None = payload.get('name')
        user_role: str | None = payload.get('role')

        method = request.method.upper()
        entity_type, entity_id = _extract_entity(path)
        description = _build_description(method, entity_type, entity_id, path)
        ip_address = _get_ip(request)

        # Agenda escrita em BackgroundTask — roda após a resposta, em thread separada
        # BackgroundTask executa funções síncronas via run_in_threadpool (sem bloquear o event loop)
        audit_task = BackgroundTask(
            _write_log,
            user_id, user_name, user_role,
            method, path,
            entity_type, entity_id,
            response.status_code,
            ip_address, description,
        )

        existing = getattr(response, 'background', None)
        if existing is None:
            response.background = audit_task
        else:
            # Encadeia preservando a task original
            _prev = existing

            async def _chain() -> None:
                await _prev()
                await audit_task()

            response.background = _chain  # type: ignore[assignment]

        return response
