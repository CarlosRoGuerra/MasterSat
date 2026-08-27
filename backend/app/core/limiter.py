"""
Rate limiting com slowapi.

Limites configurados:
  - Login:    5 req/min   (anti-brute-force)
  - Exports:  10 req/min  (operações pesadas)
  - Default:  200 req/min (geral por IP)

Uso nos endpoints:
    from app.core.limiter import limiter

    @router.post('/login')
    @limiter.limit("5/minute")
    def login(request: Request, ...):
        ...
"""
from slowapi import Limiter

from app.core.client_ip import client_ip
from app.core.config import settings

# key_func=client_ip (e nao o get_remote_address do slowapi): o
# get_remote_address le request.client.host, que a uvicorn preenche com a
# entrada MAIS A ESQUERDA do X-Forwarded-For quando roda com
# --forwarded-allow-ips=*. Essa entrada e escrita pelo cliente, entao bastava
# variar o header a cada tentativa para zerar o balde e derrubar o limite de
# 5/min do login. Ver app/core/client_ip.py.
#
# storage_uri=redis: sem isto, o Limiter guarda os contadores em memória do
# próprio processo. Com --workers N (uvicorn), cada worker tem sua memória
# separada — o limite de "5/minute" no login virava ~5*N/minute agregado,
# porque cada worker contava as requisições que ELE recebeu, não o total.
limiter = Limiter(
    key_func=client_ip,
    default_limits=['200/minute'],
    storage_uri=settings.redis_url,
    # headers_enabled requer response: Response em cada endpoint — desabilitado para evitar
    # "parameter `response` must be an instance of starlette.responses.Response"
    headers_enabled=False,
)
