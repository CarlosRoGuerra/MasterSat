"""
Middleware de auditoria — implementação ASGI pura.

Por que não BaseHTTPMiddleware:
  BaseHTTPMiddleware tem um bug documentado no Starlette onde response.background
  definido dentro de dispatch pode ser silenciosamente ignorado dependendo da versão.

Esta implementação:
  - Usa o protocolo ASGI diretamente (nenhuma dependência de BaseHTTPMiddleware)
  - Captura o status_code do próprio cabeçalho da resposta
  - Registra o log APÓS a resposta ser enviada ao cliente
  - Usa asyncio.create_task + asyncio.to_thread para não bloquear o event loop
  - Nunca interrompe a resposta principal; erros de auditoria vão apenas ao log
"""
from __future__ import annotations

import asyncio
import logging

from jose import JWTError, jwt
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.client_ip import client_ip_from_scope
from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths excluídos do registro
# ---------------------------------------------------------------------------
# Paths que nunca devem gerar registro de auditoria.
# ATENÇÃO: evitar o padrão '/' solto — casaria com QUALQUER caminho.
_SKIP_PREFIXES = (
    '/api/v1/audit-logs',
    '/docs',
    '/redoc',
    '/openapi',
)
_SKIP_EXACT = {'/', '/favicon.ico'}

# ---------------------------------------------------------------------------
# Mapeamento URL → entidade legível
# ---------------------------------------------------------------------------
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
    'ailos':          'ailos',
}

_METHOD_DESC: dict[str, str] = {
    'GET':    'Consultou',
    'POST':   'Criou',
    'PUT':    'Editou',
    'PATCH':  'Atualizou',
    'DELETE': 'Removeu',
}

# Verbo específico por sub-ação (trecho do path) — sobrepõe o verbo genérico
# do método HTTP quando a rota não é um CRUD simples (ex.: POST .../swap não
# "cria" nada, é uma troca). Precisa ter prioridade sobre entity_id em
# _build_description: como praticamente toda sub-rota já tem o id logo após
# o segmento da entidade (".../trackers/5/swap"), checar entity_id primeiro
# faria essa lista nunca ser consultada de fato.
_ACTION_VERBS: dict[str, str] = {
    'link-vehicle':      'Vinculou',
    'swap':               'Trocou',
    'uninstall':          'Desinstalou',
    'generate-billings':  'Gerou cobranças de',
    'sync-flow':          'Sincronizou',
    'sync-equipment':     'Sincronizou equipamento de',
    'sync-chip':          'Sincronizou chip de',
    'review':             'Revisou',
    'timeline-pdf':       'Gerou timeline de',
    'generate-document':  'Gerou documento de',
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
    if not entity_type:
        action = _METHOD_DESC.get(method, method)
        return f'{action} {path}'

    action = next((v for kw, v in _ACTION_VERBS.items() if kw in path), None) or _METHOD_DESC.get(method, method)

    if entity_id:
        return f'{action} {entity_type} #{entity_id}'
    return f'{action} {entity_type}'


# ---------------------------------------------------------------------------
# Escrita síncrona no banco — executada em thread pool pelo asyncio
# ---------------------------------------------------------------------------

def _write_log(
    user_id: int | None,
    user_name: str | None,
    user_role: str | None,
    method: str,
    path: str,
    entity_type: str | None,
    entity_id: int | None,
    status_code: int,
    ip_address: str | None,
    description: str,
) -> None:
    """
    Função síncrona executada em thread pool (via asyncio.to_thread).

    Se name/role não estiverem no token (tokens antigos), busca no banco
    antes de persistir o log — tudo em uma única sessão.
    """
    from app.db.session import SessionLocal
    from app.models.audit_log import AuditLog
    from app.models.user import User

    db = SessionLocal()
    try:
        # Enriquece com dados do usuário caso o token antigo não os contenha
        if user_id and (user_name is None or user_role is None):
            user_obj = db.get(User, user_id)
            if user_obj:
                _user_name = user_name or user_obj.name
                _user_role = user_role or (
                    user_obj.role.value if hasattr(user_obj.role, 'value') else str(user_obj.role)
                )
            else:
                _user_name, _user_role = user_name, user_role
        else:
            _user_name, _user_role = user_name, user_role

        db.add(AuditLog(
            user_id=user_id,
            user_name=_user_name,
            user_role=_user_role,
            method=method,
            path=path,
            entity_type=entity_type,
            entity_id=entity_id,
            status_code=status_code,
            ip_address=ip_address,
            description=description,
        ))
        db.commit()
    except Exception:
        logger.exception('Erro ao gravar log de auditoria no banco')
        try:
            db.rollback()
        except Exception:  # noqa: BLE001 — já estamos no tratamento de erro; não há mais para onde escalar
            logger.warning('Rollback do log de auditoria também falhou (conexão pode estar comprometida)', exc_info=True)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Middleware ASGI puro
# ---------------------------------------------------------------------------

class AuditMiddleware:
    """
    Middleware ASGI puro que não usa BaseHTTPMiddleware.

    Fluxo:
    1. Extrai Authorization dos headers ANTES de chamar o app
    2. Captura o status_code do message 'http.response.start'
    3. Após a resposta ser enviada, agenda asyncio.create_task com o log
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        # Lê headers da requisição (bytes)
        raw_headers: dict[bytes, bytes] = {
            k.lower(): v for k, v in scope.get('headers', [])
        }
        auth_bytes = raw_headers.get(b'authorization', b'')
        auth_header = auth_bytes.decode('latin-1', errors='replace')

        path: str = scope.get('path', '')
        method: str = scope.get('method', 'GET').upper()

        # Captura status_code do response.start
        status_code = 0

        async def send_capture(message: dict) -> None:
            nonlocal status_code
            if message.get('type') == 'http.response.start':
                status_code = message.get('status', 0)
            await send(message)

        # Executa a aplicação
        await self.app(scope, receive, send_capture)

        # ── A partir daqui a resposta já foi enviada ao cliente ──

        if path in _SKIP_EXACT or any(path.startswith(p) for p in _SKIP_PREFIXES):
            return

        if not auth_header.startswith('Bearer '):
            return

        token = auth_header[7:]

        try:
            payload = jwt.decode(
                token,
                settings.secret_key,
                algorithms=[settings.algorithm],
            )
            if payload.get('type') != 'access':
                return
        except JWTError:
            return

        raw_id = payload.get('sub')
        user_id: int | None = int(raw_id) if raw_id else None
        user_name: str | None = payload.get('name')
        user_role: str | None = payload.get('role')

        entity_type, entity_id = _extract_entity(path)
        description = _build_description(method, entity_type, entity_id, path)

        # IP do cliente — ULTIMA entrada do X-Forwarded-For, nao a primeira.
        # O nginx ANEXA o IP real no fim ($proxy_add_x_forwarded_for), entao a
        # ultima entrada e a unica que o cliente nao controla. Pegar [0] pegava
        # exatamente o que o cliente escreveu: bastava mandar
        # "X-Forwarded-For: 8.8.8.8" para gravar 8.8.8.8 na trilha de auditoria.
        ip_address: str | None = client_ip_from_scope(scope)

        # A resposta já foi enviada ao cliente no await acima.
        # Rodamos o log em thread pool (asyncio.to_thread) sem bloquear o event loop
        # e sem depender de fire-and-forget (mais confiável em todos os servidores).
        await asyncio.to_thread(
            _write_log,
            user_id, user_name, user_role,
            method, path,
            entity_type, entity_id,
            status_code, ip_address, description,
        )
