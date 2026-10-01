# Baseline de validação — Fase 00 (OPS-01, OPS-02, QA-01)

Data: 30/09/2026. Base: `28af706d1e2479d259d500dd849a4ed22d9989eb` (mesmo commit
da auditoria), branch `fase-00-backup-baseline`. Evidências brutas em
[fase-00/](fase-00/).

Todos os comandos rodaram em containers descartáveis: sem `.env` da
aplicação, sem rede de saída (`--network none` ou rede Docker `--internal`),
nenhum banco, bucket ou serviço real. Integrações desligadas
(`MULTIPORTAL_ENABLED=false`, chamadas Ailos/NFS-e mockadas pelos testes).

## 1. Ambiente

| Item | Versão |
|---|---|
| Python (imagem `backend/Dockerfile`, `python:3.12-slim`) | 3.12.14 |
| PostgreSQL (servidor e `pg_dump`/`pg_restore`, `postgres:16`) | 16.14 (Debian 16.14-1.pgdg13+1) |
| Node (imagem `frontend/Dockerfile`, `node:20-alpine`) / npm | 20.20.2 / 10.8.2 |
| rclone / GnuPG / bash (imagem `backup/Dockerfile`) | 1.60.1-DEV / 2.4.7 / 5.2.37 |
| Docker Engine / Compose | 28.3.2 / v2.39.1 |

Imagem de teste do backend = imagem do projeto + `requirements-test.txt`.

## 2. Resultados

| Comando (diretório) | Resultado |
|---|---|
| `python -m compileall -q backend` (raiz, `PYTHONPYCACHEPREFIX=/tmp/pyc`) | **OK** (saída 0) |
| `python -m pytest tests -q -p no:cacheprovider --junitxml=…` (backend, SQLite + `TEST_DATABASE_URL` em PostgreSQL 16 descartável, **sem** `backend/.env`) | **1848 aprovados, 38 falhas, 0 skips** (1886 coletados) |
| `python scripts/checar_baseline_testes.py junit.xml` (backend) | **BASELINE OK** — as 38 falhas são exatamente as triadas |
| baseline original, reproduzido (SQLite, antes das mudanças) | 1820 aprovados, 41 falhas, 12 skips — igual à auditoria |
| `npm ci` (frontend) | OK |
| `npm run lint` | OK — 0 erros, 41 avisos |
| `npm run typecheck` | OK |
| `npm test` | **167/167** (19 arquivos) |
| `npm run build` | **OK** no Linux (`node:20-alpine`). A reprovação da auditoria era do ambiente Windows — build no host Windows **NÃO EXECUTADO** nesta fase |
| `bash backup/tests/run_in_docker.sh` (raiz) | **91 ok, 0 falhas** |
| `docker compose --profile backup config -q` (base e base+prod, env vazio) | OK |
| `bash backup/exercicio/exercicio_restauracao.sh` (raiz) | **APROVADO** nos dois volumes (§4) |

Os 12 skips da auditoria eram todos `TEST_DATABASE_URL não configurada`
(`test_check_then_insert_race.py`, `test_database_concurrency_postgres.py`);
com PostgreSQL 16.14 descartável os 12 passam.

Diferença entre 1820 e 1848 aprovados: +12 testes PostgreSQL que deixaram de
ser pulados, +13 testes novos do verificador de restauração e +3 testes
corrigidos (§3.1). Coletados: 1873 → 1886 (os 13 novos). Nenhum teste
existente foi removido, desativado ou marcado como xfail.

## 3. Triagem das 41 falhas

### 3.1 Corrigidas nesta fase (defeito do próprio teste)

| Teste | Causa | Correção |
|---|---|---|
| `test_ailos_endpoints_api.py::…::test_registrar_parcela_com_sucesso` | **ambiente**: dependia de `AILOS_GATEWAY_BASE_URL` vindo do `.env` de quem rodava; sem ele, 400 "URL base não configurada" antes do mock | fixture define a URL (`.invalid`); com a variável, os 32 testes do arquivo já passavam |
| `test_ailos_endpoints_api.py::…::test_registrar_pendentes_avanca_o_que_der` | idem | idem |
| `test_client_charge_items_api.py::TestDeleteChargeItem::test_operational_cannot_delete` | **teste quebrado**: `NameError` (`http` não era parâmetro). Usar `http` e `http_op` juntos também não serviria — compartilham o mesmo override de usuário | item criado direto no banco; agora verifica o 403 **e** que o item não foi excluído |

### 3.2 Mantidas vermelhas e registradas (`backend/tests/baseline_falhas_conhecidas.txt`)

Nenhuma foi silenciada. O gate reprova falha nova e também item da lista que
passe a passar (quem corrigir remove a linha no mesmo commit).

| Código | Qtd | Classificação | Evidência | Decisão necessária |
|---|---|---|---|---|
| **G1** | 23 | **Divergência de produto** — não é só teste velho | Criação de contrato, item de cobrança e vínculo de rastreador não gera mais cobranças (`billing_cycles`, `auto_generate_billings`, `installation_fee` sumiram dos schemas no commit `14d4994`; rota `POST /contracts/{id}/generate-billings` removida → 404). Hoje as mensalidades nascem no fechamento mensal (`services/billing_closure`). **Mas `frontend/app/rastreadores/page.tsx:617-618` ainda envia `auto_generate_billings`/`billing_cycles` e mostra o campo "Ciclos (meses)", que o backend ignora em silêncio.** | Produto: remover o campo da UI (e reescrever os testes para o fechamento) **ou** reintroduzir a geração. Não alterado nesta fase (regra financeira). |
| **R1** | 5 | Teste obsoleto provável | Listagem de cobranças, itens, planos, produtos e extrato exige `ADMIN`/`FINANCIAL` (`require_roles`); os testes esperam o operacional com acesso | Confirmar matriz de papéis; se o endurecimento é intencional, atualizar os testes para esperar 403 |
| **V1** | 4 | Teste obsoleto (comportamento aceitável) | `PUT` de recurso inexistente com corpo incompleto → 422 (validação do corpo antes do 404); `register-client` → 422 com o payload antigo, mas o endpoint sempre responde 403 com payload válido | Atualizar payloads dos testes |
| **C1** | 4 | Teste obsoleto | `/reports/delinquents` devolve `{clientes, total_clientes, …}` e `/reports/summary` usa `contratos_por_plano`; o frontend (`app/relatorios/page.tsx:188`) já consome o formato novo | Atualizar testes para o contrato atual |
| **F1** | 2 | Teste obsoleto | Planos filtram por `active` (teste usa `active_only`); produtos não oferecem filtro `auto_add_on_uninstall` (parâmetro desconhecido é ignorado). Frontend não usa nenhum dos dois | Atualizar testes (ou implementar o filtro, se desejado) |

Total: 23 + 5 + 4 + 4 + 2 = **38**. Nenhuma falha foi atribuída a limitação de
ambiente depois das correções de §3.1.

## 4. Exercício integral de recuperação

`backup/exercicio/exercicio_restauracao.sh`: origem sintética (schema real via
`alembic upgrade head`) → `backup.sh` com envio para destino externo
(volume separado) → **apaga** banco, MinIO e backup local → servidores novos →
`baixar` → `banco` (nome novo, inventário conferido) → `objetos` (bucket novo,
hash conferido) → `promover` → `verificar_restauracao.py` → controle negativo
sem chave.

| | Ensaio A | Ensaio B |
|---|---|---|
| Dados | 500 clientes, 6.000 cobranças, 300 contas, 200 documentos | 20.000 clientes, **480.000 cobranças**, 300 contas, 2.000 documentos |
| Linhas / tabelas | 7.006 / 35 | 502.306 / 35 |
| Dump (`db.dump`) | 227.396 B, sha256 `d62eadbb…7db5592` | 3.968.673 B, sha256 `d1527335…bae6efa97` |
| Objetos | 193 (1,5 MB); 188 refs obrigatórias, 0 ausentes | 1.921 (79 MB); 1.871 refs obrigatórias, 0 ausentes |
| Alembic | `b3f8a1c9d2e7` | `b3f8a1c9d2e7` |
| Inventário restaurado × origem | idêntico | idêntico |
| Segredos (tokens Ailos ×2, SMTP, PFX) | 4/4 decifrados; PFX abre | 4/4; PFX abre |
| Sem chave Fernet | reprovado (saída 1) | reprovado (saída 1) |
| Bancos ao final | `rastreamento`, `rastreamento_pre_restore_…` (nada apagado) | idem |
| Backup (banco+objetos+envio) | 3 s | 9 s |
| **RTO técnico** (servidores novos → verificação aprovada) | **14 s** | **29 s** |

Saldo restaurado × origem (Ensaio B, cobranças não excluídas, em reais →
centavos do verificador): pendente 11.389.430,80 → 1.138.943.080 ·
vencida 11.392.328,40 → 1.139.232.840 · paga 22.784.596,80 → 2.278.459.680 ·
cancelada 11.392.288,40 → 1.139.228.840 — todos iguais. Detalhe completo em
`fase-00/ensaio-*/`.

O RTO técnico não inclui provisionar VPS, DNS, nem o download do volume real
pela internet; por isso a proposta de RTO é 4 h ([contingencia-recuperacao.md](../contingencia-recuperacao.md)).

## 5. Limitações — o que NÃO foi executado

- **Restauração com dados reais / produção**: não executada, por regra desta fase.
- **Destino externo real** (B2/S3/crypt): o ensaio usa rclone contra um
  volume local; credenciais, latência e o `rclone check` de um provedor real
  não foram exercitados.
- **Webhook real**: alertas testados com `curl` substituído.
- **Espelhamento com `mc`**: caminho mantido, não testado (o compose usa rclone).
- **GPG no exercício integral**: coberto nos testes automatizados (cifrar,
  restaurar, falta de chave privada), não no ensaio completo.
- **Build do frontend no host Windows**: não executado; aprovado no container.
- **Imutabilidade do destino externo**: não existe; é decisão pendente.

## 6. Status dos achados

| ID | Status | O que foi feito | O que falta |
|---|---|---|---|
| **OPS-01** | **Resolvido** | formato detectado pelo conteúdo; leitura integral e checksum antes de qualquer ação; restauração só em banco novo; inventário conferido; troca por renomeação com aprovação; `reverter-troca`; nenhum `DROP` no script; 91 testes + 2 ensaios | aprovar RPO/RTO e fazer o primeiro ensaio com o destino externo real |
| **OPS-02** | **Parcial** | um mecanismo único (cron → `pg_dump` → `backup.sh`) com banco + objetos + manifest + envio conferido; falhas propagadas e alertadas; retenção com mínimo garantido; segredos fora do backup e inventariados para o cofre | configurar destino externo real e webhook em produção; decidir proteção contra exclusão (Object Lock); monitor externo de `last_success` |
| **QA-01** | **Parcial** | baseline reproduzível em containers (backend 3.12 + PG16, frontend Node 20); 41 falhas triadas por causa; 3 corrigidas; 12 skips eliminados com PostgreSQL; gate `checar_baseline_testes.py` | decisão de produto para G1; atualizar testes R1/V1/C1/F1; gate remoto (CI) versionado — fora do escopo desta fase |
