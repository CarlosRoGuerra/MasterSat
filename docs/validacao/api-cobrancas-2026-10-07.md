# Ajustes da API de cobranças — 07/10/2026

Os dois áudios e o vídeo enviados pela integradora solicitam filtros por status e vencimento, paginação e acesso aos pagamentos confirmados. O vídeo também mostra campos de pagamento nulos em títulos sem registro bancário. A análise dos anexos foi realizada localmente, sem enviar as gravações a serviços externos.

## Comportamento entregue

- `GET /api/v1/integrations/cobrancas` aceita `status=pendente|vencida|paga|cancelada|todos`. Sem o parâmetro, conserva a seleção de pendentes/vencidas. O filtro antigo explica a ausência de pagamentos confirmados na demonstração; essa ausência não prova erro na conciliação.
- `vencimento` consulta uma data exata; `vencimento_de`/`vencimento_ate` consultam um intervalo inclusivo. `pagamento_de`/`pagamento_ate` permitem consultar pagamentos registrados em um intervalo. Todos os filtros são combinados e validados.
- `limit` continua entre 1 e 2000, com padrão 500; `offset` começa em 0. A resposta mantém `total` como quantidade desta página e acrescenta `total_registros`, `limit`, `offset`, `has_more` e `next_offset`. A ordenação utiliza vencimento e ID. A paginação permite atravessar carteiras com mais de 2000 registros.
- Lista e detalhe informam `pagamento_confirmado`, `data_pagamento`, `valor_pago` e `forma_pagamento`, usando o estado financeiro armazenado. Campos ausentes em históricos legados permanecem nulos.
- O filtro de canal usa o mesmo responsável financeiro que os contatos da resposta: snapshot da cobrança, interveniente ativo do contrato legado ou cliente de origem. Não há consulta adicional do pagador para cada linha da lista.
- Linha digitável, código de barras, nosso número e Pix da resposta vêm do registro oficial Ailos. A lista não depende de cálculos locais do boleto. Dados ausentes no banco não são fabricados.
- `boleto_registrado` exige linha digitável e código de barras. `boleto_disponivel` e `motivo_boleto_indisponivel` distinguem ausência de registro, cobrança paga/cancelada, baixa e restrição ao sistema. Quando indisponível, dados de boleto e URLs ficam nulos; o PDF autenticado retorna `409` com o motivo.
- O manual Markdown 1.1, a coleção Postman e os tipos da integração foram atualizados. Os contratos de outras rotas no arquivo TypeScript foram preservados, inclusive extensões anteriores que ainda não constavam no artefato OpenAPI local.

## Validação

Foram executados 70 testes de integração: todos passaram. Os cenários incluem limites/datas inválidos, combinação de filtros, responsáveis financeiros diferentes, pagamento confirmado pelo endpoint real de recebimento, autenticação e travessia de 2003 registros sem repetição de IDs.

Na imagem local `mastersat-f05-test:latest`, com Python 3.12.14 e dependências oficiais, a seleção ampliada de integração, boletos, recebimento, conciliação e política bancária terminou com **154 aprovados e 1 falha preexistente**, totalizando 155 testes. O container executou sem rede, com o backend montado somente para leitura e banco SQLite em memória.

A falha foi `test_fase03_titulo_bancario.py::TestTituloBaixadoNaoVoltaAoCliente::test_pdf_e_email_recusados_com_baixa_pendente`: o teste espera `detail.code`, mas o envio por e-mail recebe uma mensagem textual de outra validação. O mesmo teste falhou com o arquivo anterior de `integrations.py` extraído de `HEAD`, sobreposto somente para leitura no container. A rota de e-mail e esse teste não foram alterados neste trabalho.

Evidências locais: `tmp/api-media-20261007/api-finance-final.log` e `tmp/api-media-20261007/api-finance-final.xml`.

Comando de reprodução dos testes relevantes, a partir de `backend`:

```powershell
python -m pytest tests/test_integrations_api.py tests/test_boletos_api.py tests/test_fase03_recebimento.py tests/test_fase03_conciliacao.py tests/test_fase03_titulo_bancario.py -q -p no:cacheprovider
```

A verificação TypeScript passou com `node node_modules/typescript/bin/tsc --noEmit --incremental false`, preservando o `tsconfig.tsbuildinfo` já modificado pelo usuário. O OpenAPI da integração foi exportado com as dependências oficiais, sem rede, sem executar startup e sem carregar o `.env` local. Os arquivos locais preexistentes verificados por hash permaneceram intactos.

## Limites da validação

Não houve publicação, migração de banco, emissão bancária ou envio de mensagens. Os registros antigos mostrados no vídeo não foram conciliados nem certificados individualmente na base de produção. O status reflete o MasterSat; a consulta não confirma um pagamento diretamente no banco e não altera a classificação de vencimento executada pelo worker.

A paginação por offset não congela a carteira entre requisições: alterações concorrentes podem deslocar registros. A integradora deve manter deduplicação por ID e reconsultar o detalhe antes de notificar. PDFs do manual 1.0 distribuídos anteriormente não foram regenerados; a fonte Markdown e a coleção atualizadas estão em `docs/integracao-cobranca-whatsapp`.
