# Validação — Fase 01 (autorização, identidade e dependências alcançáveis)

- **Data:** 30/09/2026
- **Branch:** `fase-01-autorizacao-identidade`, criada sobre
  `fase-00-backup-baseline` (`6706cc2`). A Fase 00 ainda não foi mergeada e é
  pré-requisito: baseline de testes e gate `checar_baseline_testes.py`.
- **Commit da auditoria:** `28af706`. Cada achado foi confirmado no HEAD antes
  de editar; todos continuavam presentes.
- **SHA validado:** `ddfe47a` (código). A documentação vem no commit seguinte.
- **Evidências brutas:** [fase-01/](fase-01/).

Nenhum comando usou produção, `.env` real, serviço externo, e-mail real ou
chamada à Ailos. Backend em containers com a cópia do `backend/` sem `.env`;
PostgreSQL 16, Redis e MinIO descartáveis em rede Docker `--internal`.

## 1. Situação dos achados

| ID | Status | O que foi feito | O que falta |
|---|---|---|---|
| **SEC-01** | **Resolvido** | `core/permissions.py` com capacidades. Exports financeiros exigem `FINANCIAL_READ`; o PDF da timeline, o dashboard e `delinquency/status` filtram no backend; a tela do dashboard foi adaptada. Matriz em [matriz-capacidades.md](../seguranca/matriz-capacidades.md) | Empresa confirmar a proposta (operacional sem valores, com contagem de inadimplentes) e decidir planos/produtos (R1) |
| **SEC-02** | **Resolvido** | Rotação por compare-and-set; janela de 15 s responde 409 sem revogar; reuso depois disso revoga a família. O frontend trata o 409 entre abas | — |
| **SEC-03** | **Resolvido** | Bootstrap só com banco sem usuários, senha pela política, nada em log. Recuperação explícita e auditada em `scripts/reset_admin_senha.py`, com revogação de sessões | Troca obrigatória no primeiro acesso não existe (ver SEC-04) |
| **SEC-04** | **Resolvido** | `core/password_policy.py` aplicada em criação, edição, reset, cadastro, bootstrap e script. Teto de 72 bytes; 422 sem ecoar segredo | Opcional: forçar troca de senhas antigas fracas (decisão da empresa) |
| **SEC-05** | **Resolvido no código / depende de operação** | E-mail pelo SMTP do painel com 3 tentativas; token só em SHA-256; resposta uniforme; limites por IP e por conta; consumo atômico | **Configurar o SMTP em produção** (sem ele o e-mail não sai; o log registra erro) |
| **SEC-06** | **Resolvido** | `sid` no access; logout por sessão; troca de senha revoga todas as sessões | — |
| **FIN-09** | **Resolvido** (escopo da fase) | State com prazo de 15 min e uso único atômico; renovação do token do cooperado serializada e deduplicada | "Singleton por ambiente/convênio" e rotação da chave Fernet não entraram nesta fase |
| **DEP-01** (HTTP/PDF/JWT) | **Resolvido** | FastAPI 0.142.2 / Starlette 1.7.0, multipart 0.0.32, pypdf 6.19.0, PyJWT no lugar do python-jose, requests 2.33.0, pip da imagem | Pilha XMLDSig (cryptography, pyOpenSSL, signxml, lxml, zeep) fica para a **Fase 05** |
| **FE-05** | **Resolvido** | Next 15.5.26 e transitivas; `npm audit` 10 → 0 | Remover o override de postcss ao migrar para o Next 16 |

Achados de efeito colateral corrigidos nesta fase:

- **422 ecoava a senha recusada.** O handler padrão do FastAPI devolvia o
  `input`. Corrigido em `core/validation_errors.py`.
- **`password: null` na edição de usuário dava 500.** Agora mantém a senha
  atual.
- **Logs sumiam depois do boot.** O `fileConfig` do alembic desligava os
  loggers. Corrigido em `alembic/env.py`; detalhes em
  [dependencias-fase-01.md](../seguranca/dependencias-fase-01.md).

## 2. Dados afetados e migration

A migration `d9e4f1a7b2c5` é aditiva:

- `refresh_tokens.rotated_at`;
- `password_reset_tokens.token_hash`, `sent_at`, `delivery_attempts` e
  `delivery_error`; a coluna `token` passa a aceitar nulo;
- `ailos_integrations.state_expires_at`.

| Dado existente | Efeito |
|---|---|
| Pedidos de reset pendentes | convertidos para SHA-256 e com o texto puro apagado. **Continuam válidos até expirar** (≤30 min) |
| States Ailos pendentes | ficam sem prazo e passam a ser tratados como expirados. Basta clicar de novo em "Reconectar Ailos" |
| Access tokens sem `sid` | recusados; o frontend renova pelo cookie sem novo login |
| Refresh tokens, usuários, senhas, segredos Fernet, dados financeiros | inalterados |

Ensaio up → backfill → down → up em PostgreSQL 16:
[migration-up-down-up.txt](fase-01/migration-up-down-up.txt). `alembic check`
não mostra nenhuma operação nova vinda desta migration; a divergência de
índices que aparece é a mesma registrada pela auditoria.

## 3. Testes

**Versões:**

- backend: Python 3.12.14, com a imagem `python:3.12-slim` +
  `requirements.txt` + `requirements-test.txt`;
- PostgreSQL 16;
- frontend: Node 20.20.2 / npm 10.8.2 (`node:20-alpine`).

| Comando (diretório) | Resultado |
|---|---|
| `python -m pytest tests/test_auth_api.py tests/test_security_hardening.py tests/test_users_api.py tests/test_exports_api.py tests/test_ailos_connect_callback.py -q` (backend) | 130 aprovados, 1 falha: `TestRegisterClient::test_always_returns_403`, **V1 conhecida da baseline** |
| `python -m pytest tests -q` (backend, SQLite + `TEST_DATABASE_URL` PostgreSQL) | **2033 executados: 1995 aprovados, 38 falhas, 0 skips** |
| `python scripts/checar_baseline_testes.py junit.xml` | **BASELINE OK**: as 38 falhas são exatamente as triadas da Fase 00 |
| `npm ci` / `lint` / `typecheck` / `test` / `build` (frontend) | todos com saída 0. Lint com 0 erros e 41 avisos (igual à baseline); 172/172 testes |
| `npm audit --json` | 0 vulnerabilidades (antes: 10) |
| `python -m pip_audit -f json` (imagem do `backend/Dockerfile`) | restam só os 5 pacotes da pilha XMLDSig da Fase 05 |
| Imagem do backend no boot real, com PG/Redis/MinIO descartáveis | migrations até `d9e4f1a7b2c5`; admin provisionado; login, refresh, 409, logout → 401, forgot e 422 sem eco verificados; senha ausente do log |
| Imagem do frontend (`target runner`) | build ok, next 15.5.26 dentro da imagem |

A diferença de 1848 para 1995 aprovados vem dos **147 testes novos da fase**,
todos aprovados, em `tests/test_fase01_*.py`:

| Arquivo | Cobre |
|---|---|
| `test_fase01_capacidades.py` | matriz admin/operacional/financeiro/cliente/anônimo × JSON/CSV/XLSX/PDF com marcador financeiro sintético |
| `test_fase01_refresh_rotacao.py` | janela de 409; reuso revoga a família; **duas rotações concorrentes no PostgreSQL com barreira** |
| `test_fase01_sessoes.py` | logout invalida o access; outro dispositivo continua logado; logout só com Bearer; token legado; `sid` de outro usuário; troca de senha |
| `test_fase01_senha.py` | vazia, curta, Unicode, 72/73 bytes, NUL, em todas as entradas; senha ausente da resposta e do log |
| `test_fase01_bootstrap.py` | restart com admin excluído (a exclusão persiste, tokens antigos recusados); bootstrap sem senha em log; recuperação com e sem `--reativar`; script `--gerar` |
| `test_fase01_reset.py` | SMTP mock recebe exatamente um link válido; resposta indistinguível; SMTP ausente, transitório e persistente; token expirado, inexistente, reusado ou de conta desativada; limite por conta e por IP; nada de segredo em log; **reset concorrente no PostgreSQL** |
| `test_fase01_ailos_state.py` | state expirado, legado, replay, substituído, via endpoint; dedupe da renovação; **callbacks e renovações concorrentes no PostgreSQL** |

**Prova de regressão:** os testes novos também rodaram contra o código
anterior de cada correção, e falham lá:

| Achado | Resultado no código anterior |
|---|---|
| SEC-01 | 12 falhas |
| SEC-02 | a concorrência retorna `[200, 200]` (bifurcação) |
| SEC-06 | 6 falhas |
| SEC-04 | 10 falhas |
| SEC-05 | 11 falhas |
| FIN-09 | 7 falhas, incluindo as duas concorrências |
| SEC-03 | sonda direta no `_seed_admin` antigo: `reativado=True, senha_no_log=True` |

Testes existentes ajustados, com o motivo:

- `test_auth_api::test_reused_rotated_token…`: passa a exercitar o reuso fora da
  janela de 15 s;
- `test_auth_api::TestResetPassword` e
  `test_security_hardening::TestResetPasswordDerrubaSessao`: gravam o token
  como hash;
- `test_ailos_connect_callback`: os states passam a ter prazo;
- `test_security_hardening`: PyJWT no lugar do jose.

## 4. Rollback (ensaiado)

Procedimento em [sessoes-e-credenciais.md §7](../seguranca/sessoes-e-credenciais.md#7-rollback):
downgrade com a imagem nova, corte de sessões e subida da imagem anterior.

- [ensaio-rollback.txt](fase-01/ensaio-rollback.txt): o código anterior (`6706cc2`) sobe depois do downgrade e recusa todos os tokens antigos, inclusive o da sessão encerrada no código novo. Login novo funciona. O roll-forward converte em hash o token criado pelo código antigo.
- [ensaio-rollback-sem-corte.txt](fase-01/ensaio-rollback-sem-corte.txt) (controle negativo): **sem o corte**, o código anterior volta a aceitar o access da sessão encerrada. Por isso o corte é obrigatório.

## 5. Limitações — o que NÃO foi executado

- **E-mail real:** a entrega foi testada com SMTP mockado. Não foi verificado o
  SMTP de produção, nem SPF/DKIM ou chegada na caixa de entrada.
- **Ailos real:** nenhum callback nem renovação contra a Ailos. Não foi
  confirmado se a Ailos invalida o token anterior ao renovar; a serialização
  evita a disputa, mas o contrato continua não testado.
- **Build do frontend no host Windows:** não executado. Aprovado em
  `node:20-alpine`, como na Fase 00.
- **Rate limit por IP com Redis real e vários workers:** o limite do
  forgot-password está declarado e foi testado por inspeção. Não foi
  exercitado com Redis real e vários workers.
- **Downloads por `fetch` direto no frontend:** não renovam a sessão sozinhos
  (limitação anterior). Na primeira meia hora após o deploy, um download pode
  falhar uma vez.
- **Pilha XMLDSig:** fora do escopo (Fase 05).
- **`backend/openapi.json`:** não regenerado. Só a notação mudou.

## 6. Decisões pendentes para a empresa

1. **Acesso financeiro do operacional:** confirmar a proposta aplicada. Dá para
   criar uma capacidade separada se algum indicador agregado for necessário.
2. **Planos e produtos (R1):** decidir se entram em `FINANCIAL_READ`.
3. **Senhas antigas fracas:** decidir se a troca será forçada. Isso exige campo
   e fluxo de "trocar no primeiro acesso".
4. **SMTP de produção:** configurar antes de divulgar "Esqueci minha senha".
5. **Janela entre abas (15 s) e validade do state Ailos (15 min):** valores
   propostos, ajustáveis por variável de ambiente.
