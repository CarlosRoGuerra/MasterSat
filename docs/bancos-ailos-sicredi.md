# Ailos/Viacredi e Sicredi no Financeiro

A equipe escolhe **Gerar boleto (Ailos)** ou **Gerar boleto (Sicredi)** no
detalhe da cobrança. O banco é escolhido por cobrança. Depois do registro,
PDF, e-mail, link do cliente e integração de WhatsApp usam o banco escolhido.
Uma cobrança não pode ter dois títulos ativos em bancos diferentes. Registro
pendente ou sem resposta também impede uma segunda emissão até a consulta.

Os dois bancos usam o mesmo desenho MasterSat: recibo do pagador, QR Pix,
itens por placa, faixa de dados e anexo para o detalhamento que não couber.
No Sicredi, a ficha usa a logomarca original extraída do manual API 4.0,
`748-X`, agência/posto/beneficiário e nosso número `AA/nnnnnn-D`. A ficha
fica nos 100 mm inferiores e o I2of5 mede 103 × 13 mm, centrado a 12 mm do
rodapé (CNAB 400 2.9, pp. 45–46).

A impressão do Sicredi só é liberada depois do registro: código de barras,
linha digitável, Pix, valor e vencimento são os persistidos da emissão.
Baixar, enviar por e-mail ou abrir pelo WhatsApp/link público não cadastra
outro título nem depende do download do PDF padrão do banco. Consulta e
conciliação continuam confirmando o pagamento e a baixa.

## Lotes e carnês

O Financeiro oferece a escolha **Ailos / Sicredi** na emissão dos boletos
selecionados. O carnê do plano, o carnê de cobranças existentes, a central
do cliente e o cadastro de veículo também oferecem Sicredi. A opção fica
disponível quando o canal está configurado; a Ailos permanece como padrão.
No cadastro do cliente e nos filtros existe **Carnê Sicredi**.

Cada parcela corresponde a um título registrado. O Sicredi usa a mesma
emissão individual idempotente em uma fila persistente: reserva os números
antes do envio, acompanha o progresso por parcela e retoma o restante após
reinício do servidor. Uma falha não impede o registro das demais parcelas.
A nova tentativa ignora os títulos confirmados e consulta os registros
incertos antes de reenviar. Reservas na fila também impedem alterações de
valor, recebimento manual e emissão em outro banco.

O carnê usa o mesmo modelo compacto MasterSat, com três parcelas por folha
A4, identificação Sicredi e o Pix oficial de cada parcela quando devolvido
pelo banco. Download e acompanhamento não registram cobranças. Um carnê
incompleto só pode ser baixado pela ação explícita **Baixar parcial**.
O banco acompanha o lote, inclusive nas consultas, tentativas e PDFs;
os IDs de carnês Ailos e Sicredi são independentes.

## Conferência local

O ambiente local usa `SICREDI_AMBIENTE=sandbox`,
`SICREDI_PRODUCAO_CONFIRMADA=false` e `SICREDI_BOLETO_HIBRIDO=true`, com a chave
de sandbox em `C:\mastersat-secrets\.env`, fora do OneDrive. A tela informa
que o Sicredi está em teste. O PDF mantém o aviso de homologação nas
instruções; os códigos do sandbox podem ser exemplos fixos do banco e não
comprovam registro no convênio de produção da MasterSat.

As tarefas automáticas locais ficam desativadas (`RUN_BACKGROUND_JOBS=false`)
durante a conferência. A publicação na VPS e a ativação em produção aguardam
a aprovação do usuário. Chaves e Código de Acesso não pertencem ao Git.

## Nova parametrização Ailos, vigente em 01/11/2026

| Campo | Valor |
|---|---|
| Banco | 085-0 |
| Cooperativa no login | 1 / Viacredi |
| Agência e DV | 0101-5 |
| Convênio | 101004 |
| Conta com DV / código beneficiário | 454702 |
| Conta sem DV | 45470 |
| DV da conta | 2 |
| Carteira / variação | 01 / 001 |
| Beneficiário | MASTERSAT COMERCIO E SERVICO DE RASTREADORES LTDA |
| CNPJ | 14228344000167 |

Zeros à esquerda completam os campos dos arquivos: o nosso número começa por
`00454702`; a conta sem DV ocupa o tamanho definido em cada registro. O
comunicado não informa uma conta diferente de 454702; não se deve inventar
outro número.

API, PDF e CNAB leem a mesma configuração (`AILOS_NUMERO_CONVENIO`,
`AILOS_AGENCIA`, `AILOS_AGENCIA_DV`, `AILOS_CONTA_CORRENTE`, `AILOS_CONTA_DV`,
`AILOS_DEFAULT_CARTEIRA`, `AILOS_VARIACAO_CARTEIRA`). Atualizar somente o código
não substitui valores antigos já definidos no `.env` da VPS. O exemplo de
configuração do repositório contém os novos valores. A autorização do cooperado
deve ser refeita em Viacredi se a sessão anterior não for aceita pelo banco.
O login de teste usa a conta e senha específicas fornecidas para homologação,
separadas da conta de produção acima.

Boletos já registrados preservam os códigos oficiais. A reimpressão reconhece
o convênio 102004 no campo livre e mantém a identificação histórica 0102-3.
Não há reemissão nem alteração automática de títulos antigos.

## CNAB e arquivos para a homologação Ailos

Foram revisados os manuais fornecidos em `api/CNAB 240`, `api/CNAB 400` e os
documentos API da pasta `2.0`. A emissão API continua nos endpoints V2 já usados
pelo sistema. O comunicado exige alteração dos parâmetros, não uma troca de
banco ou do fluxo OAuth da Ailos.

- CNAB 240: posições dos headers, conta e DV separados, nosso número completo
  de 17 dígitos seguido de três brancos, pagador de 15 posições, emissão e
  vencimento, total de 17 posições e trailers. Arquivo 084 e lote 043 seguem
  a tabela do manual versão 14, maio/2025, pp. 15–23. O exemplo `.rem` antigo
  usa lote 045; essa divergência fica documentada para validação pelo banco.
- As instruções particulares do PDF **Dicas importantes para homologação
  CNAB 240** prevalecem nos campos de desconto isento (2), baixa (2), prazo
  225–227 (000), pagador diferente do beneficiário e omissão de R sem multa.
- CNAB 400: posições do manual pp. 16–18, convênio no header e detalhe,
  CPF/CNPJ do beneficiário separado do pagador, nosso número completo,
  vencimento com seis posições e trailer preenchido com brancos.
- Valores negativos, zero no título, centavos excedentes e campos numéricos
  maiores que o layout são recusados. Texto é convertido para ASCII e caixa
  alta para não deslocar campos por caracteres acentuados.

`backend/scripts/ailos_viacredi_homologacao.py` gera remessas 240/400 e PDFs
com os mesmos títulos (faixa 380–390). Exige nome, CPF/CNPJ e endereço do
pagador por parâmetros; não autentica no banco e não envia e-mail.

Exemplo, a partir de `backend/`:

```powershell
python scripts/ailos_viacredi_homologacao.py --pagador-nome "PAGADOR DE TESTE" --pagador-documento <CPF_VALIDO> --endereco "ENDERECO DE TESTE" --bairro CENTRO --cidade JOINVILLE --uf SC --cep 89200000 --saida output/ailos-viacredi-homologacao
```

Os arquivos gerados localmente permitem conferir o layout. A validação do
registro exige importação e aprovação no ambiente de homologação da Ailos.
O canal CNAB no sistema continua condicionado a `CNAB_REMESSA_HABILITADA`:
a conciliação automática atual usa as APIs, não interpreta retorno CNAB.

## Publicação posterior à aprovação

Aplicar as migrations até `b8e4c6d2a907`: `e3b9d5a71c42` cria a integração
Sicredi, `f4c8b2a63d10` adiciona lotes e reservas na fila e `a6e2d9c51b74`
permite a forma de cobrança Carnê Sicredi; `b8e4c6d2a907` completa o vínculo
de lote e a restrição de status em bancos marcados como atualizados com
essa estrutura ausente. Reconstruir backend, worker e
frontend. As migrations têm uma única revisão final. Em produção,
configurar a chave de produção Sicredi, Código de Acesso e convênio
2602/13/11948 no arquivo de segredos; só então usar
`SICREDI_AMBIENTE=producao` e `SICREDI_PRODUCAO_CONFIRMADA=true`. Boleto híbrido
requer `SICREDI_BOLETO_HIBRIDO=true`.

Usar o status e o teste de conexão administrativo antes de qualquer emissão.
Não transportar o título de teste já emitido para outra cobrança, nem criar
novos boletos de produção para validar a interface local.
