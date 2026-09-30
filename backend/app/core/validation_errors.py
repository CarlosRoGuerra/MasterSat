"""Resposta 422 sem ecoar segredo.

O handler padrão do FastAPI devolve em cada erro o campo ``input`` com o
valor recebido. Para uma senha recusada pela política, isso devolvia a
própria senha no corpo da resposta — que acaba em log de proxy, ferramenta
de suporte ou console do navegador. Em erro de ``model_validator`` (ex.:
"As senhas não conferem") o ``input`` é o corpo inteiro, com as duas senhas.

Aqui o formato do 422 é o mesmo (``{"detail": [...]}``, mesmos ``loc``/
``msg``/``type``); só o valor de campos sensíveis vira ``***``.
"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

SENSITIVE_FIELDS = frozenset({
    'password', 'new_password', 'password_confirmation', 'current_password',
    'senha', 'token', 'refresh_token', 'access_token', 'code', 'state',
    'client_secret', 'secret', 'api_key',
})
MASK = '***'


def _mask(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (MASK if str(k).lower() in SENSITIVE_FIELDS else _mask(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_mask(v) for v in value]
    return value


def sanitize_errors(errors: list[dict]) -> list[dict]:
    limpos = []
    for err in errors:
        err = dict(err)
        loc = [str(part).lower() for part in err.get('loc', ())]
        if 'input' in err:
            err['input'] = MASK if SENSITIVE_FIELDS.intersection(loc) else _mask(err['input'])
        limpos.append(err)
    return limpos


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={'detail': jsonable_encoder(sanitize_errors(list(exc.errors())))},
    )
