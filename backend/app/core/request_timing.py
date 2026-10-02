"""Per-request timing and SQL count without logging request data."""

import logging
import time
from contextvars import ContextVar
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.engine import Engine
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)
_query_counter: ContextVar[list[int] | None] = ContextVar('request_query_counter', default=None)


@event.listens_for(Engine, 'before_cursor_execute')
def _count_query(_conn, _cursor, _statement, _parameters, _context, _executemany):
    counter = _query_counter.get()
    if counter is not None:
        counter[0] += 1


class RequestTimingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        request_id = uuid4().hex
        start = time.perf_counter()
        counter = [0]
        token = _query_counter.set(counter)

        async def send_timing(message: dict) -> None:
            if message['type'] == 'http.response.start':
                duration_ms = (time.perf_counter() - start) * 1000
                headers = list(message.get('headers', []))
                headers.append((b'x-request-id', request_id.encode('ascii')))
                headers.append((b'server-timing', f'app;dur={duration_ms:.1f};desc="queries={counter[0]}"'.encode('ascii')))
                message = {**message, 'headers': headers}
                if duration_ms >= 500:
                    route = scope.get('route')
                    route_path = getattr(route, 'path', 'unmatched')
                    logger.warning(
                        'Slow request method=%s route=%s status=%s duration_ms=%.1f query_count=%s request_id=%s',
                        scope.get('method'), route_path, message.get('status'),
                        duration_ms, counter[0], request_id,
                    )
            await send(message)

        try:
            await self.app(scope, receive, send_timing)
        finally:
            _query_counter.reset(token)
