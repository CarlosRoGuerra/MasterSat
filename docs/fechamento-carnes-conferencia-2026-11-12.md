# Conferência e renovação dos carnês Sicredi

O fechamento de carnê usa os clientes cadastrados com a forma **Carnê Sicredi**.
O grupo **Todos** inclui PF e PJ dessa forma de cobrança. A seleção mensal e
seus relatórios mantêm o funcionamento anterior.

No carnê, o mês do serviço selecionado define o início do ciclo: novembro de
2026 prepara vencimentos de dezembro de 2026 a novembro de 2027, no dia do
contrato. A data do clique não muda esses vencimentos. A vigência explícita do
contrato continua sendo respeitada.
Ao escolher carnê, o mês sugerido é o atual; no mensal, continua sendo o
anterior. Uma escolha manual de mês é preservada.

## Renovação completa por responsável

- Uma competência ocupada em qualquer veículo impede renovar apenas parte do
  carnê do responsável. A prévia informa o motivo; títulos e pagamentos
  existentes são preservados.
- Todos os veículos do responsável são conferidos juntos. Mesmo que o
  cadastro use boletos individuais, o fechamento de carnê consolida os
  veículos em um boleto por mês. O fechamento mensal continua respeitando a
  configuração anterior.
- Mensalidades de cada contrato continuam registradas para impedir cobrança
  repetida no fechamento mensal.
- Taxas e serviços entram na primeira parcela do ciclo elegível. Sem
  renovação, ficam pendentes para outro fechamento; não viram um carnê de
  uma única parcela.
- Mudanças de veículo, mensalidade, dia de vencimento ou forma de cobrança
  durante a preparação exigem nova simulação antes de criar títulos.

## Conferência e documentos

A prévia agrupa cada responsável com veículos, valores, primeiro e último
vencimentos e as parcelas. O cadastro pode ser aberto em outra aba; depois
de salvar a correção, é necessário atualizar a prévia.
Veículos com dias de vencimento diferentes seguem a regra da consolidação:
vence no dia mais frequente das mensalidades; em empate, no mais cedo.

O PDF de conferência de carnê lista cada contrato uma vez e discrimina os
vencimentos mensais, adicionais da primeira parcela e total do ciclo. Evita
contar doze parcelas como doze rastreadores ou apresentar a soma anual como
valor de um boleto. O PDF e o Excel do fechamento mensal não foram alterados.

Depois da conclusão, **Conferir e baixar carnês** permite acompanhar a emissão
e salvar o PDF de cada responsável para imprimir ou encaminhar manualmente
pelo WhatsApp. O envio por e-mail continua disponível em ação própria. A
emissão bancária e o envio dependem de ação explícita do operador.

## Validação

Os testes cobrem renovação após doze meses, ciclo vigente, mês futuro pago,
veículos com ciclos diferentes, responsáveis distintos, seleção parcial,
alterações durante a geração e dois veículos com cadastro individual.
Também cobrem conferência/PDF, atualização da prévia, seleção por responsável,
download e regressão do fechamento mensal. Os testes bancários usam respostas
simuladas e a imagem de produção é verificada sem rede e com SQLite isolado.
