# Cancelar libera o mês, com aviso de baixa no site do banco

Cancelar uma mensalidade ou parcela de carnê no MasterSat libera o mês para um
novo carnê ou fechamento, mesmo quando o boleto ainda está registrado no
Sicredi ou na Ailos. Antes, o mês ficava preso até a baixa bancária ser
confirmada, o que impedia refazer o carnê.

O cancelamento no MasterSat **não** cancela o título no banco. Por isso, sempre
que existe boleto ativo, o sistema pede ciência e informa:

> Providencie a baixa do boleto no site do Sicredi (ou da Ailos). O cancelamento
> no MasterSat não cancela o título no banco.

O aviso aparece na confirmação do cancelamento (individual, central do cliente
e lote), na mensagem de resultado, na nota da cobrança e na liberação de
competência. A baixa continua registrada como **pendente** e a conciliação
segue acompanhando o título até a baixa ser confirmada.

## Onde vale

- Cancelamento individual no Financeiro e na central do cliente.
- Cancelamento em lote (conferência e lote antigo).
- Exclusão de cancelada: libera o mês com a confirmação já existente no lote.
- "Liberar competência" de cancelada antiga: aceita boleto ativo, com a ciência
  da baixa no site do banco.
- Carnê a partir do plano: se os meses estiverem presos só por cobranças
  canceladas — inclusive as **excluídas antes desta regra**, que não aparecem
  na carteira —, o sistema explica e, com a confirmação do operador, libera
  esses meses e gera o carnê. Se houver cobrança em aberto, paga ou
  substituída no período, o conflito continua como antes.

## O que não muda

- Boleto em registro, com desfecho desconhecido ou em Pix Automático ativo
  continua bloqueando o cancelamento/liberação.
- Cobrança substituída por boleto único ou negociação não libera o mês; a
  reversão continua sendo explícita.
- Dispensa: a API ainda aceita `liberar_competencia: false` para manter o mês
  ocupado.
- Nenhum boleto é baixado ou emitido no banco por essas ações.
- No fechamento com carnê, meses ainda presos por canceladas antigas aparecem
  como já gerados; libere-os pelo carnê a partir do plano ou por "Liberar
  competência".

## Verificação

- Backend: testes de cancelamento, lote, exclusão, competência, política
  bancária e carnê (223 nos arquivos afetados), incluindo Sicredi e Ailos,
  cancelamento individual, em lote e lote antigo, e liberação de carnê antigo
  excluído. Nenhuma chamada aos bancos.
- Frontend: TypeScript aprovado; testes de cancelamento, lote, central do
  cliente, Financeiro e do novo fluxo de carnê aprovados.
