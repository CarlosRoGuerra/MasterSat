# Cancelar e excluir boletos em lote

Disponível para **administrador e financeiro** em:

- **Clientes → Central financeira / boletos**: marque os boletos e use **Cancelar selecionados** ou **Excluir selecionados**.
- **Financeiro → Menu → Alterar situação boleto em lote** ou **Contas a Receber**: marque os boletos e use **Cancelar em lote** ou **Excluir em lote**. A carteira da aba Visão Geral também oferece essas ações.

## Como usar

1. Filtre a carteira e selecione os boletos pelas caixas das linhas ou do cabeçalho. Na central do cliente, o cabeçalho seleciona os boletos visíveis pelo filtro; no Financeiro, seleciona a página atual. Desmarcar o cabeçalho preserva a seleção das outras páginas. Boletos pagos não oferecem seleção.
2. Clique na ação desejada. A conferência mostra os boletos aptos e os ignorados, com o motivo de cada bloqueio. Essa etapa não modifica cobranças.
3. Informe uma justificativa de 3 a 500 caracteres. Quando houver título ativo no banco ou liberação de mês, leia e marque a confirmação correspondente.
4. Confirme. O resultado permanece aberto, com a situação de cada boleto. Ao fechar, a carteira mostra os dados atualizados; os itens processados saem da seleção e os ignorados permanecem selecionados para revisão.

No Financeiro, alterar vencimento, fechamento, lote ou filtros de emissão limpa a seleção, como no fluxo anterior. Na central do cliente, mudar o filtro mantém os selecionados fora dele; confira sempre a lista da confirmação antes de aplicar.

## Regras da operação

| Situação | Cancelar em lote | Excluir em lote |
| --- | --- | --- |
| Pendente ou vencida, sem título bancário | Permitido | Cancele primeiro |
| Cancelada, sem título bancário | Ignorada | Permitido, com exclusão lógica |
| Paga | Protegida | Protegida |
| Título registrado ou reserva CNAB | Exige ciência da baixa bancária | Bloqueado |
| Registro em andamento ou desfecho desconhecido | Bloqueado | Bloqueado |
| Título com baixa bancária confirmada | Permitido se ainda aberta | Bloqueado pelo histórico bancário |
| Negociação que exige reabrir originais | Use a ação individual para conferir a reversão | Use a ação individual para conferir a reversão |

**Cancelar no sistema mantém o mês ocupado e não executa baixa automática no banco.** Títulos ativos continuam pagáveis até a baixa bancária. A confirmação explica essa pendência; o resultado informa os títulos afetados e o nosso número, quando disponível. A reserva CNAB permanece preservada.

**Excluir uma mensalidade pode liberar o mês para nova cobrança pelo fechamento.** O lote exige uma confirmação específica para esse efeito. A exclusão retira a cobrança da carteira, preservando o registro e o histórico no banco de dados.

Cada item aplicado recebe a justificativa nas observações, no histórico de alterações e na auditoria. Os bloqueados permanecem intactos. Falha inesperada durante a gravação desfaz todas as alterações dessa aplicação do lote.

Se qualquer cobrança ou situação bancária mudar depois da conferência, a API recusa a revisão antiga antes de gravar. Use **Conferir novamente** e revise os dados atualizados. Durante a aplicação, a janela impede novo envio e fechamento pelo X, Esc ou clique fora. Uma falha ao atualizar a carteira após a conclusão é informada separadamente.

## Contrato da API e componente

Novo endpoint autenticado: `POST /api/v1/billings/lote/acoes`, restrito a administrador/financeiro. Limite de 1.000 IDs por requisição; IDs repetidos são processados uma única vez.

Conferência:

```json
{"billing_ids":[101,102],"action":"cancelar","simular":true}
```

Aplicação, usando a `revisao` devolvida pela conferência:

```json
{
  "billing_ids":[101,102],
  "action":"cancelar",
  "simular":false,
  "reason":"Solicitação do cliente",
  "revisao":"<revisao devolvida pela API>",
  "confirmar_boleto_ailos":false,
  "confirmar_liberacao_competencia":false
}
```

Para excluir, use `action: "excluir"` em uma nova conferência. As confirmações devem ser verdadeiras quando exigidas pela prévia. A resposta inclui `simulacao`, `action`, `total_enviados`, `aplicados`, `ignorados`, `revisao` e `itens`. Na conferência, `aplicados` representa os aptos; na aplicação, representa os efetivamente processados. Cada item informa `aplicavel`, `aplicado` ou `ignorado`, motivo, situação, nosso número e efeitos bancários/financeiros.

`BillingBatchModal` é compartilhado entre Clientes e Financeiro, usando os componentes existentes de diálogo, botões e formulário. Recebe ação, cópia dos IDs selecionados, token, `onClose` e `onCompleted`. Os pais mantêm a seleção e atualizam suas carteiras; a cópia dos IDs permanece estável enquanto o resultado é exibido. Estados: conferindo, revisão, aplicando, resultado e erro. A interface usa caixas nativas rotuladas, indicação de seleção parcial, alertas/status acessíveis e botões com quebra de linha em telas estreitas. Os fluxos individuais e o endpoint anterior `/lote/situacao` continuam disponíveis.

## Atualizar na VPS

Esta entrega exige reconstruir **backend, backend-worker e frontend**. Não há nova migração de banco. Execute no terminal da VPS:

```bash
(
  set -e
  cd ~/MasterSat

  mkdir -p backups
  backup="backups/mastersat_antes_boletos_lote_$(date +%Y%m%d_%H%M%S).dump"
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db \
    sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup"
  test -s "$backup"
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db \
    pg_restore --list < "$backup" > /dev/null
  printf 'Backup criado: %s\n' "$backup"

  git fetch origin
  git switch fix/rastreador-dia-vencimento
  git pull --ff-only origin fix/rastreador-dia-vencimento

  docker compose -f docker-compose.yml -f docker-compose.prod.yml build backend backend-worker frontend
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps backend backend-worker frontend
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T nginx nginx -s reload
  docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
)
```

Depois, atualize o navegador com **Ctrl + F5**. Entre como administrador/financeiro e abra uma das carteiras indicadas acima. A conferência pode ser aberta e fechada sem aplicar alterações. O backup permanece no diretório `~/MasterSat/backups` da VPS.

## Validação

- **215 testes distintos do backend passaram**, com dados sintéticos e contêiner sem rede: novos lotes, endpoints individuais, concorrência, competência, negociação, reservas CNAB e política bancária. Os 27 novos casos incluem prévia sem escrita, justificativa/auditoria, itens ignorados, revisão desatualizada, confirmações, permissões, limite de seleção e rollback integral.
- **65 testes do frontend passaram**, incluindo a composição real nas páginas Clientes e Financeiro, seleção com filtros/paginação, proteção de pagas, exclusão parcial, confirmação bancária/competência, erro, atualização da carteira e bloqueio durante processamento.
- TypeScript passou. ESLint sem erros; três avisos preexistentes no Financeiro (componente e variável sem uso, dependência de efeito).
- As verificações usaram uma cópia limpa do commit anterior com apenas os arquivos desta entrega, preservando alterações locais de outros trabalhos. Nenhum dado de produção foi alterado.

Duas expectativas antigas foram excluídas da seleção de regressão, conforme diagnóstico já documentado na entrega de [cancelamento individual](alterar-boleto-cancelar-excluir-2026-10-09.md): `test_operational_can_list` espera acesso do operacional onde a API já exige administrador/financeiro; `test_pdf_e_email_recusados_com_baixa_pendente` espera um erro bancário estruturado depois de uma validação de situação que já recusa o envio. Essas expectativas não foram modificadas.
