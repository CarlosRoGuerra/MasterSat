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
   Num banco recém-migrado a resposta tem de ser `No new upgrade operations
   detected.` — o teste `test_fase02_migrations_postgres.py::test_upgrade_vazio_sem_drift_entre_metadata_e_migrations`
   garante isso. Até a Fase 02, 12 índices criados só em migrations faltavam
   nos models e o autogenerate propunha removê-los (inclusive a unicidade de
   mensalidade).

## Regras para não perder proteção

- **Todo índice/constraint criado numa migration precisa existir no model**,
  com o mesmo nome e forma. Índice parcial: `postgresql_where` **e**
  `sqlite_where` (senão o SQLite dos testes cria o índice sem predicado).
  Índice GIN trigram: `trigram_index()` de `app/models/base.py`.
- **O que o autogenerate não compara**, e por isso fica documentado aqui como
  exceção nominal, gerido só pela migration que o cria:
  - CHECK constraints (o Alembic não as compara; mesmo assim estão declaradas
    nos models para o SQLite dos testes aplicá-las);
  - função `mastersat_competencia(text)`, função `mastersat_billings_competencia()`
    e trigger `trg_billings_competencia` (migration `e5c2a9d71f04`; o
    `create_all` dos testes em PostgreSQL recria as três via evento
    `after_create` em `app/models/billing.py`);
  - extensões `unaccent` e `pg_trgm` (migration `c3f9a1b2d4e6`).
- **Migration que cria garantia sobre dado existente começa por um preflight**
  que lista violações com IDs e valores e aborta **antes** de alterar qualquer
  coisa (o PostgreSQL desfaz a transação inteira). Nunca apaga, cancela ou
  funde títulos para "fazer passar". Exemplo e script de leitura equivalente:
  `e5c2a9d71f04` + `scripts/preflight_fase02.py`.
- **Downgrade não pode destruir dado financeiro para voltar.** Se o schema
  antigo não comporta um dado novo, o downgrade o guarda (ex.:
  `fase02_competencias_liberadas`) ou recusa listando o que impede.

## Testes de migration

`tests/test_fase02_migrations_postgres.py` cria um **banco** novo por teste
no servidor de `TEST_DATABASE_URL` (nunca aponte para um banco com dados) e
passa a conexão para o Alembic por `config.attributes['connection']` — o
`alembic/env.py` usa essa conexão quando ela existe, sem mexer no
`DATABASE_URL` do processo. Cobrem: upgrade vazio sem drift,
base → head → base → head, revisão anterior → head com dados legados,
preflight que aborta sem alterar nada, downgrade recusado e preservação no
rollback.

## Banco novo (setup local ou ambiente novo)

O próprio `on_startup` (`app/main.py::_apply_database_migrations`) já aplica
o Alembic no boot — não precisa rodar `alembic upgrade head` manualmente nem
chamar a aplicação duas vezes. `Base.metadata.create_all()` e
`ensure_schema_updates()` SAÍRAM do `on_startup`; `ensure_schema_updates` continua
no arquivo só como registro histórico (não é mais chamada por ninguém).

`_apply_database_migrations()` decide a cada boot:
- **Banco vazio** (sem `alembic_version` e sem nenhuma tabela da baseline):
  `alembic upgrade head` — cria o schema inteiro a partir das migrations.
- **Banco já carimbado** (produção): `alembic upgrade head`, só o que houver
  de novo.
- **Banco pré-Alembic** (sem `alembic_version`, com schema criado pelo antigo
  `create_all`/`ensure_schema_updates`): o schema é comparado com
  `app/db/manifesto_schema_legado.json` (`app/db/legacy_schema.py`). Só é
  carimbado numa revisão legada (`96f61a589162`, ou `e0905f77f744` se já tem
  `refresh_tokens`) se contiver **todas** as tabelas, colunas e índices que
  ela cria e **nada** que só uma migration posterior cria; depois roda
  `upgrade head`. Caso contrário o boot para com o diagnóstico e não altera
  nada (DB-03). Antes da Fase 02 bastava existir a tabela `users`.

Nunca rode `alembic stamp head` à mão para "destravar": o carimbo diz que
todas as migrations foram aplicadas, e as que não foram (com seus backfills e
preflights) ficam puladas para sempre. Com banco legado que o boot recusou:
backup, completar o schema num ensaio até o diagnóstico aceitar, e só então
subir. O manifesto é regenerado pelas próprias migrations com
`scripts/gerar_manifesto_schema_legado.py` (não deve ser preciso: são
revisões já aplicadas).

## Downgrade e rollback

- `downgrade base` remove também os tipos enum da baseline (antes sobravam e o
  upgrade seguinte falhava — DB-04). Serve para ensaio em banco descartável;
  **em produção apaga todos os dados** e não é rollback.
- Com migrations no boot, a imagem antiga não sobe com uma revisão que ela não
  conhece. Voltar o código exige, nesta ordem: `alembic downgrade <revisão
  anterior>` **com a imagem nova**, depois subir a imagem antiga. Cada
  migration documenta o que o downgrade preserva ou recusa (ex.:
  `e5c2a9d71f04` guarda as competências liberadas e recusa se uma delas já foi
  recobrada). Ensaio completo em `docs/validacao/fase-02/ensaio-rollback.txt`.
- Prefira correção para frente. Downgrade é para quando o código novo não
  pode ficar no ar.

## Se importa modelos fora do FastAPI (scripts standalone)

Use `from app.models import registry_all` (não `app.models` sozinho) — é o
único import que garante que **todos** os modelos, incluindo
`multiportal_outbox`, estão registrados no `Base.metadata` antes de qualquer
`db.add()`/`flush()`. Faltar um aqui é a causa mais comum de
`NoReferencedTableError`.
