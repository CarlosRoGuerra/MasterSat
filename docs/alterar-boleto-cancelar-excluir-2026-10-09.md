# Cancelar ou excluir pelo ícone Alterar boleto

Em **Clientes → Central financeira / boletos → Alterar boleto**, a janela oferece:

- **Salvar alteração**: altera valor ou vencimento, com justificativa.
- **Cancelar boleto**: exige justificativa e confirmação. Mantém a cobrança cancelada no sistema, com o motivo nas observações.
- **Excluir boleto**: disponível para cobrança cancelada sem histórico bancário. A exclusão é lógica: retira da carteira e preserva o registro e seu histórico no banco de dados.
- **Fechar janela**: fecha a edição.

Após cancelar, a janela permanece aberta. Se a exclusão for permitida, o botão fica disponível imediatamente. Ao excluir, a janela fecha e a carteira é atualizada. A seleção de cobranças e os carnês também são atualizados. Uma falha na atualização da carteira é informada separadamente da operação já concluída.

As ações são restritas a administrador e financeiro. Cobranças pagas ficam protegidas contra alteração, cancelamento e exclusão.

## Regras financeiras mantidas

As ações usam os endpoints existentes `PUT /billings/{id}`, `POST /billings/{id}/cancel` e `DELETE /billings/{id}`. Não há alteração no backend ou migração de banco nesta entrega.

- **Boleto ativo no banco**: o cancelamento local exige a confirmação apresentada pela API. O título continua pagável até a baixa bancária; a janela mostra essa pendência. Cancelar no sistema não executa baixa automática na Ailos.
- **Histórico bancário**: cobranças registradas, baixadas, em registro, com desfecho desconhecido ou em remessa CNAB não podem ser excluídas. A API continua validando a situação no momento da ação.
- **Competência**: cancelar nesta janela mantém a competência ocupada, evitando nova cobrança do mês no fechamento. Excluir uma mensalidade pode liberar o mês para ser cobrado novamente; a confirmação explica esse efeito.
- **Negociação/boleto único**: se a API exigir reabrir as cobranças originais, o operador precisa confirmar antes de prosseguir.
- **Erro ou desistência**: a janela mantém os dados para revisão. Enquanto uma ação está em andamento, os campos e as outras ações ficam bloqueados, inclusive o fechamento por Esc ou pelo X.

## Atualizar na VPS

Execute a partir do terminal da VPS. Esta entrega exige reconstruir somente o frontend; um `git pull` sozinho não atualiza a imagem em execução.

```bash
(
  set -e
  cd ~/MasterSat
  git fetch origin
  git switch fix/rastreador-dia-vencimento
  git pull --ff-only origin fix/rastreador-dia-vencimento

  docker compose -f docker-compose.yml -f docker-compose.prod.yml build frontend
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps frontend
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T nginx nginx -s reload
)
```

Depois, atualize o navegador com **Ctrl + F5** e abra o ícone **Alterar boleto** novamente.

## Validação

Os testes de interface cobrem os dois perfis financeiros, justificativa, desistência, confirmação bancária, cancelamento seguido da disponibilidade de exclusão, bloqueios por situação bancária, cobrança paga, confirmação de reversão de negociação, erro da API, atualização da carteira, falha de atualização, bloqueio durante processamento e edição de valor existente.

As regras existentes de cancelamento, exclusão lógica, concorrência, competência e título bancário são verificadas em testes do backend com banco isolado, sem conexão à produção ou a serviços externos.

Resultado: **44 testes do frontend passaram**, assim como TypeScript e ESLint nos arquivos alterados. No backend, **181 testes passaram e 2 falharam** em uma cópia limpa do commit anterior à entrega (`d1ed582`), sem incluir alterações locais de outros trabalhos:

- `TestListBillings::test_operational_can_list`: espera acesso do operacional, mas a API atual restringe a listagem a administrador/financeiro e responde 403.
- `TestTituloBaixadoNaoVoltaAoCliente::test_pdf_e_email_recusados_com_baixa_pendente`: espera um erro bancário estruturado ao enviar cobrança paga por e-mail, mas a API atual já recusa cobranças fora de aberto antes dessa verificação, com mensagem em texto.

Essas duas expectativas antigas não foram alteradas nesta entrega. Os testes de cancelamento, exclusão, competência e concorrência passaram.
