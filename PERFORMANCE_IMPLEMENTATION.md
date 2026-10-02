# MasterSat Performance Implementation

## Alterações realizadas

Implementação incremental de PERF-01 a PERF-05, parte de PERF-07/08/09 e instrumentação para PERF-10. O trabalho não foi implantado em produção. O baseline de tempo/RPS/payload real ainda depende de homologação com dados representativos.

## Arquivos modificados

Backend: `app/api/v1/endpoints/{clients,dashboard,audit_logs}.py`, `app/core/{audit,audit_queue,dashboard_cache,request_timing}.py`, `app/main.py`, `app/worker.py`, `app/models/audit_log.py`, `app/schemas/{client,audit_log}.py`, migration e testes. Frontend: telas de clientes/auditoria, hooks de clientes, detalhe de cliente, home e `next.config.ts`. Infraestrutura: Compose base e produção, Nginx. Documentos e cenário de carga: `PERFORMANCE_AUDIT.md`, este arquivo, `docs/performance-postgres.md`, `scripts/performance/mastersat.js`.

Alterações locais pré existentes em veículos, `client_charge_item`, `tsconfig.tsbuildinfo`, `output/` e `tmp/` foram preservadas.

## Migrations criadas

`b2e8c4a19d30_audit_event_id.py` adiciona `audit_logs.event_id` nullable e índice único parcial (`event_id IS NOT NULL`). Linhas antigas continuam válidas com `NULL` e ficam fora do novo índice; downgrade remove coluna e índice. O identificador torna a entrega ao menos uma vez da fila idempotente. `alembic heads` mostra uma única head (`b2e8c4a19d30`). Upgrade real em PostgreSQL não foi executado neste workspace.

## Índices adicionados

Somente `uq_audit_logs_event_id`, parcial para não indexar o histórico sem chave e necessário para deduplicar eventos reenviados após commit seguido de falha antes do `XACK`. Índices de busca/filtro adicionais dependem de planos e cardinalidade reais; ver `docs/performance-postgres.md`.

## Queries otimizadas

- `/dashboard/`: agregações condicionais consolidam seis contagens de clientes, três de rastreadores, três de OS e quatro métricas financeiras. Mantidas as regras de caixa, título legado sem `paid_amount`, veículos distintos com rastreador e corte financeiro por perfil.
- `/clients/`: `skip/limit` existentes passam a ser usados pela tela; ordenação com allowlist ocorre no SQL. `COUNT ... GROUP BY client_id` busca quantidades de veículos não excluídos só para os clientes da página. `/clients/summary` agrega indicadores com os mesmos filtros.
- `/audit-logs/paged`: busca, filtros, ordenação, `OFFSET/LIMIT` e indicadores calculados no SQL. `/audit-logs/` preserva a resposta antiga em array.
- Auditoria: 50 eventos são persistidos com um INSERT e um COMMIT no teste de lote; redelivery mantém 50 linhas graças a `event_id`.

## Cache implementado

Dashboard em Redis por perfil (`admin`, `operacional`, `financeiro`), TTL 20 segundos. Mutations de clientes, veículos, rastreadores, OS e cobranças invalidam as três chaves; mudanças por jobs ficam limitadas pelo TTL. Falha de cache cai para consulta SQL. O header `X-Dashboard-Cache` indica HIT/MISS. O cache é desabilitado na suíte SQLite para que os testes não dependam de Redis.

## Mudanças frontend

- Clientes: página visível (10/25/50/100), busca/filtro/ordenação no backend, `total` do servidor e dados anteriores mantidos durante refetch com React Query. O detalhe carrega veículos do cliente aberto; a tabela recebe apenas `vehicle_count` agregado. Os indicadores usam `/clients/summary`.
- Auditoria: 50 registros por página via `/audit-logs/paged`, busca debounced no backend, indicadores de todo o conjunto filtrado e cache React Query.
- Home: removida a dependência de download de Inter do Google no build; usa a pilha `font-sans` do sistema. `outputFileTracingRoot` foi fixado na pasta do frontend para impedir tracing do diretório do usuário em Windows.

## Mudanças backend

`RequestTimingMiddleware` devolve `X-Request-ID` e `Server-Timing` com duração e contagem SQL, e registra requests acima de 500 ms usando rota template, status e ID. Não registra query text, parâmetros, token nem payload.

## Background worker

Em produção, `backend` mantém três workers Uvicorn com `RUN_BACKGROUND_JOBS=false`; `backend-worker` executa o scheduler existente com `RUN_BACKGROUND_JOBS=true` e consome a fila de auditoria. Os advisory locks existentes permanecem. O modo de desenvolvimento mantém jobs no backend para compatibilidade. Threads iniciadas diretamente por alguns endpoints, como processamento de NFS-e, ainda precisam de migração específica.

## Auditoria

Eventos entram no stream `mastersat:audit:v1` de um Redis dedicado com AOF `appendfsync=always`, volume persistente e política `noeviction`. O worker lê até 50 eventos, aguarda brevemente outros eventos, insere em lote e só então confirma/remove mensagens. Mensagens pendentes de worker interrompido são recuperadas; `event_id` evita duplicação. Mensagem inválida vai para dead-letter antes do ACK. Falha na fila faz gravação direta no PostgreSQL; falha também no fallback produz log crítico com `event_id`. O worker registra profundidade e pendências periodicamente; no shutdown aguarda até cinco segundos e qualquer lote interrompido permanece pendente para retomada.

Limite material: auditoria ainda é enfileirada **após** a resposta HTTP, como antes; encerramento abrupto exatamente nesse intervalo pode perder um evento. Uma falha simultânea de Redis e PostgreSQL não pode ser resolvida automaticamente. Monitorar logs críticos, fila e volume AOF; testar recuperação antes de declarar garantia operacional de perda zero.

## Infraestrutura

Nginx aplica cache de um ano com `immutable` apenas a `/_next/static/`. API e HTML autenticado não recebem cache compartilhado. Compose configura `redis-audit` e `backend-worker`; `docker compose config --quiet` passou com arquivo `.env` temporário vazio. O daemon Docker não estava acessível para executar `nginx -t` ou subir os containers.

Pool SQLAlchemy mantido em `5 + 10` por processo. Três workers HTTP permitem até 45 conexões; o processo `backend-worker` pode acrescentar até 15, totalizando **até 60 conexões teóricas** da aplicação, além de manutenção. Não há medição de `max_connections` em produção para justificar mudança.

## Benchmarks

`scripts/performance/mastersat.js` executa login único e cenários de dashboard, listagem/busca de clientes, veículos e resumo financeiro com 10/25/50 VUs. Relata RPS, p50/p95/p99 e erros pelo k6. Não foi executado: falta ambiente de homologação com credenciais e base representativa. Não execute em produção sem planejamento de carga.

Verificação local: 162 testes backend direcionados passaram; 193 testes frontend passaram; typecheck, lint dos arquivos alterados, build Next.js e `docker compose config --quiet` passaram. `nginx -t` e testes integrados PostgreSQL/Redis não rodaram porque o daemon Docker não estava acessível.

O build reportou First Load JS de 161 kB para `/clientes` e 135 kB para `/auditoria` após as mudanças. Sem build anterior comparável, esses números não demonstram ganho de bundle.

## Antes x depois

| Fluxo | Antes | Depois | Fonte |
| --- | --- | --- | --- |
| `/dashboard/` com finanças | 20 SELECTs | até 8 SELECTs em miss, 0 em hit | Contagem estática antes; orçamento SQL testado depois em SQLite |
| `/dashboard/` operacional | 14 SELECTs | até 5 SELECTs em miss, 0 em hit | Mesma metodologia |
| Tela de clientes, 10 linhas | pedidos de até 200 clientes + 500 veículos | 10 clientes + uma linha de contagem por cliente da página; veículos detalhados só ao abrir | Limites do código, não medição de rede |
| Auditoria, 50 eventos | 50 INSERTs e 50 COMMITs | 1 INSERT e 1 COMMIT no teste de lote | Teste SQLite; produção ainda não medida |
| Tela de auditoria | até 500 registros para paginar no navegador | 50 registros e total/indicadores em SQL | Contratos de API |

Não há números confiáveis de latência, payload em KB, RPS ou p95 antes/depois. Essas métricas requerem ambiente real.

## Riscos restantes

- Listagens de veículos, rastreadores, OS, cobranças, contratos, NFS-e, relatórios e opções de formulários ainda podem buscar centenas de registros. Há alterações locais pré existentes em veículos; continuar essa migração sem sobrescrevê-las.
- Não foram medidos planos SQL em PostgreSQL. O único índice criado foi o de idempotência da auditoria.
- A fila e o worker não passaram por teste integrado com Redis/PostgreSQL/Docker; testes de unidade cobrem lote, deduplicação e fallback.
- A suíte backend completa neste ambiente terminou com 2248 aprovados, 38 falhas, 12 erros e 28 casos PostgreSQL excluídos. Exemplos de falhas: testes esperam campos de cobrança removidos do schema e permissões incompatíveis com regras atuais; não há baseline limpo dessa suíte para atribuir cada falha. Os erros de restauração foram causados por restrição de acesso ao diretório temporário. Quatro módulos PostgreSQL também não coletam no Python 3.13 local porque Alembic não está instalado nesse interpretador.
- Cache admite até 20 segundos de defasagem para alterações feitas por jobs ou corridas com invalidação.

## Próximas otimizações

1. Capturar `pg_stat_statements`, `EXPLAIN (ANALYZE, BUFFERS)`, cardinalidades e métricas dos headers em homologação.
2. Paginar demais listas por fluxo e separar options de formulários das listas completas. Validar seleção e cálculo financeiro antes de alterar cobranças.
3. Revisar o caminho de NFS-e que ainda inicia thread durante HTTP e migrar quando houver estado persistido de execução.
4. Testar Redis AOF, redelivery, fallback e desligamento real em Compose; incluir backup/alerta do volume da fila no plano operacional.
5. Reconciliar as falhas da suíte backend contra uma baseline atualizada das regras de negócio, sem restaurar comportamentos inseguros ou financeiros antigos.

## Checklist para deploy

1. Fazer backup verificável de PostgreSQL/MinIO, reservar capacidade para o volume AOF e checar `max_connections`.
2. Planejar janela para construir o índice da migration `b2e8c4a19d30` sobre a tabela de auditoria; confirmar coluna e índice antes de servir requests com auditoria em fila.
3. Subir `redis-audit`, `backend-worker` e API via Compose; confirmar somente um processo scheduler e três workers HTTP.
4. Confirmar logs do worker, `XLEN mastersat:audit:v1`, `XPENDING mastersat:audit:v1 mastersat-audit-workers`, dead-letter e ausência de logs críticos de fallback.
5. Fazer smoke tests de permissões, dashboard por perfil, criação de cliente, cobrança, auditoria, NFS-e/Ailos/Multiportal e soft delete.
6. Verificar `X-Request-ID`, `Server-Timing`, HIT/MISS, cache dos assets versionados e ausência de cache em API/HTML.
7. Executar k6 em homologação, comparar p50/p95/p99, RPS, erros e top queries; ajustar índices e pool apenas com evidência.
