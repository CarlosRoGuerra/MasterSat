# Fechamento de carnês Sicredi: o ano inteiro de uma vez

O fechamento tem agora dois tipos, escolhidos no primeiro passo:

- **Boletos mensais** — o fechamento de sempre, um título por mês.
- **Carnês Sicredi (12 meses)** — gera o carnê do ano para os clientes com a
  forma de cobrança Carnê Sicredi. Já seleciona a forma e as 12 parcelas.

## Quem entra no fechamento de carnês

Entra cada contrato de cliente Carnê Sicredi cujo mês do fechamento ainda não
foi cobrado, ou seja, cujo carnê anterior terminou (ou que nunca teve carnê).
Ele recebe as 12 parcelas: serviços de novembro/2026 geram parcelas de
dezembro/2026 a novembro/2027, cada uma com o próprio boleto.

Quem ainda tem carnê em andamento aparece como **já gerado** e não recebe nada.
Antes, um carnê que ia até setembro/2027 ganhava outubro e novembro/2027
soltos (um "carnê" de duas parcelas); isso não acontece mais. O cliente volta
no fechamento do mês em que o carnê dele terminar.

Meses isolados já cobrados no meio do ano (por exemplo, um mês pago à parte)
continuam preservados e não recebem uma segunda cobrança.

## Emitir todos os carnês

Na conferência do lote do fechamento, **Emitir todos os carnês pelo Sicredi**
inicia o carnê de todos os responsáveis do fechamento numa só ação — um carnê
por responsável, com os 12 boletos separados. O Sicredi registra as parcelas no
servidor; a lista se atualiza sozinha e mostra quantas já foram registradas.
Uma falha em um carnê não impede os demais e aparece na tela com o nome do
cliente. Cada carnê pode ser acompanhado e baixado (três parcelas por folha)
quando estiver completo. Continua possível emitir um carnê por vez.

Nada é registrado no banco ao simular, gerar o fechamento ou abrir a
conferência: o registro só começa no clique em emitir.

## Verificação

- Backend: novos testes do fechamento de carnês (carnê em andamento sem
  parcelas soltas, carnê terminado renovado com 12 parcelas, só Carnê Sicredi)
  e a regressão do fechamento, conferência e carnês Sicredi.
- Frontend: tipo de fechamento, emissão de todos os carnês (inclusive com
  falha em um deles e recusa da confirmação); TypeScript aprovado.
