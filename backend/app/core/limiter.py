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
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=['200/minute'],
    # Em produção com nginx: respeita X-Forwarded-For
    headers_enabled=True,
)
