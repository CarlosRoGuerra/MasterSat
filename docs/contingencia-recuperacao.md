# Procedimento de contingência — recuperação do Mastersat

Para quem vai operar uma recuperação real (ou o ensaio mensal). Detalhes de
cada comando em [backup/README.md](../backup/README.md).

**Regras fixas**

- Nunca `DROP DATABASE`, `docker compose down -v` ou "recriar o banco" para
  resolver problema. O `restore.sh` não apaga nada; a troca de banco é por
  renomeação e reversível.
- Restaure sempre em banco **novo**, verifique, e só então troque — com
  aprovação registrada.
- Não reemita boleto nem NFS-e para "testar" a recuperação.
- Não use produção como ambiente de ensaio.

---

## 1. Decisões operacionais (PENDENTES de aprovação da empresa)

Os números abaixo são **proposta**. Enquanto não forem aprovados, valem só
como referência.

| Decisão | Proposta | Efeito demonstrável | Responsável pela decisão |
|---|---|---|---|
| **RPO** (perda máxima aceitável) | 24 h — backup diário às 02:00 UTC | `last_success` com mais de 26 h = alerta; o manifest registra o horário exato do snapshot | Direção |
| RPO mais curto (opcional) | 6 h — cron `0 */6 * * *` | mesma verificação, custo 4× de armazenamento/tráfego | Direção |
| **RTO** (tempo para voltar ao ar) | 4 h a partir da decisão de recuperar | etapas técnicas medidas no ensaio (ver §5): minutos; o resto é provisionar VPS, DNS e baixar o volume real | Direção |
| Retenção local | 30 dias, mínimo 7 execuções OK; objetos locais nas 7 mais novas | conferível em `backup_data:/backup/runs` | Operação |
| Retenção externa | 90 dias de execuções; objetos acumulam | `rclone lsf <remoto>/runs` | Operação |
| Proteção contra exclusão no destino externo | **não existe hoje**. Proposta: bucket com Object Lock/retenção e chave de aplicação sem permissão de exclusão | tentativa de `rclone delete` com a chave do servidor falha | Direção + Operação |
| Ensaio de recuperação | mensal, com `backup/exercicio/exercicio_restauracao.sh` + trimestral a partir do destino externo real num servidor isolado | relatório arquivado em `docs/validacao/` | Operação |
| Guarda das chaves | cofre fora do servidor (gerenciador de senhas da empresa) com 2 pessoas com acesso | conferir impressão digital Fernet do manifest com a do cofre a cada ensaio | Direção (guardião) |

**Papéis** (nomes a preencher pela empresa):

| Papel | Quem | Faz |
|---|---|---|
| Aprovador | _a definir_ | autoriza a troca de banco (`RESTORE_APROVACAO`) |
| Operador | _a definir_ | executa backup/ensaio/restauração, lê alertas |
| Guardião do cofre | _a definir_ (+1 substituto) | entrega chave Fernet, senha do crypt, `.env` |

---

## 2. Conteúdo do cofre (fora do servidor)

- `AILOS_TOKEN_ENCRYPTION_KEY` — decifra tokens Ailos, senha SMTP e o
  certificado A1 guardados no banco. Impressão digital:
  `printf '%s' "$CHAVE" | sha256sum | cut -c1-16` deve bater com
  `fernet_key_fingerprint` do `manifest.json`.
- `.env` de produção completo (senhas, `SECRET_KEY`, credenciais Ailos/SGR/Multiportal).
- `.pfx` de `NFSE_CERT_DIR` e senha, se ainda usados pelo caminho legado.
- Credencial do provedor externo e senha do remoto `crypt` do rclone.
- Chave privada GPG, se o dump for cifrado com GPG.

Sem a chave Fernet a verificação pós-restauração **reprova** (comprovado no
ensaio: controle negativo).

---

## 3. Recuperação — servidor perdido

1. **Declarar o incidente** e registrar horário. Se houve invasão, não reaproveite o host.
2. **Novo servidor**: clonar o repositório no commit indicado em
   `app_git_sha` do manifest da execução escolhida; `.env` do cofre;
   `backup/backup.env` com o destino externo.
3. **Subir só a infraestrutura**: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d db minio redis`
   (backend parado).
4. **Escolher a execução**: `rclone lsf <remoto>/runs` — use a mais nova **com
   `RESULT`** e `STATUS=OK` (`rclone cat <remoto>/runs/<exec>/RESULT`).
5. **Baixar e conferir**:
   `$R baixar <remoto>/runs/<exec> /backup/restauracao/<exec>`
6. **Banco em nome novo**: `$R banco /backup/restauracao/<exec> --destino rastreamento_restaurado`
   — precisa terminar com "inventário idêntico ao do backup".
7. **Objetos**: `$R objetos /backup/restauracao/<exec> /backup/restauracao/objetos --para-bucket minio_src:rastreamento`
   (bucket vazio no MinIO novo).
8. **Verificar** (chave Fernet do cofre): `scripts/verificar_restauracao.py --database-url …/rastreamento_restaurado --minio`
   — precisa sair `APROVADO`; conferir a impressão digital.
9. **Revisão Alembic**: se `alembic_revision` do manifest for anterior ao
   código implantado, rodar `alembic upgrade head` **no banco restaurado antes
   da troca** e repetir o passo 8.
10. **Aprovação + troca**: `RESTORE_APROVACAO="<aprovador> / <registro>" $R promover --de rastreamento_restaurado`.
11. **Subir backend/frontend/nginx**, smoke test (login, uma cobrança, abrir um
    documento), reabilitar integrações por último (Multiportal, SGR, Ailos).
12. **Registrar** RTO real e execução usada em `docs/validacao/`.

`R="docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile backup run --rm pg_dump /opt/mastersat-backup/restore.sh"`

## 4. Recuperação — banco corrompido, servidor íntegro

Mesmos passos 4–10 com a execução local (`runs/<exec>` em `backup_data`), sem
baixar. O banco atual fica preservado como `rastreamento_pre_restore_<data>`
para comparação e para `reverter-troca` se algo der errado.

**Rollback da troca:** `RESTORE_APROVACAO=… $R reverter-troca --anterior rastreamento_pre_restore_<data>`
(ensaiado nos testes automatizados: dados originais voltam intactos e nenhum banco é apagado).

---

## 5. Medições do ensaio

Ver [docs/validacao/baseline-fase-00.md](validacao/baseline-fase-00.md) §4 —
contagens, hashes, saldo e tempos dos exercícios executados em 30/09/2026.

## 6. Monitoramento mínimo

- Alerta do webhook em qualquer `STATUS=FALHA`.
- Checagem externa diária: idade de `/backup/last_success` (< 26 h) e
  existência de `RESULT` na execução remota mais nova.
- Falha do próprio webhook fica registrada no `backup.log` ("webhook de alerta
  não respondeu"), mas não pode ser vista por ele mesmo — por isso a checagem
  externa acima.
