#!/usr/bin/env bash
# =============================================================================
# Exercício integral de recuperação — SOMENTE com containers descartáveis.
#
#  1. Sobe PostgreSQL 16 + MinIO de ensaio numa rede Docker interna (sem
#     saída para a internet, sem portas publicadas, dados em tmpfs).
#  2. Cria o schema real (alembic upgrade head) e semeia dados sintéticos:
#     clientes, cobranças, contas a pagar, documentos no MinIO, retorno Ailos,
#     tokens cifrados e um certificado A1 autoassinado, com chave Fernet gerada
#     só para o ensaio.
#  3. Roda backup.sh com envio para um "destino externo" (volume separado).
#  4. Desastre: apaga banco, MinIO e o backup local.
#  5. Recupera em servidores NOVOS usando apenas o destino externo + a chave
#     do "cofre": baixar → banco (novo nome) → objetos → promover.
#  6. Verifica segredos, PFX, documentos e saldo com o código da aplicação.
#  7. Mede tempos (backup, RTO) e grava relatório.
#
# Nunca usa .env, volumes ou containers do sistema. Tudo que cria leva o
# prefixo ensaio<data> e é removido no fim (KEEP=1 mantém para inspeção).
#
# Uso (raiz do repositório):  bash backup/exercicio/exercicio_restauracao.sh [dir-relatorio]
# =============================================================================
set -Eeuo pipefail

# Git Bash no Windows: o docker.exe precisa receber caminhos de container
# (/offsite, /data...) sem conversão; o git nativo precisa da conversão.
docker() { MSYS_NO_PATHCONV=1 command docker "$@"; }

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
ROOT_HOST=$(cd "$ROOT" && (pwd -W 2>/dev/null || pwd))
ID="ensaio$(date -u +%Y%m%d%H%M%S)"
NET="$ID-net"
OUT="${1:-$ROOT/output/$ID}"
mkdir -p "$OUT"
BACKEND_IMG="mastersat-backend:$ID"
BACKUP_IMG="mastersat-backup:$ID"
GIT_SHA=$(git -C "$ROOT" rev-parse HEAD)
GIT_DIRTY=$(git -C "$ROOT" status --porcelain -- backup backend/app backend/scripts | wc -l | tr -d ' ')

log() { echo "[$(date -u '+%H:%M:%S')] $*" | tee -a "$OUT/exercicio.log" >&2; }
now() { date -u +%s; }
TEMPOS="$OUT/tempos.tsv"; : > "$TEMPOS"
etapa() {  # etapa NOME comando...
  local nome="$1"; shift
  local t0; t0=$(now)
  log "▶ $nome"
  "$@"
  local dt=$(( $(now) - t0 ))
  printf '%s\t%s\n' "$nome" "$dt" >> "$TEMPOS"
  log "✓ $nome (${dt}s)"
}

CONTAINERS=(); VOLUMES=()
cleanup() {
  if [ "${KEEP:-0}" = "1" ]; then log "KEEP=1: recursos $ID mantidos"; return; fi
  for c in "${CONTAINERS[@]}"; do docker rm -f "$c" >/dev/null 2>&1 || true; done
  for v in "${VOLUMES[@]}"; do docker volume rm -f "$v" >/dev/null 2>&1 || true; done
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker image rm "$BACKEND_IMG" "$BACKUP_IMG" >/dev/null 2>&1 || true
}
trap cleanup EXIT

rand() { docker run --rm --network none "$BACKUP_IMG" sh -c 'head -c 24 /dev/urandom | od -An -tx1 | tr -d " \n"'; }

# ── Preparação ────────────────────────────────────────────────────────────────
log "Exercício $ID — commit $GIT_SHA (arquivos alterados não commitados: $GIT_DIRTY)"
etapa "build imagem backend" docker build -q -t "$BACKEND_IMG" "$ROOT_HOST/backend" >/dev/null
etapa "build imagem backup" docker build -q -t "$BACKUP_IMG" "$ROOT_HOST/backup" >/dev/null
docker network create --internal "$NET" >/dev/null

PGPASS=$(rand); MINIO_USER="ensaio"; MINIO_PASS=$(rand)
FERNET_KEY=$(docker run --rm --network none "$BACKEND_IMG" python -c \
  'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
FERNET_FP="sha256:$(printf '%s' "$FERNET_KEY" | sha256sum | cut -c1-16)"
log "Chave Fernet do ensaio gerada (impressão digital $FERNET_FP) — guardada só em memória, como se estivesse no cofre"

sobe_servidores() {  # $1 = sufixo
  local db="$ID-db$1" minio="$ID-minio$1"
  CONTAINERS+=("$db" "$minio")
  docker run -d --name "$db" --network "$NET" --tmpfs /var/lib/postgresql/data \
    -e POSTGRES_PASSWORD="$PGPASS" -e POSTGRES_DB=rastreamento postgres:16 >/dev/null
  docker run -d --name "$minio" --network "$NET" --tmpfs /data \
    -e MINIO_ROOT_USER="$MINIO_USER" -e MINIO_ROOT_PASSWORD="$MINIO_PASS" \
    minio/minio:latest server /data >/dev/null
  for _ in $(seq 1 60); do docker exec "$db" pg_isready -U postgres -d rastreamento >/dev/null 2>&1 && break; sleep 1; done
  docker run --rm --network "$NET" "$BACKUP_IMG" sh -c \
    "for i in \$(seq 1 60); do curl -sf http://$minio:9000/minio/health/live && exit 0; sleep 1; done; exit 1"
}

app_env() {  # $1 = sufixo dos servidores; $2 = banco
  echo "-e DATABASE_URL=postgresql+psycopg://postgres:$PGPASS@$ID-db$1:5432/$2"
  echo "-e MINIO_ENDPOINT=$ID-minio$1:9000 -e MINIO_ROOT_USER=$MINIO_USER -e MINIO_ROOT_PASSWORD=$MINIO_PASS"
  echo "-e MINIO_BUCKET=rastreamento -e REDIS_URL=redis://indisponivel:6379/0 -e MULTIPORTAL_ENABLED=false"
}

# ── 1–2. Origem sintética ─────────────────────────────────────────────────────
etapa "subir servidores de origem" sobe_servidores ""
# shellcheck disable=SC2046
etapa "schema (alembic upgrade head)" docker run --rm --network "$NET" $(app_env "" rastreamento) \
  "$BACKEND_IMG" alembic upgrade head
semear() {
  # shellcheck disable=SC2046
  docker run --rm -i --network "$NET" $(app_env "" rastreamento) -e AILOS_TOKEN_ENCRYPTION_KEY="$FERNET_KEY" -e ENSAIO_CLIENTES -e ENSAIO_COBRANCAS_POR_CLIENTE -e ENSAIO_DOCUMENTOS     "$BACKEND_IMG" python - < "$ROOT/backup/exercicio/semear_dados_sinteticos.py" > "$OUT/semeadura.json"
}
etapa "semear dados sintéticos" semear
log "Semeadura: $(cat "$OUT/semeadura.json")"

# ── 3. Backup ─────────────────────────────────────────────────────────────────
VOLUMES+=("$ID-backup" "$ID-offsite" "$ID-restauracao")
backup_env=(
  -e POSTGRES_HOST="$ID-db" -e POSTGRES_PASSWORD="$PGPASS" -e POSTGRES_DB=rastreamento
  -e BACKUP_DIR=/backup -e RCLONE_REMOTE=/offsite
  -e MASTERSAT_GIT_SHA="$GIT_SHA" -e BACKUP_FERNET_FINGERPRINT="$FERNET_FP"
  -e BACKUP_MINIO_SOURCE=minio_src:rastreamento
  -e RCLONE_CONFIG_MINIO_SRC_TYPE=s3 -e RCLONE_CONFIG_MINIO_SRC_PROVIDER=Minio
  -e RCLONE_CONFIG_MINIO_SRC_ENDPOINT="http://$ID-minio:9000"
  -e RCLONE_CONFIG_MINIO_SRC_ACCESS_KEY_ID="$MINIO_USER" -e RCLONE_CONFIG_MINIO_SRC_SECRET_ACCESS_KEY="$MINIO_PASS"
)
T_BACKUP=$(now)
etapa "backup.sh (banco + objetos + envio externo)" docker run --rm --network "$NET" \
  -v "$ID-backup:/backup" -v "$ID-offsite:/offsite" "${backup_env[@]}" "$BACKUP_IMG"
RUN=$(docker run --rm -v "$ID-offsite:/offsite:ro" "$BACKUP_IMG" ls /offsite/runs | tail -1)
for f in manifest.json RESULT SHA256SUMS inventory.tsv minio-objects.tsv backup.log; do
  docker run --rm -v "$ID-offsite:/offsite:ro" "$BACKUP_IMG" cat "/offsite/runs/$RUN/$f" > "$OUT/backup-$f"
done
grep -qx 'STATUS=OK' "$OUT/backup-RESULT" || { log "backup não terminou OK"; exit 1; }
log "Execução $RUN enviada ao destino externo"

# ── 4. Desastre ───────────────────────────────────────────────────────────────
T_DESASTRE=$(now)
log "DESASTRE: removendo banco, MinIO e backup local de origem"
docker rm -f "$ID-db" "$ID-minio" >/dev/null
docker volume rm "$ID-backup" >/dev/null

# ── 5. Recuperação só a partir do destino externo ─────────────────────────────
T_RTO=$(now)
etapa "subir servidores novos (vazios)" sobe_servidores "2"
restore_env=(
  -e POSTGRES_HOST="$ID-db2" -e POSTGRES_PASSWORD="$PGPASS" -e POSTGRES_DB=rastreamento
  -e RCLONE_CONFIG_MINIO_DST_TYPE=s3 -e RCLONE_CONFIG_MINIO_DST_PROVIDER=Minio
  -e RCLONE_CONFIG_MINIO_DST_ENDPOINT="http://$ID-minio2:9000"
  -e RCLONE_CONFIG_MINIO_DST_ACCESS_KEY_ID="$MINIO_USER" -e RCLONE_CONFIG_MINIO_DST_SECRET_ACCESS_KEY="$MINIO_PASS"
)
restaurar() {
  docker run --rm --network "$NET" -v "$ID-offsite:/offsite:ro" -v "$ID-restauracao:/restauracao" \
    "${restore_env[@]}" "$@" "$BACKUP_IMG" /opt/mastersat-backup/restore.sh "${RESTORE_ARGS[@]}" 2>&1 | tee -a "$OUT/restauracao.log"
}
RESTORE_ARGS=(baixar "/offsite/runs/$RUN" /restauracao/run);                     etapa "baixar do destino externo" restaurar
RESTORE_ARGS=(banco /restauracao/run --destino rastreamento_restaurado);        etapa "restaurar banco em nome novo + conferir inventário" restaurar
RESTORE_ARGS=(objetos /restauracao/run /restauracao/objetos --para-bucket minio_dst:rastreamento); etapa "restaurar objetos no MinIO novo" restaurar
RESTORE_ARGS=(promover --de rastreamento_restaurado)
etapa "promover (troca controlada)" restaurar -e RESTORE_APROVACAO="exercicio automatizado $ID" -e RESTORE_CONFIRMAR=rastreamento

verificar() {
  # shellcheck disable=SC2046
  docker run --rm --network "$NET" $(app_env 2 rastreamento) "$@" "$BACKEND_IMG" \
    sh -c 'python scripts/verificar_restauracao.py --database-url "$DATABASE_URL" --minio --json /tmp/v.json >&2; ec=$?; cat /tmp/v.json; exit $ec'
}
verificar_com_chave() { verificar -e AILOS_TOKEN_ENCRYPTION_KEY="$FERNET_KEY" > "$OUT/verificacao.json"; }
etapa "verificar segredos, PFX, documentos e saldo" verificar_com_chave
T_RTO_FIM=$(now)

# Controle negativo: sem a chave do cofre, a verificação TEM de reprovar.
set +e
verificar > "$OUT/verificacao-sem-chave.json" 2>"$OUT/verificacao-sem-chave.log"
SEM_CHAVE=$?
set -e
[ "$SEM_CHAVE" -ne 0 ] || { log "controle negativo falhou: verificação aprovou sem a chave"; exit 1; }
log "Controle negativo: sem a chave Fernet a verificação reprova (saída $SEM_CHAVE)"

# Nenhum banco foi apagado: o vazio original foi preservado pela troca.
BANCOS=$(docker exec "$ID-db2" psql -U postgres -Atc "SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database WHERE datname LIKE 'rastreamento%'")

# ── 7. Relatório ──────────────────────────────────────────────────────────────
mf() { sed -n "s/^  \"$1\": \"\{0,1\}\([^\",]*\)\"\{0,1\},\{0,1\}$/\1/p" "$OUT/backup-manifest.json" | head -1; }
{
  echo "# Exercício de recuperação $ID"
  echo
  echo "- Commit do código testado: \`$GIT_SHA\` (arquivos não commitados em backup/ e backend/: $GIT_DIRTY)"
  echo "- PostgreSQL servidor: $(mf server_version); $(mf pg_dump_version)"
  echo "- Revisão Alembic no backup: \`$(mf alembic_revision)\`"
  echo "- Execução: \`$RUN\` — dump \`$(mf db_artifact)\` sha256 \`$(mf db_plain_sha256)\` ($(mf db_plain_bytes) bytes)"
  echo "- Tabelas: $(mf tables); linhas: $(mf rows_total); objetos: $(mf minio_objects) ($(mf minio_bytes) bytes); referências obrigatórias: $(mf object_refs_required), ausentes: $(mf object_refs_missing)"
  echo "- Impressão digital da chave Fernet no manifest: \`$(mf fernet_key_fingerprint)\`"
  echo "- Bancos no servidor novo ao final: \`$BANCOS\` (o banco vazio original foi preservado pela troca)"
  echo "- Controle negativo sem chave Fernet: saída $SEM_CHAVE (reprovado, como esperado)"
  echo
  echo "## Tempos (segundos)"
  echo
  echo "| Etapa | s |"; echo "|---|---|"
  while IFS=$'\t' read -r n s; do echo "| $n | $s |"; done < "$TEMPOS"
  echo "| **RTO medido** (servidores novos → verificação aprovada) | **$((T_RTO_FIM - T_RTO))** |"
  echo "| Janela de perda no ensaio (início do backup → desastre) | $((T_DESASTRE - T_BACKUP)) |"
  echo
  echo "## Saldo financeiro restaurado (centavos)"
  echo
  echo '```json'
  cat "$OUT/verificacao.json"
  echo
  echo '```'
  echo
  echo "## Inventário de cobranças na origem (mesmo snapshot do dump)"
  echo
  echo '```'
  awk -F'\t' '$1=="billings"' "$OUT/backup-inventory.tsv"
  echo '```'
} > "$OUT/relatorio.md"
log "Relatório: $OUT/relatorio.md"
