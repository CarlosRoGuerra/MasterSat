# Migração SGR — hosts permitidos para download (SGR-06)

O SGR não entrega o PDF do boleto nem o XML da NFS-e: devolve uma **URL**,
às vezes de um gateway de terceiros. Desde a Fase 04 o MasterSat só baixa
dessas URLs se todas as regras abaixo passarem (`app/services/sgr_migration/download.py`):

| Regra | Recusa com |
|---|---|
| `https`, porta 443, sem usuário/senha na URL | `esquema_nao_https`, `porta_nao_padrao`, `credencial_na_url` |
| Host na allowlist | `host_nao_permitido` |
| Todo IP resolvido é público (não loopback, privado, link-local/metadados, multicast, reservado, IPv4 mapeado em IPv6) | `ip_nao_publico` |
| Redirect: cada salto repete as regras, até `SGR_DOWNLOAD_MAX_REDIRECTS` (3) | `redirects_demais`, mais a regra do salto |
| Tamanho ≤ `SGR_DOWNLOAD_MAX_BYTES` (10 MiB), também contra `Content-Length` | `arquivo_grande_demais` |
| PDF começa com `%PDF-`; XML sem `DOCTYPE`/`ENTITY` e bem formado | `conteudo_nao_pdf`, `xml_com_dtd`, `xml_malformado` |

Recusa de **destino** deixa o arquivo `bloqueado` no outbox (`sgr_arquivos`)
— ele é tentado de novo quando a configuração mudar, sem nova leitura do
SGR (`python scripts/sgr_import.py --arquivos --apply`). Recusa de
**conteúdo** e falha de rede contam tentativa (até
`SGR_DOWNLOAD_MAX_TENTATIVAS`, 5).

## Configuração

```env
# separados por vírgula; ".dominio.com.br" libera os subdomínios
SGR_DOWNLOAD_HOSTS=
SGR_DOWNLOAD_MAX_BYTES=10485760
SGR_DOWNLOAD_MAX_REDIRECTS=3
SGR_DOWNLOAD_MAX_TENTATIVAS=5
```

O host de `SGR_BASE_URL` (hoje `sgr.hinova.com.br`) entra sempre.

## Como descobrir e aprovar (decisão D7)

Os hosts reais **não são conhecidos** pelo repositório: a importação antiga
não guardava a URL. Procedimento proposto:

1. Simulação: `python scripts/sgr_import.py --limit 20 --notas` (sem
   `--apply`). O resumo imprime **"HOSTS DOS ARQUIVOS"** — só o nome do
   host e a quantidade, nunca o caminho/token.
2. Conferir cada host com o fornecedor (Hinova/banco/prefeitura): é o
   gateway do boleto/NFS-e do contrato? É HTTPS válido?
3. Registrar a aprovação (quem, data, host) e preencher `SGR_DOWNLOAD_HOSTS`
   no `.env` do ambiente que roda a migração.
4. `--arquivos --apply` baixa o que ficou bloqueado.

Mudança de host (gateway novo) segue o mesmo caminho; remover um host não
apaga documentos já baixados.

## Risco residual

A resolução DNS é conferida antes da conexão, mas o `requests` resolve de
novo ao conectar: um DNS controlado por terceiro poderia trocar o IP entre
as duas consultas (*DNS rebinding*). A allowlist de hosts é o controle
principal — não aprovar domínios genéricos de hospedagem/CDN compartilhada.
