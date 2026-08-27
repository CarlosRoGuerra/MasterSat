# Migrations (Alembic)

A partir da migration `96f61a589162` (baseline), toda mudança de schema é uma
migration versionada aqui — não mais um `ALTER TABLE` manual em
`app/main.py::ensure_schema_updates` (essa função fica congelada, só como
histórico do que já existia antes do Alembic).

## Fluxo do dia a dia

1. Mude o(s) model(s) em `app/models/`.
2. Gere a migration a partir do diff entre os models e o banco:
   ```
   docker compose exec backend alembic revision --autogenerate -m "descrição curta"
   ```
3. **Leia o arquivo gerado em `alembic/versions/`** antes de aplicar — o
   autogenerate detecta a maioria das mudanças de coluna/índice/FK, mas não
   detecta tudo (ex.: renomear uma coluna aparece como "dropar + criar";
   mudanças de `server_default` às vezes precisam de ajuste manual).
4. Aplique:
   ```
   docker compose exec backend alembic upgrade head
   ```
5. Para conferir se o banco está em sincronia com os models sem gerar nada:
   ```
   docker compose exec backend alembic check
   ```

## Banco novo (setup local ou ambiente novo)

`alembic upgrade head` sozinho já cria o schema completo — não precisa mais
rodar a aplicação primeiro. `Base.metadata.create_all()` e
`ensure_schema_updates()` continuam no `on_startup` só por compatibilidade com
ambientes que ainda não foram carimbados (`alembic stamp head`); em um banco
vazio + já migrado pelo Alembic, ambos são no-op.

## Se importa modelos fora do FastAPI (scripts standalone)

Use `from app.models import registry_all` (não `app.models` sozinho) — é o
único import que garante que **todos** os modelos, incluindo
`multiportal_outbox`, estão registrados no `Base.metadata` antes de qualquer
`db.add()`/`flush()`. Faltar um aqui é a causa mais comum de
`NoReferencedTableError`.
