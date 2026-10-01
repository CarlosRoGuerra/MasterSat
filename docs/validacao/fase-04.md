# Validação — Fase 04 (migração SGR conservando valores e identidade)

- **Data:** 30/09/2026
- **Branch:** `fase-04-migracao-sgr`, criada sobre `fase-03-emissao-conciliacao`
  (`731461e`). As Fases 00–03 ainda não foram mergeadas e são pré-requisito
  (gate `checar_baseline_testes.py`, política `titulo_bancario.exigir`,
  `release_billing_competencia`, competência canônica, migration
  `b7d3e1f5a902`).
- **Commit da auditoria:** `28af706`. Cada achado foi reproduzido no HEAD
  `731461e` antes de editar — todos continuavam presentes
  ([provas-antes.json](fase-04/provas-antes.json)).
- **SHA validado:** `1ad34eb` (código e testes). A documentação vem no commit seguinte.
- **Evidências:** [fase-04/](fase-04/).

Nenhum comando usou produção, `.env` real, a API do SGR, boleto, NFS-e,
MinIO ou pagamento reais. Backend em container com `backend/` montado e
`.env` sobreposto por arquivo vazio; PostgreSQL 16 descartável em rede
Docker `--internal`; SGR, HTTP, DNS e storage são dublês.

## 1. Situação dos achados

| ID | Status | O que foi feito | O que falta |
|---|---|---|---|
| **SGR-01** desconto descartado | **Resolvido** (importação) · **Parcial** (legado) | Documento conciliado antes de liberar cobrança (soma das linhas = total em centavos); desconto alocado na obrigação da mesma placa; cobrança com o líquido; bruto/desconto/alocação em `sgr_documento_linhas`; bonificação integral = cancelada que ocupa o mês; documento que não fecha fica bloqueado. Backfill mede e compensa o legado com histórico | Decisão D4 para compensar os 1.079 documentos legados (R$ 33.758,16; **R$ 383,08 ainda em aberto**); D3 para os 435 documentos cuja soma ≠ total |
| **SGR-02** reexecução insert-only | **Resolvido** | Estado da origem por documento + impressão digital da cobrança local; aplica aberto→pago/cancelado/vencimento pela política bancária com histórico; edição local, transição fora da tabela, valor alterado e título ativo viram `sgr_conflitos`; `manter_local`/`aplicar_origem` registrados; reemissão no mesmo mês | Cliente que sai do escopo (cancelado no SGR) não é mais lido — conferir na janela de corte (guia, seção 7) |
| **SGR-03** PDF não retomado | **Resolvido** | Outbox `sgr_arquivos` reconciliado em toda rodada, independente da cobrança; `--arquivos` só reprocessa; documento antigo reaproveitado | — |
| **SGR-04** placa/IMEI de outro dono | **Resolvido** (importação) · **Parcial** (legado) | Identidade de origem; reuso só com dono conferido; transferência e IMEI em outro veículo viram conflito, sem contrato/cobrança cruzados; pagador interveniente vira `payer_client_id`; aprovação de transferência recusada com contrato ativo do dono anterior | 160 cobranças pagas legadas (7 veículos) com cliente ≠ dono atual, listadas para revisão (D6) |
| **SGR-05** coleta truncada | **Resolvido** | Paginador único em todos os caminhos (cliente, veículos, vínculos, rastreadores, boletos por cliente/abertos/período); página curta exige confirmação; página repetida/deslocada/teto = erro; manifesto de completude; cliente com coleta incompleta não é importado | Premissa não homologada: fim do índice = lista vazia/404 na API real (validar com `--limit 5` antes do corte) |
| **SGR-06** download sem controle | **Resolvido** | HTTPS, allowlist, IP público em cada salto, redirect manual limitado, streaming com teto, validação de PDF/XML (sem DTD) | Hosts reais a aprovar (D7); risco residual de DNS rebinding documentado |
| **SGR-07** upload órfão / transação longa | **Resolvido** | Checkpoint por cliente (transação curta); arquivos fora da transação, chave de objeto estável; falha do banco após upload converge na rodada seguinte; simulação sem rastro | — |

Achados novos (encontrados no caminho):

- **Dedup antigo colapsava linhas idênticas** do mesmo boleto (mesma placa,
  competência e valor): 89 documentos e R$ 7.158,92 nunca importados na
  base de desenvolvimento (D5). A importação nova preserva por ocorrência.
- **Dívida em aberto importada como cancelada**: se `/buscar_boletos_abertos_cliente`
  falhava, o importador seguia com a lista vazia e gravava os boletos
  ABERTO como cancelados. Agora o cliente fica com coleta incompleta.
- **Índice de rastreadores** parava em 15 páginas ou no primeiro erro,
  silenciosamente; agora pagina até o fim ou interrompe.
- **Valor malformado** de linha (`'49,995'`) era convertido em float e a
  linha zerada/nula sumia; agora bloqueia o documento quando muda a soma.
- **Ambiente de desenvolvimento**: o compose local monta `backend/` com
  `uvicorn --reload` e o boot roda `alembic upgrade head` — editar a branch
  aplicou a migration `a4c7e2f9b1d6` ao banco local (7 tabelas vazias;
  nenhum dado alterado). Para voltar a branch anterior:
  `docker compose exec backend python -m alembic downgrade b7d3e1f5a902`.

## 2. Provas antes/depois (mesmo script, mesma interface pública)

Script [provas_fase04.py](fase-04/provas_fase04.py): SQLite em memória com
os models de cada versão; transporte HTTP falso no nível do adapter (o
`requests` antigo segue redirect de verdade), DNS e storage falsos.

| Cenário | HEAD `731461e` | Branch `1ad34eb` |
|---|---|---|
| Boleto 100 − 20 (total 80) | cobranças somam **100,00** | **80,00** |
| Aberto na 1ª rodada, BAIXADO na 2ª | **PENDING**, sem data | PAID, 10/10/2026 |
| PDF com timeout na 1ª rodada, ok na 2ª | 1 tentativa, **0 documentos** | 2 tentativas, 1 documento |
| Mesma placa/IMEI em outro CPF | 2 contratos, **1 com cliente ≠ dono** | 1 contrato, 0 cruzados (conflito) |
| Cliente com 201 veículos | **200** coletados | 201 |
| Link que redireciona para `169.254.169.254` + arquivo de 12 MB | **acessou o endereço interno**, 2 documentos gravados | não acessou, 0 documentos (recusados) |
| Banco cai depois do upload | **2 objetos, 1 órfão** | 1 chave, 1 documento, 0 órfãos |
| Simulação (dry-run) | 0 escritas, 0 uploads, 0 requisições | idem |

Arquivos: [provas-antes.json](fase-04/provas-antes.json), [provas-depois.json](fase-04/provas-depois.json).

## 3. Dados afetados e backfill

Migration `a4c7e2f9b1d6` é aditiva (só cria as 7 tabelas); valores, status
e vínculos existentes não mudam. A proveniência do que já foi importado é
reconstruída pelo `scripts/sgr_backfill.py` a partir do `sgr_payload`
preservado — cliente/veículo/rastreador NÃO recebem código de origem
inventado (o payload não o tem): a próxima rodada cria a identidade por
chave natural, depois da verificação de dono.

Simulação sobre uma **cópia do banco local de desenvolvimento** (36.501
cobranças SGR, 35 s; cópia e dump apagados depois —
[agregado](fase-04/backfill-copia-dev-agregado.json), só números):

| Resultado | Documentos | Valor |
|---|---|---|
| Conciliados automaticamente | 11.404 | — |
| Desconto descartado pela importação antiga | 1.079 | R$ 33.758,16 (pagos R$ 31.024,36 · **em aberto R$ 383,08** · cancelados R$ 2.350,72) |
| Soma das linhas ≠ total | 435 | arredondamento 73 (−R$ 0,22) · desconto não discriminado 261 (−R$ 6.794,99) · acréscimo 101 (+R$ 3.930,85) |
| Linhas idênticas colapsadas (legado incompleto) | 89 | R$ 7.158,92 não importados (R$ 64,99 em aberto) |
| Desconto maior que as obrigações | 6 | — |
| Cobranças com cliente ≠ dono do veículo | 160 (7 veículos) | todas pagas, sem contrato |

Nenhuma correção foi aplicada no banco de desenvolvimento. A compensação
(D4) é `--aplicar --compensar-descontos --operador`, com `billing_change_logs`
(antes/depois) em cada cobrança; cancelada, com título bancário,
bonificada ou com valor pago próprio fica de fora e listada.

## 4. Testes

Comandos e saída em [backend-suite.txt](fase-04/backend-suite.txt)
(Python 3.12.14; alembic 1.13.2, SQLAlchemy 2.0.35, psycopg 3.2.1, FastAPI
0.142.2, pydantic 2.9.2, requests 2.33.0, lxml 5.3.0).

| Comando | Resultado |
|---|---|
| Comando-base da auditoria (6 arquivos `test_sgr_*`) | 177 passed |
| `tests/test_fase04_*.py` + `test_sgr_config/masking` | 104 passed |
| Suíte inteira + `checar_baseline_testes.py` | 2314 executados, 38 falhas — todas da baseline conhecida; **BASELINE OK** |
| `-m postgres` | 52 passed |
| `alembic upgrade head` + `alembic check` (banco novo) | ok; "No new upgrade operations detected" |
| CLI contra API simulada + PostgreSQL/MinIO descartáveis | o fluxo da CLI (`import_poc_result` + `processar_arquivos`) roda nos testes PG e no ensaio; o binário `sgr_import.py` em si **NÃO EXECUTADO** contra API simulada — ele instancia o `SGRClient` real e a regra é não chamar o SGR. MinIO descartável **NÃO EXECUTADO** (storage é dublê) |

Depois das 21h (BRT) o container em UTC já está no dia seguinte e 6
testes antigos com data fixa falham (`test_trackers_api::TestLinkVeiculo`,
`test_fase03_titulo_bancario::TestDesinstalacaoNaoMudaValorDeTitulo`) —
também no commit base `731461e`. A suíte foi rodada com
`TZ=America/Sao_Paulo`; sem isso, o gate acusa essas 6 depois das 21h.

## 5. Rollback ensaiado

[ensaio_rollback.py](fase-04/ensaio_rollback.py) /
[ensaio-rollback.txt](fase-04/ensaio-rollback.txt), PostgreSQL descartável:

1. Código novo: `upgrade head`, duas rodadas (desconto, pagamento entre
   rodadas, transferência pendente) → 2 cobranças (80,00 aberta; 50,00 paga).
2. Código novo: `downgrade b7d3e1f5a902` → 7 tabelas `fase04_preservado_*`;
   cobranças intactas.
3. Código **antigo** (`731461e`): revisão `b7d3e1f5a902 (head)`, a listagem
   da API serializa as 2 cobranças. O importador antigo, simulado sobre a
   mesma origem, **criaria 2 cobranças** (desconto em duplicidade e o
   documento da transferência) → proibição no guia de corte.
4. Código novo: `upgrade head` restaura as tabelas; nova rodada: 0 criadas,
   2 reaproveitadas, documentos inalterados.

Revertendo só o código, nada importado é apagado; correção de dado é
sempre operação compensatória registrada.

## 6. Contrato, frontend e compatibilidade

Nenhuma rota, schema de resposta ou tela mudou (`BillingOut` intacto; o
valor líquido usa as mesmas colunas). Sem commit de frontend. A CLI
mantém os parâmetros antigos e acrescenta novos. `ImportStats` mantém os
campos e ganha outros. Configurações novas têm padrão seguro
(`SGR_DOWNLOAD_HOSTS` vazio = só o host do SGR).

## 7. Decisões pendentes da empresa

Detalhe e efeito em [politica-conflitos.md](../migracao-sgr/politica-conflitos.md#6-decisões-da-empresa):
D3 (soma ≠ total), D4 (compensar descontos legados), D5 (linhas idênticas
colapsadas), D6 (vínculos históricos com dono diferente), D7 (hosts de
download). D1 (regra de alocação do desconto) e D2 (bonificação integral)
foram implementadas como proposta e podem ser revistas.

## 8. Limitações

- Cliente que sai do escopo ativo/inadimplente deixa de ser sincronizado.
- Cadastro (cliente/veículo/rastreador/contrato) não sincroniza atributos.
- Premissa de fim de paginação não homologada contra a API real.
- `montar_manifesto` consulta documento a documento (N+1); o tempo em base
  inteira (~13 mil documentos) **não foi medido** — medir na simulação
  antes do corte.
- DNS rebinding (seção "Risco residual" de [hosts-permitidos.md](../migracao-sgr/hosts-permitidos.md)).
