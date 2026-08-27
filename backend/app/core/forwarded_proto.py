"""Corrige scope['scheme'] a partir do X-Forwarded-Proto enviado pelo nginx.

Middleware ASGI puro (mesmo motivo do AuditMiddleware/MaxBodySizeMiddleware:
BaseHTTPMiddleware tem bugs conhecidos no Starlette).

Por que isto é necessário: nginx termina o TLS e fala com o backend em HTTP
puro dentro da rede do Docker (proxy_pass http://backend:8000). Sem correção,
a uvicorn enxerga TODA requisição como 'http', mesmo vindo de
https://app.mastersat.com.br. Isso não afeta o conteúdo das respostas, mas
afeta qualquer URL ABSOLUTA que o Starlette gera sozinho a partir do scope —
o caso real que quebrou em produção: uma rota registrada como
`@router.get('/')` sob um prefixo (ex.: dashboard, clients, vehicles...)
responde a `/api/v1/dashboard` (sem barra) com um redirect 307 automático
para `/api/v1/dashboard/`; o Location desse redirect vem como
`http://api.mastersat.com.br/...` — e o navegador BLOQUEIA isso como mixed
content numa página https://, porque o scheme errado veio do próprio scope.

Por que NÃO usar `uvicorn --proxy-headers --forwarded-allow-ips=...` em vez
disto: esse flag também passa a confiar no X-Forwarded-For para
`request.client.host`, usando a entrada MAIS A ESQUERDA do header (forjável
pelo cliente) — exatamente o problema que app/core/client_ip.py foi escrito
para evitar (ele lê a ÚLTIMA entrada, a que o nginx anexa). Este middleware
só toca scope['scheme']; scope['client'] nunca é alterado, então o rate
limit e a auditoria continuam seguros exatamente como estão.

Seguro por construção: o backend só é alcançável pela rede interna do
Docker (a porta não é publicada — ver docker-compose.prod.yml), então o
único emissor possível de X-Forwarded-Proto é o próprio nginx.
"""
from __future__ import annotations


class ForwardedProtoMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'http':
            for name, value in scope.get('headers') or []:
                if name == b'x-forwarded-proto':
                    proto = value.decode('latin-1').split(',')[0].strip().lower()
                    if proto in ('http', 'https'):
                        scope = {**scope, 'scheme': proto}
                    break
        await self.app(scope, receive, send)
