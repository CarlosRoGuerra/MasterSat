# Validação — Fase 03 (emissão, conciliação, pagamentos e histórico financeiro)

- **Data:** 30/09/2026
- **Branch:** `fase-03-emissao-conciliacao`, criada sobre
  `fase-02-obrigacoes-financeiras` (`44733f0`). As Fases 00, 01 e 02 ainda não
  foram mergeadas e são pré-requisito (gate `checar_baseline_testes.py`,
  migrations `d9e4f1a7b2c5` e `e5c2a9d71f04`, competência canônica e
  substituição estrutural).
- **Commit da auditoria:** `28af706`. Cada achado foi reproduzido no HEAD
  `44733f0` antes de editar — todos continuavam presentes
  ([provas-antes-44733f0.json](fase-03/provas-antes-44733f0.json)).
- **SHA validado:** `1b14099` (código). A documentação vem no commit seguinte.
- **Evidências brutas:** [fase-03/](fase-03/).

Nenhum comando usou produção, `.env` real, Ailos, boleto, NFS-e ou pagamento
reais. Backend em container com `backend/` montado e `.env` sobreposto por
arquivo vazio; PostgreSQL 16 descartável em rede Docker `--internal`; a Ailos
é sempre um transporte HTTP falso (títulos, timeouts e 5xx sintéticos).

## 1. Situação dos achados

| ID | Status | O que foi feito | O que falta |
|---|---|---|---|
| **FIN-02** | **Resolvido** | Política única (`app/services/titulo_bancario.py`) aplicada em todos os escritores: PUT, lote, unificação, receber, cancelar, excluir, liberar, reabrir, exclusão de contrato, pró-rata da desinstalação, regravação forçada. Cobrança com histórico bancário não é removida; cancelada/recebida por fora com título ativo fica com **baixa pendente** estrutural e segue monitorada; liberar o mês exige baixa confirmada (consulta situação 3/5 ou administrador). PDF, e-mail, link público e carnê não entregam título baixado/pendente | Baixa pela API não existe no convênio (manual no internet banking); confirmar papel de quem confirma baixa (decisão 7) |
| **FIN-03** | **Resolvido** | Cliente separa "não saiu" de "pode ter chegado"; falha do log não derruba registro aceito; emissão individual/lote/carnê marca `DESFECHO_DESCONHECIDO` em timeout, 5xx, "já cadastrado" sem dados e falha local após aceite; reserva órfã (30 min) idem; bloqueia toda mutação e reemissão; resolução pela consulta do número do documento (automática no worker e manual), retry que consulta antes, saída administrativa auditada | Validar em homologação que a Ailos responde 400/404 para documento inexistente (premissa da resolução automática) |
| **FIN-05** | **Resolvido** | Fila por `ultima_consulta_em` (nunca consultado primeiro), checkpoint também em erro, erro individual contado e gravado, orçamento por rodada; monitora também baixa pendente e desfecho; métricas de atraso/janela no log, em `GET /ailos/conciliacao` e alerta | Worker continua thread no processo web (INT-02); orçamento definitivo depende do limite da Ailos (decisão 6) |
| **FIN-06** | **Resolvido** | Diferença entre título e recebido exige `desconto`/`parcial`/`encargos`/`credito` (ajustes auditados; parcial cria cobrança do saldo); baixa automática só quita com valor igual, divergência vira pendência; estorno auditado; notas não sobrescritas; cancelada nunca quitada | Crédito sem uso automático e teto de encargos (decisões 3 e 4) |
| **FIN-07** | **Resolvido** (canal indisponível) | CNAB desligado por flag com motivo explícito na API e na tela; ligado (homologação): seleção validada sem parcial, sequência por layout, hash e conteúdo guardados, reserva compartilhada com a API, descarte auditado | **Homologação** com o banco e leitura do retorno CNAB antes de ligar |
| **FIN-08** | **Resolvido** | Máquina de estados (cancelada não paga; terminal não muda valor/vencimento; paga não é removida), estorno, trava de linha, `payable_change_logs` com antes/depois e responsável | — |
| **FIN-09** | **Resolvido na Fase 01** (`ed4e550`) | State com prazo e uso único atômico, refresh serializado — `tests/test_fase01_ailos_state.py` passa neste SHA | "Singleton por ambiente/convênio" e rotação da chave Fernet (fora do escopo, ver Fase 01) |
| **FIN-10** | **Resolvido** | `base_query` não esconde cobrança por contrato/plano/veículo/rastreador/cliente removido; lista, detalhe, exportação, recibo e extrato seguem; `relacoes_removidas` rotula; recibo usa o pagador histórico; nova cobrança continua recusando referência removida | — |
| **PROD-01** | **Resolvido** | Canceladas fora de todos os totais; cada campo com uma base nomeada; `/reports/revenue?base=vencimento|competencia`, caixa, encargos, principal, recorrente × avulso, definições na resposta; extrato atendido × pagador; série ordenada por ano/mês; [dicionário](../financeiro/dicionario-metricas.md) | Base padrão dos relatórios mantida em vencimento — confirmar |
| **PROD-02** | **Resolvido** | Suspensos e total (denominador), veículos distintos com rastreador, próximos vencimentos só hoje→+7 com vencidas em lista própria | — |

Achados novos corrigidos nesta fase (encontrados no caminho):

- **Desinstalação alterava valor de mensalidade com boleto registrado** (outro
  escritor fora da trava do PUT). Agora não altera e devolve
  `ajustes_pro_rata_pendentes`.
- **`marcar_billing_pago` sobrescrevia as notas** (a baixa automática apagava
  o histórico de negociação/cancelamento nas notas) e podia quitar uma
  cobrança cancelada entre a leitura e a trava. Agora acrescenta e recusa.
- **Regravação forçada** (`generate_monthly_billings(force)`) regravava até
  cobrança paga/cancelada. Agora só em aberto e sem título.
- **Série do gráfico do Financeiro ordenada como texto** (`01/2027` antes de
  `12/2026`); `revenue.slice(-8)` mostrava meses errados na virada do ano.
- **Migration**: o inventário levava 159,5 s em volume (LATERAL por título em
  `ailos_api_logs` sem índice); reescrito em operações de conjunto — 1,8 s.
- **Mensagem de erro** do cancelamento de cobrança paga citava "estorno", que
  não existia; agora existe.

## 2. Provas antes/depois (mesmo script, schema migrado pelo Alembic de cada versão)

| Cenário da auditoria | HEAD `44733f0` | Branch `1b14099` |
|---|---|---|
| Excluir cobrança com boleto registrado e lançar a mensalidade do mês | exclusão 200, nova 200 — **boleto antigo ativo sem obrigação** | exclusão 409 `boleto_ailos_registrado`, nova 409 |
| Cancelar liberando o mês com boleto ativo | 200 — mês liberado com boleto pagável | 409 `baixa_bancaria_pendente`; cancelar sem liberar 200 (baixa pendente); liberar depois 409 |
| Timeout de leitura após envio, depois PUT 100→130 e cancelar | gerar 400 `ERRO_REGISTRO`; **PUT 200 (130,00)**; cancelar 200 | gerar 502 `DESFECHO_DESCONHECIDO`; PUT 409 (100,00); cancelar 409 |
| Ailos aceita, gravação do log falha | **500, `ERRO_REGISTRO`** (título existe no banco) | 200, registrado |
| 1.001 títulos, 300 primeiros nunca pagos, 4 rodadas de 300 | rodada 1: **300 distintos, 0 baixas de 701** · rodada 2: 752 distintos, 452 baixas (ordem física, sem garantia) | **1.001 consultados, 701 baixas**, 0 erros |
| CNAB `[paga, paga, cancelada, registrada na API]` | 200, **4 segmentos P** no arquivo | 409 `canal_cnab_indisponivel`; com o canal ligado 422 (repetida) |
| Receber R$ 1 de R$ 100 | 200 — **paga, 99 somem** | 409 `recebimento_divergente`, continua pendente |
| Contas a pagar: pagar cancelada; editar valor/excluir paga | 200 / 200 / 200 (**valor 1,00, removida**) | 409 / 409 / 409 (500,00, preservada) |
| Excluir contrato com cobrança paga | lista 1→**0**, detalhe **404**, recibo **404** | lista 1→1, detalhe 200, recibo 200 |
| 2×R$100 negociadas em R$200 + R$100 de agosto paga em setembro | rota do Financeiro: emitido ago **400**; extrato **500** | emitido ago 300; caixa ago 0 / set 100; extrato 300 |
| 5 ativos + 5 suspensos; 5 atrasos antigos + 1 vence em 3 dias | total **sem suspensos (100% saudável)**; título de 3 dias **fora** da lista | total 11 (55%); próximos = [título de 3 dias]; vencidas separadas |

Arquivos: [provas-antes-44733f0.json](fase-03/provas-antes-44733f0.json),
[provas-antes-44733f0-rodada1.json](fase-03/provas-antes-44733f0-rodada1.json)
(mesma versão do código; o script mudou só no cálculo do percentual do
PROD-02), [provas-depois.json](fase-03/provas-depois.json), script
[provas_fase03.py](fase-03/provas_fase03.py).

## 3. Dados afetados e migration `b7d3e1f5a902`

Aditiva. Nenhum valor, status ou vínculo de cobrança muda; nada é apagado;
nenhuma chamada ao banco.

| Dado existente | Efeito |
|---|---|
| Título registrado de cobrança cancelada, removida ou paga fora do boleto | `baixa_status = 'pendente'` + observação "Fase 03 (inventário)" → volta à conciliação; liberar o mês passa a exigir baixa confirmada |
| `ERRO_REGISTRO` cuja última tentativa individual (log `POST gerar/boleto`) não teve HTTP, teve 408 ou 5xx | `status_ailos = 'DESFECHO_DESCONHECIDO'` + linha em `billing_change_logs` citando o log → a conciliação consulta e resolve |
| `ERRO_REGISTRO` sem log individual (ex.: lote), reserva órfã, paga com valor ≠ título sem ajuste, conta a pagar inconsistente | **só relatório** (preflight) para revisão |
| Tabelas novas | `billing_adjustments`, `payable_change_logs`, `cnab_remessas`, `cnab_remessa_itens` |

Antes do deploy: `python scripts/preflight_fase03.py` num **snapshot
restaurado** (só leitura; `--json` grava o inventário completo com IDs e
nossos números). Volume ([ensaio-volume.txt](fase-03/ensaio-volume.txt)):
300 mil cobranças, 150 mil títulos, 450 mil logs → upgrade **1,8 s**,
downgrade 1,3 s, novo upgrade 1,7 s; inventário 25.500 baixas pendentes e
3.000 desfechos, idêntico depois do ciclo.

### Contrato HTTP

Compatível para leitura (campos novos opcionais). Mudanças deliberadas de
comportamento — consumidor único é o frontend deste repositório, atualizado
no mesmo branch:

- `BillingOut`: `titulo_bancario`, `relacoes_removidas`. `AilosBoletoOut`:
  `baixa_status`, `pendencia`, `ultima_consulta_em`. `AilosPagamentoOut`:
  `pendencia`, `quitada`. `RevenueReportItem`: `total_received_by_due`.
- `POST /billings/{id}/receive`: `tratamento_diferenca`,
  `justificativa_diferenca`, `saldo_vencimento`; **recebimento divergente sem
  tratamento passa de 200 a 409**.
- Novos: `POST /billings/{id}/estornar`, `GET /billings/{id}/ajustes`,
  `POST /ailos/boletos/{id}/consultar-desfecho|declarar-nao-registrado|confirmar-baixa|resolver-pendencia`,
  `GET /ailos/conciliacao`, `GET /ailos/pendencias`, `GET /boletos/canais`,
  `GET /boletos/remessas`, `GET /boletos/remessas/{id}/arquivo`,
  `POST /boletos/remessas/{id}/descartar`, `POST /payables/{id}/estornar`,
  `GET /payables/{id}/historico`.
- 409 novos: `boleto_ailos_desfecho_desconhecido`, `boleto_ailos_baixado`,
  `baixa_bancaria_pendente`, `titulo_em_remessa_cnab`,
  `recebimento_divergente`, `parcial_indisponivel`, `saldo_em_aberto`,
  `pagamento_bancario_confirmado`, `nfse_vinculada`, `cobranca_em_aberto`,
  `canal_cnab_indisponivel`, `selecao_invalida`, `remessa_concorrente`,
  `conta_cancelada`, `conta_em_estado_terminal`, `conta_paga`, `conta_nao_paga`.
  502 `desfecho_desconhecido` na emissão.
- `DELETE /billings/{id}` de cobrança com histórico bancário: 200 → 409.
  Manutenção durante registro: código `boleto_ailos_registrado` →
  `boleto_ailos_em_registro` (mesmo 409; o frontend só trata o primeiro no
  cancelamento, que não mudou).
- `POST /boletos/cnab240|400`: 200 → 409 enquanto o canal estiver desligado.
- `/billings/reports/revenue`: canceladas fora; emitido por vencimento
  (antes: pagamento para as pagas). `/reports/client-statement`: canceladas
  fora dos totais, linhas do cliente como pagador incluídas (campo `papel`),
  `resumo_responsavel_financeiro`. Dashboard: `upcoming_billings` só hoje→+7
  (vencidas em `overdue_billings`).
- `DELETE /payables/{id}` de conta paga: 200 → 409; `PUT` de valor/vencimento
  em conta paga/cancelada: 200 → 409.

## 4. Testes

**Versões:** Python 3.12.14, alembic 1.13.2, SQLAlchemy 2.0.35, psycopg 3.2.1,
FastAPI 0.142.2, Pydantic 2.9.2, PostgreSQL 16; Node 20.20.2 / npm 10.8.2.

| Comando (diretório) | Resultado |
|---|---|
| Comando-base do prompt: `python -m pytest tests/test_ailos_boletos_service.py … tests/test_reports_api.py -q` (backend) | **189 aprovados, 6 falhas — as 6 da baseline** (R1 ×2, C1 ×4) |
| `python -m alembic upgrade head` (banco novo) | exit 0 até `b7d3e1f5a902` |
| `python -m alembic check` | exit 0 — `No new upgrade operations detected.` |
| `python -m pytest tests -q` + `checar_baseline_testes.py` | **2220 executados: 2182 aprovados, 38 falhas — as 38 conhecidas; gate OK**, 0 skips |
| `python -m pytest tests -q -m postgres` | 46 aprovados |
| `python -m pytest tests/test_fase03_*.py -q` | 101 aprovados |
| `tests/test_fase03_postgres.py` repetido 10× | 60/60 |
| `npm run lint` / `typecheck` / `test` / `build` (frontend) | todos exit 0; lint 0 erros / 40 avisos (igual à Fase 02); **189/189** testes; build ok |

Saídas: [comando-base.txt](fase-03/comando-base.txt),
[backend-suite.txt](fase-03/backend-suite.txt),
[frontend-resumo.txt](fase-03/frontend-resumo.txt).

Cobertura nova (101 backend + 11 frontend) — ver o commit `eeaaa2e`: timeout
após aceite simulado, DELETE/PUT/cancel em paralelo, consulta repetida, 1.001
títulos com os 300 primeiros não pagos, CNAB duplicado/pago/cancelado/já
registrado na API, parcial/maior/zero/negativo, contrato removido, receitas
por caixa/competência e reconciliação entre rotas.

Testes antigos atualizados (3 arquivos) por mudança **intencional**:
`test_ja_cadastrado_mas_consulta_vazia_*` (agora desfecho desconhecido),
`test_ailos_registration_serializes_financial_maintenance` (código
`boleto_ailos_em_registro`) e três testes da migration da Fase 02, que usavam
`head` como sinônimo da revisão da Fase 02.

**NÃO EXECUTADO:** homologação com a Ailos (consulta de documento
inexistente, tempo real de lote/carnê, limite de requisições); homologação
CNAB; teste de carga do worker; migração de snapshot de produção.

## 5. Rollback (ensaiado)

Com migrations no boot, a imagem antiga não sobe com a revisão
`b7d3e1f5a902`. Ordem ensaiada ([ensaio-rollback.txt](fase-03/ensaio-rollback.txt),
script [ensaio_rollback.py](fase-03/ensaio_rollback.py)):

1. Imagem **nova**: `alembic downgrade e5c2a9d71f04`. Estado de
   `ailos_boletos` vai para `fase03_preservado_ailos_boletos`; tabelas novas
   com dados viram `fase03_preservado_*` (vazias são removidas);
   `DESFECHO_DESCONHECIDO` vira `PROCESSANDO`, que o código antigo trata como
   "em registro".
2. Imagem **antiga** (`44733f0`) sobe e opera: PUT e cancelamento da cobrança
   ex-desfecho **continuam 409**; recebimento, criação e cancelamento
   normais 200; nenhum 500.
3. Imagem nova de novo: o upgrade devolve tabelas, ajustes (`desconto 10,00`),
   histórico de contas a pagar, desfecho desconhecido e pendência; a cobrança
   que o código antigo recebeu por fora com título ativo entra no inventário
   (baixa pendente). `alembic check` limpo; PUT no desfecho volta a 409.

Nenhum passo apaga evento, número bancário ou pendência, nem reenvia nada ao
banco. **Rollback sem downgrade** também é possível para o CNAB (flag) — os
demais comportamentos dependem do código. Preferir correção para frente.

## 6. Decisões pendentes da empresa

Proposta e efeito de cada uma em
[politica-pagamento-estorno-conciliacao.md](../financeiro/politica-pagamento-estorno-conciliacao.md#decisões-pendentes-da-empresa):
parcial (saldo em nova cobrança), parcial com serviço, uso do crédito, teto
de encargos, prazos de desfecho (30/10 min), orçamento da conciliação
(300/h), quem confirma baixa manual, estorno de pagamento bancário, conta a
pagar cancelada terminal e revisão do inventário. Mais: base padrão dos
relatórios (vencimento, mantida).

## 7. Limitações e riscos residuais

- **Baixa bancária é manual** no convênio; o sistema rastreia e confirma pela
  consulta, mas não executa a baixa.
- **Semântica de "não encontrado"** na consulta Ailos (400/404) é premissa não
  homologada; se a Ailos responder diferente, a resolução automática fica em
  "aguardar" e a saída administrativa resolve.
- **Retry manual de reserva recente** (menos de 30 min) ainda reenvia o POST,
  como antes — protegido pela deduplicação por número do documento no banco.
- **CNAB**: sem homologação e sem leitura de retorno; itens de remessa não
  têm rastreio de baixa. Canal desligado.
- **Cliente removido** não cancela as cobranças abertas dele (anterior à fase);
  agora elas aparecem nas listas (antes ficavam ocultas, mas já somavam nos
  totais).
- **Worker em thread do processo web** (INT-02): com vários workers, o
  advisory lock evita sobreposição, mas não há fila durável.
- Documentação e relatório estão nesta branch; **nada foi mergeado, enviado
  ou implantado**.

## Commits

| SHA | Commit |
|---|---|
| `1a4aecb` | feat(db): estado bancário, ajustes, remessa CNAB e histórico de contas a pagar |
| `a512c55` | fix(ailos): desfecho desconhecido bloqueia a cobrança; conciliação percorre a carteira inteira |
| `d2e9389` | fix(financeiro): política bancária única, recebimento sem diferença silenciosa e histórico de cadastros removidos |
| `feb36b3` | fix(cnab): canal indisponível até homologação; remessa registrada com reserva compartilhada |
| `3ef38f5` | fix(payables): máquina de estados, trava e histórico nas contas a pagar |
| `dc166c5` | fix(relatorios): canceladas fora dos totais, bases temporais nomeadas e dashboard com denominadores explícitos |
| `eeaaa2e` | test: regressões da Fase 03 |
| `019e5bc` | feat(frontend): desfecho, baixa e pendências bancárias, recebimento com diferença e métricas com base nomeada |
| `1b14099` | perf(db): inventário da migration da Fase 03 em operações de conjunto |
