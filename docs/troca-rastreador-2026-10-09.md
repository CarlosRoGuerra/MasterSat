# Troca de rastreador — QIV3234

Ocorrência de 09/10/2026: ao confirmar a troca de `864421065724075` por `866557080296066` na placa `QIV3234`, o cliente relata que a janela fecha e o equipamento não muda.

## Evidência e correção

No backup de 08/10/2026 às 16:37, o rastreador 12 (`864421065724075`) está fisicamente vinculado ao veículo 743 (`QIV3234`). O contrato ativo 669 também pertence a essa placa. Porém, o contrato ativo 223, importado do SGR e pertencente à placa `TPV6I75`, ainda referencia esse mesmo rastreador. O histórico registra uma troca anterior na `QIV3234` em 08/10.

O endpoint bloqueia a substituição quando encontra esse conflito. A interface anterior mostrava erros fora da janela, ignorava os vínculos devolvidos pela operação e dependia de outra consulta para atualizar o equipamento exibido. Essas falhas foram corrigidas. O backup é anterior ao relato: não houve acesso ao estado atual da produção para confirmar o motivo exato da tentativa de 09/10.

A janela agora:

- Mostra erros e contratos em conflito dentro do próprio formulário.
- Permite conferir referências antigas a esse equipamento em contratos de outras placas. A liberação exige marcar cada contrato explicitamente; não ocorre ao abrir a janela ou na primeira tentativa.
- Atualiza a lista imediatamente com os vínculos confirmados pela API e abre a visão do equipamento instalado após a troca. Resposta que mantém os vínculos antigos não fecha a janela nem anuncia sucesso.
- Impede fechar, alterar a seleção ou reenviar enquanto a troca está sendo gravada.
- Oferece todos os equipamentos disponíveis, com paginação. Um equipamento em estoque que ainda consta em contrato ativo fica fora da seleção, e a API também impede sua reutilização.

## Atualizar na VPS

Execute no terminal da VPS. Este bloco guarda backup e para se algum comando falhar:

```bash
(
  set -e
  umask 077
  cd ~/MasterSat
  git fetch origin
  git switch fix/rastreador-dia-vencimento
  git pull --ff-only origin fix/rastreador-dia-vencimento
  dc() { docker compose -f docker-compose.yml -f docker-compose.prod.yml "$@"; }

  mkdir -p backups-manuais
  BACKUP="backups-manuais/antes_troca_rastreador_$(date +%Y%m%d_%H%M%S).dump"
  dc exec -T db sh -c 'pg_dump -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-rastreamento}" -Fc' > "$BACKUP"
  test -s "$BACKUP"
  echo "Backup salvo em: $BACKUP"

  dc build backend backend-worker frontend
  dc run --rm --no-deps backend python -m alembic upgrade head
  dc up -d --no-deps backend backend-worker frontend
  dc exec -T nginx nginx -s reload
)
```

Esta correção não adiciona migration. O `upgrade head` aplica somente migrations anteriores ainda pendentes. Em produção o código fica na imagem Docker: executar apenas `git pull` não atualiza os contêineres.

## Fazer a troca no sistema

1. Recarregue o navegador com **Ctrl+F5**.
2. Abra **Veículos → QIV3234 → Ver rastreadores vinculados** pelo botão verde.
3. Clique em **Trocar rastreador** no equipamento `864421065724075`.
4. Selecione `866557080296066`, informe o motivo e clique em **Confirmar troca**.
5. Se houver conflito, a janela permanece aberta e mostra os contratos e suas placas. Caso apareça o contrato 223 da `TPV6I75`, confira se esse equipamento realmente não pertence mais àquela placa. Nesse caso, marque **Remover este equipamento do contrato #223 (TPV6I75), mantendo o contrato e as cobranças** e confirme novamente. Se o equipamento deveria permanecer naquela placa, corrija a instalação antes de liberar a referência.
6. Confira o novo IMEI na visão de rastreadores da `QIV3234` após o sucesso.

Os IDs do conflito vêm do cadastro atual da produção, sem fixar os números encontrados no backup. Não é necessário executar script de reconciliação de proprietário nem script de importação do SGR para esta troca.

## Efeito nos contratos e nas cobranças

A operação troca o equipamento nos contratos ativos da placa atual. Se o operador conferir e marcar referências antigas em outras placas, somente o campo do rastreador desses contratos é liberado. Eles permanecem ativos, com cliente, pagador, plano, início, vencimento e assinatura preservados. O contrato da outra placa não é movido para o novo equipamento.

O antigo equipamento volta ao estoque e o novo fica instalado no mesmo veículo e cliente. A operação não gera cobrança, não cancela contrato e não muda títulos existentes. Histórico e auditoria registram os equipamentos e contratos envolvidos. Todos os ajustes são gravados juntos; uma falha desfaz a transação inteira.

A troca e a conferência usam o cadastro local do MasterSat, sem consultar a Multiportal.

## API interna

`POST /api/v1/trackers/{rastreador_atual_id}/swap`, autenticado pelo JWT de administrador ou operacional:

```json
{
  "new_tracker_id": 700,
  "reason": "posição",
  "expected_vehicle_id": 743
}
```

`expected_vehicle_id` é opcional para compatibilidade, mas a interface sempre informa a placa carregada. Se o equipamento tiver mudado de veículo, a operação retorna 409.

Quando a resposta 409 contém `code: inconsistent_active_contract_assignment`, `stale_contracts` informa os contratos e placas a conferir. Depois da conferência explícita, o formulário reenvia `release_stale_contract_ids` com os IDs marcados. Eles precisam coincidir exatamente com todos os conflitos atuais; IDs incorretos, repetidos ou desatualizados são recusados. O sucesso retorna `old_tracker`, `new_tracker`, `contracts_updated` da placa atual e `contracts_reconciled` das referências liberadas.

`GET /api/v1/trackers/?available_for_swap=true` lista somente equipamentos em estoque, sem veículo e sem referência em contrato ativo. O parâmetro é opcional; as consultas existentes mantêm seu comportamento.

## Validação

- **668 testes de backend passaram**, com Python 3.12 e dependências da imagem. Os seis testes financeiros com falhas anteriores ficaram excluídos, conforme a [validação anterior](reconciliacao-proprietario-2026-10-09.md#valida%C3%A7%C3%A3o-realizada).
- **26 testes de frontend passaram**, incluindo sucesso, erro visível, conferência explícita, resposta sem troca, bloqueio durante gravação, veículo aberto pelo assistente fora da lista filtrada, transferência de proprietário e exclusão em lote. TypeScript passou. ESLint não encontrou erros; os nove avisos anteriores da página de veículos permanecem.
- Na cópia isolada do backup, a primeira tentativa retornou o conflito sem gravar alterações. Após selecionar o contrato 223, o contrato 669 recebeu o novo equipamento, o contrato 223 perdeu somente a referência incorreta e os rastreadores ficaram em suas situações esperadas. Os **38.198 registros de cobrança permaneceram idênticos em todos os campos**, assim como os demais clientes, veículos, planos e documentos. O IMEI substituto não constava no backup e foi criado como registro sintético apenas na cópia isolada para reproduzir a seleção do cliente.
- Testes de simulação do fechamento antes/depois preservaram placas, responsáveis financeiros e valores dos dois contratos. Falha de commit desfez troca, liberação, histórico e auditoria. Repetir uma troca concluída não produziu nova alteração.

Nenhuma verificação alterou a produção. A atualização e a troca devem ser executadas pelos passos acima.
