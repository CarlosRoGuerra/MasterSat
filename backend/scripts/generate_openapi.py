"""Exporta o schema OpenAPI da aplicação para um arquivo JSON.

Uso (dentro do container, onde as dependências pinadas em requirements.txt
estão instaladas — rodar com o python do host pode gerar um schema
ligeiramente diferente por causa da versão do FastAPI):

    docker compose exec backend python scripts/generate_openapi.py

O arquivo gerado alimenta o openapi-typescript no frontend
(ver frontend/package.json → "generate:api-types"). Não expõe rede nem
depende de ENABLE_DOCS: chama app.openapi() diretamente em processo, o
mesmo método que o FastAPI usa internamente para servir /openapi.json.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from app.main import app  # noqa: E402

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "openapi.json"


def main() -> None:
    schema = app.openapi()
    OUTPUT_PATH.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OpenAPI schema escrito em {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
