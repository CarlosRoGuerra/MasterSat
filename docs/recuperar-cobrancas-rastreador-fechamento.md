# Cobranças antigas do rastreador no fechamento

O formulário antigo de cobrança manual do rastreador gravava uma cobrança
`avulsa` diretamente no Financeiro. O fechamento recebe serviços pendentes em
`client_charge_items`, portanto essas avulsas não apareciam na simulação.
Os novos cadastros já usam o fluxo de adicionais do fechamento.

Para recuperar as avulsas antigas, execute dentro do container backend:

```bash
python scripts/recuperar_cobrancas_rastreador.py \
  --cliente "NOME COMPLETO DO CLIENTE" --mes-servico 2026-10
```

Sem `--aplicar`, o comando somente confere e mostra as cobranças selecionadas.
Com `--aplicar`, converte o lote em uma transação. O mês é o do **serviço**:
um vencimento original em novembro não determina o fechamento de novembro.
Depois, simule o fechamento de outubro na aplicação.

A seleção automática exige nome exato e único, cliente atendido, criação no mês
informado, tipo avulsa e vínculo com rastreador e veículo. Para indicar cobranças
específicas ou cadastradas em outro mês, repita `--cobranca-id NUMERO`.
A data de início usa a data de cadastro em Brasília quando pertence ao mês
informado; caso contrário, usa o primeiro dia da competência explicitamente escolhida.

A recuperação conserva valor, cliente, contrato, placa, rastreador, notas e
histórico. A avulsa original fica cancelada e vinculada ao novo adicional, com
auditoria. O fechamento é responsável por gerar a cobrança efetiva. Reexecutar
o comando devolve o mesmo adicional, inclusive após o fechamento.

Recebimentos, boleto bancário, remessa CNAB reservada, processo fiscal, negociação
ou mudança de pagador/vínculos impedem a conversão. Se uma cobrança for recusada,
nenhuma do lote será convertida. Não há chamadas à Ailos nem emissão de NFS-e.

O comando não gera o fechamento nem altera parâmetros da integração bancária.
