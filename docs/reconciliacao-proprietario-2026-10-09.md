# Reconciliação de proprietário — OXD0A94

O veículo passou para **RUBERVAL DA SILVA FILHO**, mas o rastreador não apareceu na ficha do cliente. O IMEI informado é **869671078139498**.

No backup de **08/10/2026 às 16:37**, anterior à ocorrência relatada, a placa está no veículo 720, o IMEI no rastreador 631 e o contrato ativo é o 630. Os três ainda pertencem a Cleovir. A produção atual não foi acessada: o comando abaixo confirma o proprietário atual antes de alterar qualquer registro.

A correção permite reconciliar rastreador e contratos ativos com o proprietário do veículo, inclusive quando a troca do veículo já foi salva. A ficha de veículos agora busca os equipamentos pelo vínculo físico com o veículo, percorre todas as páginas e informa divergências de cliente ou falhas da consulta. A reconciliação não consulta a Multiportal; apenas registra a intenção de sincronização na fila local existente.

## 1. Atualizar na VPS e guardar backup

Execute no terminal da VPS. O bloco para se algum comando falhar:

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
  BACKUP="backups-manuais/antes_reconciliacao_$(date +%Y%m%d_%H%M%S).dump"
  dc exec -T db sh -c 'pg_dump -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-rastreamento}" -Fc' > "$BACKUP"
  test -s "$BACKUP"
  echo "Backup salvo em: $BACKUP"

  dc build backend backend-worker frontend
  dc run --rm --no-deps backend python -m alembic upgrade head
  dc up -d --no-deps backend backend-worker frontend
  dc exec -T nginx nginx -s reload
)
```

Esta correção não adiciona migration. O `upgrade head` aplica as migrations anteriores que ainda estiverem pendentes, incluindo a de identificadores sem placa da atualização de 08/10.

## 2. Simular somente esta placa e este IMEI

```bash
cd ~/MasterSat
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend \
  python scripts/reconciliar_proprietario_veiculo.py \
  --placa OXD0A94 \
  --imei 869671078139498 \
  --cliente-esperado "RUBERVAL DA SILVA FILHO" \
  --operator administrator
```

A resposta traz `modo: SIMULACAO_SEM_GRAVACAO`, o proprietário, os IDs a reconciliar, o status do rastreador, a data de instalação e os contratos ativos. A transação é desfeita ao final. Os IDs são resolvidos pelo cadastro atual, sem fixar os números encontrados no backup.

O script recusa a operação se a placa não estiver no nome de Ruberval, se o IMEI não estiver fisicamente vinculado a essa placa ou se houver contrato ativo com rastreador fora do veículo. Não tenta adivinhar um vínculo ou transferir equipamento de outra placa. Nesse caso, guarde a mensagem completa para verificar o cadastro atual.

## 3. Aplicar a reconciliação

Se a simulação terminar com sucesso e mostrar a placa, o IMEI e Ruberval corretamente:

```bash
cd ~/MasterSat
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend \
  python scripts/reconciliar_proprietario_veiculo.py \
  --placa OXD0A94 \
  --imei 869671078139498 \
  --cliente-esperado "RUBERVAL DA SILVA FILHO" \
  --operator administrator \
  --apply
```

A resposta de sucesso contém `modo: APLICADO`. A operação atualiza os rastreadores vinculados e os contratos ativos dessa placa, mantendo o proprietário já cadastrado e o pagador configurado nos contratos. Não reinstala, não cria contrato e não gera cobranças. Status, data de instalação, plano, início e vencimento do contrato permanecem. Repetir o comando após a correção retorna listas vazias de alterações.

Títulos existentes mantêm cliente, valores, situação, vencimentos, referência do boleto e dados fiscais. Apenas títulos legados sem o pagador gravado recebem o responsável anterior como snapshot antes da mudança do contrato. A assinatura do contrato cujo proprietário mudou fica pendente novamente; documentos anteriores continuam no cadastro de origem. A alteração fica na auditoria e no histórico do rastreador.

## 4. Gerar o contrato para Ruberval

1. Recarregue o sistema e abra **Clientes → RUBERVAL DA SILVA FILHO → Veículos**. Confira `OXD0A94` e o IMEI `869671078139498`.
2. Abra **Ficha de adesão / contrato** pelo botão da impressora no cadastro de Ruberval.
3. Abra o PDF do contrato ativo da `OXD0A94` e confira o nome e a placa antes de enviá-lo ao cliente.
4. Depois de receber a assinatura, envie o documento assinado nessa ficha e selecione o contrato correspondente.

Também é possível reconciliar pela interface: **Veículos → OXD0A94 → Detalhes → Dados → Trocar cliente / pagador**, mantendo Ruberval e escolhendo o pagador desejado. Esse fluxo permite ajustar o pagador das cobranças futuras explicitamente.

O script de recuperação `sgr_recuperar_veiculos_ativos.py` usado para `TRATOR` e `BEY4J77` não participa deste ajuste.

## Validação realizada

- Backend com Python 3.12 e dependências da imagem: **657 testes passaram**, incluindo a reconciliação, contratos/PDF, cobranças, NFSe e recuperação de veículos. Seis testes financeiros antigos, já identificados como falhas anteriores, ficaram excluídos: `TestLinkVeiculo::test_link_with_plan_generates_billings`, `TestLinkVeiculo::test_link_sem_prorata_gera_exato`, `TestCreateContrato::test_generates_billings_by_default`, `TestCreateContrato::test_billing_cycles_out_of_range_422`, `TestGenerateBillings::test_generates_billings` e `TestGenerateBillings::test_default_12_months`.
- Frontend: **19 testes passaram** nos fluxos de veículos, troca de cliente, rastreadores, pagador e ficha de veículos. TypeScript e ESLint dos arquivos alterados passaram.
- Cópia isolada do backup: reprodução de uma troca incompleta, simulação sem gravação, aplicação e repetição sem novas alterações. Ruberval não consta nesse backup; um cliente sintético com o nome informado foi criado somente na cópia para reproduzir o caso. O PDF foi conferido nos campos do contratante e da placa. As **38.198 cobranças** foram preservadas; dois registros legados receberam o snapshot do pagador antigo. Demais veículos, rastreadores, contratos e documentos não mudaram.

Nenhuma dessas verificações alterou a produção. A correção desse cadastro em produção depende da execução dos comandos acima.
