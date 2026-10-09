# Troca rápida de cliente e pagador

Solicitação do cliente de 08/10/2026: após a venda de um veículo, trocar seu cliente e/ou interveniente financeiro dentro do MasterSat, mantendo o equipamento instalado e sem consultar ou comparar vínculos com a API da Multiportal.

## Como usar

1. Cadastre o comprador em **Clientes**, se ainda não existir.
2. Abra **Veículos**, encontre a placa e clique em **Detalhes**.
3. Na aba **Dados**, clique em **Trocar cliente / pagador**.
4. Escolha o cliente do veículo. Para trocar só o pagador, mantenha o cliente atual.
5. Escolha **O próprio cliente** ou **Outro cliente como interveniente (pagador)** e selecione quem pagará.
6. Clique em **Confirmar troca**.
7. Simule o **Fechamento** para o novo responsável financeiro e confira a placa e o valor antes de gerar o título.

A operação atualiza o veículo, todos os rastreadores instalados nessa placa e seus contratos ativos na mesma transação. O plano, a data de instalação, o início do contrato e o dia de vencimento permanecem. Serviços ativos vinculados aos contratos acompanham o novo cliente nas parcelas ainda a gerar. Contratos encerrados e outras placas do antigo cliente permanecem no histórico.

Títulos já gerados, inclusive em aberto, mantêm seu cliente e pagador. Cobranças legadas sem o snapshot do pagador recebem o responsável anterior antes de o contrato mudar, preservando também a consulta pela API e os documentos fiscais. A troca não cancela nem reemite boletos e não cobra novamente uma competência que já possui título.

O menu também corrige o caso em que o cadastro do veículo já aponta para o comprador, mas o rastreador e o contrato ainda estão no cliente anterior: mantenha o comprador selecionado e confirme o pagador desejado.

Salvar novamente o proprietário atual na edição do veículo ou vincular o rastreador novamente à mesma placa, sem pedir outro plano, também reconcilia essa divergência. A consulta de veículos do cliente encontra o equipamento pela placa vinculada, mesmo que seu cliente ainda seja o anterior, e mostra um aviso para conferir o vínculo. Falhas da consulta aparecem como erro, em vez de ocultar o equipamento.

Quando o cliente de um contrato ativo muda, a marcação de assinatura e a data da assinatura anterior são limpas, permitindo colher a assinatura do novo proprietário. Os documentos anexados continuam no cadastro original. Trocar somente o pagador, ou repetir uma operação já concluída, preserva a assinatura do proprietário atual.

Para o caso específico da placa `OXD0A94` / IMEI `869671078139498`, veja o [guia de reconciliação e atualização de 09/10/2026](reconciliacao-proprietario-2026-10-09.md).

Na edição comum do veículo, alterar o cliente usa a mesma operação e define o novo cliente como pagador das novas cobranças. Para escolher um interveniente diferente, use o menu dos detalhes. O financeiro pode mudar o pagador; a troca do cliente do veículo permanece disponível ao administrador e ao operacional.

Também é possível trocar pela tela **Rastreadores → Editar**: busque o novo cliente pelo nome ou CPF/CNPJ, mantenha a placa atual e clique em **Atualizar rastreador**. A placa continua selecionada durante a busca. O veículo, seus rastreadores, os contratos ativos e os dados técnicos editados são salvos na mesma transação. O novo cliente passa a ser o pagador das próximas cobranças; para outro interveniente, use o menu dos detalhes do veículo. Não há desinstalação nem consulta à Multiportal.

A troca pela edição do rastreador mantém o status e a data da instalação. Para criar um contrato em um equipamento instalado sem contrato, salve a troca de cliente primeiro e depois escolha o plano. Identificadores importados como `DES000005` são preservados quando o campo ID não é alterado.

## Multiportal e busca por placa

A troca e suas validações usam exclusivamente o banco do MasterSat. Não executam consulta, comparação ou desvínculo na Multiportal. A intenção de sincronizar os dados alterados fica na fila local existente, sem condicionar a conclusão da troca à plataforma externa.

O controle **Consultar vínculo** foi removido dos detalhes do rastreador e da tela de integração. A busca do assistente abre somente o cadastro local. Permanecem o envio após vincular à placa e a ação **Sincronizar** nos detalhes do rastreador.

A busca em **Rastreadores** agora encontra o equipamento pela placa do veículo, inclusive com letras minúsculas, espaço ou hífen. Continua aceitando IMEI, modelo e os demais identificadores já suportados.

## API interna

Endpoint autenticado com o JWT do usuário do sistema:

```http
POST /api/v1/vehicles/{vehicle_id}/change-client
Authorization: Bearer <token_do_usuario>
Content-Type: application/json

{
  "client_id": 123,
  "interveniente_client_id": null,
  "expected_client_id": 456
}
```

`client_id` é o cliente desejado. `interveniente_client_id: null` faz esse cliente pagar; outro ID indica o interveniente. Ao mudar o cliente sem enviar `interveniente_client_id`, o novo cliente será o pagador. Quando o cliente permanece, omitir esse campo preserva os intervenientes atuais.

`expected_client_id` é o cliente do veículo que a tela carregou. Se alguém já o tiver alterado, a API retorna 409 e exige atualizar a tela. A resposta de sucesso é o veículo atualizado. IDs inexistentes ou removidos retornam 404; o financeiro tentando trocar o proprietário recebe 403. Contrato ativo com rastreador fora da placa retorna 409 antes de qualquer alteração.

Para trocar o cliente pela edição de um rastreador e salvar seus dados técnicos na mesma transação:

```http
POST /api/v1/trackers/{tracker_id}/change-client
Authorization: Bearer <token_do_usuario>
Content-Type: application/json

{
  "client_id": 123,
  "expected_vehicle_id": 7,
  "expected_client_id": 456,
  "tracker_update": {
    "model": "ST 300",
    "notes": "Cliente atualizado"
  }
}
```

Os três IDs são obrigatórios: cliente desejado, placa carregada pela tela e cliente atual dessa placa. A resposta é o rastreador atualizado. `tracker_update` é opcional; aceita os campos técnicos da edição e não pode conter `client_id` ou `vehicle_id`. O status e a data de instalação, se enviados, devem permanecer iguais aos cadastrados. Omita `imei` quando o identificador não mudar, especialmente para IDs importados alfanuméricos.

Se a placa do rastreador ou seu cliente já tiverem mudado, a API retorna 409. Dados técnicos inválidos e conflitos de ID não deixam uma troca parcial. Esta operação permite administrador e operacional; o financeiro altera somente o pagador pelo endpoint do veículo.

## Atualização da VPS

Depois do commit disponibilizado na branch, execute:

```bash
cd ~/MasterSat &&
git fetch origin &&
git switch fix/rastreador-dia-vencimento &&
git pull --ff-only origin fix/rastreador-dia-vencimento &&
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --no-deps backend frontend &&
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec nginx nginx -s reload
```

Não há migration nem alteração de banco a executar. Após atualizar, recarregue a página do sistema. A troca fica disponível nos detalhes do veículo e na edição do rastreador, pelos caminhos indicados acima.
