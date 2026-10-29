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
O vencimento original fica preservado como data da cobrança, com interpretação
explícita de vencimento. O mês do fechamento é o anterior ao vencimento.

A recuperação conserva valor, cliente, contrato, placa, rastreador, notas e
histórico. A avulsa original fica cancelada e vinculada ao novo adicional, com
auditoria. O fechamento é responsável por gerar a cobrança efetiva. Reexecutar
o comando devolve o mesmo adicional, inclusive após o fechamento.

Recebimentos, boleto bancário, remessa CNAB reservada, processo fiscal, negociação
ou mudança de pagador/vínculos impedem a conversão. Se uma cobrança for recusada,
nenhuma do lote será convertida. Não há chamadas à Ailos nem emissão de NFS-e.

O comando não gera o fechamento nem altera parâmetros da integração bancária.

## Adicional já cadastrado com vencimento interpretado como data do serviço

Após atualizar o banco (`alembic upgrade head`), é possível corrigir um adicional
pendente sem recriá-lo. Informe o cliente exato, o vencimento e o valor. O conjunto
deve identificar um único adicional manual com rastreador e veículo.

```bash
python scripts/recuperar_cobrancas_rastreador.py \
  --cliente "NOME COMPLETO DO CLIENTE" --mes-servico 2026-10 \
  --adicional-vencimento 2026-11-10 --valor 5.00 --aplicar
```

A data permanece 10/11/2026. Muda apenas sua interpretação para vencimento, o que
o inclui no fechamento de outubro. Há auditoria, proteção contra cobranças já
faturadas e verificação de duplicidade. Repetir o comando não cria outro adicional.
A migração de banco não reinterpreta dados históricos automaticamente.
