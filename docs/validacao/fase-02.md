# Validação — Fase 02 (obrigações financeiras, constraints e migrations)

- **Data:** 30/09/2026
- **Branch:** `fase-02-obrigacoes-financeiras`, criada sobre
  `fase-01-autorizacao-identidade` (`71acf47`). As Fases 00 e 01 ainda não
  foram mergeadas e são pré-requisito (baseline de testes, gate
  `checar_baseline_testes.py`, migration `d9e4f1a7b2c5`).
- **Commit da auditoria:** `28af706`. Cada achado foi reproduzido no HEAD
  `71acf47` antes de editar — todos continuavam presentes
  ([provas-antes-71acf47.json](fase-02/provas-antes-71acf47.json)).
- **SHA validado:** `db64531` (código). A documentação vem no commit seguinte.
- **Evidências brutas:** [fase-02/](fase-02/).

Nenhum comando usou produção, `.env` real, serviço externo, boleto, NFS-e ou
Ailos. Backend em container com cópia do `backend/` sem `.env`; PostgreSQL 16
descartável em rede Docker `--internal`; cada teste de migration usa um banco
criado e apagado por ele.

## 1. Situação dos achados

| ID | Status | O que foi feito | O que falta |
|---|---|---|---|
| **FIN-01** | **Resolvido** | `substituted_by_id` estrutural em consolidação e negociação (backfill pelas notas). Cancelar/excluir substituto exige reverter e reabre as originais; excluir original com substituto válido e cancelar substituto em lote são recusados. Cancelada continua ocupando o mês; liberar é explícito e auditado (`liberar_competencia` / `POST /billings/{id}/liberar-competencia`). Mensagem do carnê corrigida | Empresa confirmar a política de cancelamento (decisão 1) |
| **FIN-04** | **Resolvido** (mensal) / **parcial** (não mensal) | `billings.competencia` canônica mantida por trigger; índice por competência no lugar do textual; API grava rótulo canônico, valida tipo e período; 409 de domínio no lugar de 500 | Carnê para plano não mensal (decisão 2) |
| **FIN-11** | **Resolvido** | Divisão em centavos: mesma regra histórica onde já era válida, sem parcela ≤ 0, recusa quando não cabe 1 centavo. Total em `Decimal`, preço com até 2 casas, prévia do fechamento igual ao gravado | Valor mínimo por parcela (decisão 3) |
| **FIN-12** | **Resolvido** | Trava do serviço + releitura antes de gerar; serviço embutido revalidado sob trava; índice `uq_billings_item_parcela_efetiva` como barreira final | — |
| **DB-01** | **Resolvido** | Os 12 índices declarados nos models (a unicidade de mensalidade substituída pela de competência). `alembic check` limpo; teste de drift | — |
| **DB-02** | **Parcial** (por decisão) | CHECKs de valor, parcela, liberação e substituição em `billings`, `client_charge_items`, `billing_charge_items`, com preflight e exceção nominal para removidos. POST `/billings` valida veículo/rastreador | FK composta cliente/contrato/veículo não foi criada: dados históricos apontam legitimamente para cadastros transferidos. Escritores fora da API (importador SGR) seguem protegidos só por índice/CHECK |
| **DB-03** | **Resolvido** | Carimbo de banco pré-Alembic só com schema completo, comparado a um manifesto gerado das migrations; senão o boot para com diagnóstico | — |
| **DB-04** | **Resolvido** | Downgrade da baseline remove os 8 enums (sem CASCADE); ciclo base→head→base→head testado | — |

Achado novo corrigido nesta fase:

- **Deadlock carnê × mensalidade manual (500).** Encontrado pelo teste de
  duas sessões: o carnê travava o contrato e esperava o índice; o POST
  `/billings` tinha o índice e esperava o contrato na checagem da FK. Agora
  os dois seguem o mesmo protocolo (trava do contrato antes da checagem).
  Ver [concorrencia.md](../financeiro/concorrencia.md).

## 2. Provas antes/depois (mesmo script, schema migrado pelo Alembic de cada versão)

| Cenário da auditoria | HEAD `71acf47` | Branch `db64531` |
|---|---|---|
| `09/2099` e `9/2099` no mesmo contrato | 200, 200 — **2 gravadas** | 200, 409 — 1 gravada |
| Cancelar e gerar carnê do mês | 409 mandando "cancele" (já cancelada) | 409 explicando liberação; liberar → carnê 200 |
| Excluir boleto único e simular | exclusão 200, **0 cobranças em aberto** | 409 sem confirmar; revertendo → **2 em aberto** |
| Serviço 0,02 em 4 parcelas | 200, parcelas `0,01 ×3 e -0,01` | 422 |
| Fechamentos set/out do mesmo serviço, em paralelo | **2 parcelas 1** | 1 parcela; o outro 200 com `services_already_billed` |
| `alembic check` num banco recém-migrado | exit 255, **12 remove_index** | exit 0, nenhuma operação |
| upgrade → downgrade base → upgrade | 3º passo falha: `type "clientstatus" already exists` | os três exit 0 |
| Banco pré-Alembic sem `billings.period_label` | **carimbou `96f61a589162`** e quebrou numa migration adiante | recusou antes de carimbar; banco intocado |

Arquivos: [provas-antes-71acf47.json](fase-02/provas-antes-71acf47.json),
[provas-depois.json](fase-02/provas-depois.json), script
[provas_fase02.py](fase-02/provas_fase02.py).

## 3. Dados afetados e migration `e5c2a9d71f04`

Aditiva. Nada é apagado, cancelado ou tem valor alterado.

| Dado existente | Efeito |
|---|---|
| Todas as cobranças com rótulo | `competencia` preenchida (lotes de 5.000 ids); rótulo não muda |
| Canceladas com "Consolidada no boleto único #N." / "Unificada na cobrança #N." | `substituted_by_id = N`, se #N existe e a nota não registra a reversão |
| Cancelada simples que divide o mês com outra do contrato (`9/2026` + `09/2026`) | `competencia_liberada = true` + nota + `billing_change_logs` |
| Duas mensalidades **efetivas** no mesmo mês, parcela de serviço duplicada, valor ≤ 0 não removido, parcela fora do intervalo | **migration aborta** listando IDs e valores; saneamento manual em [saneamento-fase-02.md](../financeiro/saneamento-fase-02.md) |
| Índice `uq_billings_contract_period_recurring` | substituído por `uq_billings_contract_competencia_recorrente` |

Antes do deploy: `python scripts/preflight_fase02.py` num **snapshot
restaurado** (só leitura). Volume: 575 mil cobranças sintéticas → upgrade em
**37,5 s** (backend fora do ar durante esse tempo, pois o boot espera a
migration); downgrade 1,6 s; novo upgrade 24,6 s
([ensaio-volume.txt](fase-02/ensaio-volume.txt)).

### Contrato HTTP (compatível)

- `BillingOut`: `competencia`, `competencia_liberada`, `substituted_by_id` (novos, opcionais).
- `POST /billings/{id}/cancel`: `liberar_competencia`, `reverter_substituicao` (opcionais, padrão `false`).
- `DELETE /billings/{id}?reverter_substituicao=true`; resposta ganha `reabertas`.
- `POST /billings/{id}/liberar-competencia` (novo).
- `POST /billings`: passa a recusar `billing_type` desconhecido e mensalidade
  sem período reconhecível (422); rótulo equivalente vira `09/2026`.
- `POST /client-charge-items`: recusa preço com mais de 2 casas e total que
  não comporta as parcelas (422).
- Fechamento: resultado ganha `services_already_billed`.
- 409 novos: `competencia_ocupada`, `titulo_substituto`, `titulo_substituido`,
  `cobranca_nao_cancelada`, `competencia_nao_ocupada`,
  `original_com_boleto_registrado`.

## 4. Testes

**Versões:** Python 3.12.14 (`python:3.12-slim` + `requirements*.txt`),
alembic 1.13.2, SQLAlchemy 2.0.35, psycopg 3.2.1, PostgreSQL 16; Node 20.20.2
/ npm 10.8.2 (`node:20-alpine`).

| Comando (diretório) | Resultado |
|---|---|
| `python -m alembic upgrade head` (backend, banco novo) | exit 0 até `e5c2a9d71f04` |
| `python -m alembic check` (idem) | exit 0 — `No new upgrade operations detected.` |
| `python -m pytest tests/test_billings_concurrency_guards.py tests/test_database_concurrency_postgres.py tests/test_financial_decimal_precision.py tests/test_billing_closure_service.py -q` | **113 aprovados** |
| `python -m pytest tests -q` (SQLite + `TEST_DATABASE_URL`) + `checar_baseline_testes.py` | **2119 executados: 2081 aprovados, 38 falhas, 0 skips — as 38 conhecidas da baseline; gate OK** |
| `python -m pytest tests -q -m postgres` | 36 aprovados |
| `tests/test_fase02_postgres.py` repetido 12× (depois da correção do deadlock) | 60/60 |
| `npm ci`, `lint`, `typecheck`, `test`, `build` (frontend) | todos exit 0; lint 0 erros / 40 avisos (baseline 41); **178/178** testes |

Saídas: [comando-base.txt](fase-02/comando-base.txt),
[backend-suite.txt](fase-02/backend-suite.txt),
[frontend-resumo.txt](fase-02/frontend-resumo.txt).

Cobertura nova (86 testes backend + 6 frontend): duas sessões mesmo
contrato/mês, rótulos equivalentes, carnê × manual, parcela entre meses,
agregado removido, cancelamento legítimo × substituído, liberação, 0,02/4,
varredura 1 centavo–R$ 7 × 1–60 parcelas (soma exata, nada ≤ 0, idêntico à
regra antiga onde ela era válida), soft delete/reuso, snapshot legado parcial
e adiantado, upgrade vazio, anterior→head, check sem drift, ciclo reversível,
preflight sem efeito colateral, paridade parser Python × SQL.

Dois testes antigos foram corrigidos por criarem dado que o índice de produção
já recusava (duas mensalidades do mesmo contrato no mesmo mês); só passavam
porque o índice não estava no metadata (DB-01).

## 5. Rollback (ensaiado)

Com migrations no boot, a imagem antiga **não sobe** com a revisão
`e5c2a9d71f04`. Ordem ensaiada ([ensaio-rollback.txt](fase-02/ensaio-rollback.txt)):

1. Imagem **nova**: `alembic downgrade d9e4f1a7b2c5`. Guarda as competências
   liberadas em `fase02_competencias_liberadas`; recusa, listando, se alguma
   já foi recobrada. Vínculos de substituição continuam nas notas.
2. Subir a imagem **antiga** (`71acf47`). No ensaio ela operou (mensalidade
   `9/2099`, carnê, negociação, cancelamento) sem nenhum 500.
3. Voltando à imagem nova, o upgrade refaz competência e vínculos — inclusive
   da negociação feita pelo código antigo —, devolve as liberações, e o mês
   `9/2099` criado pelo código antigo bloqueia uma nova `09/2099` (409).
   `alembic check` limpo.

Nenhum passo reverte pagamento ou obrigação. Preferir correção para frente.

## 6. Decisões pendentes da empresa

Detalhes, proposta e efeito em
[invariantes-financeiras.md](../financeiro/invariantes-financeiras.md#decisões-pendentes):

1. Cancelamento ocupa o mês por padrão (mantido) — confirmar.
2. Carnê para plano não mensal — proposta: recusar até definir.
3. Valor mínimo por parcela de serviço — proposta: R$ 5,00.
4. Rótulos legados fora de formato — corrigir pela lista do preflight.

## 7. Limitações e riscos residuais

- Deadlock fechamento × exclusão de contrato (anterior à fase, janela
  estreita) e deadlock que ainda vira 500 — ver
  [concorrencia.md](../financeiro/concorrencia.md#riscos-residuais-conhecidos).
- Contrato trimestral que vira mensal colide só no primeiro mês do trimestre.
- Excluir um contrato não mexe no boleto único que cobre a mensalidade dele
  (o boleto único não tem contrato): a cobrança consolidada segue aberta.
- O tempo real da migration depende do volume de produção; medir no snapshot.
- Documentação e relatório estão nesta branch; nada foi mergeado, enviado ou
  implantado.
