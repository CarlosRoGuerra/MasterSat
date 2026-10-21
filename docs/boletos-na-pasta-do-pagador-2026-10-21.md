# Boletos na pasta do responsável financeiro — 21/10/2026

## Problema e correção

No vídeo, os boletos da **FORTMAIS MATERIAIS ELETRICOS E HIDRAULICOS EIRELI** foram emitidos corretamente para a **ACQUAFORT COMERCIO DE MATERIAIS ELETRICOS E HIDRAULICOS EIRELI**, interveniente financeiro. O recibo já mostrava a Acquafort. A central financeira consultava `client_id`, o dono do contrato, e por isso o título aparecia na pasta da Fortmais e faltava na pasta da Acquafort.

A central financeira agora consulta `payer_client_id`, o pagador gravado na cobrança. O boleto pago **38709**, de **R$ 649,90**, passa a ser consultado pela pasta da Acquafort quando ela consta como pagadora. Contratos e veículos continuam ligados à Fortmais. Boletos já emitidos mantêm o pagador histórico, mesmo se o interveniente do contrato mudar depois.

A mesma regra foi aplicada à reabertura da carteira após alterações, à lista de carnês e às consultas de boletos para envio/carnê em Financeiro. Não há reemissão, nova baixa, alteração de valor ou transferência do cliente atendido. As componentes de boletos únicos continuam ocultas na listagem principal para não duplicar a cobrança.

## API

- `GET /api/v1/billings/?payer_client_id=ID`: pasta financeira do pagador.
- `GET /api/v1/billings/?client_id=ID`: consulta operacional pelo cliente atendido, mantida para compatibilidade.
- Os dois filtros podem ser combinados; ambos precisam corresponder à cobrança.
- As exportações `/api/v1/billings/exports/csv` e `/exports/xlsx` também aceitam `payer_client_id`.
- `GET /api/v1/boletos/carne?payer_client_id=ID`: carnês das cobranças desse pagador. O filtro antigo `client_id` continua disponível. É obrigatório informar ao menos um deles.
- Sem pagador explícito, usa-se `COALESCE(billings.payer_client_id, billings.client_id)`. A listagem não infere o pagador a partir de um contrato que pode ter sido alterado depois da emissão.
- Permissões financeiras existentes permanecem: administrador ou financeiro.

## Atualizar a VPS

```bash
cd ~/MasterSat
git switch main
git pull --ff-only origin main
docker compose -f docker-compose.yml -f docker-compose.prod.yml build backend backend-worker frontend
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps backend backend-worker frontend
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T nginx nginx -s reload
```

Não há migration. Reabra a central financeira da Acquafort após atualizar a página com Ctrl+F5.

## Consulta no banco de produção

Executar na VPS, dentro de `~/MasterSat`. O arquivo abaixo contém apenas consultas: mostra o título 38709 e os boletos efetivos da Acquafort, com cliente atendido e pagador lado a lado.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db \
  sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -P pager=off' \
  < backend/scripts/sql/consultar_pasta_pagador_fortmais.sql
```

Se a coluna `responsavel_financeiro` do boleto 38709 já mostrar a Acquafort e `payer_client_id` estiver preenchido, a atualização do sistema resolve o caso; não é necessário mudar o banco.

## Saneamento pontual, somente se o pagador estiver vazio

O SQL abaixo foi preparado para o título específico do vídeo. Ele valida clientes sem ambiguidade, boleto 38709, cliente atendido Fortmais, situação paga, valor R$ 649,90 e vencimento em 15/10/2026.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db \
  sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -P pager=off' \
  < backend/scripts/sql/corrigir_pasta_pagador_38709.sql
```

- Pagador já é a Acquafort: apenas informa que o banco já está correto; nenhuma escrita.
- Pagador vazio: preenche exclusivamente `payer_client_id` do boleto 38709, atualiza seu timestamp e registra o motivo em `billing_change_logs`.
- Outro pagador explícito, nome ambíguo, título inexistente ou dados diferentes: aborta toda a transação. Confira a consulta antes de preparar outro saneamento.
- Não modifica `client_id`, contratos, veículos, componentes, pagamento, valor, recibo, dados bancários ou notas fiscais. Não percorre nem atualiza em massa os boletos antigos da Fortmais.

Os comandos não foram executados em produção. O saneamento foi validado em PostgreSQL descartável, com dados fictícios.

## Validação

- 17 testes novos de API passaram: pasta do pagador, títulos pagos/abertos, fallback legado, origem operacional, filtros combinados, componentes, exportações, permissões e carnês.
- 9 testes PostgreSQL do SQL passaram: saneamento, preservação integral do pagamento, repetição, nenhuma escrita quando já correto e bloqueio das divergências.
- 34 testes de interface passaram, incluindo a abertura do boleto da Fortmais pela pasta da Acquafort e a atualização da carteira após manutenção.
- TypeScript passou; ESLint sem erros, com três avisos preexistentes em Financeiro.
- A suíte ampliada de cobranças e boletos teve 135 testes aprovados e dois testes preexistentes falhando, reproduzidos também no commit original `a2ce8ff`: `TestListBillings.test_operational_can_list` espera acesso operacional que a rota já recusa; `test_envio_de_boleto_com_nfse_anexa_os_dois_pdfs_no_mesmo_email` espera competência antiga no nome do anexo. Essas regras não foram alteradas nesta correção.
