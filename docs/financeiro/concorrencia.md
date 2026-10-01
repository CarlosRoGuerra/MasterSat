# Contrato de concorrência — obrigações financeiras

O que o sistema garante quando duas operações disputam a mesma obrigação, e
em que ordem as travas são tomadas. Vale para PostgreSQL (produção); o SQLite
dos testes não tem travas de linha nem advisory lock.

## Garantia

Quem perde uma corrida recebe **conflito de domínio (409, mensagem de
negócio)** ou **resultado idempotente** — nunca 500 e nunca uma segunda
obrigação efetiva. Duas camadas:

1. **Protocolo de travas** na aplicação: serializa quem mexe na mesma
   obrigação; o segundo relê e decide com dado atual.
2. **Índice único** no banco como barreira final, para qualquer caminho que
   esqueça a trava (SQL manual, código antigo num rollback, rota nova). A
   violação é traduzida para 409 em `billings.py` (`_BILLING_CONFLICTS`).

## Protocolo por operação

| Operação | Trava, nesta ordem | Perdedor recebe |
|---|---|---|
| Fechamento (`/billing-closure/generate`) | advisory da competência (`AAAAMM`) → contratos envolvidos `FOR UPDATE` (ordem de id) → serviços embutidos na 1ª mensalidade `FOR UPDATE` (revalida) → cada serviço avulso `FOR UPDATE` ao gerar | Mesmo mês: espera e vê `already_generated`. Mês diferente, mesmo serviço: 200 com o item em `services_already_billed`. Serviço embutido faturado no meio: 409 "Refaça a simulação" |
| Carnê (`/billings/parcelar`) | contrato `FOR UPDATE` | 409 com os meses já ocupados |
| Mensalidade manual (`POST /billings`, tipo mensal com contrato) | contrato `FOR UPDATE` (**novo na Fase 02**) | 409 `competencia_ocupada` |
| Cobrança avulsa manual | nenhuma (não ocupa mês) | — |
| Receber / cancelar / manutenção / excluir | cobrança `FOR UPDATE` (+ serviços da cobrança) | 400/409 com estado atual |
| Unificar (negociação) | cobranças `FOR UPDATE` (ordem de id) → serviços | 400 "apenas pendentes/vencidas" |
| Cancelar/excluir substituto com reversão | substituto → originais `FOR UPDATE` → serviços | 409 `titulo_substituto` se sem confirmação |
| Excluir contrato | contrato `FOR UPDATE` → cobranças abertas → serviços | 409 se alguma cobrança está em registro/desfecho desconhecido (Fase 03) |
| Emitir boleto Ailos (individual/lote/carnê) — Fase 03 | cobranças `FOR UPDATE` → política → reserva `REGISTRANDO` **confirmada antes** da chamada HTTP (trava liberada durante a rede) | 409 `boleto_ailos_em_registro`/`..._desfecho_desconhecido`/`..._registrado`/`titulo_em_remessa_cnab` |
| Consulta de pagamento (conciliação/manual) — Fase 03 | chamada HTTP **sem** trava → cobrança `FOR UPDATE` com releitura → aplica | cancelada/recebida no meio: vira pendência, nunca quitação |
| Remessa CNAB (canal ligado) — Fase 03 | cobranças `FOR UPDATE` → política → itens `reservado` | 409 `titulo_remessa_cnab`/`remessa_concorrente` (índice `uq_cnab_remessa_itens_billing_reservado`) |
| Contas a pagar: pagar/cancelar/editar/estornar/excluir — Fase 03 | conta `FOR UPDATE` com releitura | 400/409 com o estado atual |

Ordem global resultante: **advisory → contrato → cobrança → serviço**. Nenhum
caminho trava contrato depois de cobrança, nem cobrança depois de serviço.

### Por que a mensalidade manual passou a travar o contrato

Teste `test_carne_e_lancamento_manual_concorrentes_no_mesmo_mes` achou um
deadlock: o carnê segurava o contrato e esperava a entrada do índice único; o
`POST /billings` já tinha a entrada do índice e, na checagem da FK de
`contract_id`, esperava o contrato. O PostgreSQL abortava um dos dois (500).
Com o mesmo protocolo nos dois caminhos, o segundo espera e recebe 409. O ciclo
já existia antes da fase com rótulos idênticos; com a competência canônica ele
passou a ocorrer também com `9/2026` × `09/2026`.

## Barreiras do banco

| Índice | Garante | Predicado |
|---|---|---|
| `uq_billings_contract_competencia_recorrente` | uma mensalidade por contrato/competência | não removida, competência não liberada, tipo mensal |
| `uq_billings_item_parcela_efetiva` | uma parcela efetiva por serviço/número | não removida e (não cancelada ou substituída) |
| `ix_ailos_boletos_billing_id` (único, anterior) | um registro Ailos por cobrança | — |

## Evidência

`backend/tests/test_fase02_postgres.py` (duas sessões reais, repetido 12× sem
falha) e `docs/validacao/fase-02/provas-*.json` (antes: duas parcelas 1 e duas
mensalidades de setembro; depois: uma de cada).

Fase 03: `backend/tests/test_fase03_postgres.py` (repetido 10× sem falha) —
DELETE/PUT/cancelar em paralelo sobre título registrado (só o cancelamento
vence, baixa pendente gravada); quatro mutações simultâneas sobre desfecho
desconhecido (todas 409); manutenção durante um POST de registro que termina
em timeout (409 em registro → 502 desfecho → 409 desfecho); cancelamento no
meio da consulta de pagamento (fica cancelada, vira pendência); pagar ×
cancelar conta a pagar (um vence); duas remessas CNAB para o mesmo título
(uma vence).

## Riscos residuais conhecidos

- **Fechamento × exclusão de contrato.** O fechamento reclassifica
  pendente→vencida (trava linhas de cobrança) **antes** de travar contratos; a
  exclusão de contrato trava contrato → cobranças. Se as duas coincidirem num
  contrato com cobrança mudando de status naquele instante, o PostgreSQL pode
  abortar uma delas por deadlock (500; a outra conclui, sem dado inconsistente).
  Janela estreita porque o worker horário já reclassifica. Anterior a esta
  fase; correção sugerida: mover a reclassificação do fechamento para depois
  das travas de contrato.
- **Deadlock não vira 409.** Um `DeadlockDetected` que escape do protocolo
  ainda aparece como 500 (a transação é desfeita inteira). Tratar com retry
  automático fica para quando houver caso real.
- **Importador SGR** grava sem travar contrato; roda offline, em lote único.
  O índice o protege; rodar com usuários operando exige cuidado.
- **Registro Ailos: a trava do Billing é solta durante a rede** (Fase 03, de
  propósito — segurar linha durante 30 s de timeout travaria o financeiro). O
  que protege a janela é a reserva `REGISTRANDO` confirmada antes da chamada;
  a política a trata como "em registro" e, após 30 min sem resposta, como
  desfecho desconhecido. Retry manual numa reserva *recente* ainda é aceito
  (comportamento anterior, coberto pela deduplicação do banco por número do
  documento + recuperação "já cadastrado").
- **Conciliação e resolução manual em paralelo** podem consultar o mesmo título
  duas vezes; a aplicação do resultado é serializada pela trava da cobrança e
  idempotente (segunda consulta não quita de novo).
