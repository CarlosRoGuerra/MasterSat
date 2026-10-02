# PostgreSQL: diagnóstico de performance

O MasterSat usa PostgreSQL 16. Execute estas consultas em homologação ou numa janela de observação de produção. Capture os valores antes e depois de cada mudança. **Não execute `EXPLAIN ANALYZE` em comandos de escrita no banco de produção:** ele executa o comando de fato.

## Habilitar `pg_stat_statements`

O módulo precisa estar em `shared_preload_libraries`, o que exige reiniciar o PostgreSQL. Verifique primeiro as bibliotecas atuais; ao habilitar, preserve as demais. No container oficial, a opção pode ser passada como argumento do processo:

```yaml
# Exemplo para um override de homologação; mantenha as demais opções existentes.
services:
  db:
    command: ["postgres", "-c", "shared_preload_libraries=pg_stat_statements", "-c", "compute_query_id=on"]
```

Depois do reinício, execute no banco `rastreamento` com uma conta autorizada:

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SHOW shared_preload_libraries;
SELECT extname FROM pg_extension WHERE extname = 'pg_stat_statements';
```

O módulo e o requisito de preload estão descritos na [documentação oficial do PostgreSQL 16](https://www.postgresql.org/docs/16/pgstatstatements.html).

## Top queries

```sql
-- Tempo total acumulado: onde o banco mais trabalha.
SELECT calls, round(total_exec_time::numeric, 1) AS total_ms,
       round(mean_exec_time::numeric, 2) AS mean_ms,
       rows, shared_blks_read, left(query, 250) AS query
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY total_exec_time DESC LIMIT 20;

-- Mais chamadas: detectar SELECT/COUNT repetidos.
SELECT calls, round(total_exec_time::numeric, 1) AS total_ms,
       left(query, 250) AS query
FROM pg_stat_statements
ORDER BY calls DESC LIMIT 20;

-- Maior média: consultas individuais lentas (mínimo 10 chamadas).
SELECT calls, round(mean_exec_time::numeric, 2) AS mean_ms,
       shared_blks_read, left(query, 250) AS query
FROM pg_stat_statements
WHERE calls >= 10
ORDER BY mean_exec_time DESC LIMIT 20;

-- Mais leitura física, para investigar índices e cache do banco.
SELECT calls, shared_blks_read, shared_blks_hit,
       left(query, 250) AS query
FROM pg_stat_statements
ORDER BY shared_blks_read DESC LIMIT 20;

-- Somente depois de exportar/salvar o baseline; apaga as estatísticas coletadas.
SELECT pg_stat_statements_reset();
```

## Plano de execução

Use parâmetros e cardinalidade representativos. Compare `actual rows` com as estimativas, observe `Buffers`, ordenações, leituras e tempo total. Por exemplo:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, name, status
FROM clients
WHERE is_deleted = false AND status = 'ACTIVE'
ORDER BY name ASC, id ASC
LIMIT 20 OFFSET 0;

EXPLAIN (ANALYZE, BUFFERS)
SELECT client_id, count(*)
FROM vehicles
WHERE is_deleted = false AND client_id IN (1, 2, 3)
GROUP BY client_id;
```

O valor persistido do enum SQLAlchemy pode diferir do valor exibido pela API; confirme antes de executar o exemplo. `EXPLAIN ANALYZE` executa a consulta e `BUFFERS` informa blocos lidos e encontrados em cache, conforme a [documentação oficial](https://www.postgresql.org/docs/16/sql-explain.html).

## Capacidade de conexões e índices

Em produção há 3 workers Uvicorn, cada um com `pool_size=5` e `max_overflow=10`: até **45 conexões teóricas da API**. Jobs, migrations e acessos de manutenção somam conexões adicionais. Monitore:

```sql
SHOW max_connections;
SELECT application_name, state, count(*)
FROM pg_stat_activity
WHERE datname = current_database()
GROUP BY application_name, state ORDER BY count(*) DESC;

SELECT schemaname, relname, indexrelname, idx_scan, idx_tup_read
FROM pg_stat_user_indexes
WHERE relname IN ('clients', 'vehicles', 'billings', 'audit_logs')
ORDER BY relname, idx_scan DESC;
```

Antes de adicionar índice: registre query e plano atual, crie o índice em homologação, compare `EXPLAIN (ANALYZE, BUFFERS)` e custo de escrita. Considere `CREATE INDEX CONCURRENTLY` em produção quando necessário, fora de transação de migration. Nenhum índice foi criado apenas com base em suspeita estática.
