![MasterSat - Solução completa em Rastreamento](assets/mastersat-logo.png)

# API de cobranças

## Manual de integração para notificações via WhatsApp

**MasterSat | Documentação para a empresa integradora**

Versão documental 1.0 | 29 de setembro de 2026

Consulta de cobranças, dados de pagamento e acesso ao boleto em PDF.

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

> **Escopo disponível:** três operações de leitura autenticadas e um link público protegido por token. Emissão de boleto, baixa financeira, alteração cadastral e envio pelo WhatsApp não fazem parte deste contrato de integração.

<!-- pagebreak -->

## 02. Acesso e autenticação

### Configuração inicial

| Item | Orientação |
| --- | --- |
| URL base | Domínio HTTPS da API, sem barra final e sem `/api/v1`. No Postman, usar a variável `base_url`. |
| Endereço previsto no projeto | `https://api.mastersat.com.br`, conforme a documentação de implantação. A MasterSat deve confirmar o ambiente disponibilizado ao parceiro. |
| Prefixo das rotas | `/api/v1`, conforme a configuração padrão do projeto. |
| Credencial | Chave de serviço fornecida pela MasterSat, enviada no cabeçalho `X-API-Key`. |
| Respostas | JSON para consultas e `application/pdf` para boletos. |
| Homologação | Usar cobranças e destinatários de teste definidos entre as equipes. |

Todas as três rotas de integração exigem o cabeçalho abaixo. A chave não utiliza o login do painel, não requer Bearer e não possui endpoint de renovação nesta integração.

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
| Documento | Exigir `boleto_registrado = true`, linha digitável e código de barras preenchidos. Conferir a abertura do link público antes de distribuir o boleto. |
| Agenda | Aplicar dias, horários e frequência aprovados pela MasterSat. Não enviar a cada consulta. |
| Duplicidade | Garantir uma única execução para a mesma cobrança, etapa da régua e ocorrência agendada. |

> **Consulta não é confirmação bancária instantânea.** O status reflete o estado registrado no MasterSat. A atualização depende das rotinas financeiras. Reconsultar antes do envio reduz mensagens indevidas, mas não elimina o intervalo entre pagamento, conciliação e disparo.

<!-- pagebreak -->

## 04. Listar cobranças em aberto

```http
GET /api/v1/integrations/cobrancas
X-API-Key: CHAVE_FORNECIDA_PELA_MASTERSAT
```

Retorna cobranças com status `pendente` ou `vencida`, excluindo cobranças e clientes de origem excluídos. A ordenação é por vencimento crescente. A resposta é um objeto com `total` e `cobrancas`.

### Parâmetros de consulta

| Parâmetro | Tipo / padrão | Comportamento |
| --- | --- | --- |
| `forma_envio` | Texto opcional; sem filtro por padrão. | `whatsapp`: inclui cadastro com envio `whatsapp`, `todos` ou opção de boleto por WhatsApp marcada. `email`: inclui `email` ou `todos`. |
| `limit` | Inteiro; padrão `500`. | Mínimo `1`, máximo `2000`. Limita a quantidade de registros retornados. |

```http
GET /api/v1/integrations/cobrancas?forma_envio=whatsapp&limit=500
```

| Campo da resposta | Significado |
| --- | --- |
| `total` | Quantidade de itens **nesta resposta**, depois da aplicação do limite. |
| `cobrancas` | Lista de objetos de cobrança. Pode ser vazia. |

```json
{"total": 0, "cobrancas": []}
```

### Limites que afetam a integração

- Não há `page`, `offset`, cursor, filtro por data, por status ou por alteração recente nesta rota. Não enviar parâmetros não documentados esperando esse comportamento.
- Se `total` for igual ao `limit`, o conjunto pode estar incompleto. Aumentar o limite até `2000`, se necessário. Se o teto for atingido, combinar uma evolução da API com a MasterSat antes de considerar a consulta completa.
- Repetir a mesma consulta não avança para os próximos registros. A ausência de um ID na lista não comprova pagamento nem cancelamento.
- `forma_envio` é sensível ao valor informado: valores diferentes de `whatsapp` e `email` não aplicam filtro. Usar exatamente `whatsapp` para esta integração.

> **Pagador diferente do cliente de origem:** o filtro da lista usa o cadastro do cliente vinculado à cobrança, mas os dados retornados são do responsável financeiro resolvido pelo sistema. Revalidar a preferência retornada e homologar esse cenário; cobranças de um pagador elegível podem não entrar no filtro se o cadastro de origem não estiver habilitado.

<!-- pagebreak -->

## 05. Exemplo de resposta JSON

Exemplo ilustrativo de listagem com um boleto registrado. Os códigos, contatos, domínio e token abaixo são fictícios e não são utilizáveis. As URLs reais são devolvidas completas pela API.

```json
{
  "total": 1,
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
      "forma_envio": "whatsapp",
      "enviar_boleto_whatsapp": true,
      "nosso_numero": "EXEMPLO_NAO_PAGAVEL",
      "linha_digitavel": "LINHA_FICTICIA_NAO_PAGAVEL",
      "codigo_barras": "CODIGO_FICTICIO_NAO_PAGAVEL",
      "pix_copia_cola": null,
      "boleto_registrado": true,
      "boleto_pdf_url": "https://api.exemplo.invalid/api/v1/integrations/cobrancas/73/pdf",
      "boleto_link_cliente": "https://api.exemplo.invalid/api/v1/public/boleto/73/TOKEN_EXEMPLO",
      "valor_com_juros": null
    }
  ]
}
```

### Como interpretar

`id` é o identificador da cobrança no MasterSat. Utilizá-lo na consulta de detalhe e no controle de notificações. `cliente.id` identifica o pagador e não substitui o ID da cobrança.

`boleto_pdf_url` exige a chave de integração. `boleto_link_cliente` foi preparado para ser aberto pelo destinatário sem login. O Pix pode ser `null` mesmo quando o boleto está registrado.

Quando não há registro identificado na integração, a cobrança continua na resposta com `boleto_registrado = false`, dados de pagamento nulos e `boleto_link_cliente = null`. Nesse caso, aguardar regularização pela MasterSat.

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
| `status` | Texto | `pendente`, `vencida`, `paga` ou `cancelada`. A lista inclui somente as duas primeiras situações. |
| `forma_envio` | Texto | Preferência do pagador: `email`, `whatsapp` ou `todos`. Quando o cadastro está vazio, a API retorna `email`. |
| `enviar_boleto_whatsapp` | Booleano | Opção específica de envio por WhatsApp. Pode habilitar o canal mesmo se `forma_envio` for `email`. |

### Quem deve receber a mensagem

O sistema prioriza o responsável financeiro salvo na cobrança. Em registros legados, pode utilizar o interveniente financeiro do contrato; na ausência dele, utiliza o cliente da cobrança. A integradora deve usar o objeto `cliente` retornado, sem substituir o contato pelo proprietário do veículo.

O endpoint entrega o telefone principal. Contatos adicionais, contatos de emergência e e-mails extras não são retornados neste contrato.

<!-- pagebreak -->

## 07. Dicionário: boleto, valores e status

| Campo | Tipo | Descrição e uso |
| --- | --- | --- |
| `nosso_numero` | Texto ou `null` | Identificador bancário para referência. Não substituir `id` por esse campo nas rotas. |
| `linha_digitavel` | Texto ou `null` | Linha de pagamento retornada pelo sistema. Preservar como texto, inclusive zeros iniciais. |
| `codigo_barras` | Texto ou `null` | Representação numérica do código de barras. Não converter para número. |
| `pix_copia_cola` | Texto ou `null` | Código Pix quando disponível. Enviar exatamente como recebido; omitir o bloco Pix quando nulo. |
| `boleto_registrado` | Booleano | Indica que existe registro local Ailos com linha digitável. Não representa consulta bancária em tempo real. |
| `boleto_pdf_url` | Texto / URL | Endpoint de download autenticado para a integradora. É retornado mesmo quando o registro do boleto ainda não foi identificado. |
| `boleto_link_cliente` | Texto / URL ou `null` | Link do PDF com token para o cliente. Nulo quando `boleto_registrado` é falso. |
| `valor_com_juros` | Número ou `null` | Valor atualizado calculado pelo backend quando o status é `vencida` e há atraso. Não recalcular encargos na integradora. |

### Interpretação da situação financeira

| Status | Ação |
| --- | --- |
| `pendente` | Considerar para lembrete conforme vencimento e régua acordada. |
| `vencida` | Considerar para aviso de atraso. Se houver valor atualizado, identificá-lo como tal na mensagem. |
| `paga` | Suspender notificações de cobrança e retirar da fila. |
| `cancelada` | Suspender notificações de cobrança e retirar da fila. |

### Particularidades da versão atual

O backend reclassifica vencimentos em rotina periódica, prevista a cada hora. O status e a data podem apresentar diferença temporária. A rotina financeira usa a data de Brasília (UTC-03:00 na implementação analisada).

O indicador `boleto_registrado` verifica a linha digitável, enquanto o link público exige linha digitável **e** código de barras oficiais. Uma falha ao montar os dados também pode manter campos nulos com o indicador verdadeiro. Confirmar a disponibilidade do link público antes de distribuir o documento.

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

> **Verificação obrigatória no consumidor:** a rota de PDF autenticado não bloqueia automaticamente títulos pagos, cancelados ou sem registro bancário. Ela pode gerar um PDF local. Revalidar o detalhe, os critérios da seção 03 e a disponibilidade do link público antes de enviar qualquer documento ao cliente.

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

<!-- pagebreak -->

## 12. Automação e modelo de mensagem

### Lógica de referência

Este fluxo é pseudocódigo para implementação pela integradora; não representa um serviço adicional já disponível no MasterSat.

```text
a cada ciclo acordado:
  lote = consultar_cobrancas(forma_envio="whatsapp", limit=500)
  se lote.total == 500:
    sinalizar_possivel_truncamento()
  para cada candidato em lote.cobrancas:
    se fora_da_regua(candidato): continuar
    chave = (candidato.id, etapa_regua, ocorrencia_agendada)
    se nao_reservar_atomicamente(chave): continuar
    atual = consultar_detalhe(candidato.id)
    se status_nao_aberto(atual): suspender(chave); continuar
    se canal_ou_telefone_invalido(atual): suspender(chave); continuar
    se boleto_incompleto(atual): suspender(chave); continuar
    se link_publico_indisponivel(atual): suspender(chave); continuar
    se fora_da_regua(atual): liberar(chave); continuar
    resultado = enviar_mensagem_com_dados_atualizados(atual)
    registrar_resultado(chave, resultado)
```

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
| Volume no limite | Detectar possível truncamento; não assumir cobertura completa da carteira. |
| Falhas HTTP e indisponibilidade | Interromper/reagendar conforme a seção 10, sem enviar com verificação incompleta. |

### Informações para início da operação

A MasterSat e a integradora devem registrar: nome e contato dos responsáveis técnicos, URL de homologação/produção, entrega segura da chave, IDs e telefones de teste, régua de cobrança, volume esperado e canal para incidentes. A ativação deve ocorrer após a validação conjunta desses itens.

Ao relatar um problema, informar data/hora com fuso, método, rota, ID da cobrança, status HTTP e mensagem de erro sanitizada. Nunca enviar a chave, o token do link ou dados pessoais desnecessários no chamado.

### Base técnica e versão

Documento conferido contra o código do projeto na revisão `28af706`, em 29/09/2026. Referências principais: `backend/app/api/v1/endpoints/integrations.py`, `backend/app/api/v1/endpoints/boletos.py`, `backend/app/api/deps.py` e `backend/tests/test_integrations_api.py`.

Os 14 testes existentes de integração passaram em ambiente local isolado. Essa verificação não certifica a configuração do servidor publicado, a entrega pelo WhatsApp ou a homologação bancária. As limitações e particularidades descritas correspondem à implementação consultada.

**Arquivos entregues:** manual em PDF, fonte editável em Markdown, logo e coleção Postman sem credenciais. Versões futuras da API devem motivar revisão deste documento.
