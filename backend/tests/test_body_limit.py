"""Middleware que barra requisições acima do teto (anti-DoS por upload gigante)."""
from __future__ import annotations

import asyncio

from app.core.body_limit import MaxBodySizeMiddleware


class _FakeApp:
    def __init__(self) -> None:
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True


async def _call(mw, content_length):
    sent = []

    async def receive():
        return {'type': 'http.request', 'body': b''}

    async def send(message):
        sent.append(message)

    scope = {'type': 'http', 'headers': [(b'content-length', str(content_length).encode())]}
    await mw(scope, receive, send)
    return sent


def test_rejeita_acima_do_limite():
    app = _FakeApp()
    sent = asyncio.run(_call(MaxBodySizeMiddleware(app, max_bytes=100), 200))
    assert app.called is False          # nem chega no app
    assert sent[0]['status'] == 413


def test_passa_abaixo_do_limite():
    app = _FakeApp()
    asyncio.run(_call(MaxBodySizeMiddleware(app, max_bytes=100), 50))
    assert app.called is True
