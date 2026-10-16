# Desinstalação sem depender da Multiportal

Em **Veículos → Registrar desinstalação**, a confirmação passa a concluir apenas com os dados do MasterSat. A operação não consulta, compara nem altera vínculos na Multiportal. Integração desativada, erro do provedor ou configuração de transporte externo não bloqueiam a retirada.

A mesma operação local:

- Retira os rastreadores do veículo e do cliente, devolvendo-os ao estoque.
- Registra a data da retirada no veículo e nos equipamentos.
- Mantém os contratos ativos por padrão. As mensalidades continuam integrais nos próximos fechamentos, com o mesmo cliente, pagador e plano.
- Registra a desinstalação no histórico de cada rastreador, com vínculos anteriores e operador.
- Preserva a taxa acordada como evento pendente para o fechamento, sem gerar boleto imediato.
- Quando o encerramento dos contratos é solicitado e confirmado, registra a data final nesses contratos e aplica as regras de pró-rata. Cobrança com título bancário conserva o valor e registra ajuste pendente para o financeiro.

## Escolher se os contratos serão encerrados

A janela oferece duas caixas, inicialmente desmarcadas:

1. **Deseja encerrar os contratos ativos deste veículo?** A segunda confirmação aparece somente quando essa opção é marcada.
2. **Tenho certeza de que desejo encerrar os contratos e interromper as mensalidades futuras.** Sem essa confirmação adicional, o botão de desinstalação fica bloqueado. A API também recusa o encerramento sem as duas opções.

Sem escolher o encerramento, as datas, o pagador, o plano e as cobranças existentes dos contratos são preservados. Apenas a referência dos contratos aos equipamentos retirados é liberada: assim o rastreador retorna ao estoque e pode ser usado em outra instalação sem ficar preso a um contrato ativo da placa anterior. O contrato financeiro continua no veículo e no cliente originais.

Ao optar pelo encerramento e confirmar, todos os contratos ativos daquele veículo são encerrados na data informada. Valores devidos até a retirada e a taxa de desinstalação continuam sujeitos ao fechamento.

A segunda confirmação é desmarcada ao mudar a primeira opção ou a data. Fechar e reabrir a janela restaura as duas opções desmarcadas. Durante a gravação, edição, novo envio e fechamento pelo X, Esc ou clique fora ficam bloqueados. O resultado informa quantos contratos foram mantidos ou encerrados. A escolha e as duas confirmações ficam na auditoria da operação.

Essa escolha vale para novas desinstalações; atualizar o código não reabre contratos encerrados anteriormente.

Continuam válidas as permissões de administrador/operacional, as validações de datas, os limites de desconto e a proteção contra taxa duplicada por repetição da desinstalação. Falha na gravação desfaz as alterações locais da transação.

O rastreador mostra **Desinstalado no sistema**. Esse estado informa a retirada local; não confirma exclusão do vínculo no provedor. Não é criada uma nova sincronização para equipamento em estoque. Um item antigo da fila processado enquanto o equipamento estiver sem veículo é encerrado sem chamada externa. Os demais fluxos de envio à Multiportal permanecem disponíveis.

## API

Endpoint mantido: `POST /api/v1/vehicles/{id}/uninstall`, com os parâmetros de data, serviço e valor, mais:

| Parâmetro de consulta | Padrão | Efeito |
| --- | --- | --- |
| `encerrar_contratos` | `false` | Solicita encerrar os contratos ativos deste veículo. |
| `confirmar_encerramento_contratos` | `false` | Segunda confirmação obrigatória se o encerramento foi solicitado. |

Solicitar encerramento sem a segunda confirmação retorna HTTP 409 com código `contract_closure_confirmation_required`, antes de qualquer alteração. Omitir os novos parâmetros mantém os contratos ativos. A resposta acrescenta `contracts_closed` e `contracts_kept_active`, com as respectivas quantidades, e preserva os campos existentes. `multiportal_unlinked` retorna `false`, pois esta operação não remove o vínculo externo.

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

**194 testes distintos do backend passaram** em uma cópia limpa do projeto com apenas esta correção, usando banco sintético e contêiner sem rede. Foram verificados veículos, escolha de contratos, fila de integração, transações, fechamento e política bancária.

Os testes da desinstalação proíbem chamadas de consulta, desvínculo, compensação e sincronização à Multiportal. Cobrem os dois modos de contrato com equipamento sincronizado, com erro, pendente e sem estado anterior; integração habilitada, desabilitada e com transporte inseguro; taxa de R$ 160,00; histórico; fila antiga; e rollback de taxa/vínculos/histórico/auditoria quando o commit falha. Também comprovam mensalidade integral futura com contrato mantido, ausência de mensalidade futura após encerramento confirmado, preservação de cobrança existente e reutilização do rastreador em outro cliente sem encerrar o contrato original.

**21 testes de interface passaram**, incluindo a página real de veículos: padrão de manter contratos, exigência das duas opções, reinício das confirmações ao mudar a decisão/data ou reabrir, erro da API e bloqueio durante gravação, além dos fluxos existentes de troca de rastreador e mudança de cliente.

Uma expectativa antiga da política bancária foi excluída da seleção: `TestTituloBaixadoNaoVoltaAoCliente::test_pdf_e_email_recusados_com_baixa_pendente`, já diagnosticada na [entrega de cancelamento individual](alterar-boleto-cancelar-excluir-2026-10-09.md), espera um erro estruturado após uma validação anterior que já recusa a cobrança paga.

TypeScript passou. ESLint sem erros, com nove avisos preexistentes na tela de veículos. As alterações locais de outros trabalhos foram preservadas. Nenhuma desinstalação foi executada em dados locais ou de produção durante a validação.
