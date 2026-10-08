![MasterSat - Solução completa em Rastreamento](assets/mastersat-logo.png)

# API de cobranças

## Manual de integração para notificações via WhatsApp

**MasterSat | Documentação para a empresa integradora**

Versão documental 1.4 | 7 de outubro de 2026

Consulta de cobranças, dados de pagamento, boleto em PDF e NFS-e em PDF/XML.

Este manual descreve a implementação existente no projeto. O endereço de acesso, a chave de integração e a liberação do ambiente serão fornecidos pela MasterSat. Os exemplos são fictícios e não devem ser usados para pagamentos ou mensagens reais.

<!-- pagebreak -->

## 01. Visão geral e índice

A integração permite que a empresa parceira consulte as cobranças da MasterSat e utilize os dados retornados para enviar lembretes e boletos pelo WhatsApp. A comunicação ocorre por HTTPS, com respostas JSON e arquivos PDF.

O modelo é de **consulta periódica pela integradora**: a empresa parceira inicia as requisições. Os endpoints deste manual não disparam mensagens e não recebem confirmações de entrega, leitura ou pagamento.

### Responsabilidades

| MasterSat | Empresa integradora |
| --- | --- |
| Manter cobranças, pagadores e preferências de envio. | Consultar a API e selecionar os títulos elegíveis. |
| Disponibilizar dados do boleto e situação financeira registrada no sistema. | Validar o destinatário e enviar mensagens e anexos. |
| Atualizar pagamentos e cancelamentos no sistema. | Controlar agenda, tentativas, duplicidade e histórico dos envios. |
| Fornecer endereço, chave e cobranças de teste. | Proteger a chave e informar falhas à MasterSat. |

### Navegação do manual

| Seção | Página | Seção | Página |
| --- | --- | --- | --- |
| Acesso e autenticação | 3 | Detalhe e PDF autenticado | 9 |
| Fluxo e critérios de envio | 4 | Link do cliente | 10 |
| Listar cobranças | 5 | Erros e recuperação | 11 |
| Exemplo de resposta | 6 | Primeiras requisições | 12 |
| Dicionário: cobrança e pagador | 7 | Automação e mensagem | 13 |
| Dicionário: boleto e status | 8 | Homologação e entrega | 14 |

> **Escopo disponível:** seis operações de leitura autenticadas e links de PDF de boleto/NFS-e protegidos por token, que abrem sem chave de API. A seção 14 descreve a consulta e o download da NFS-e. Emissão de boleto ou NFS-e, baixa financeira, alteração cadastral e envio pelo WhatsApp não fazem parte deste contrato de integração.

<!-- pagebreak -->

## 02. Acesso e autenticação

### Configuração inicial

| Item | Orientação |
| --- | --- |
| URL base | Domínio HTTPS da API, sem barra final e sem `/api/v1`. No Postman, usar a variável `base_url`. |
| Endereço previsto no projeto | `https://api.mastersat.com.br`, conforme a documentação de implantação. A MasterSat deve confirmar o ambiente disponibilizado ao parceiro. |
| Prefixo das rotas | `/api/v1`, conforme a configuração padrão do projeto. |
| Credencial | Chave de serviço fornecida pela MasterSat, enviada no cabeçalho `X-API-Key`. |
| Respostas | JSON para consultas, `application/pdf` para documentos e `application/xml` para XML fiscal. |
| Homologação | Usar cobranças e destinatários de teste definidos entre as equipes. |

Todas as seis rotas de integração exigem o cabeçalho abaixo. A chave não utiliza o login do painel, não requer Bearer e não possui endpoint de renovação nesta integração.

```http
GET /api/v1/integrations/cobrancas?forma_envio=whatsapp HTTP/1.1
Host: api.exemplo.invalid
X-API-Key: CHAVE_FORNECIDA_PELA_MASTERSAT
Accept: application/json
```

### Preparação pela equipe MasterSat

1. Configurar `INTEGRATION_API_KEY` no ambiente do backend e disponibilizar a chave por canal seguro.
2. Configurar `BACKEND_PUBLIC_URL` com o domínio HTTPS acessível externamente. Esse valor compõe os links retornados pela API.
3. Confirmar que a cobrança de teste tem pagador, telefone, preferência de envio e boleto registrado.
4. Confirmar acesso externo às rotas autenticadas e ao link público do boleto.

> **Credencial de serviço:** executar a integração no servidor da parceira. Não colocar a chave no aplicativo do cliente, na mensagem, no link, em logs ou em arquivos compartilhados. A troca da chave exige atualizar o consumidor; não há rotação automática ou chaves individuais por parceiro nestes endpoints.

O Swagger pode estar desabilitado no ambiente publicado. A coleção Postman que acompanha este manual pode ser utilizada independentemente dele.

<!-- pagebreak -->

## 03. Fluxo e critérios de envio

### Sequência operacional

1. **Consultar:** solicitar `GET /api/v1/integrations/cobrancas?forma_envio=whatsapp` com a chave de integração.
2. **Selecionar:** aplicar a régua acordada com a MasterSat e validar telefone, preferência de envio e dados do boleto.
3. **Reservar o envio:** verificar o histórico e impedir que dois processos trabalhem na mesma notificação.
4. **Revalidar:** consultar o detalhe da cobrança imediatamente antes do disparo. Refazer as verificações com os dados atualizados.
5. **Obter o documento:** usar `boleto_link_cliente` para o cliente ou baixar o PDF para anexar, após as verificações de elegibilidade.
6. **Enviar e registrar:** salvar o resultado, o identificador da mensagem da plataforma e a próxima ação, se houver.

### Condições mínimas para disparar

| Verificação | Regra da integradora |
| --- | --- |
| Situação financeira | Aceitar somente `pendente` ou `vencida`. Nunca disparar cobrança para `paga` ou `cancelada`. |
| Canal do pagador | Exigir `forma_envio` igual a `whatsapp` ou `todos`, ou `enviar_boleto_whatsapp` igual a `true`. |
| Destinatário | Utilizar `cliente.telefone`, após validação. Suspender o item se estiver vazio, ambíguo ou inválido. |
| Documento | Exigir `boleto_disponivel = true`, linha digitável e código de barras preenchidos. Conferir a abertura do link público antes de distribuir o boleto. |
| Agenda | Aplicar dias, horários e frequência aprovados pela MasterSat. Não enviar a cada consulta. |
| Duplicidade | Garantir uma única execução para a mesma cobrança, etapa da régua e ocorrência agendada. |

> **Consulta não é confirmação bancária instantânea.** O status reflete o estado registrado no MasterSat. A atualização depende das rotinas financeiras. Reconsultar antes do envio reduz mensagens indevidas, mas não elimina o intervalo entre pagamento, conciliação e disparo.

<!-- pagebreak -->

## 04. Consultar cobranças e pagamentos

```http
GET /api/v1/integrations/cobrancas
X-API-Key: CHAVE_FORNECIDA_PELA_MASTERSAT
```

Sem o parâmetro `status`, retorna cobranças com status `pendente` ou `vencida`, preservando o comportamento da versão 1.0. Para consultar pagamentos confirmados, informar `status=paga`; para todas as situações, `status=todos`. Cobranças, clientes de origem e pagadores de snapshot removidos são excluídos. A ordenação é por vencimento crescente, com desempate por ID.

### Parâmetros de consulta

| Parâmetro | Tipo / padrão | Comportamento |
| --- | --- | --- |
| `forma_envio` | `whatsapp` ou `email`; opcional. | Usa o cadastro do pagador retornado. `whatsapp`: envio `whatsapp`, `todos` ou opção de boleto por WhatsApp marcada. `email`: envio `email`, `todos` ou preferência vazia (padrão `email`). |
| `status` | `pendente`, `vencida`, `paga`, `cancelada` ou `todos`; opcional. | Sem o parâmetro, somente abertas. `paga` corresponde ao pagamento confirmado no MasterSat. |
| `vencimento` | Data opcional `AAAA-MM-DD`. | Filtra o vencimento exato. |
| `vencimento_de` | Data opcional `AAAA-MM-DD`. | Vencimento inicial, inclusive. |
| `vencimento_ate` | Data opcional `AAAA-MM-DD`. | Vencimento final, inclusive. |
| `pagamento_de` | Data opcional `AAAA-MM-DD`. | Data de pagamento inicial, inclusive. |
| `pagamento_ate` | Data opcional `AAAA-MM-DD`. | Data de pagamento final, inclusive. Registros sem data de pagamento não entram nesse filtro. |
| `limit` | Inteiro; padrão `500`. | Mínimo `1`, máximo `2000`. Limita a quantidade de registros retornados. |
| `offset` | Inteiro; padrão `0`. | Mínimo `0`. Quantidade de registros a pular no conjunto filtrado. |

```http
GET /api/v1/integrations/cobrancas?forma_envio=whatsapp&status=vencida&vencimento_de=2026-09-01&vencimento_ate=2026-09-30&limit=500&offset=0

# Pendentes com vencimento em uma data
GET /api/v1/integrations/cobrancas?status=pendente&vencimento=2026-10-10

# Pagamentos confirmados entre duas datas
GET /api/v1/integrations/cobrancas?status=paga&pagamento_de=2026-10-01&pagamento_ate=2026-10-07
```

| Campo da resposta | Significado |
| --- | --- |
| `total` | Quantidade de itens **nesta resposta**, depois da aplicação do limite. |
| `total_registros` | Quantidade de registros que atendem aos filtros, antes de `limit` e `offset`. |
| `limit`, `offset` | Valores aplicados à página atual. |
| `has_more` | Indica se há registros após a página atual. |
| `next_offset` | Offset da próxima página; `null` quando terminou. |
| `cobrancas` | Lista de objetos de cobrança. Pode ser vazia. |

```json
{"total": 0, "total_registros": 0, "limit": 500, "offset": 0, "has_more": false, "next_offset": null, "cobrancas": []}
```

### Paginação e filtros

- Enquanto `has_more` for `true`, repetir os mesmos filtros e `limit`, utilizando `offset=next_offset`. O teto de `2000` vale por página; a carteira pode ter mais registros.
- Todos os filtros são combinados. Datas inicial e final são inclusivas; intervalos invertidos, datas inválidas, status/canais desconhecidos e limites inválidos retornam `422`.
- A paginação por offset não congela a carteira entre consultas. Uma alteração concorrente pode deslocar registros; manter deduplicação por ID e revalidar o detalhe antes do envio.
- Não há filtro por data de alteração recente nesta versão. A ausência de um ID em qualquer lista não comprova pagamento nem cancelamento.

> **Pagador diferente do cliente de origem:** o filtro de canal e os contatos da resposta utilizam o mesmo responsável financeiro. O snapshot salvo na cobrança tem precedência; em cobranças legadas, utiliza-se o interveniente ativo do contrato e, na ausência dele, o cliente da cobrança.

<!-- pagebreak -->

## 05. Exemplo de resposta JSON

Exemplo ilustrativo de listagem com um boleto registrado. Os códigos, contatos, domínio e token abaixo são fictícios e não são utilizáveis. As URLs reais são devolvidas completas pela API.

```json
{
  "total": 1,
  "total_registros": 1,
  "limit": 500,
  "offset": 0,
  "has_more": false,
  "next_offset": null,
  "cobrancas": [
    {
      "id": 73,
      "titulo": "Mensalidade de rastreamento",
      "cliente": {
        "id": 25,
        "nome": "Cliente de Homologação",
        "cpf_cnpj": "00000000000",
        "telefone": "5500000000000",
        "email": "cliente@example.invalid"
      },
      "valor": 99.90,
      "vencimento": "2026-10-10",
      "status": "pendente",
      "pagamento_confirmado": false,
      "data_pagamento": null,
      "valor_pago": null,
      "forma_pagamento": null,
      "forma_envio": "whatsapp",
      "enviar_boleto_whatsapp": true,
      "nosso_numero": "EXEMPLO_NAO_PAGAVEL",
      "linha_digitavel": "LINHA_FICTICIA_NAO_PAGAVEL",
      "codigo_barras": "CODIGO_FICTICIO_NAO_PAGAVEL",
      "pix_copia_cola": null,
      "boleto_registrado": true,
      "boleto_disponivel": true,
      "motivo_boleto_indisponivel": null,
      "boleto_pdf_url": "https://api.exemplo.invalid/api/v1/integrations/cobrancas/73/pdf",
      "boleto_link_cliente": "https://api.exemplo.invalid/api/v1/public/boleto/73/TOKEN_EXEMPLO",
      "valor_com_juros": null,
      "nfse": null
    }
  ]
}
```

### Como interpretar

`id` é o identificador da cobrança no MasterSat. Utilizá-lo na consulta de detalhe e no controle de notificações. `cliente.id` identifica o pagador e não substitui o ID da cobrança.

`boleto_pdf_url` exige a chave de integração. `boleto_link_cliente` foi preparado para ser aberto pelo destinatário sem login. O Pix pode ser `null` mesmo quando o boleto está registrado.

Quando não há linha digitável e código de barras oficiais, a cobrança continua na resposta com `boleto_registrado = false`, `boleto_disponivel = false`, códigos de pagamento e URLs de boleto nulos. `motivo_boleto_indisponivel` explica a situação. `nosso_numero` preserva a referência oficial armazenada, quando existente, mesmo que o boleto esteja indisponível. A API não calcula códigos ou referências locais para preencher dados ausentes. O Pix permanece `null` quando não foi fornecido pelo banco.

<!-- pagebreak -->

## 06. Dicionário: cobrança e pagador

Valores monetários são números em reais, com separador decimal de JSON. Preservar a precisão monetária no consumidor e formatar a apresentação em reais, com duas casas decimais. Datas de vencimento usam `AAAA-MM-DD`, sem horário.

| Campo | Tipo | Descrição e uso |
| --- | --- | --- |
| `id` | Inteiro | Identificador da cobrança. Chave de referência para consultas e histórico de notificações. |
| `titulo` | Texto ou `null` | Descrição cadastrada. Se estiver vazia, usar um texto neutro aprovado pela MasterSat. |
| `cliente` | Objeto | Responsável financeiro selecionado pelo sistema para a cobrança. |
| `cliente.id` | Inteiro | Identificador do responsável financeiro. |
| `cliente.nome` | Texto | Nome ou razão social do pagador. |
| `cliente.cpf_cnpj` | Texto | Documento cadastrado. Preservar como texto; não é necessário expô-lo na mensagem. |
| `cliente.telefone` | Texto ou `null` | Telefone do pagador, sem garantia de normalização internacional ou de disponibilidade no WhatsApp. |
| `cliente.email` | Texto ou `null` | E-mail principal cadastrado. |
| `valor` | Número | Valor nominal/original da cobrança, em reais. |
| `vencimento` | Texto de data ou `null` | Vencimento. Não converter a data em timestamp UTC para montar a régua. Suspender itens sem data válida. |
| `status` | Texto | `pendente`, `vencida`, `paga` ou `cancelada`. A lista permite consultar todas; sem filtro, inclui somente as duas primeiras. |
| `pagamento_confirmado` | Booleano | `true` somente quando `status = paga`, conforme o estado financeiro registrado no MasterSat. |
| `data_pagamento` | Data ou `null` | Data registrada de pagamento. Pode faltar em históricos legados; a API não inventa uma data. |
| `valor_pago` | Número ou `null` | Valor efetivamente recebido, quando registrado. Mantém-se separado do valor nominal. |
| `forma_pagamento` | Texto ou `null` | Meio de pagamento registrado, quando disponível. |
| `forma_envio` | Texto | Preferência do pagador: `email`, `whatsapp` ou `todos`. Quando o cadastro está vazio, a API retorna `email`. |
| `enviar_boleto_whatsapp` | Booleano | Opção específica de envio por WhatsApp. Pode habilitar o canal mesmo se `forma_envio` for `email`. |
| `nfse` | Objeto ou `null` | Nota fiscal vinculada à cobrança, com dados e URLs de PDF/XML descritos na seção 14. `null` quando não há nota. |

### Quem deve receber a mensagem

O sistema prioriza o responsável financeiro salvo na cobrança. Em registros legados, pode utilizar o interveniente financeiro do contrato; na ausência dele, utiliza o cliente da cobrança. A integradora deve usar o objeto `cliente` retornado, sem substituir o contato pelo proprietário do veículo.

O endpoint entrega o telefone principal. Contatos adicionais, contatos de emergência e e-mails extras não são retornados neste contrato.

<!-- pagebreak -->

## 07. Dicionário: boleto, valores e status

| Campo | Tipo | Descrição e uso |
| --- | --- | --- |
| `nosso_numero` | Texto ou `null` | Número oficial salvo no registro Ailos, preservado como referência mesmo em cobranças pagas/canceladas ou boletos baixados/liquidados. Manter os zeros iniciais. Nulo se a referência não está armazenada. Não substituir `id` por esse campo nas rotas. |
| `linha_digitavel` | Texto ou `null` | Linha de pagamento retornada pelo sistema. Preservar como texto, inclusive zeros iniciais. |
| `codigo_barras` | Texto ou `null` | Representação numérica do código de barras. Não converter para número. |
| `pix_copia_cola` | Texto ou `null` | Código Pix quando disponível. Enviar exatamente como recebido; omitir o bloco Pix quando nulo. |
| `boleto_registrado` | Booleano | Indica registro local Ailos com linha digitável **e** código de barras. Não representa consulta bancária em tempo real nem dívida em aberto. |
| `boleto_disponivel` | Booleano | Cobrança aberta, com dados oficiais completos, sem baixa pendente/confirmada ou liquidação registrada no título, e não restrita ao sistema. |
| `motivo_boleto_indisponivel` | Texto ou `null` | `sem_registro_bancario`, `cobranca_paga`, `cobranca_cancelada`, `boleto_baixado` ou `somente_sistema`. Nulo quando disponível. |
| `boleto_pdf_url` | Texto / URL ou `null` | Endpoint de download autenticado para a integradora. Nulo quando `boleto_disponivel` é falso. |
| `boleto_link_cliente` | Texto / URL ou `null` | Link do PDF com token para o cliente. Nulo quando `boleto_disponivel` é falso. |
| `valor_com_juros` | Número ou `null` | Valor atualizado calculado pelo backend quando o status é `vencida` e há atraso. Não recalcular encargos na integradora. |

### Interpretação da situação financeira

| Status | Ação |
| --- | --- |
| `pendente` | Considerar para lembrete conforme vencimento e régua acordada. |
| `vencida` | Considerar para aviso de atraso. Se houver valor atualizado, identificá-lo como tal na mensagem. |
| `paga` | Suspender notificações de cobrança e retirar da fila. Pode ser usada para uma mensagem de confirmação de pagamento, com controle de duplicidade na integradora. |
| `cancelada` | Suspender notificações de cobrança e retirar da fila. |

### Particularidades da versão atual

O backend reclassifica vencimentos em rotina periódica, prevista a cada hora. O status e a data podem apresentar diferença temporária. A rotina financeira usa a data de Brasília (UTC-03:00 na implementação analisada).

`boleto_registrado` verifica linha digitável e código de barras oficiais. `boleto_disponivel` acrescenta a situação financeira e bancária local. Os campos de boleto vêm diretamente do registro Ailos, sem depender da geração de PDF na listagem. Revalidar o detalhe e a abertura do link antes do envio.

Desde a versão 1.4, `nosso_numero` permanece na listagem e no detalhe como referência histórica, independentemente de `boleto_disponivel`. Uma cobrança paga com referência armazenada retorna esse número; linha digitável, código de barras, Pix e URLs de boleto continuam nulos, e o PDF do boleto permanece bloqueado para pagamento. Sem número oficial armazenado, o campo permanece `null`.

O valor do boleto continua nominal; `valor_com_juros` é uma informação separada. Não modificar PDF, linha digitável, código de barras ou Pix para embutir encargos.

<!-- pagebreak -->

## 08. Consultar detalhe e baixar PDF

### Detalhe de uma cobrança

```http
GET /api/v1/integrations/cobrancas/{billing_id}
X-API-Key: CHAVE_FORNECIDA_PELA_MASTERSAT
Accept: application/json
```

Substituir `{billing_id}` pelo `id` retornado na listagem. O parâmetro deve ser inteiro. Uma resposta `200` contém diretamente o objeto da cobrança, com os mesmos campos apresentados no dicionário; não há envelope `total` ou `cobrancas`.

Ao contrário da listagem, o detalhe pode devolver uma cobrança `paga` ou `cancelada`. Essa é a consulta a utilizar para revalidar um item já colocado na fila de envio. Não interpretar a existência da cobrança como autorização para disparo.

### PDF autenticado para a integradora

```http
GET /api/v1/integrations/cobrancas/{billing_id}/pdf
X-API-Key: CHAVE_FORNECIDA_PELA_MASTERSAT
Accept: application/pdf
```

| Item | Retorno |
| --- | --- |
| Status de sucesso | `200 OK` |
| Tipo de conteúdo | `application/pdf` |
| Corpo | Conteúdo binário do PDF. Não é JSON e não é Base64. |
| Nome sugerido | `boleto_000073.pdf` para a cobrança de ID `73`. |
| Disposição | `Content-Disposition: inline; filename="boleto_000073.pdf"` |

A integradora deve baixar os bytes no seu servidor e anexá-los pelo mecanismo de mídia da plataforma de mensagens. Se a plataforma não aceitar cabeçalhos customizados, ela não conseguirá baixar diretamente a URL autenticada sem intermediação.

> **PDF indisponível:** títulos pagos, cancelados, restritos ao sistema, sem dados oficiais completos ou com baixa/liquidação registrada retornam `409` com o código do motivo. A rota não entrega um boleto local calculado para cobrir a ausência de registro. Revalidar o detalhe e os critérios da seção 03 imediatamente antes do envio.

Não encaminhar `boleto_pdf_url` como link de acesso do cliente: esse endereço exige a chave que pertence à integradora.

<!-- pagebreak -->

## 09. Link de boleto para o cliente

O campo `boleto_link_cliente` contém a URL pronta para ser inserida na mensagem. O destinatário abre o PDF sem login e sem a chave da integração.

```http
GET /api/v1/public/boleto/{billing_id}/{token}
```

### Como utilizar

1. Consultar e revalidar a cobrança pelas rotas autenticadas.
2. Obter `boleto_link_cliente` diretamente da resposta. Não calcular o token nem montar a URL manualmente.
3. Confirmar que o link responde com `200` e `Content-Type: application/pdf` antes de distribuí-lo.
4. Inserir a URL na mensagem ou utilizar o documento validado como anexo, conforme o fluxo acordado.

| Situação | Comportamento atual |
| --- | --- |
| Token válido e boleto disponível | Retorna o PDF. |
| Token inválido | `404`, sem disponibilizar o documento. |
| Cobrança cancelada ou excluída | `404`. |
| Ausência de linha digitável ou código de barras oficiais | `404`. |
| Cobrança paga | O link não possui bloqueio específico por status `paga`; sua abertura não comprova que existe dívida. |

### Proteção e validade

O token protege o acesso ao documento. Quem possui o link consegue abri-lo enquanto as condições do endpoint permitirem. Não publicar esse endereço em páginas abertas, planilhas amplamente compartilhadas ou logs de acesso irrestrito.

Não há prazo de expiração temporal programado no token desta versão. A troca da chave de API não revoga os links já emitidos. Por isso, a integradora deve reconsultar o status, usar o link retornado e seguir a retenção de dados acordada com a MasterSat.

### Telefone e conteúdo

O telefone chega como cadastrado. Normalizar somente após identificar país e DDD; não duplicar o código do país e não completar números ambíguos por suposição. Não existe verificação de presença no WhatsApp neste endpoint.

Enviar ao pagador apenas as informações necessárias: identificação da MasterSat, descrição, vencimento, valor e acesso ao boleto. A chave de API, CPF/CNPJ completo e informações técnicas internas não precisam constar da mensagem.

<!-- pagebreak -->

## 10. Erros, limites e recuperação

Erros tratados pela aplicação normalmente possuem o campo JSON `detail`. Falhas de validação retornam uma lista nesse campo. O proxy pode responder em outro formato, portanto a integradora deve avaliar primeiro o status HTTP e o tipo de conteúdo.

| HTTP | Causa provável | Tratamento recomendado |
| --- | --- | --- |
| `200` | Consulta ou PDF obtido. | Validar os dados antes de utilizar. Lista vazia é uma resposta válida. |
| `401` | Chave ausente ou incorreta. | Suspender as tentativas e conferir o cabeçalho com a MasterSat. |
| `404` | Cobrança/cliente indisponível ou link público inválido/indisponível. | Suspender o item e reavaliar. Não interpretar automaticamente como pagamento. |
| `409` | Boleto autenticado indisponível para pagamento. | Ler `detail.code`, suspender o envio do documento e reconsultar o detalhe. |
| `422` | Parâmetro inválido ou falha ao gerar o PDF autenticado. | Corrigir a requisição ou encaminhar o ID à MasterSat. Não reenviar em sequência. |
| `429` | Limite de requisições atingido. | Reduzir a frequência e reagendar a tentativa. |
| `503` | Integração sem chave configurada; também pode haver indisponibilidade da infraestrutura. | Ler o erro. Se a chave estiver ausente no servidor, solicitar configuração à MasterSat. |
| `500`, `502`, `504` | Falha interna, de infraestrutura ou timeout. | Registrar a ocorrência e repetir com espera progressiva, sem disparar mensagem com dados não revalidados. |

### Exemplos de erros da aplicação

```json
{"detail": "API key inválida ou ausente."}
```

```json
{"detail": "Cobrança não encontrada"}
```

```json
{"detail": "Não foi possível gerar o boleto desta cobrança."}
```

### Política de execução recomendada

O limitador geral do backend está definido em `200` requisições por minuto, por IP; as demais camadas do ambiente também podem impor limites. Homologar a carga total, incluindo consulta de detalhes e downloads. Não depender de cabeçalhos de limite ou `Retry-After`, pois eles não são garantidos.

Como configuração inicial da integradora, propor consulta a cada 15 minutos, timeout de 30 segundos e até três novas tentativas de consultas transitórias, com esperas de 5, 15 e 60 segundos e pequena variação aleatória. São sugestões para acordo entre as equipes, não comportamentos executados pela API.

<!-- pagebreak -->

## 11. Primeiras requisições

### cURL em Bash

Definir `MASTERSAT_BASE_URL` com o domínio confirmado e injetar `MASTERSAT_API_KEY` pelo gerenciador de segredos. Para as duas últimas chamadas, definir `BILLING_ID` com uma cobrança de homologação.

```bash
# 1. Consultar candidatos
curl --fail-with-body --get \
  "$MASTERSAT_BASE_URL/api/v1/integrations/cobrancas" \
  --header "X-API-Key: $MASTERSAT_API_KEY" \
  --header "Accept: application/json" \
  --data-urlencode "forma_envio=whatsapp" \
  --data-urlencode "limit=500"

# 2. Revalidar uma cobrança
curl --fail-with-body \
  "$MASTERSAT_BASE_URL/api/v1/integrations/cobrancas/$BILLING_ID" \
  --header "X-API-Key: $MASTERSAT_API_KEY"

# 3. Baixar o PDF de uma cobrança validada
curl --fail \
  "$MASTERSAT_BASE_URL/api/v1/integrations/cobrancas/$BILLING_ID/pdf" \
  --header "X-API-Key: $MASTERSAT_API_KEY" \
  --header "Accept: application/pdf" \
  --output boleto.pdf
```

### PowerShell

```powershell
$apiBase = $env:MASTERSAT_BASE_URL.TrimEnd('/')
$cabecalhos = @{ 'X-API-Key' = $env:MASTERSAT_API_KEY }
$uri = "$apiBase/api/v1/integrations/cobrancas" +
       '?forma_envio=whatsapp&limit=500'
$resposta = Invoke-RestMethod -Method Get -Uri $uri `
    -Headers $cabecalhos
$resposta.cobrancas
```

### Coleção Postman incluída

Importar `mastersat-cobrancas.postman_collection.json`. Criar um ambiente local com `base_url`, `api_key`, `billing_id` e `boleto_link_cliente`. A chave fica vazia no arquivo distribuído: preencher somente no ambiente privado e não exportar a credencial.

Executar a listagem, selecionar um ID, consultar o detalhe e testar os PDFs. A requisição de link público está configurada sem autenticação. A coleção não envia mensagens e não altera cobranças.

O [guia de comandos da versão 1.1](guia-comandos-v1.1.md) contém exemplos completos para PowerShell e cURL, incluindo pagamentos confirmados e paginação automática.

<!-- pagebreak -->

## 12. Automação e modelo de mensagem

### Lógica de referência

Este fluxo é pseudocódigo para implementação pela integradora; não representa um serviço adicional já disponível no MasterSat.

```text
a cada ciclo acordado:
  offset = 0
  repetir:
    lote = consultar_cobrancas(forma_envio="whatsapp", limit=500, offset=offset,
                              status=etapa.status, vencimento_de=etapa.inicio,
                              vencimento_ate=etapa.fim)
    para cada candidato em lote.cobrancas:
      se fora_da_regua(candidato): continuar
      chave = (candidato.id, etapa_regua, ocorrencia_agendada)
      se nao_reservar_atomicamente(chave): continuar
      atual = consultar_detalhe(candidato.id)
      se status_nao_aberto(atual): suspender(chave); continuar
      se canal_ou_telefone_invalido(atual): suspender(chave); continuar
      se nao atual.boleto_disponivel: suspender(chave); continuar
      se link_publico_indisponivel(atual): suspender(chave); continuar
      se fora_da_regua(atual): liberar(chave); continuar
      resultado = enviar_mensagem_com_dados_atualizados(atual)
      registrar_resultado(chave, resultado)
    se nao lote.has_more: terminar
    offset = lote.next_offset
```

Para a etapa de confirmação de pagamento, consultar separadamente `status=paga` com intervalo de `pagamento_de`/`pagamento_ate`, revalidar `pagamento_confirmado` no detalhe e reservar uma única notificação por cobrança. Não enviar boleto nem lembrete de cobrança nessa etapa. Se o histórico não possui data de pagamento, consultar por status/vencimento; esse registro não entra no filtro de data de pagamento.

### Duplicidade e falhas de envio

A reserva e o histórico devem ficar no banco da integradora, com unicidade por cobrança, etapa e ocorrência agendada. Guardar o horário, a tentativa, o resultado e o ID da mensagem do provedor. Erros de consulta liberam ou reagendam a reserva com controle.

Se houver timeout depois de solicitar o envio ao provedor, o resultado pode ser desconhecido. Consultar o status pelo identificador/idempotência oferecido pela plataforma antes de repetir. O MasterSat não possui endpoint de confirmação de mensagem enviada.

### Mensagem sugerida para lembrete

> Olá, {nome}. Aqui é a MasterSat. Seu boleto de {titulo}, no valor de {valor_formatado}, vence em {vencimento_formatado}. Acesse o documento: {boleto_link_cliente}. Se já realizou o pagamento, por favor desconsidere este lembrete.

Os campos entre chaves são substituições feitas pela integradora. Para aviso de atraso, adaptar o texto conforme a régua aprovada. Quando houver `valor_com_juros`, apresentá-lo como valor atualizado informado pelo sistema, sem alterar os códigos de pagamento.

O conteúdo, os horários, a periodicidade e os modelos utilizados na plataforma de WhatsApp devem ser definidos entre a MasterSat e a empresa responsável pelos envios antes da ativação.

<!-- pagebreak -->

## 13. Homologação e entrega à integradora

### Cenários de aceite

| Teste | Resultado esperado |
| --- | --- |
| Chave válida, ausente e incorreta | Sucesso com a chave válida; `401` quando ausente/incorreta em ambiente configurado. |
| Consulta sem resultados | `200` com `total = 0` e lista vazia. |
| Preferências e responsável financeiro | Validar `whatsapp`, `todos`, opção específica e pagador diferente do cliente de origem. |
| Boleto registrado e sem registro | Distribuir somente o documento elegível e disponível; suspender sem registro ou com dados incompletos. |
| PDF e link público | Abrir PDF autenticado e público; token incorreto deve falhar. Não inserir chave no link do cliente. |
| Pago/cancelado depois da listagem | Reconsulta de detalhe impede o envio. Abertura do link não substitui a consulta de status. |
| Pix ausente e telefone inválido | Omitir Pix nulo; suspender o item com destinatário inválido. |
| Reexecução e timeout | Não duplicar mensagens; tratar resultado desconhecido de envio junto ao provedor. |
| Filtros de data/status | Combinar vencimento exato ou intervalo com `pendente`, `vencida`, `paga` e `todos`; parâmetros inválidos retornam `422`. |
| Pagamentos confirmados | `status=paga` retorna confirmação, data e valor recebido quando registrados; nenhum pagamento é inferido pela ausência de um ID. |
| Volume acima de 2000 | Percorrer `next_offset` até `has_more = false`, com ordenação por vencimento e ID. |
| Falhas HTTP e indisponibilidade | Interromper/reagendar conforme a seção 10, sem enviar com verificação incompleta. |

### Informações para início da operação

A MasterSat e a integradora devem registrar: nome e contato dos responsáveis técnicos, URL de homologação/produção, entrega segura da chave, IDs e telefones de teste, régua de cobrança, volume esperado e canal para incidentes. A ativação deve ocorrer após a validação conjunta desses itens.

Ao relatar um problema, informar data/hora com fuso, método, rota, ID da cobrança, status HTTP e mensagem de erro sanitizada. Nunca enviar a chave, o token do link ou dados pessoais desnecessários no chamado.

### Base técnica e versão

Versão 1.4 atualizada em 07/10/2026 para preservar `nosso_numero` como referência dos boletos pagos/indisponíveis, mantendo o link direto do PDF da NFS-e, consulta fiscal, filtros, paginação e dados de pagamento anteriores. Referências principais: `backend/app/api/v1/endpoints/integrations.py`, `backend/app/schemas/integration_billing.py`, `backend/app/api/v1/endpoints/boletos.py`, `backend/app/api/deps.py`, `backend/tests/test_integrations_api.py`, `backend/tests/test_integrations_nfse_api.py` e `backend/tests/test_integrations_nfse_public.py`.

Os 70 testes de integração passaram com dados sintéticos em banco SQLite isolado, incluindo leitura acima de 2000 registros e consulta de pagamentos. Essa verificação não certifica os registros da carteira de produção, a configuração do servidor publicado, a entrega pelo WhatsApp ou a homologação bancária. Consultas não alteram status, não conciliam pagamentos e não chamam o banco para emitir boletos.

**Arquivos atualizados na versão 1.4:** fonte editável deste manual em Markdown, guia de comandos e coleção Postman sem credenciais. PDFs distribuídos da versão 1.0 precisam ser regenerados a partir desta fonte antes da entrega. Versões futuras da API devem motivar revisão deste documento.

<!-- pagebreak -->

## 14. Consultar e baixar a nota fiscal de serviço

A listagem e o detalhe da cobrança agora incluem `nfse`. Quando não existe nota vinculada, o campo é `null`. Quando existe, retorna os dados fiscais e a disponibilidade dos documentos, inclusive para cobranças pagas. Para obter notas dessas cobranças na listagem, usar `status=paga` ou `status=todos`; sem filtro, a lista continua mostrando somente cobranças abertas.

Os filtros `vencimento_de`/`vencimento_ate` e `pagamento_de`/`pagamento_ate` continuam filtrando as datas da cobrança. Eles não filtram `nfse.data_emissao` ou `nfse.competencia`.

### Dados da nota

| Campo em `nfse` | Tipo | Descrição |
| --- | --- | --- |
| `nota_id`, `billing_id` | Inteiros | ID da nota e ID da cobrança vinculada. As rotas abaixo recebem `billing_id`. |
| `status` | Texto | Situação fiscal armazenada: `emitida`, `pending`, `processing`, `erro`, `desconhecido` ou outra situação fiscal cadastrada. Independente do status financeiro. |
| `numero_nfse`, `serie_nfse` | Texto ou `null` | Número e série da NFS-e. Preservar como texto. |
| `codigo_verificacao`, `chave_acesso` | Texto ou `null` | Identificadores fiscais retornados pelo provedor. |
| `link_visualizacao` | URL ou `null` | Endereço de consulta fornecido pelo provedor, quando registrado. |
| `data_emissao` | Data/hora ISO ou `null` | Data de emissão registrada. |
| `competencia` | Data ou `null` | Competência fiscal em `AAAA-MM-DD`. |
| `ambiente` | Texto ou `null` | Ambiente fiscal registrado, por exemplo `producao`. |
| `pdf_disponivel`, `xml_disponivel` | Booleanos | Nota com `status=emitida` e XML de retorno armazenado. O download do PDF ainda pode retornar `422` se o XML não puder ser interpretado. |
| `motivo_indisponibilidade` | Texto ou `null` | `nfse_nao_emitida`, `xml_indisponivel` ou `null` quando disponível. |
| `pdf_url` | URL ou `null` | Link direto do PDF com token, para abrir no navegador ou enviar ao cliente. Dispensa `X-API-Key` e login. Nulo quando indisponível. |
| `pdf_api_url` | URL ou `null` | Download autenticado do PDF com a mesma `X-API-Key`. Nulo quando indisponível. |
| `xml_url` | URL ou `null` | Download autenticado do XML com a mesma `X-API-Key`. Nulo quando indisponível. |

### Rotas

| Método e rota | Resposta de sucesso |
| --- | --- |
| `GET /api/v1/integrations/cobrancas/{billing_id}/nfse` | `200`, objeto JSON da nota, sem envelope. |
| `GET /api/v1/integrations/cobrancas/{billing_id}/nfse/pdf` | `200`, bytes `application/pdf`; arquivo `nfse_000073.pdf` para cobrança 73. |
| `GET /api/v1/integrations/cobrancas/{billing_id}/nfse/xml` | `200`, XML fiscal armazenado em UTF-8, `application/xml`; arquivo `nfse_000073.xml`. |
| `GET /api/v1/public/nfse/{billing_id}/{token}` | `200`, PDF para o cliente, sem chave/login e com token válido. Abre no navegador com `Content-Disposition: inline`. |

O PDF é o DANFSE gerado localmente a partir do XML fiscal salvo, usando o mesmo gerador do envio por e-mail. O download do XML entrega `xml_retorno`, sem substituir pelo RPS/DPS de envio nem reformatar o documento. As consultas não emitem notas, não consultam o governo e não alteram o registro fiscal.

Antes de baixar ou enviar o link, reconsultar a nota e verificar os indicadores. Na versão 1.3, `pdf_url` passa a apontar para o PDF com token, que pode ser enviado ao cliente e aberto diretamente no navegador. O campo novo `pdf_api_url` aponta para a rota autenticada existente; `xml_url` também exige a chave. A integradora pode usar essas rotas para baixar anexos no próprio servidor. Nunca incluir a chave de integração na mensagem ou na URL.

### Link do PDF para o cliente

Copiar `nfse.pdf_url` da resposta atualizada. Não montar o token manualmente nem usar a URL `/integrations/cobrancas/{id}/nfse/pdf` como link de navegador: essa rota continua exigindo `X-API-Key`.

O token HMAC-SHA256 usa `SECRET_KEY` e é vinculado à cobrança, ao registro da nota e aos identificadores fiscais (chave, número e série). Um token de boleto, de outra nota ou alterado não abre o documento. Substituir o registro ou mudar os identificadores fiscais invalida o link anterior. Nota não emitida, cancelada/substituída, sem XML ou vinculada a cobrança/cliente removido não é entregue por um link já enviado; retorna `404`.

O link não tem expiração por tempo nesta versão. Alterar `SECRET_KEY` revoga os links anteriores; alterar apenas `INTEGRATION_API_KEY` não revoga os links dos clientes. Compartilhar a URL somente com o destinatário: quem possui o link pode abrir a nota enquanto disponível. O PDF é servido sem cache compartilhado ou armazenamento em cache, com `Referrer-Policy: no-referrer` e orientação de não indexação.

### Erros e situações financeiras

- `404`: cobrança, cliente ou nota inexistente/indisponível por remoção.
- `404` no link para o cliente: token inválido ou documento que deixou de estar disponível.
- `409` no download: nota com status diferente de `emitida` ou XML ausente. `detail.code` identifica o motivo. Notas canceladas/substituídas não são distribuídas por estas rotas.
- `422`: ID inválido ou XML que não permite gerar o PDF.
- `401` e `503`: mesmas regras de chave da integração.

Uma cobrança paga pode ter uma NFS-e emitida disponível mesmo com `boleto_disponivel=false`. Uma cobrança cancelada também pode manter nota emitida: cancelamento financeiro não cancela automaticamente o documento fiscal. A disponibilidade da nota não autoriza um lembrete de cobrança. Se a nota não existe ou ainda não foi emitida, a integração apenas informa essa situação.
