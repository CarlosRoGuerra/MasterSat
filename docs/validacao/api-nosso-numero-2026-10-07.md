# Referência bancária das cobranças pagas — 07/10/2026

A resposta de integração ocultava `nosso_numero` junto com os códigos/links de pagamento quando o boleto estava indisponível, inclusive em cobranças pagas. O número é uma referência histórica do título e precisa continuar visível quando armazenado.

## Correção

Lista e detalhe retornam `AilosBoleto.nosso_numero` como texto, preservando os zeros iniciais, independentemente do status financeiro ou da disponibilidade do boleto. A referência continua disponível após pagamento, liquidação ou baixa, em cobranças canceladas/restritas ao sistema e em registros com códigos de pagamento incompletos.

Linha digitável, código de barras, Pix e links de boleto continuam nulos quando indisponíveis. O PDF autenticado do boleto pago permanece bloqueado com `409`. Sem registro Ailos ou sem número oficial salvo, `nosso_numero` permanece `null`; a API não calcula uma referência a partir do ID da cobrança e não altera dados do banco.

Manual, guia e Postman foram atualizados para a versão 1.4. O exemplo PowerShell de pagamentos agora mostra `nosso_numero`.

## Validação

**216 testes passaram**, no ambiente local Python 3.13.5 e no snapshot isolado com dependências oficiais/Python 3.12.14. São 75 casos da integração financeira, 40 da integração fiscal, 31 do link de NFS-e, 36 de DANFSE e 34 de boletos.

Os cinco novos casos verificam pagamentos recentes por boleto, preservação de referência na listagem filtrada por data de pagamento e no detalhe, liquidação/baixa bancária e ausência de número oficial sem criação de referência local. O teste de indisponibilidade também verifica a referência em oito situações, mantendo bloqueados os códigos/PDF.

O snapshot usa o backend de `eecddff` com somente os dois arquivos Python desta correção. O container executou sem rede, com código somente para leitura e SQLite isolado. Evidência: `tmp/api-media-20261007/api-nosso-numero-final.xml`. As fontes são comparadas com o commit, normalizando CRLF/LF, antes do push.

Comando de reprodução, a partir de `backend`:

```powershell
python -m pytest tests/test_integrations_api.py tests/test_integrations_nfse_api.py tests/test_integrations_nfse_public.py tests/test_nfse_danfse.py tests/test_nfse_danfse_endpoint.py tests/test_boletos_api.py -q -p no:cacheprovider
```

Os 13 blocos PowerShell e os blocos Bash do guia passaram pela análise de sintaxe. A coleção Postman foi validada como JSON sem credenciais. Os 13 arquivos preexistentes acompanhados por hash, incluindo o RAR local do usuário, foram preservados.

## Implantação

É necessário atualizar o backend da VPS com os comandos do guia para o campo deixar de ser ocultado. Não houve implantação, emissão, consulta bancária ou alteração de dados de produção. Esta correção conserva referências armazenadas; registros sem número oficial exigem análise da fonte de dados.
