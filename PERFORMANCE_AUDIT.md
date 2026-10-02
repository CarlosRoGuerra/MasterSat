# MasterSat Performance Audit

## Resumo executivo

Auditoria estática inicial em 2026-10-01. Não há acesso a métricas da VPS nem a uma base PostgreSQL representativa neste workspace; tempos, RPS e tamanho de payload em produção ainda precisam ser medidos. Os números de consultas abaixo são contagens do código, não medições de produção. Prioridade: paginação real na interface, consolidação das agregações do dashboard e isolamento dos jobs e da auditoria.

## Arquitetura atual

Next.js 15/React Query consome FastAPI/SQLAlchemy. PostgreSQL 16 armazena dados; Redis serve o rate limiter; MinIO armazena documentos. Nginx faz proxy e gzip. Em produção, `docker-compose.prod.yml` inicia três workers Uvicorn. Cada processo possui pool de cinco conexões com até dez extras (`backend/app/db/session.py`): teto teórico de 45 conexões da API, além de jobs e serviços auxiliares. O frontend é construído em modo standalone.

Antes desta implementação, cada request atravessava `ForwardedProtoMiddleware`, SlowAPI, CORS, `MaxBodySizeMiddleware` e `AuditMiddleware`. Não foi encontrado `RequestTimingMiddleware` no código local apesar de ele ter sido citado no pedido; a instrumentação foi adicionada nesta entrega. O rate limiter usa Redis. O middleware de auditoria fazia um commit PostgreSQL por evento autenticado.

| Carregamento inicial | Chamadas de API identificadas | SQL / volume antes (inspeção estática) |
| --- | --- | --- |
| Dashboard | `/dashboard/`; outras chamadas da página exigem medição no navegador | 20 SELECTs no endpoint financeiro, 14 no operacional |
| Clientes | `/clients?limit=200` e `/vehicles?limit=500` | `/clients` fazia 3 SELECTs; até 700 objetos das duas listas; SQL de `/vehicles` depende dos vínculos |
| Rastreadores | lista `/trackers?limit=100`, clientes 300, veículos 400, planos 100 e fabricantes Multiportal | Ao menos 5 chamadas; contagens da tela calculadas dos primeiros 100 rastreadores |
| Ordens de serviço | OS 200, clientes 200, veículos 500, rastreadores 200, usuários e produtos | 6 chamadas paralelas; paginação de OS no navegador |
| Auditoria | `/audit-logs?limit=500` | 1 SELECT de até 500 linhas; busca e paginação no navegador |
| Veículos | `apiFetchAll` de veículos e clientes | Quantidade de round trips cresce com os registros; página local de 20 |

Esses valores não são p50/p95 nem contagens medidas em produção. `Server-Timing` e o cenário k6 foram preparados para obter esses dados em homologação.

## Gargalos críticos

| Prioridade | Local / fluxo | Comportamento, impacto e solução | Risco |
| --- | --- | --- | --- |
| P1 | `frontend/app/clientes/_components/queries.ts`, `useClientsQuery`; `/clients` | Pede 200 clientes para exibir 10, ordena e pagina no navegador. O endpoint já suporta `skip/limit`, mas só ordena por ID. Solicitar uma página, ordenar no SQL e usar `total`. | Alteração de ordenação e estado da página; exige teste de filtros e navegação. |
| P1 | mesmo arquivo, `useVehicleSummariesQuery`; `/vehicles` | Pede até 500 veículos apenas para calcular quantidades por cliente e alimentar detalhes. Agregar `COUNT` por clientes da página e carregar veículos do cliente aberto sob demanda. | Preservar a aba de detalhes e soft delete. |
| P1 | `backend/app/api/v1/endpoints/dashboard.py`, `dashboard`; `/dashboard` | A leitura dispara aproximadamente 20 SELECTs para perfis com finanças, 14 sem finanças (contagem estática, sem middleware). Consolidar contagens e somas condicionais sem mudar o corte por permissão. | Erro em filtros de status, datas, valor pago legado ou permissões. |
| P1 | `backend/app/core/audit.py`, `_write_log`; todos os requests autenticados | Um INSERT e um COMMIT por evento após a resposta, em thread do processo HTTP. Mover para fila durável e gravação em lote, com fallback explícito e monitoração. | Perda de eventos se a fila ou worker falhar; requer rollout controlado. |

## Gargalos de backend

- P1: `backend/app/api/v1/endpoints/clients.py`, `list_items`, `GET /clients/`: `COUNT` e SELECT de objetos completos, inclusive campos JSON/texto. Contrato de lista leve pode reduzir serialização, mas exige verificar todos os consumidores. Risco: modais usam o objeto completo; adiar troca de schema até ter detalhe sob demanda.
- P1: `backend/app/api/v1/endpoints/audit_logs.py`, `list_logs`, `GET /audit-logs/`: limite de 500, sem total/offset e com busca `%texto%` em três colunas; a tela `frontend/app/auditoria/page.tsx` pagina localmente. Solução: filtros, contagem e paginação SQL. Risco: compatibilidade da resposta de lista.
- P2: `backend/app/api/v1/endpoints/exports.py`, exportações: `.all()` em listagens potencialmente grandes e mapas completos de clientes/veículos. Verificar memória e uso de streaming por exportação; risco de mudar formato dos arquivos.
- P2: `backend/app/api/v1/endpoints/clients.py`, timeline: caches por ID ainda executam SELECT por ID diferente. Prefetch em lote preservando permissões; risco de alterar histórico.

## Gargalos de banco

- P1: `clients` filtra `is_deleted`, status/tipo e ordena por ID ou nome. Há índices individuais e GIN trigram para nome/nome fantasia; índices compostos devem ser decididos com `EXPLAIN (ANALYZE, BUFFERS)` sobre dados reais antes de migration. Risco de write amplification.
- P2: buscas `ILIKE '%texto%'` em CPF/CNPJ, email, telefone e cidade no `list_items`. CPF/CNPJ já possui índice único parcial para igualdade; separar busca estruturada de busca textual após validar UX e formato dos dados. Risco de deixar de encontrar entradas antigas.
- P2: avaliar planos de `billings` por status/vencimento/pagamento e de `audit_logs` por data/filtros usando `pg_stat_statements`. Sem evidência de plano, não acrescentar índices indiscriminadamente.

## Gargalos de frontend

- P1: `frontend/app/veiculos/page.tsx`, `loadData`: `apiFetchAll` de veículos e clientes; a tela usa `usePagination` local. Arquivo possui mudanças locais pré existentes, então a migração precisa ser coordenada com esse trabalho.
- P1: `frontend/app/rastreadores/page.tsx`, `frontend/app/ordens-servico/page.tsx` e `frontend/app/financeiro/page.tsx`: listas e opções com limites 200–500 e paginação local. Migrar cada fluxo sem truncar opções de formulários.
- P2: `frontend/app/clientes/page.tsx`, modais de cobrança: até 1000 cobranças por cliente ao abrir; paginar/filtrar na API após verificar seleção múltipla e cálculos financeiros.
- P2: `frontend/app/clientes/page.tsx`, `loading`: `isFetching` troca a tabela inteira por skeleton também em refetch. Manter a página anterior durante troca de filtros/página quando houver dados.

## Gargalos de infraestrutura

- P2: `nginx/nginx.conf` já usa gzip, porém não define cache explícito para `/_next/static/`. Aplicar `immutable` apenas a arquivos versionados; HTML, API e respostas autenticadas permanecem sem cache compartilhado.
- P2: três workers vezes pool máximo 15 implicam até 45 conexões da API. Medir `pg_stat_activity`, conexões de jobs e `max_connections` antes de ajustar pool ou workers.
- P3: `frontend/next.config.ts` já usa standalone; Docker de produção seleciona o estágio runner. Verificar build no CI.

## Jobs e integrações

- P1: `backend/app/main.py`, `startup`: inicia threads de inadimplência, Ailos, Multiportal e retenção em cada worker HTTP. Advisory locks evitam parte da duplicação, mas CPU, memória e pool ainda competem com requests. Separar scheduler em um único processo worker, preservando locks e plano de transição.
- P1: `backend/app/core/audit.py`, `_write_log`: a escrita ocorre após a resposta, porém continua consumindo uma sessão/commit por evento. Ver gargalos críticos.
- P2: Ailos, NFS-e, ViaCEP e migração SGR usam `requests` com timeouts explícitos nos caminhos inspecionados. Revisar operações longas feitas durante HTTP, retries e estado de processamento antes de movê-las para job. MinIO é usado por `backend/app/services/storage.py` para documentos.

## Riscos de escalabilidade

O custo das páginas cresce com o total de registros baixados, mesmo quando se exibem poucas linhas. A auditoria e os schedulers escalam com workers HTTP. `COUNT` em listas filtradas também pode tornar-se dominante em bases grandes. Ainda faltam cardinalidade, planos SQL e métricas de carga reais.

## Quick Wins

1. Usar `skip/limit`, ordenação SQL e agregação de veículos na tela de clientes.
2. Consolidar contagens do dashboard e comparar valores em testes.
3. Servir assets versionados do Next.js com cache longo no Nginx.
4. Capturar top queries via `pg_stat_statements` antes de adicionar índices.

## Melhorias estruturais

Fila confiável para auditoria, worker de jobs separado, listas de consulta com schemas leves, paginação real dos demais módulos e eventuais índices baseados em planos observados.

## Plano de implementação

PERF-01: baseline, documentação e contagem SQL de testes. PERF-02: clientes primeiro; depois veículos, rastreadores, cobranças, ordens, auditoria, notas e contratos por fluxo. PERF-03: dashboard. PERF-04: cache Redis segregado por permissão após verificar invalidação. PERF-05: auditoria em lote. PERF-06: índices guiados por `EXPLAIN`. PERF-07: React Query e payloads. PERF-08: worker único. PERF-09: Nginx. PERF-10: carga em ambiente de homologação. Cada fase exige testes de função e permissão; não serão apresentados tempos ou ganhos de produção sem medição.
