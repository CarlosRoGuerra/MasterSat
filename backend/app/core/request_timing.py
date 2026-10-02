"""Tempo de resposta da API para localizar consultas lentas em qualquer tela."""

import logging
from time import perf_counter

from starlette.types import ASGIApp, Receive, Scope, Send


logger = logging.getLogger('uvicorn.error')


class RequestTimingMiddleware:
    def __init__(self, app: ASGIApp, slow_ms: float = 500) -> None:
        self.app = app
        self.slow_ms = slow_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        started = perf_counter()

        async def send_timed(message: dict) -> None:
            if message['type'] == 'http.response.start':
                elapsed_ms = (perf_counter() - started) * 1000
                headers = list(message.get('headers', []))
                headers.append((b'server-timing', f'app;dur={elapsed_ms:.1f}'.encode('ascii')))
                message['headers'] = headers
                if elapsed_ms >= self.slow_ms:
                    # Não registra query string: buscas podem conter dados pessoais.
                    logger.warning(
                        'API lenta: %s %s -> %s em %.0f ms',
                        scope.get('method'), scope.get('path'), message['status'], elapsed_ms,
                    )
            await send(message)

        await self.app(scope, receive, send_timed)
