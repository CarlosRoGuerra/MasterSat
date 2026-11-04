# Sicredi — API de Cobrança: homologação e operação

Integração com a API de Cobrança v1 do Sicredi (OAuth2 `password`). O banco é
escolhido **a cada emissão**: no Financeiro, uma cobrança sem boleto mostra
"Gerar boleto (Ailos)" e "Gerar boleto (Sicredi)". Uma cobrança nunca tem
boleto ativo nos dois bancos — a política única (`titulo_bancario`) bloqueia.

Impressão operacional: modelo MasterSat compartilhado com a Ailos, conforme
pedido de design de 03/11/2026. Mantém os códigos e dados registrados na API,
a identificação própria Sicredi e o Pix devolvido pelo banco. O capítulo 9
do manual API 4.0 permite a impressão pelo beneficiário. A logomarca colorida
foi extraída desse manual, sem redesenho; o arquivo preto e branco continua
disponível para o roteiro de homologação.

Decisões (30/10/2026): sem juros, multa,
desconto, protesto ou negativação (igual à Ailos); boleto híbrido (Pix) assim
que a cooperativa habilitar a modalidade.

## Regras do Sicredi aplicadas (documentos fornecidos na pasta `sicred`)

| Regra | Onde |
|---|---|
| Nosso número `AABnnnnnD`, DV módulo 11 (confere com "Calculo DVs - Nosso Número.xlsx": 242077510) | `services/sicredi_nosso_numero.py` |
| Só caracteres da planilha "Caracteres válidos": ASCII 32–125 sem `" ' ^` e crase; acento removido | `texto_sicredi` em `services/sicredi_boletos.py` |
| Nome, endereço, cidade até 40; `seuNumero` até 10; mensagens até 80 | idem |
| Híbrido nunca leva `dataInicioJuros`/`dataInicioMulta` (o Sicredi converteria para boleto comum sem avisar) | idem |
| Liquidação só é aplicada depois da consulta individual do título | `conciliar` / `consultar` |

O nosso número é gerado aqui e reservado no banco de dados **antes** do envio.
Se a resposta não chegar (timeout), a consulta pelo nosso número diz se o
título existe; uma nova tentativa reenvia o mesmo número, que o Sicredi recusa
se já estiver cadastrado. A cobrança fica bloqueada enquanto isso não se resolve.

## 1. Homologação no sandbox

1. No Portal do Desenvolvedor (https://sicredi.sensedia-devportal.com/pt/),
   crie uma aplicação "API Cobrança 2602 11948 SandBox" com a **API de
   Cobrança** e a **API de Autenticação de Parceiros**, e abra o chamado de
   liberação do token em **Suporte > Abrir chamado**. A x-api-key de sandbox
   aparece em Minhas Apps > (app) > Ver detalhes. A chave deve corresponder
   ao ambiente utilizado (manual 4.0, pp. 11 e 13).
2. Rode o roteiro (a partir de `backend/`):

   ```bash
   SICREDI_API_KEY=<x-api-key do SANDBOX> python scripts/sicredi_homologacao.py --no-impressao-local
   SICREDI_API_KEY=<x-api-key do SANDBOX> python scripts/sicredi_homologacao.py --no-impressao-local --hibrido --alterar-vencimento
   ```

   Ele autentica, cadastra, consulta, baixa o PDF e testa liquidados,
   (opcional) altera o vencimento e dá baixa. A consulta de duplicidade
   `/boletos/cadastrados` é exclusiva de produção e não é chamada no sandbox
   (manual 4.0, pp. 173–177). Gera
   `relatorio.json` (log mascarado) e `boleto.pdf`. Nas chamadas realizadas
   em 03/11/2026, o endpoint sandbox devolveu o mesmo PDF com marca MOCK
   mesmo após o cadastro com outro CPF. Isso é uma **observação dos testes**;
   as páginas 51–54 do manual 4.0 documentam a geração do PDF, mas não
   dizem que ele obrigatoriamente será um modelo fixo. O CPF do exemplo de cadastro do manual
   (`02738306006`, p. 45) possui DV inválido e provoca HTTP 400 "Documento
   inválido". O roteiro usa um CPF de teste válido, exclusivamente no sandbox,
   e valida o documento informado antes de qualquer chamada.

   O roteiro só salva `boleto.pdf` se receber conteúdo PDF. Falhas em etapas
   executadas ficam no relatório e resultam em código de saída 1.

## 2. Boleto de homologação impresso pelo beneficiário

O **capítulo 9 do manual API 4.0, páginas 218–219**, permite que o próprio
beneficiário imprima o boleto conforme o layout fornecido. O roteiro agora
oferece `--impressao-local`: gera um documento com os dados do convênio e do
pagador, marcado **HOMOLOGAÇÃO — SEM VALIDADE FINANCEIRA**, sem autenticar,
registrar cobrança ou comandar baixa na API. Esse arquivo permite conferir
a impressão; não deve ser apresentado como boleto registrado pela API.

A regra do nosso número segue as páginas 216–218 do manual API. O código
de barras, seus DVs e a linha digitável seguem o **Manual CNAB 400 2.9,
páginas 43–49**, também fornecido, incluindo o reinício do fator em
22/02/2025. O código I2of5 mede 103 × 13 mm, como especificado na página 45.
A ficha fica nos 100 mm inferiores da página A4 e tem 200 mm de largura
(p. 46). A impressão usa o logo preto e branco fornecido, os campos da
página 41 e Arial negrito incorporada para `748-X`. No Windows, a fonte é
lida de `C:/Windows/Fonts/arialbd.ttf`; em outro ambiente, configure
`SICREDI_HOMOLOGACAO_FONTE_ARIAL_NEGRITO` com um arquivo Arial negrito licenciado.

No `backend/.env` local (ignorado pelo Git):

```dotenv
SICREDI_HOMOLOGACAO_IMPRESSAO_LOCAL=true
SICREDI_BENEFICIARIO_NOME=MASTERSAT COMERCIO E SERVICO DE RASTREADORES LTDA
SICREDI_BENEFICIARIO_CNPJ=14228344000167
SICREDI_COOPERATIVA=2602
SICREDI_POSTO=13
SICREDI_CODIGO_BENEFICIARIO=11948
SICREDI_CONTA_CORRENTE=20886-5
SICREDI_HOMOLOGACAO_PAGADOR_NOME=<nome completo>
SICREDI_HOMOLOGACAO_PAGADOR_DOCUMENTO=<CPF ou CNPJ>
SICREDI_HOMOLOGACAO_PAGADOR_ENDERECO=<logradouro, número e bairro>
SICREDI_HOMOLOGACAO_PAGADOR_CIDADE=<cidade>
SICREDI_HOMOLOGACAO_PAGADOR_UF=<UF>
SICREDI_HOMOLOGACAO_PAGADOR_CEP=<oito dígitos>
# Somente se o endereço informado for fictício:
SICREDI_HOMOLOGACAO_ENDERECO_FICTICIO=true
# Opcional: demonstração do layout com QR Code sem cobrança vinculada:
SICREDI_HOMOLOGACAO_PIX_DEMONSTRATIVO=true
```

Com o perfil preenchido, a partir de `backend/`:

```powershell
python scripts/sicredi_homologacao.py
```

O modo `--impressao-local --pix-demonstrativo` inclui QR Code vetorial e
Pix Copia e Cola com a mesma string EMV, CRC16-CCITT e valor do boleto.
A location aleatória usa o domínio de homologação documentado na página 39,
mas **não é cadastrada no Sicredi**. O documento identifica o Pix como
demonstrativo, sem cobrança vinculada. Serve à conferência do layout e da
leitura; não comprova o registro ou a homologação bancária do Pix.
`--no-pix-demonstrativo` imprime sem esse QR.

Para um híbrido efetivo, a página 39 do CNAB 400 exige os dados do retorno
tipo 8: TXID (21–55), location (57–133) e Pix Copia e Cola (135–390).
Os exemplos `.CRM` fornecidos são remessas e não contêm esse retorno.
Pela API, use `--no-impressao-local --hibrido` para obter os dados devolvidos
pelo banco. A flag demonstrativa não é aceita nas chamadas à API.

Para testar a API sandbox e baixar o arquivo devolvido pelo banco:

```powershell
python scripts/sicredi_homologacao.py --no-impressao-local
```

O `Manual Portal de Testes.pdf` trata da homologação de **arquivos CNAB**.
Sua página 3 pede cinco registros e um PDF com os mesmos cinco boletos
quando a emissão é pelo beneficiário. O roteiro acima gera um boleto para
conferência, sem criar remessa ou enviar arquivos ao portal.

## 3. Teste opcional em produção (boleto real, valor baixo)

O convênio informado para a MasterSat é:

| Dado | Valor |
|---|---|
| CNPJ do beneficiário | 14.228.344/0001-67 |
| Banco | 748 — Sicredi |
| Agência/cooperativa | 2602 (DV 6) |
| Posto/UA | 13 |
| Beneficiário | 11948 |
| Conta corrente | 20886-5 |
| Username da API | 119482602 |

Na API, o username é beneficiário + cooperativa, sem DV. A autenticação
também exige o **Código de Acesso** gerado no Internet Banking, além da
`x-api-key` do ambiente. A API key não substitui esse código nem o JWT
`access_token` devolvido pela autenticação. Conta e CNPJ identificam o
beneficiário; não devem substituir o código de beneficiário ou o documento
do pagador no cadastro do boleto. O PDF oficial usa o cadastro do convênio
no Sicredi. Em produção, nome e CPF/CNPJ do pagador devem ser informados
explicitamente; o roteiro não usa os dados fictícios do sandbox.

Use um pagador seu (CPF/CNPJ de um sócio) e valor baixo. O roteiro usa o byte
9 do nosso número, separado da sequência do sistema (byte 2).

```bash
SICREDI_AMBIENTE=producao SICREDI_PRODUCAO_CONFIRMADA=true \
SICREDI_API_KEY=<x-api-key de produção> SICREDI_CODIGO_ACESSO=<código de acesso> \
SICREDI_COOPERATIVA=2602 SICREDI_POSTO=13 SICREDI_CODIGO_BENEFICIARIO=11948 \
python scripts/sicredi_homologacao.py --no-impressao-local --producao --confirmo-boleto-real \
    --valor 10.00 --pagador-documento <CPF> --pagador-nome "NOME DO PAGADOR"
```

Sem `--baixar-no-fim` o boleto continua ativo (dá para pagar e ver a baixa
automática). Depois, responda à cooperativa (Angelita) informando que a API
está implementada, com o nosso número do boleto de teste.

## 4. Ligar no sistema (VPS)

No `.env` da VPS (nunca no repositório):

```
SICREDI_AMBIENTE=producao
SICREDI_PRODUCAO_CONFIRMADA=true
SICREDI_API_KEY=<x-api-key de produção>
SICREDI_CODIGO_ACESSO=<código de acesso>
SICREDI_COOPERATIVA=2602
SICREDI_POSTO=13
SICREDI_CODIGO_BENEFICIARIO=11948
# depois que a cooperativa habilitar a modalidade híbrida:
SICREDI_BOLETO_HIBRIDO=true
```

Rebuild de `backend`, `backend-worker` e `frontend`, aplicando as migrations
até `b8e4c6d2a907` (integração, fila de lotes, forma Carnê Sicredi e
conferência do vínculo de lote).
Teste em Configurações →
`POST /api/v1/sicredi/testar-conexao` (admin): só autentica.

## 5. Operação

| Ação | Onde | O que faz |
|---|---|---|
| Gerar boleto (Sicredi) | Financeiro → cobrança | Registra pela API e imprime no modelo MasterSat |
| Emitir selecionados | Financeiro → ações em lote | Escolhe Ailos ou Sicredi e informa o resultado por cobrança |
| Gerar carnê (Sicredi) | Financeiro, central do cliente ou cadastro do veículo | Registra as parcelas e acompanha o progresso por lote |
| PDF do boleto (Sicredi) | idem | Mesmo layout Ailos, com identificação e códigos oficiais Sicredi |
| Consultar no Sicredi | idem | Quita se pago, confirma baixa, resolve registro incerto |
| Dar baixa no Sicredi | idem (registrado ou com baixa pendente) | Instrução de baixa pela API; confirmação chega pela consulta |
| Encerrar pendência | idem | Fecha divergência de conciliação (fica no histórico) |
| Link público, e-mail, WhatsApp | como na Ailos | Entregam o mesmo PDF MasterSat do Financeiro |
| Conciliação automática | worker, de hora em hora | Liquidados de hoje e ontem + rodízio dos ativos |

Cancelar uma cobrança com boleto Sicredi pede confirmação e deixa a baixa
**pendente**: em seguida use "Dar baixa no Sicredi".

O carnê Sicredi reserva cada nosso número antes do envio. A fila é durável
e o worker recupera lotes interrompidos. Uma falha parcial mantém o que já
foi registrado; as ações por parcela e de pendentes consultam títulos
incertos antes de permitir outro POST. O mesmo conjunto de cobranças e
convênio reutiliza o lote. Não se misturam responsáveis diferentes em um
carnê, nem títulos ativos de outro banco. O cadastro **Carnê Sicredi**
identifica a forma de cobrança, mas não emite parcelas automaticamente.

Rotas (admin/financeiro):

- `POST /api/v1/sicredi/carne/lote` e `/boletos/lote`: `billing_ids`.
- `GET /api/v1/sicredi/lotes/{ticket}`: progresso e resultado por parcela,
  sem emissão; `partial` aguarda ação manual.
- `POST /api/v1/sicredi/lotes/{id}/registrar-pendentes` e
  `/parcelas/{billing_id}/registrar`: retomada segura.
- `GET /api/v1/boletos/carne`: carteira unificada, com campo `banco`.
- `GET /api/v1/boletos/carne/{id}/pdf?banco=sicredi`: três parcelas por folha,
  códigos e Pix oficiais. Se houver parcelas indisponíveis, somente a ação
  explícita com `&parcial=true` permite imprimir as disponíveis.

## 6. Ainda não coberto (próxima etapa)

- Modal "Pendências bancárias" do Financeiro lista só as da Ailos (as do
  Sicredi aparecem no detalhe da cobrança).
- Alterar vencimento pela API (hoje "Corrigir vencimento" cria cobrança nova e
  deixa a baixa pendente, como na Ailos).
- Webhook de liquidação (só existe em produção; a conciliação por consulta já cobre).
