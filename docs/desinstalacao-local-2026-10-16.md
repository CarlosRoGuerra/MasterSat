# Desinstalação sem depender da Multiportal

Em **Veículos → Registrar desinstalação**, a confirmação passa a concluir apenas com os dados do MasterSat. A operação não consulta, compara nem altera vínculos na Multiportal. Integração desativada, erro do provedor ou configuração de transporte externo não bloqueiam a retirada.

A mesma operação local:

- Retira os rastreadores do veículo e do cliente, devolvendo-os ao estoque.
- Registra a data da retirada no veículo, nos equipamentos e nos contratos ativos daquele veículo.
- Cancela esses contratos e registra a desinstalação no histórico de cada rastreador, com vínculos anteriores e operador.
- Preserva a taxa acordada como evento pendente para o fechamento, sem gerar boleto imediato.
- Mantém as regras de pró-rata: cobrança com título bancário conserva o valor e registra ajuste pendente para o financeiro.

Continuam válidas as permissões de administrador/operacional, as validações de datas, os limites de desconto e a proteção contra taxa duplicada por repetição da desinstalação. Falha na gravação desfaz as alterações locais da transação.

O rastreador mostra **Desinstalado no sistema**. Esse estado informa a retirada local; não confirma exclusão do vínculo no provedor. Não é criada uma nova sincronização para equipamento em estoque. Um item antigo da fila processado enquanto o equipamento estiver sem veículo é encerrado sem chamada externa. Os demais fluxos de envio à Multiportal permanecem disponíveis.

## API

Endpoint mantido: `POST /api/v1/vehicles/{id}/uninstall`, com os mesmos parâmetros de data, serviço e valor. A resposta preserva os campos existentes. `multiportal_unlinked` retorna `false`, pois esta operação não remove o vínculo externo.

Não há migração de banco.

## Atualizar na VPS

```bash
(
  set -e
  cd ~/MasterSat
  git fetch origin
  git switch fix/rastreador-dia-vencimento
  git pull --ff-only origin fix/rastreador-dia-vencimento

  docker compose -f docker-compose.yml -f docker-compose.prod.yml build backend backend-worker frontend
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps backend backend-worker frontend
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T nginx nginx -s reload
)
```

Atualize o navegador com **Ctrl + F5** e tente registrar a desinstalação novamente. A atualização de código não executa desinstalações automaticamente.

## Desenvolvimento local

O Compose de desenvolvimento monta os arquivos do projeto e usa recarga automática. Com os serviços já iniciados, atualize a página e tente novamente. Para iniciar os serviços:

```powershell
docker compose up -d backend frontend
```

## Validação

**173 testes passaram** em uma cópia limpa do projeto com apenas esta correção, usando banco sintético e contêiner sem rede. Foram verificados veículos, fila de integração, transações, fechamento e política bancária.

Os testes da desinstalação proíbem chamadas de consulta, desvínculo, compensação e sincronização à Multiportal. Cobrem equipamento sincronizado, com erro, pendente e sem estado anterior; integração habilitada, desabilitada e com transporte inseguro; taxa de R$ 160,00; histórico; fila antiga; e rollback de taxa/vínculos/histórico quando o commit falha.

Uma expectativa antiga da política bancária foi excluída da seleção: `TestTituloBaixadoNaoVoltaAoCliente::test_pdf_e_email_recusados_com_baixa_pendente`, já diagnosticada na [entrega de cancelamento individual](alterar-boleto-cancelar-excluir-2026-10-09.md), espera um erro estruturado após uma validação anterior que já recusa a cobrança paga.

TypeScript passou. ESLint sem erros, com três avisos preexistentes na tela de rastreadores. As alterações locais de outros trabalhos foram preservadas. Nenhuma desinstalação foi executada em dados locais ou de produção durante a validação.
