# Excluir qualquer cobrança cancelada

Administrador e Financeiro podem excluir da carteira qualquer cobrança cancelada,
individualmente ou em lote, inclusive com título ativo, baixa pendente, registro
incerto, remessa CNAB, divergência financeira ou vínculo de substituição.
Cobranças pagas e abertas continuam protegidas pela interface de exclusão.

O aviso exibido na confirmação individual, no detalhe e na conferência do lote é:

> Excluir no MasterSat não dá baixa no banco ou sistema de pagamento de origem.
> Providencie ou confirme a baixa também na origem.

A exclusão é lógica. O título bancário, os valores, as pendências e o histórico
permanecem armazenados. Uma exclusão não confirma baixa nem envia uma instrução
ao banco. A conciliação continua acompanhando títulos pendentes; pagamentos
posteriores em cobranças removidas geram pendência financeira.

Mensalidades sem risco bancário e sem vínculo de substituição podem liberar a
competência, com confirmação no lote. Quando existe risco ou substituição, a
competência/parcela continua reservada mesmo após a retirada da carteira. Uma
exclusão de cancelada não reabre originais automaticamente. Se uma negociação
for posteriormente revertida de forma explícita, as originais reservadas podem
ser restauradas, inclusive as retiradas da carteira.

## Migração

`d2a7c9e41b60` mantém essa reserva nos índices únicos de competências e parcelas.
O campo existente `competencia_liberada` diferencia exclusões que liberam a
obrigação das que mantêm a reserva. O backfill marca exclusões canceladas antigas
como liberadas, preservando seu comportamento anterior e evitando reocupar meses
já cobrados novamente. Não altera valores, vencimentos ou situação bancária.

O downgrade recusa remover a proteção enquanto houver competências ou parcelas
reservadas por canceladas excluídas. A liberação posterior de competência aceita
o ID de uma cobrança removida, exige situação bancária resolvida e preserva a
justificativa no histórico.

## Verificação

- 70 testes de exclusão, lote e política bancária passaram.
- 68 testes de obrigações e migração passaram.
- 61 testes da interface passaram; TypeScript aprovado.
- PostgreSQL isolado: upgrade de legado, exclusão com reserva, duas tentativas
  concorrentes de duplicação recusadas pelo índice, liberação após baixa e
  downgrade protegido, seguido de novo upgrade, aprovados.
- Na regressão ampliada, seis expectativas antigas de permissões, geração
  imediata de serviços e PDF em duas páginas foram reproduzidas sem alterações
  no commit anterior `54ddcd8`. Não pertencem a esta mudança.
