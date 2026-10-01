# Backup e recuperação — Mastersat

Backup que se prova restaurável: cada execução carrega o que é preciso para
conferir a si mesma, e a restauração **nunca apaga o banco em uso**.

- Procedimento de desastre passo a passo: [docs/contingencia-recuperacao.md](../docs/contingencia-recuperacao.md)
- Resultado do último exercício e baseline de testes: [docs/validacao/baseline-fase-00.md](../docs/validacao/baseline-fase-00.md)

---

## O que mudou (09/2026) e por quê

| Antes | Problema | Agora |
|---|---|---|
| `pg_dump --format=custom \| gzip` salvo como `*.sql.gz` | o nome dizia SQL, o conteúdo era archive custom | `db.dump` (custom, sem gzip extra) + formato registrado no manifest |
| `restore.sh` fazia `DROP DATABASE` e depois `gunzip \| psql` | `psql` não lê archive custom: banco apagado e **zero tabelas** restauradas | formato detectado pelo conteúdo, arquivo lido inteiro **antes** de qualquer ação, restauração num banco **novo**, troca só com aprovação e sem `DROP` |
| cron chamava só o `pg_dump` inline (`sh`, sem `pipefail`) | falha do `pg_dump` podia ser mascarada pelo `gzip` | imagem própria executando `backup.sh` com `set -euo pipefail` |
| MinIO e nuvem só no `backup.sh`, que o cron não chamava | objetos e cópia externa nunca aconteciam | banco + objetos + envio externo na mesma execução |
| falhas de upload/MinIO/GPG viravam aviso e o log dizia "sucesso" | falha silenciosa | toda falha entra em `RESULT`, dispara alerta e sai com código ≠ 0 |
| GPG falhou → enviava o dump **aberto** para a nuvem | vazamento | dump aberto nunca sai do servidor |
| retenção por idade apagava tudo se o backup parasse | perda do último backup bom | as `BACKUP_MIN_KEEP` execuções OK mais novas nunca saem |

O serviço `backup` (docker-volume-backup) do `docker-compose.yml` continua lá,
mas **não** é o backup do sistema: só empacota o volume `backup_data` dentro
do próprio container, e o deploy de produção nem o sobe.

---

## Uma execução de backup

`docker compose --profile backup run --rm pg_dump` (o cron do `deploy.sh`
chama exatamente isto às 02:00) grava em `backup_data:/backup/runs/<AAAAMMDD_HHMMSS>/` (UTC):

| Arquivo | Conteúdo |
|---|---|
| `db.dump` | `pg_dump --format=custom` (ou `db.dump.gpg`) |
| `inventory.tsv` | linhas por tabela, soma exata de toda coluna `numeric`, cobranças por situação, revisão Alembic |
| `object-refs.tsv` | chaves de objeto que o banco referencia (documento ativo = obrigatório) |
| `minio/` | cópia dos objetos do bucket |
| `minio-objects.tsv` | chave, bytes e sha256 de cada objeto |
| `objects-missing.tsv` | referências obrigatórias sem objeto (só se houver) |
| `manifest.json` | formato, versões do PostgreSQL/pg_dump, commit da aplicação, revisão Alembic, checksums, contagens, tempos, impressão digital da chave Fernet |
| `SHA256SUMS` | confere tudo acima com `sha256sum -c` |
| `backup.log` | log da execução |
| `RESULT` | `STATUS=OK` ou `STATUS=FALHA` + erros — escrito por último |

`$BACKUP_DIR/last_success` guarda a última execução OK (para monitoramento) e
`$BACKUP_DIR/last_run` a última execução com o status.

**Consistência.** `inventory.tsv` e `object-refs.tsv` são calculados dentro do
**mesmo snapshot exportado** que o `pg_dump` usa — descrevem exatamente o dump.
Os objetos são copiados *depois* (não há snapshot atômico entre PostgreSQL e
MinIO); por isso o script confere que toda referência obrigatória do snapshot
está na cópia. Objeto a mais (enviado depois do snapshot) é normal; objeto a
menos é falha.

**O manifest não contém segredos**: nem senha do banco, nem chave Fernet
(só a impressão digital `sha256:` de 16 dígitos), nem credenciais do MinIO ou
do destino externo. Os testes conferem isso.

**Códigos de saída:** `0` tudo OK · `1` artefato gerado, mas alguma etapa
falhou · `2` nenhum dump utilizável.

### Destino externo

O envio é feito com `rclone copy` e **conferido** com `rclone check --one-way`:
código 0 do `copy` sem os arquivos no destino é detectado. Layout remoto:

```
<RCLONE_REMOTE>/runs/<AAAAMMDD_HHMMSS>/   manifest, SHA256SUMS, dump, inventários, RESULT (por último)
<RCLONE_REMOTE>/minio/objects/            objetos (acumulam: copy, nunca sync)
```

Execução remota **sem `RESULT`** não chegou ao fim. Retenção remota: execuções
com mais de `BACKUP_REMOTE_KEEP_DAYS` (90) saem, preservando sempre as
`BACKUP_MIN_KEEP` mais novas. Objetos remotos não são apagados pelo script.

> **Sem promessa de imutabilidade.** Quem tem a credencial do rclone no
> servidor consegue apagar o destino externo. Proteção contra isso
> (Object Lock/bucket com retenção, chave de aplicação sem permissão de
> exclusão) depende de configuração no provedor e está registrada como
> decisão pendente em [docs/contingencia-recuperacao.md](../docs/contingencia-recuperacao.md).

---

## Configuração

1. `cp backup/backup.env.example backup/backup.env` (ignorado pelo git) e
   preencha o destino externo, o webhook e a impressão digital da chave Fernet.
   Esse arquivo vai **só** para o container de backup — o `.env` da aplicação
   (com a chave Fernet) não é entregue a ele.
2. Recomendado: remoto `crypt` do rclone por cima do bucket (PDFs de contratos
   e documentos de clientes saem cifrados do servidor). A senha do `crypt` vai
   para o cofre.
3. `docker compose --profile backup build pg_dump`
4. Primeira execução manual e conferência:
   ```bash
   docker compose --profile backup run --rm pg_dump; echo "saída=$?"
   docker compose --profile backup run --rm pg_dump cat /backup/last_run
   ```

Variáveis principais (todas documentadas no cabeçalho de `backup.sh`):
`RCLONE_REMOTE`, `BACKUP_OFFSITE_REQUIRED` (1 = sem destino externo é falha),
`BACKUP_KEEP_DAYS`, `BACKUP_MIN_KEEP`, `BACKUP_MINIO_KEEP_RUNS`,
`BACKUP_REMOTE_KEEP_DAYS`, `ALERT_WEBHOOK`, `BACKUP_FERNET_FINGERPRINT`,
`BACKUP_ENCRYPT_KEY`/`BACKUP_GPG_PUBLIC_KEY_FILE`, `MASTERSAT_GIT_SHA`.

### O que o backup NÃO leva (vai para o cofre, fora do servidor)

| Item | Por quê |
|---|---|
| `AILOS_TOKEN_ENCRYPTION_KEY` (chave Fernet) | sem ela, tokens Ailos, senha SMTP e o certificado A1 guardados no banco são irrecuperáveis. Confira pela impressão digital do manifest |
| `.env` de produção completo | `SECRET_KEY`, credenciais Ailos/SGR/Multiportal, senha do banco |
| `.pfx` montado em `NFSE_CERT_DIR` + senha (se ainda usado) | certificado ICP-Brasil |
| senha do remoto `crypt` e credencial do provedor | sem elas o destino externo é ilegível/inacessível |
| chave privada GPG (se `BACKUP_ENCRYPT_KEY` for usado) | decifra o dump |

---

## Restauração

Todos os subcomandos rodam na mesma imagem:

```bash
R="docker compose --profile backup run --rm pg_dump /opt/mastersat-backup/restore.sh"
```

| Comando | O que faz | Toca em banco? |
|---|---|---|
| `$R verificar runs/<exec>` | checksums, formato real, leitura integral | não |
| `$R banco runs/<exec> [--destino NOME]` | cria banco **novo** (padrão `rastreamento_restore_<data>`), restaura em transação única, compara inventário com o manifest | cria só o novo |
| `$R objetos runs/<exec> /backup/restauracao/objetos [--para-bucket REMOTO:bucket]` | copia para diretório vazio, confere hash e referências; opcionalmente envia a bucket **vazio** | não |
| `$R baixar <REMOTO>/runs/<exec> /backup/restauracao/<exec>` | traz execução + objetos do destino externo e confere | não |
| `$R promover --de NOME` | renomeia `rastreamento` → `rastreamento_pre_restore_<data>` e `NOME` → `rastreamento` | renomeia; nada é apagado |
| `$R reverter-troca --anterior rastreamento_pre_restore_<data>` | desfaz a troca | renomeia |

Garantias (cobertas por `backup/tests/`):

- arquivo truncado, hash divergente, formato desconhecido, sem permissão de
  leitura ou chave GPG ausente → sai com código 2 **antes** de criar banco;
- destino igual ao banco em uso ou já existente → recusado;
- inventário restaurado diferente do manifest → código 3, banco não recebe a
  marca de validado e `promover` o recusa;
- `promover` exige `RESTORE_APROVACAO` (quem aprovou + registro) e digitar o
  nome do banco em uso; pare o backend antes;
- backups antigos `*.sql.gz` (custom dentro de gzip) são reconhecidos e lidos
  com `pg_restore`; `restore.sh <arquivo>` (forma antiga) restaura num banco
  novo.

> Um corte só nos bytes finais de um dump custom pode passar pela leitura
> integral do `pg_restore` sem perda de conteúdo (verificado: SQL gerado
> idêntico). Arquivos novos têm SHA-256 no manifest, que pega qualquer byte
> alterado; para arquivos legados sem manifest a leitura integral é o
> melhor disponível.

### Depois de restaurar o banco: segredos e documentos

```bash
docker compose run --rm \
  -e AILOS_TOKEN_ENCRYPTION_KEY="<chave do cofre>" \
  backend python scripts/verificar_restauracao.py \
    --database-url "postgresql+psycopg://postgres:<senha>@db:5432/rastreamento_restore_<data>" \
    --minio --json /tmp/verificacao.json
```

Confere, com o código da aplicação e só com leituras: todos os segredos
cifrados decifram com a chave informada (e a impressão digital dela bate com
a do manifest), o certificado A1 abre com a senha decifrada (e até quando
vale), todo documento ativo e todo retorno Ailos existem no armazenamento com
o tamanho registrado, e o saldo por situação em centavos. Sai 0 = aprovado.

---

## Testes e exercício

```bash
bash backup/tests/run_in_docker.sh                 # 91 casos, container sem rede, PostgreSQL descartável
bash backup/exercicio/exercicio_restauracao.sh     # ciclo completo em containers descartáveis
```

O exercício sobe PostgreSQL 16 + MinIO numa rede interna, cria o schema real
(`alembic upgrade head`), semeia dados sintéticos (cobranças, documentos,
tokens cifrados, PFX autoassinado), faz backup com envio externo, **apaga**
banco, MinIO e backup local, recupera em servidores novos só a partir do
destino externo, promove, verifica e mede os tempos. Não usa `.env`, volumes
ou containers do sistema e remove tudo o que criou no fim.

**Nunca aponte `restore.sh` ou o exercício para produção sem o procedimento de
contingência e a aprovação registrada.**
