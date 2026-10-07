# Comandos da integração de cobranças — versão 1.1

Estes comandos consultam a API MasterSat. As alterações precisam estar publicadas no backend para os novos filtros e a paginação funcionarem. A URL usada abaixo é `https://api.mastersat.com.br`; para homologação, substituir pelo domínio informado pela MasterSat. A chave é a mesma chave de integração enviada no cabeçalho `X-API-Key`.

As datas dos exemplos são substituíveis e seguem `AAAA-MM-DD`. `paga` é o status do pagamento confirmado registrado no MasterSat. Não usar `confirmado` ou `pagamento_confirmado` como valor do parâmetro `status`.

## PowerShell — comandos para copiar e executar

### 1. Configurar o endereço e informar a chave

```powershell
$apiBase = 'https://api.mastersat.com.br/api/v1'
$credencialIntegracao = [System.Management.Automation.PSCredential]::new(
    'api', (Read-Host 'Cole a chave X-API-Key' -AsSecureString)
)
$cabecalhos = @{
    'X-API-Key' = $credencialIntegracao.GetNetworkCredential().Password
    'Accept' = 'application/json'
}
```

### 2. Consultar pendentes com vencimento em uma data

```powershell
$resposta = Invoke-RestMethod -Method Get -Headers $cabecalhos -Uri "$apiBase/integrations/cobrancas?forma_envio=whatsapp&status=pendente&vencimento=2026-10-10&limit=500&offset=0"
$resposta.cobrancas | ConvertTo-Json -Depth 10
```

### 3. Consultar pendentes com vencimento em um intervalo

```powershell
$resposta = Invoke-RestMethod -Method Get -Headers $cabecalhos -Uri "$apiBase/integrations/cobrancas?forma_envio=whatsapp&status=pendente&vencimento_de=2026-10-07&vencimento_ate=2026-10-14&limit=500&offset=0"
$resposta.cobrancas | ConvertTo-Json -Depth 10
```

### 4. Consultar vencidas

Todas as vencidas elegíveis para WhatsApp:

```powershell
$resposta = Invoke-RestMethod -Method Get -Headers $cabecalhos -Uri "$apiBase/integrations/cobrancas?forma_envio=whatsapp&status=vencida&limit=500&offset=0"
$resposta.cobrancas | ConvertTo-Json -Depth 10
```

Somente vencidas cujo vencimento está entre 01/09/2026 e 30/09/2026:

```powershell
$resposta = Invoke-RestMethod -Method Get -Headers $cabecalhos -Uri "$apiBase/integrations/cobrancas?forma_envio=whatsapp&status=vencida&vencimento_de=2026-09-01&vencimento_ate=2026-09-30&limit=500&offset=0"
$resposta.cobrancas | ConvertTo-Json -Depth 10
```

### 5. Consultar pagamentos confirmados

Todos os pagamentos registrados, sem restringir canal ou data:

```powershell
$resposta = Invoke-RestMethod -Method Get -Headers $cabecalhos -Uri "$apiBase/integrations/cobrancas?status=paga&limit=500&offset=0"
$resposta.cobrancas | ConvertTo-Json -Depth 10
```

Pagamentos entre 01/10/2026 e 07/10/2026, para clientes elegíveis ao WhatsApp:

```powershell
$resposta = Invoke-RestMethod -Method Get -Headers $cabecalhos -Uri "$apiBase/integrations/cobrancas?forma_envio=whatsapp&status=paga&pagamento_de=2026-10-01&pagamento_ate=2026-10-07&limit=500&offset=0"
$resposta.cobrancas | Select-Object id,status,pagamento_confirmado,data_pagamento,valor_pago,forma_pagamento
```

`pagamento_confirmado` será `true`. `data_pagamento`, `valor_pago` e `forma_pagamento` podem ser nulos em históricos legados. Registros sem data de pagamento não entram em um filtro de `pagamento_de`/`pagamento_ate`; consultá-los por `status=paga` sem esse intervalo.

### 6. Percorrer todas as páginas automaticamente

O exemplo consulta vencidas. Para outra consulta, substituir `$filtros` mantendo o mesmo valor durante toda a paginação.

```powershell
$filtros = 'forma_envio=whatsapp&status=vencida'
$offset = 0
$limite = 500
$idsVistos = [System.Collections.Generic.HashSet[int]]::new()

do {
    $pagina = Invoke-RestMethod -Method Get -Headers $cabecalhos -Uri "$apiBase/integrations/cobrancas?${filtros}&limit=$limite&offset=$offset"

    foreach ($cobranca in $pagina.cobrancas) {
        if ($idsVistos.Add([int]$cobranca.id)) {
            $cobranca | ConvertTo-Json -Depth 10 -Compress
        }
    }

    if ($pagina.has_more) {
        if ($null -eq $pagina.next_offset -or [int]$pagina.next_offset -le $offset) {
            throw 'A API não informou uma próxima página válida.'
        }
        $offset = [int]$pagina.next_offset
    }
} while ($pagina.has_more)
```

O limite de 2000 é **por página**. `total` conta os itens da página atual; `total_registros` conta os itens que atendem aos filtros. Usar `next_offset` enquanto `has_more` for `true`. A paginação por offset não congela o banco entre requisições: guardar os IDs e revalidar o detalhe antes de notificar.

### 7. Consultar o detalhe de uma cobrança

Informar um ID retornado pela listagem:

```powershell
$billingId = [int](Read-Host 'ID da cobrança retornado pela API')
$cobrancaAtual = Invoke-RestMethod -Method Get -Headers $cabecalhos -Uri "$apiBase/integrations/cobrancas/$billingId"
$cobrancaAtual | ConvertTo-Json -Depth 10
```

### 8. Baixar o boleto em PDF

Após o comando anterior, exigir cobrança aberta e `boleto_disponivel = true`:

```powershell
if ($cobrancaAtual.status -notin @('pendente', 'vencida') -or -not $cobrancaAtual.boleto_disponivel) {
    throw "Boleto indisponível: $($cobrancaAtual.motivo_boleto_indisponivel)"
}

Invoke-WebRequest -UseBasicParsing -Headers $cabecalhos -Uri "$apiBase/integrations/cobrancas/$billingId/pdf" -OutFile "boleto_$billingId.pdf"
```

O PDF autenticado exige `X-API-Key`. Na mensagem ao cliente, utilizar o campo `boleto_link_cliente` devolvido pela API, que dispensa essa chave. `409` significa boleto indisponível e informa o motivo em `detail.code`.

## cURL — Linux, macOS ou Git Bash

### Configuração

```bash
export MASTERSAT_BASE_URL='https://api.mastersat.com.br/api/v1'
read -rsp 'Cole a chave X-API-Key: ' MASTERSAT_API_KEY
printf '\n'
export MASTERSAT_API_KEY
```

### Pendente com vencimento em uma data

```bash
curl --fail-with-body --silent --show-error \
  --header "X-API-Key: $MASTERSAT_API_KEY" \
  "$MASTERSAT_BASE_URL/integrations/cobrancas?forma_envio=whatsapp&status=pendente&vencimento=2026-10-10&limit=500&offset=0"
```

### Vencidas em um intervalo

```bash
curl --fail-with-body --silent --show-error \
  --header "X-API-Key: $MASTERSAT_API_KEY" \
  "$MASTERSAT_BASE_URL/integrations/cobrancas?forma_envio=whatsapp&status=vencida&vencimento_de=2026-09-01&vencimento_ate=2026-09-30&limit=500&offset=0"
```

### Pagamentos confirmados em um intervalo

```bash
curl --fail-with-body --silent --show-error \
  --header "X-API-Key: $MASTERSAT_API_KEY" \
  "$MASTERSAT_BASE_URL/integrations/cobrancas?forma_envio=whatsapp&status=paga&pagamento_de=2026-10-01&pagamento_ate=2026-10-07&limit=500&offset=0"
```

### Todas as situações, sem restrição de canal

```bash
curl --fail-with-body --silent --show-error \
  --header "X-API-Key: $MASTERSAT_API_KEY" \
  "$MASTERSAT_BASE_URL/integrations/cobrancas?status=todos&limit=500&offset=0"
```

### Próxima página

Depois da primeira consulta de vencidas, se `has_more = true`, copiar `next_offset` para `OFFSET`. Os demais filtros e o limite permanecem iguais.

```bash
read -rp 'Valor de next_offset retornado: ' OFFSET
curl --fail-with-body --silent --show-error \
  --header "X-API-Key: $MASTERSAT_API_KEY" \
  "$MASTERSAT_BASE_URL/integrations/cobrancas?forma_envio=whatsapp&status=vencida&vencimento_de=2026-09-01&vencimento_ate=2026-09-30&limit=500&offset=$OFFSET"
```

### Detalhe e PDF

```bash
read -rp 'ID da cobrança retornado pela API: ' BILLING_ID
curl --fail-with-body --silent --show-error \
  --header "X-API-Key: $MASTERSAT_API_KEY" \
  "$MASTERSAT_BASE_URL/integrations/cobrancas/$BILLING_ID"
```

Se o detalhe ainda apresenta `status=pendente|vencida` e `boleto_disponivel=true`:

```bash
curl --fail --silent --show-error \
  --header "X-API-Key: $MASTERSAT_API_KEY" \
  "$MASTERSAT_BASE_URL/integrations/cobrancas/$BILLING_ID/pdf" \
  --output "boleto_$BILLING_ID.pdf"
```

## Postman

Importar `mastersat-cobrancas.postman_collection.json`. Preencher `base_url=https://api.mastersat.com.br` (**sem `/api/v1`** na coleção), `api_key` e as datas de consulta. Existem exemplos para pendentes por vencimento, vencidas em intervalo, pagamentos confirmados e todas as situações. Atualizar `offset` com `next_offset` para avançar.

Datas inicial e final são inclusivas. `422` indica parâmetro inválido ou intervalo invertido; `401`, chave ausente/incorreta; `503`, integração sem chave configurada no backend ou indisponibilidade. Pagamentos confirmados devem encerrar os lembretes de cobrança; a mensagem de confirmação, quando utilizada, precisa de controle de duplicidade da integradora.
