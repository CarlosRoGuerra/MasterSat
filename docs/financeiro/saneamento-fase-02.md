# Saneamento e backfill — migration `e5c2a9d71f04` (Fase 02)

Para quem vai aplicar a Fase 02 num banco com dados. A migration roda no boot
do backend; se o preflight encontrar dado incompatível, **o boot para** e nada
é alterado. Este roteiro evita essa parada e diz como resolver cada caso sem
apagar histórico financeiro.

## 1. Antes do deploy: rodar o preflight num snapshot

Rode num **backup restaurado** (procedimento em
[contingencia-recuperacao.md](../contingencia-recuperacao.md)), não na produção.
O script é só leitura — cria uma cópia temporária da função em `pg_temp`, dentro
de uma transação que sempre termina em `ROLLBACK` — mas não há motivo para
carregar o banco de produção com ele.

```bash
# dentro de um container do backend apontando para o snapshot
python scripts/preflight_fase02.py --json preflight.json
echo $?   # 0 = pode aplicar; 1 = há bloqueios
```

A saída lista cada código com IDs e valores. **BLOQUEIA** faria a migration
abortar; **inventário** é informativo ou será tratado pela própria migration.

## 2. Códigos que bloqueiam

Regra geral: resolva pela API sempre que der (fica no histórico da cobrança).
SQL direto só onde indicado, com backup feito e anotação na cobrança.

### `mensalidade_duplicada`

Duas ou mais mensalidades **efetivas** (não canceladas, ou canceladas por
substituição) do mesmo contrato na mesma competência — por exemplo `09/2026` e
`9/2026`, que o índice textual antigo aceitava. É dupla cobrança.

| Situação | O que fazer |
|---|---|
| Uma paga e outra em aberto, sem boleto registrado | Remover a aberta: `DELETE /api/v1/billings/{id}` (soft delete; a linha fica) |
| Uma em aberto com boleto Ailos registrado | Resolver antes a situação bancária (baixa manual na Ailos), depois remover |
| Duas pagas (dinheiro real recebido duas vezes) | **Decisão financeira** (estorno/crédito). Tecnicamente, a segunda deixa de ser mensalidade: `UPDATE billings SET billing_type = 'avulsa', notes = notes \|\| ' \| Mensalidade {mês} em duplicidade — reclassificada como avulsa no saneamento da Fase 02' WHERE id = …` — o mesmo tratamento que o importador SGR já dá a um 2º boleto do mês |
| Original consolidada (boleto único) + mensalidade avulsa viva do mesmo mês | O mês está cobrado duas vezes (no boleto único e na avulsa). Remover a que não deveria existir; se for o boleto único, excluí-lo pela API com `reverter_substituicao=true` **depois** de aplicar a fase |

### `parcela_servico_duplicada`

Duas parcelas efetivas do mesmo serviço e número (o FIN-12). Em aberto:
cancelar a repetida pela API (`POST /billings/{id}/cancel`) — parcela cancelada
sem substituto sai do índice. Duas pagas: decisão financeira; tecnicamente
`UPDATE billings SET installment_number = NULL, notes = notes || ' | Parcela paga em duplicidade (FIN-12) — ver saneamento Fase 02' WHERE id = …`.

### `cobranca_valor_invalido`

Cobrança **não removida** com valor ≤ 0, ou qualquer cobrança com valor pago
≤ 0.

- Parcela negativa/zerada de serviço (FIN-11), em aberto: ajuste a parcela
  irmã para absorver a diferença (`PUT /billings/{id}` com justificativa, se não
  houver boleto registrado) e **remova** a inválida (`DELETE`). Cobrança
  removida não é validada (exceção nominal, ver abaixo).
- `paid_amount = 0` (vinha de importação antiga, significa "sem valor pago
  informado"): `UPDATE billings SET paid_amount = NULL WHERE id = … AND paid_amount = 0`.
- Valor ≤ 0 em cobrança paga: decisão financeira; não há correção automática.

### `cobranca_parcela_invalida`

Número de parcela < 1 ou maior que o total. Corrigir o número/total conforme
o documento de origem (SGR: campo `parcela` "N de M" no `sgr_payload`).

### `servico_avulso_invalido`

Serviço avulso não removido com quantidade/parcelas < 1 ou preço/total ≤ 0.
Sem cobrança efetiva: remover (`DELETE /client-charge-items/{id}`) ou corrigir
por `PUT` (que agora valida o par total/parcelas). Com cobrança: decisão caso a
caso.

### `vinculo_servico_invalido`

Vínculo cobrança↔serviço com valor ≤ 0. Não há caso conhecido; se aparecer,
investigar a cobrança combinada antes de corrigir.

## 3. O que a própria migration faz (backfill)

Tudo na mesma transação; se qualquer passo falhar, nada fica.

| Passo | Dado afetado | Como |
|---|---|---|
| Competência | todas as cobranças com rótulo | `competencia = mastersat_competencia(period_label)`, em lotes de 5.000 ids |
| Substituição | canceladas com marcador nas notas | `substituted_by_id` = alvo de "Consolidada no boleto único #N." / "Unificada na cobrança #N.", só se #N existe, não é ela mesma e a nota não registra "substituição pela cobrança #N revertida" |
| `cancelada_redundante_liberada` | canceladas simples que dividem o mês com outra cobrança do contrato | `competencia_liberada = true` + nota + `billing_change_logs` (usuário nulo, justificativa citando a migration). Valor e status **não** mudam. Mês só com canceladas: a mais recente continua ocupando |
| Rollback anterior | tabela `fase02_competencias_liberadas`, se existir | devolve `competencia_liberada` a quem continua cancelada e apaga a tabela |

Medido em PostgreSQL 16 com 575 mil cobranças sintéticas: **37,5 s** para o
upgrade inteiro (preflight, colunas, backfill, índices e CHECKs), durante os
quais o backend não atende (o boot espera a migration). Tempo em produção será
proporcional ao volume real; meça no snapshot junto com o preflight.

## 4. Inventário (não bloqueia)

| Código | Significado | Ação sugerida |
|---|---|---|
| `mensalidade_sem_competencia` | Mensalidade com rótulo fora de formato: fica sem competência e fora do índice | Corrigir o rótulo (`PUT` não altera rótulo; via SQL com nota) |
| `substituida_sem_substituto_efetivo` | Original consolidada/negociada cujo substituto foi removido ou cancelado — **mês sem cobrança em aberto** (o caso do FIN-01) | Decidir por cobrança: liberar a competência (`POST /billings/{id}/liberar-competencia`) para o fechamento/carnê recobrar, ou deixar como está se foi perdão |
| `marcador_sem_alvo` | Nota cita substituto que não existe; o vínculo não é gravado | Conferir a nota; normalmente nada a fazer |
| `cancelada_redundante_liberada` | Ver seção 3 | Conferir a lista após aplicar |

## 5. Exceções nominais das constraints

- **Removidos não são validados** pelos CHECKs de valor
  (`ck_billings_amount_positivo`, `ck_client_charge_items_*`): `is_deleted OR …`.
  Remover é o próprio saneamento de um valor inválido e a linha é histórico.
- `ck_billings_paid_amount_positivo` e `ck_billings_parcela_no_intervalo`
  valem para todas as linhas.
- O teto de 60 parcelas por serviço fica só na API (importação/legado podem ter
  mais sem incoerência).
- Função `mastersat_competencia`, função `mastersat_billings_competencia` e
  trigger `trg_billings_competencia` são objetos geridos pela migration; o
  autogenerate não os compara (ver [README do Alembic](../../backend/alembic/README.md)).
