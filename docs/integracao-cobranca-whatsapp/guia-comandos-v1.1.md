# Comandos da integração de cobranças e NFS-e — versão 1.4

Estes comandos consultam a API MasterSat. As alterações precisam estar publicadas no backend para os filtros, a paginação e as rotas de NFS-e funcionarem. A URL usada abaixo é `https://api.mastersat.com.br`; para homologação, substituir pelo domínio informado pela MasterSat. A chave é a mesma chave de integração enviada no cabeçalho `X-API-Key`. O nome deste arquivo foi preservado para manter os links compartilhados.

As datas dos exemplos são substituíveis e seguem `AAAA-MM-DD`. `paga` é o status do pagamento confirmado registrado no MasterSat. Não usar `confirmado` ou `pagamento_confirmado` como valor do parâmetro `status`.

## Atualizar o backend na VPS

Na VPS com o projeto e o ambiente de produção já configurados em `~/MasterSat`:

```bash
cd ~/MasterSat &&
git fetch origin &&
git switch fix/rastreador-dia-vencimento &&
git pull --ff-only origin fix/rastreador-dia-vencimento &&
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --no-deps backend &&
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec nginx nginx -s reload
```

Depois da atualização, `nfse.pdf_url` passa a começar com `https://api.mastersat.com.br/api/v1/public/nfse/`, seguido do ID e token. Esse é o link que abre diretamente no navegador. `nfse.pdf_api_url` mantém o download com chave. Se `pdf_url` ainda contém `/integrations/cobrancas/`, o backend acessado ainda está na versão anterior. `nfse: null` indica que a cobrança consultada não tem nota vinculada.

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
$resposta.cobrancas | Select-Object id,nosso_numero,status,pagamento_confirmado,data_pagamento,valor_pago,forma_pagamento
```

`pagamento_confirmado` será `true`. `data_pagamento`, `valor_pago` e `forma_pagamento` podem ser nulos em históricos legados. Registros sem data de pagamento não entram em um filtro de `pagamento_de`/`pagamento_ate`; consultá-los por `status=paga` sem esse intervalo.

`nosso_numero` é a referência oficial do título bancário, mantida mesmo após pagamento/baixa quando armazenada. Preservar como texto, inclusive os zeros iniciais. O boleto pago continua com `boleto_disponivel=false` e códigos/links de pagamento nulos. Se não existe referência oficial no registro Ailos, `nosso_numero` permanece `null`.

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

### 9. Consultar a NFS-e e baixar PDF/XML

Usar o `$billingId` informado no passo 7. A listagem e o detalhe já retornam o objeto `nfse`; `null` significa ausência de nota. Para incluir notas de cobranças pagas na listagem, usar `status=paga` ou `status=todos`.

```powershell
$notaFiscal = Invoke-RestMethod -Method Get -Headers $cabecalhos -Uri "$apiBase/integrations/cobrancas/$billingId/nfse"
$notaFiscal | ConvertTo-Json -Depth 10

if (-not $notaFiscal.pdf_disponivel -or -not $notaFiscal.xml_disponivel) {
    throw "NFS-e indisponível: $($notaFiscal.motivo_indisponibilidade)"
}

Invoke-WebRequest -UseBasicParsing -Headers $cabecalhos -Uri "$apiBase/integrations/cobrancas/$billingId/nfse/pdf" -OutFile "nfse_$billingId.pdf"
Invoke-WebRequest -UseBasicParsing -Headers $cabecalhos -Uri "$apiBase/integrations/cobrancas/$billingId/nfse/xml" -OutFile "nfse_$billingId.xml"
```

Para obter o link que abre no navegador e pode ser enviado ao cliente:

```powershell
$notaFiscal.pdf_url
```

Esse link também permite baixar o PDF sem cabeçalhos de autenticação:

```powershell
Invoke-WebRequest -UseBasicParsing -Uri $notaFiscal.pdf_url -OutFile "nfse_$billingId.pdf"
```

O PDF é o DANFSE gerado do XML fiscal salvo. As rotas consultam documentos já emitidos; não emitem nem atualizam a nota. A NFS-e de uma cobrança paga permanece disponível quando a situação fiscal é `emitida` e existe XML. `pdf_url` é o link para o cliente, protegido por token. `pdf_api_url` e `xml_url` exigem a chave para baixar os anexos no servidor da integradora.

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

### NFS-e: dados, PDF e XML

Após informar `BILLING_ID` no comando de detalhe acima:

```bash
curl --fail-with-body --silent --show-error \
  --header "X-API-Key: $MASTERSAT_API_KEY" \
  "$MASTERSAT_BASE_URL/integrations/cobrancas/$BILLING_ID/nfse"
```

Se `pdf_disponivel` e `xml_disponivel` forem `true`, baixar os documentos:

```bash
curl --fail --silent --show-error \
  --header "X-API-Key: $MASTERSAT_API_KEY" \
  "$MASTERSAT_BASE_URL/integrations/cobrancas/$BILLING_ID/nfse/pdf" \
  --output "nfse_$BILLING_ID.pdf"

curl --fail --silent --show-error \
  --header "X-API-Key: $MASTERSAT_API_KEY" \
  "$MASTERSAT_BASE_URL/integrations/cobrancas/$BILLING_ID/nfse/xml" \
  --output "nfse_$BILLING_ID.xml"
```

`404`: sem nota ou cobrança/cliente removido. `409`: nota ainda não emitida ou XML ausente; verificar `detail.code`. `422` no PDF: XML inválido para renderização. Os filtros da lista continuam sendo por vencimento/pagamento da cobrança, não por emissão/competência fiscal.

### Link direto do PDF da NFS-e para abrir no navegador

Com `MASTERSAT_API_KEY` configurada acima, o comando imprime o link para a cobrança de exemplo `38257`:

```bash
curl --fail-with-body --silent --show-error \
  --header "X-API-Key: $MASTERSAT_API_KEY" \
  "$MASTERSAT_BASE_URL/integrations/cobrancas/38257/nfse" \
| python3 -c 'import json,sys; nota=json.load(sys.stdin); print(nota.get("pdf_url") or "NFS-e indisponível")'
```

Copiar a URL retornada e abrir no navegador. Ela contém `/api/v1/public/nfse/38257/` e um token; não exige chave nem login. Pode ser enviada por WhatsApp. Usar a URL completa retornada pela API. URLs antigas com `/integrations/cobrancas/38257/nfse/pdf` continuam protegidas por chave; consultar novamente para obter o novo link. O token inválido, nota cancelada/substituída, XML ausente ou remoção da cobrança/cliente retorna `404`.

## Postman

Importar `mastersat-cobrancas.postman_collection.json`. Preencher `base_url=https://api.mastersat.com.br` (**sem `/api/v1`** na coleção), `api_key` e as datas de consulta. Existem exemplos para pendentes por vencimento, vencidas em intervalo, pagamentos confirmados, todas as situações e dados/PDF/XML da NFS-e. Atualizar `offset` com `next_offset` para avançar. Para PDFs/XML, usar a opção de salvar a resposta em arquivo.

Datas inicial e final são inclusivas. `422` indica parâmetro inválido ou intervalo invertido; `401`, chave ausente/incorreta; `503`, integração sem chave configurada no backend ou indisponibilidade. Pagamentos confirmados devem encerrar os lembretes de cobrança; a mensagem de confirmação, quando utilizada, precisa de controle de duplicidade da integradora.
