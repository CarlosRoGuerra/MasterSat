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

O próprio `on_startup` (`app/main.py::_apply_database_migrations`) já aplica
o Alembic no boot — não precisa rodar `alembic upgrade head` manualmente nem
chamar a aplicação duas vezes. `Base.metadata.create_all()` e
`ensure_schema_updates()` SAÍRAM do `on_startup`; `ensure_schema_updates` continua
no arquivo só como registro histórico (não é mais chamada por ninguém).

`_apply_database_migrations()` decide sozinho entre `upgrade` e `stamp` a cada
boot:
- **Banco vazio de verdade** (sem `alembic_version` e sem a tabela `users`):
  roda `alembic upgrade head` — cria o schema inteiro a partir das migrations.
- **Banco pré-Alembic** (sem `alembic_version`, mas com o schema já criado
  pelo antigo `create_all`/`ensure_schema_updates`): roda `alembic stamp head`
  — só grava a revisão atual, sem tentar recriar tabela nenhuma (senão falharia
  com "relation already exists"). É o caso da produção até o primeiro deploy
  com esta mudança.
- **Banco já carimbado**: roda `alembic upgrade head` normalmente, aplicando
  só o que houver de novo.

Isso cobre o cutover automático de QUALQUER ambiente na primeira vez que subir
com esta versão — não precisa de um `alembic stamp head` manual antes do
deploy. (Se algum banco ficar com o `alembic_version` desatualizado por ter
rodado `create_all` em paralelo por um tempo — como aconteceu no banco de dev
durante o desenvolvimento desta migração — corrija com um `alembic stamp head`
manual uma única vez; o boot seguinte já roda `upgrade head` normalmente.)

## Se importa modelos fora do FastAPI (scripts standalone)

Use `from app.models import registry_all` (não `app.models` sozinho) — é o
único import que garante que **todos** os modelos, incluindo
`multiportal_outbox`, estão registrados no `Base.metadata` antes de qualquer
`db.add()`/`flush()`. Faltar um aqui é a causa mais comum de
`NoReferencedTableError`.
