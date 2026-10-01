# Política de pagamento, estorno e conciliação

Fase 03 (FIN-02, FIN-05, FIN-06, FIN-08). O que o sistema faz hoje, o que foi
decidido nesta fase e o que **depende da empresa** (seção final, com
proposta e efeito).

## Princípio

Nenhum centavo muda de lugar sem um registro explícito: quem, quando, quanto e
por quê. O sistema **não** concede desconto nem cobra encargo por conta
própria — ele registra o que o operador decidiu e recusa o que não foi
decidido.

## Contas a receber — recebimento

| Situação | Comportamento |
|---|---|
| Recebido = valor do título | quita, como sempre foi |
| Recebido < título, sem classificação | **409 `recebimento_divergente`** (antes: quitava e a diferença sumia) |
| Recebido < título, `desconto` | quita; ajuste `desconto` com a diferença, justificativa obrigatória |
| Recebido < título, `parcial` | quita esta cobrança pelo recebido; o saldo vira **nova cobrança avulsa** (mesmo atendido/pagador/contrato/veículo) com o vencimento informado; ajuste `saldo_transferido` aponta para ela. Indisponível para cobrança com serviço avulso vinculado |
| Recebido > título, `encargos` | quita; ajuste `encargos` (multa/juros de atraso), justificativa opcional |
| Recebido > título, `credito` | quita; ajuste `credito` a favor do cliente, justificativa obrigatória; **sem compensação automática** |
| Zero ou negativo | 422 |
| Cobrança já paga / cancelada | 400 |
| Título com desfecho desconhecido ou em registro | 409 (resolver antes — runbook) |
| Título registrado, recebido por fora | quita e marca **baixa pendente** do boleto |

Invariante (testado): `paid_amount = amount − desconto − saldo_transferido +
encargos + credito`, sobre os ajustes ativos.

Recebimento em lote: sempre o valor integral de cada título (sem diferença).

## Baixa automática (Ailos)

A conciliação quita **só** quando o banco informa pagamento com valor igual ao
título (ou pagamento sem valor informado, como antes). O convênio emite com
`tipoPagamentoDivergente = 0` (não autoriza pagamento divergente), então
divergência deve ser rara; quando acontece vira pendência
`pagamento_divergente` e o financeiro classifica pelo recebimento manual.
Cobrança cancelada/removida/recebida por fora nunca é quitada pela
conciliação: pagamento no banco vira pendência (runbook §5).

## Estorno

- `POST /billings/{id}/estornar` (justificativa ≥ 3 caracteres): a cobrança
  volta a pendente/vencida; data, forma, valor e recibo do pagamento desfeito
  ficam num ajuste `estorno`; os ajustes daquele pagamento ficam
  `reversed_at`; `billing_change_logs` registra a mudança de status. Novo
  recebimento gera recibo novo.
- Recusado quando: o banco confirmou o pagamento (boleto liquidado — devolução
  é fora do sistema); há NFS-e emitida ou em emissão (cancelar a nota antes);
  o saldo daquele recebimento virou outra cobrança ainda válida (cancelar a do
  saldo antes).
- Estorno de cobrança com baixa pendente retira a pendência (o boleto volta a
  ser a cobrança válida).

## Cancelamento e exclusão com título no banco

- **Excluir** cobrança com qualquer histórico bancário é recusado; o caminho é
  cancelar.
- **Cancelar** título registrado exige confirmação e grava baixa pendente.
- **Liberar competência** exige baixa confirmada (situação 3/5 na consulta ou
  confirmação do administrador).
- Excluir contrato cancela as abertas e marca baixa pendente nas que têm
  título; recusa se houver desfecho desconhecido/registro em andamento.

## Contas a pagar

`pendente → paga → (estorno) pendente`; `pendente → cancelada` (terminal).
Valor e vencimento só mudam em pendente; descrição, fornecedor, categoria e
observações podem ser corrigidos sempre. Conta paga não é removida.
Transições com trava de linha; tudo em `payable_change_logs` (antes/depois,
responsável, justificativa).

## Conciliação

- Carteira monitorada: abertas com nosso número + baixas pendentes +
  desfechos desconhecidos/reservas órfãs.
- Ordem: quem está há mais tempo sem consulta (nunca consultado primeiro).
- Orçamento: `AILOS_CONCILIACAO_ORCAMENTO` títulos por rodada (1 rodada/hora).
  Janela da carteira ≈ carteira ÷ orçamento horas.
- Erro de um título: contado, gravado nele (`ultima_consulta_erro`,
  `falhas_consulta`), e o título vai para o fim da fila.
- Alerta: pendência nova → aviso ao administrador; título sem consulta há mais
  de `AILOS_CONCILIACAO_ATRASO_ALERTA_HORAS` → aviso no log.

## Decisões pendentes da empresa

Cada item tem o comportamento implementado (conservador), a proposta e como
verificar o efeito.

1. **Recebimento parcial.** *Implementado:* quita a cobrança e cria nova
   cobrança avulsa do saldo (o status da cobrança não ganha "parcialmente
   paga"). *Proposta:* manter — não muda relatórios nem a máquina de estados.
   *Alternativa:* saldo aberto na própria cobrança (exige novo status e
   migração). *Efeito:* `test_parcial_quita_esta_e_cria_cobranca_do_saldo`.
2. **Parcial de cobrança com serviço avulso.** *Implementado:* recusado
   (409 `parcial_indisponivel`). *Proposta:* manter até definir se o serviço
   conta como pago pela metade.
3. **Crédito a favor do cliente.** *Implementado:* registrado, sem uso
   automático. *Proposta:* abater na próxima mensalidade só por ação explícita
   (negociação) — nunca automático.
4. **Encargos.** *Implementado:* valor informado pelo operador, sem teto.
   *Proposta:* avisar (sem bloquear) quando os encargos passarem de
   `valor_com_juros` (multa 2% + juros 1% a.m., cláusula 4.3).
5. **Prazos de desfecho.** *Implementado:* reserva órfã após 30 min
   (`AILOS_RESERVA_ORFA_MINUTOS`); "não registrado" só após 10 min do envio
   (`AILOS_AUSENCIA_CONFIRMADA_MINUTOS`). *Proposta:* manter e validar em
   homologação o tempo real de processamento de lote/carnê.
6. **Orçamento da conciliação.** *Implementado:* 300/h, alerta em 26 h.
   *Proposta:* manter até a carteira monitorada passar de ~6.000 títulos (janela
   de 20 h) e então subir, após confirmar o limite de requisições com a Ailos.
   *Efeito:* `GET /ailos/conciliacao` → `janela_estimada_horas`.
7. **Quem confirma baixa manual.** *Implementado:* só administrador.
   *Alternativa:* incluir o perfil financeiro.
8. **Estorno de pagamento confirmado pelo banco.** *Implementado:* recusado;
   devolução fora do sistema. *Proposta:* manter.
9. **Conta a pagar cancelada.** *Implementado:* terminal (sem reabrir).
   *Proposta:* manter; lançar outra conta.
10. **Inventário da migration.** Títulos marcados com baixa pendente que não
    forem baixados/liquidados no banco continuam monitorados (são pagáveis de
    fato). *Proposta:* o financeiro revisa a lista de pendências nas primeiras
    semanas e baixa no internet banking o que não deve mais ser pago.
