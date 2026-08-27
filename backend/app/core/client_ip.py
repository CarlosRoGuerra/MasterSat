"""Resolução do IP real do cliente atrás do nginx.

Por que não usar o ``--proxy-headers`` da uvicorn:
    Com ``--forwarded-allow-ips=*`` a uvicorn confia cegamente no header e usa a
    entrada MAIS À ESQUERDA do X-Forwarded-For — que é escrita pelo cliente.
    Qualquer um mandando ``X-Forwarded-For: 1.2.3.4`` passava a ser "1.2.3.4"
    para o rate limiter e para a auditoria: o limite de login virava contornável
    e a trilha de auditoria, forjável.

    Restringir ``--forwarded-allow-ips`` à sub-rede do Docker resolveria, mas a
    uvicorn 0.30.6 (fixada no requirements.txt) é anterior ao suporte a CIDR —
    a máscara nunca casaria e TODAS as requisições cairiam no mesmo balde de
    rate limit (o IP do container do nginx). Por isso resolvemos aqui.

Como o nginx monta o header:
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

    ``$proxy_add_x_forwarded_for`` = "$http_x_forwarded_for, $remote_addr" —
    ou seja, ANEXA o IP real no FIM. A última entrada é a única que o cliente
    não controla; tudo à esquerda dela pode ter vindo forjado na requisição.

    Atrás da Cloudflare o mesmo vale: o ``real_ip_header CF-Connecting-IP`` do
    nginx já reescreveu ``$remote_addr`` para o IP do visitante antes de anexar.
"""
from __future__ import annotations

from starlette.requests import Request
from starlette.types import Scope

_FALLBACK = '127.0.0.1'


def _from_headers(xff: str | None, peer: str | None) -> str:
    """Última entrada do X-Forwarded-For (a que o nginx anexou), ou o peer."""
    if xff:
        entradas = [parte.strip() for parte in xff.split(',') if parte.strip()]
        if entradas:
            return entradas[-1]
    return peer or _FALLBACK


def client_ip_from_scope(scope: Scope) -> str:
    """Versão ASGI crua — usada pelo middleware de auditoria."""
    xff = None
    for nome, valor in scope.get('headers') or []:
        if nome == b'x-forwarded-for':
            xff = valor.decode('latin-1', errors='replace')
            break
    peer = None
    cliente = scope.get('client')
    if cliente:
        peer = cliente[0]
    return _from_headers(xff, peer)


def client_ip(request: Request) -> str:
    """Chave do rate limiter (substitui o get_remote_address do slowapi)."""
    return _from_headers(
        request.headers.get('x-forwarded-for'),
        request.client.host if request.client else None,
    )
