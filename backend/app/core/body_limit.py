"""Limite global de tamanho de requisição (proteção contra upload gigante).

Middleware ASGI puro (mesmo motivo do AuditMiddleware: BaseHTTPMiddleware tem
bugs conhecidos no Starlette). Rejeita cedo, pelo Content-Length, qualquer
requisição acima do teto — antes de o corpo ser lido para a memória.
"""
from __future__ import annotations


class MaxBodySizeMiddleware:
    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'http':
            for name, value in scope.get('headers') or []:
                if name == b'content-length':
                    try:
                        declared = int(value)
                    except (ValueError, TypeError):
                        break
                    if declared > self.max_bytes:
                        await self._too_large(send)
                        return
                    break
        await self.app(scope, receive, send)

    async def _too_large(self, send) -> None:
        body = b'{"detail":"Arquivo ou requisicao excede o tamanho maximo permitido."}'
        await send({
            'type': 'http.response.start',
            'status': 413,
            'headers': [
                (b'content-type', b'application/json'),
                (b'content-length', str(len(body)).encode()),
            ],
        })
        await send({'type': 'http.response.body', 'body': body})
