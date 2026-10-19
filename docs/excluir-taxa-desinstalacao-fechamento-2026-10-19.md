# Excluir taxa de desinstalação já recebida

O fechamento passa a oferecer uma lixeira em cada linha de **Taxas de desinstalação**, inclusive nas taxas que aguardam acumulação. Use quando a taxa já foi recebida por boleto simples ou não deve ser cobrada novamente.

## Como retirar a taxa

1. Abra **Fechamento**, escolha o mês do serviço e o cliente e clique em **Simular fechamento**.
2. Na seção **Taxas de desinstalação**, confira o cliente, a placa e o valor. Clique na lixeira dessa linha.
3. A janela **Excluir taxa do fechamento** apresenta os dados da taxa. Informe o motivo, por exemplo: `Desinstalação já paga pelo boleto simples`.
4. Clique em **Confirmar exclusão da taxa**. A tela recalcula o total, os títulos previstos e a acumulação das outras taxas desse responsável financeiro.

A exclusão é permanente para esse evento: ele sai da prévia atual e dos próximos fechamentos. O evento da retirada, o valor original, o motivo e o operador ficam preservados nas observações e na auditoria. O boleto simples já pago e seu recebimento permanecem intactos. Essa ação não registra um novo pagamento nem altera os contratos ou o pró-rata.

Se a geração já tiver transformado a taxa em uma cobrança, a exclusão pela prévia será recusada. Confira o título existente no Financeiro, com suas regras de pagamento e cancelamento. A ação da prévia atende somente taxas ainda pendentes de faturamento.

## Segurança e atualização da tela

- Disponível para administrador e financeiro.
- Justificativa de 3 a 500 caracteres, obrigatória após remover espaços nas pontas.
- Taxas pendentes e registros legados em acumulação podem ser excluídos; taxas faturadas e estados desconhecidos ficam protegidos.
- Repetir uma exclusão confirmada devolve o resultado existente, sem substituir o motivo ou duplicar a auditoria.
- Durante o envio, motivo, confirmação e fechamento pelo X/Esc/clique fora ficam bloqueados.
- Se a exclusão concluir e o recálculo falhar, a tela informa que a taxa foi retirada e volta ao escopo. É necessário simular novamente antes de gerar, evitando usar totais desatualizados.
- Exclusão e geração compartilham a trava da taxa no PostgreSQL. Se a exclusão concluir primeiro, a geração concorrente recusa a prévia antiga antes de criar cobranças. Se a geração concluir primeiro, a taxa fica protegida como faturada.

## API

Novo endpoint autenticado:

```http
POST /api/v1/billing-closure/uninstall-events/{event_id}/exclude
Content-Type: application/json

{"reason":"Desinstalação já paga pelo boleto simples"}
```

A resposta informa `event_id`, `status: "canceled"`, `message` e `already_excluded`. A exclusão altera somente o estado do evento e acrescenta observações/auditoria. A coluna `status` já aceita esse valor; **não há nova migração de banco**.

Taxa inexistente responde 404; motivo inválido responde 422; taxa faturada responde 409 com código `uninstall_fee_already_billed`. A geração revalida os eventos selecionados sob trava antes de iniciar as gravações.

## Atualizar na VPS pela main

```bash
(
  set -e
  cd ~/MasterSat
  git fetch origin
  git switch main
  git pull --ff-only origin main

  docker compose -f docker-compose.yml -f docker-compose.prod.yml build backend backend-worker frontend
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps backend backend-worker frontend
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T nginx nginx -s reload
)
```

Depois, atualize com **Ctrl + F5**, simule o fechamento do cliente e use a lixeira na taxa já recebida. Publicar o código não exclui taxas automaticamente.

## Verificação

Foram aprovados **168 testes do backend** com banco sintético, **2 testes de concorrência em PostgreSQL temporário** e **7 testes da página de fechamento**. Todos passaram, sem exclusões de casos na seleção executada. Os testes cobrem persistência da exclusão em meses futuros, auditoria, preservação do boleto simples pago, taxa já faturada, repetição, permissões, motivo, rollback, revalidação após simulação, recálculo de acumulação e as duas ordens possíveis de exclusão/geração simultâneas.

TypeScript passou. ESLint sem erros, com quatro avisos preexistentes na página de fechamento. As verificações usaram apenas esta entrega sobre uma cópia limpa do projeto, sem alterações locais de outros trabalhos. O PostgreSQL de teste foi removido ao final. Nenhuma cobrança real foi alterada durante a implementação.
