# Dicionário de métricas financeiras

Fase 03 (PROD-01, PROD-02). Cada número tem **uma** base temporal e **uma**
regra de inclusão, escrita aqui. Se duas telas mostram o mesmo rótulo, elas
usam a mesma definição; quando a base difere, o rótulo diz qual é.

## Regras comuns

- **Removida** (`is_deleted`) nunca entra em total.
- **Cancelada nunca soma** em emitido, recebido ou aberto: não é receita nem
  dívida. Consolidação e negociação cancelam as originais — somá-las contava a
  dívida duas vezes. Cancelada continua aparecendo nas listas e no extrato.
- Cadastro removido (contrato, veículo, plano, cliente) **não** tira a
  cobrança dos totais nem das listas (FIN-10).
- Dinheiro somado em `Decimal`, arredondado a centavos só na resposta.
- Paga sem `paid_amount` (legado) conta pelo valor do título.

## Bases temporais

| Base | Data usada | Responde a |
|---|---|---|
| **vencimento** | `due_date` | quanto venceu no mês e quanto disso entrou |
| **competência** | `competencia` (período cobrado; sem rótulo reconhecível cai no vencimento) | quanto o mês de serviço gerou |
| **caixa** | `payment_date` | quanto dinheiro entrou no mês, de qualquer vencimento |

## Métricas

| Métrica | Onde | Definição |
|---|---|---|
| Emitido | `/reports/revenue` `total_emitido`; `/billings/reports/revenue` `total_billed` | soma de `amount` das não canceladas, pela base escolhida (padrão: vencimento; a rota do Financeiro é sempre vencimento) |
| Recebido do emitido | `/reports/revenue` `total_recebido`; `/billings/reports/revenue` `total_received_by_due` | soma do pago das cobranças daquele mês **na mesma base do emitido**. Taxa de recebimento = este ÷ emitido |
| Em aberto | `total_aberto` / `total_outstanding` | soma de `amount` de pendentes e vencidas, na base do emitido |
| Recebido (caixa) | `/reports/revenue` `total_recebido_caixa`; `/billings/reports/revenue` `total_received`; `/billings/summary` `paid_this_month`; dashboard `received_month` | soma do pago com `payment_date` no mês |
| Encargos (caixa) | `encargos_caixa` | ajustes `encargos` + `credito` ativos dos recebimentos do mês |
| Principal recebido (caixa) | `recebido_principal_caixa` | caixa − encargos |
| Descontos (caixa) | `descontos_caixa` | ajustes `desconto` ativos dos recebimentos do mês (não entram no caixa) |
| Recorrente × avulso | `emitido_recorrente`, `emitido_avulso` | emitido dos tipos que ocupam competência (mensalidade, pró-rata, 1ª mensalidade, carnê) × os demais (serviço, taxas, negociação, saldo) |
| Receita 6 meses | `/reports/summary` `receita_6_meses` (`bases.receita_6_meses = vencimento`) | recebido do emitido, por vencimento |
| Inadimplência | `/reports/delinquents`, `/billings/reports/delinquent` | vencidas em aberto, agrupadas pelo **responsável financeiro** (pagador) |
| Extrato — `resumo` | `/reports/client-statement/{id}` | cobranças em que o cliente é o **atendido** (formato anterior); canceladas listadas, fora dos totais |
| Extrato — `resumo_responsavel_financeiro` | idem | cobranças em que o cliente é o **pagador** (inclui as de outros clientes que ele paga como interveniente) |
| Clientes — total | dashboard `clients.total` | ativos + inativos + inadimplentes + **suspensos** (denominador do "% saudável") |
| Veículos com rastreador | dashboard `vehicles.with_tracker` | veículos **distintos** não removidos com rastreador instalado não removido |
| Próximos vencimentos | dashboard `upcoming_billings` | pendentes/vencidas com vencimento de **hoje a hoje+7**, mais próximas primeiro (máx. 5) |
| Vencidas | dashboard `overdue_billings` | em aberto com vencimento **antes de hoje**, mais antigas primeiro (máx. 5) |

## Reconciliação entre telas

Na mesma base e período, para cada mês:

- `/reports/revenue` (base vencimento) `total_emitido` = `/billings/reports/revenue` `total_billed`;
- `total_recebido` = `total_received_by_due`; `total_aberto` = `total_outstanding`;
- `total_recebido_caixa` = `total_received`.

Provado em `tests/test_fase03_historico_metricas.py::TestBasesDasMetricas::test_rotas_reconciliam_na_mesma_base`.

## Mudanças de significado nesta fase (comunicar)

| Número | Antes | Agora |
|---|---|---|
| "Faturamento mensal" do Financeiro (emitido) | somava canceladas; paga ia para o mês do pagamento | sem canceladas; sempre mês do vencimento |
| Extrato `total_cobrado` | somava canceladas | sem canceladas |
| Dashboard "% clientes saudáveis" | suspensos fora do denominador | todos os estados no denominador |
| Dashboard "Com rastreador" | nº de rastreadores instalados | nº de veículos distintos |
| Dashboard "Próximos vencimentos" | incluía atrasos antigos | só hoje→+7; vencidas em lista própria |
| Série do gráfico | ordenada como texto (virada de ano fora de ordem) | ordenada por ano/mês |
