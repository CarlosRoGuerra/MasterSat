# Link de PDF da NFS-e para o cliente — 07/10/2026

O PDF já podia ser baixado pela integradora com `X-API-Key`, mas a URL em `nfse.pdf_url` retornava `401` quando aberta diretamente no navegador. A resposta agora entrega o link do PDF com token nesse campo, para envio ao cliente e abertura sem chave/login.

## Comportamento entregue

- `nfse.pdf_url` aponta para `GET /api/v1/public/nfse/{billing_id}/{token}`. A resposta é `application/pdf` com disposição `inline`, permitindo abrir o PDF no navegador.
- `nfse.pdf_api_url` é o novo campo com o endereço autenticado anterior. A rota `/integrations/cobrancas/{id}/nfse/pdf` e o XML continuam exigindo `X-API-Key`.
- Lista, detalhe e consulta fiscal retornam os mesmos links. Quando a nota não está emitida ou não tem XML, os três campos de URL permanecem nulos.
- O token é HMAC-SHA256 completo, assinado com `SECRET_KEY`, com finalidade própria e vínculo à cobrança, ao registro da nota, chave de acesso, número e série. Tokens alterados, de outra nota ou de boleto não entregam o PDF.
- Um link já enviado deixa de entregar o documento quando a nota é cancelada/substituída, deixa de estar emitida, perde o XML ou há remoção da cobrança, cliente/pagador ou registro fiscal. A resposta pública é `404` com mensagem genérica.
- Cobranças pagas ou financeiramente canceladas mantêm a nota emitida disponível. A situação fiscal é independente da situação financeira.
- O link não tem expiração por tempo; alterar `SECRET_KEY` ou a identidade fiscal revoga o link anterior. Alterar apenas a chave de integração não revoga os links enviados aos clientes.
- A rota inclui `Cache-Control: private, no-store`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff` e orientação de não indexação. O conteúdo continua sendo o DANFSE local do XML fiscal armazenado.
- Manual/guia 1.3, coleção Postman com 12 requisições e tipos da API foram atualizados. A consulta de dados permanece autenticada; não há emissão fiscal ou consulta externa ao abrir o link.

## Validação

**211 testes passaram**: 70 da integração financeira, 40 da integração fiscal, 31 novos cenários do link público, 36 de DANFSE e 34 de boletos. Incluem abrir a URL retornada pela API sem cabeçalhos de autenticação, PDF válido com disposição inline, recusa de token inválido/Unicode/de outro documento, identidade fiscal alterada, substituição da nota, remoções, indisponibilidade e revogação pela chave de assinatura.

A seleção passou no ambiente local Python 3.13.5 e com as dependências oficiais em Python 3.12.14. O segundo teste usou um snapshot do backend de `13d125d` contendo somente os cinco arquivos Python desta correção, sem as alterações locais não relacionadas. O container executou sem rede, com código somente para leitura e SQLite isolado. Evidência: `tmp/api-media-20261007/api-nfse-public-final.xml`. Os arquivos Python do commit são comparados com esse snapshot antes do push.

Reprodução, a partir de `backend`:

```powershell
python -m pytest tests/test_integrations_api.py tests/test_integrations_nfse_api.py tests/test_integrations_nfse_public.py tests/test_nfse_danfse.py tests/test_nfse_danfse_endpoint.py tests/test_boletos_api.py -q -p no:cacheprovider
```

TypeScript passou com `node node_modules/typescript/bin/tsc --noEmit --incremental false`. Os 13 blocos PowerShell e os blocos Bash do guia passaram pela análise de sintaxe. A coleção Postman foi validada como JSON, com o novo link configurado como `noauth`, sem chave ou token real. Os 12 arquivos locais preexistentes acompanhados por hash foram preservados.

## Publicação e uso

É necessário atualizar o backend da VPS com o comando do guia e consultar novamente a API. URLs antigas com `/integrations/cobrancas/{id}/nfse/pdf` continuam exigindo a chave. O novo valor de `nfse.pdf_url` deve conter `/api/v1/public/nfse/`, ID e token e pode ser enviado ao destinatário.

Não houve implantação na VPS, emissão fiscal/bancária, envio de mensagens ou alteração de registros de produção. A validação usa SQLite e dados sintéticos, não a base PostgreSQL de produção. Quem possui o link completo pode abrir a nota enquanto disponível; a chave de integração não faz parte desse link.
