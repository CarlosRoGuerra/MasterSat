#!/usr/bin/env bash
# =============================================================================
# backup.sh — Backup verificável do Mastersat
#
# Cada execução gera um diretório autocontido em $BACKUP_DIR/runs/<AAAAMMDD_HHMMSS>/
# (horário UTC):
#
#   db.dump            pg_dump --format=custom (ou db.dump.gpg se cifrado)
#   inventory.tsv      contagem de linhas, somas numeric e revisão Alembic,
#                      calculadas DENTRO do mesmo snapshot do pg_dump
#   object-refs.tsv    chaves de objeto que o banco referencia (mesmo snapshot)
#   minio/             cópia dos objetos do bucket
#   minio-objects.tsv  chave, bytes e sha256 de cada objeto copiado
#   manifest.json      formato, versões, commit, checksums, resumo, tempos
#   SHA256SUMS         checksums verificáveis com `sha256sum -c`
#   backup.log         log desta execução (sem segredos)
#   RESULT             STATUS=OK ou STATUS=FALHA + lista de erros (escrito por último)
#
# Ordem: dump do banco → objetos → verificação de referências → manifest →
# envio externo (rclone) + conferência → retenção → RESULT.
#
# Falhas NÃO são engolidas: qualquer etapa com problema entra em RESULT,
# dispara alerta e faz o script sair com código ≠ 0.
#   0  sucesso completo
#   1  artefato gerado, mas alguma etapa falhou (objetos, envio, retenção...)
#   2  falha fatal — nenhum dump utilizável foi produzido
#
# O snapshot de objetos NÃO é atômico com o dump: o banco é congelado num
# snapshot exportado; os objetos são copiados logo depois. Por isso o script
# verifica que toda referência obrigatória do snapshot existe na cópia.
#
# Variáveis de ambiente:
#   POSTGRES_HOST, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
#   BACKUP_DIR                 diretório local (padrão /backup)
#   BACKUP_KEEP_DAYS|KEEP_DAYS retenção local em dias (padrão 30)
#   BACKUP_MIN_KEEP            execuções OK mantidas mesmo se antigas (padrão 7)
#   BACKUP_MINIO_KEEP_RUNS     execuções que mantêm a cópia local de minio/ (padrão 7)
#   BACKUP_MINIO_SOURCE        origem rclone dos objetos, ex. "minio_src:rastreamento"
#   MINIO_MC_ALIAS             alternativa: alias do `mc` (usa MINIO_BUCKET)
#   BACKUP_SKIP_MINIO=1        pula objetos (só dev; fica registrado como pulado)
#   RCLONE_REMOTE              destino externo, ex. "b2:mastersat-backups"
#   BACKUP_OFFSITE_REQUIRED    1 (padrão) = sem destino externo conta como falha
#   BACKUP_REMOTE_KEEP_DAYS    retenção das execuções no destino externo (padrão 90)
#   BACKUP_ENCRYPT_KEY         destinatário GPG do dump (opcional)
#   ALERT_WEBHOOK              webhook Discord/Slack para alertas (opcional)
#   MASTERSAT_GIT_SHA          commit da aplicação em produção (registrado no manifest)
#   BACKUP_FERNET_FINGERPRINT  impressão digital da chave Fernet (vai ao manifest,
#                              para saber qual chave do cofre decifra este dump).
#                              Na falta dela, é calculada de AILOS_TOKEN_ENCRYPTION_KEY
#                              se estiver no ambiente — a chave em si nunca é gravada.
#   BACKUP_GPG_PUBLIC_KEY_FILE chave PÚBLICA a importar antes de cifrar (opcional)
#   RCLONE_BIN                 binário do rclone (padrão "rclone")
# =============================================================================

set -Eeuo pipefail
umask 077

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

# ── Configurações ─────────────────────────────────────────────────────────────
BACKUP_DIR="${BACKUP_DIR:-/backup}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-${KEEP_DAYS:-30}}"
BACKUP_MIN_KEEP="${BACKUP_MIN_KEEP:-7}"
BACKUP_MINIO_KEEP_RUNS="${BACKUP_MINIO_KEEP_RUNS:-7}"
BACKUP_OFFSITE_REQUIRED="${BACKUP_OFFSITE_REQUIRED:-1}"
BACKUP_REMOTE_KEEP_DAYS="${BACKUP_REMOTE_KEEP_DAYS:-90}"
POSTGRES_HOST="${POSTGRES_HOST:-db}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-rastreamento}"
RCLONE_REMOTE="${RCLONE_REMOTE:-}"
RCLONE_BIN="${RCLONE_BIN:-rclone}"
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"
MINIO_BUCKET="${MINIO_BUCKET:-rastreamento}"

TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
RUN_DIR="${BACKUP_DIR}/runs/${TIMESTAMP}"
LOG_FILE=/dev/null
ERRORS=()
OFFSITE_BLOCKED=0
OFFSITE_STATUS="nao_configurado"
MINIO_METHOD="nenhum"
MINIO_STATUS="nao_executado"
SNAP_PID=""
SNAP_FD=""

export PGPASSWORD="${POSTGRES_PASSWORD:-}"
PG_CONN=(-h "$POSTGRES_HOST" -U "$POSTGRES_USER" --no-password)

# ── Funções auxiliares ────────────────────────────────────────────────────────

log() { echo "[$(date -u '+%H:%M:%S')] $*" | tee -a "$LOG_FILE" >&2; }
err() { ERRORS+=("$*"); log "ERRO: $*"; }
now() { date -u +%s; }

_notify() {
  [ -n "$ALERT_WEBHOOK" ] || return 0
  local body
  body="{\"content\":$(json_str "$1")}"
  if ! curl -fsS --max-time 20 -X POST "$ALERT_WEBHOOK" \
      -H 'Content-Type: application/json' -d "$body" >/dev/null 2>>"$LOG_FILE"; then
    # Não altera o código de saída, mas fica no log: alerta que não chega
    # precisa ser visível em quem lê o log/RESULT.
    log "AVISO: webhook de alerta não respondeu com sucesso"
  fi
}

close_snapshot() {
  if [ -n "$SNAP_FD" ]; then
    trap '' PIPE
    printf 'COMMIT;\n' 1>&"$SNAP_FD" 2>/dev/null || true
    trap - PIPE
    exec {SNAP_FD}>&- || true
    SNAP_FD=""
  fi
  if [ -n "$SNAP_PID" ]; then
    wait "$SNAP_PID" 2>/dev/null || true
    SNAP_PID=""
  fi
}

fatal() {
  err "$*"
  close_snapshot
  write_result "FALHA"
  _notify "🚨 Backup Mastersat FALHOU (${TIMESTAMP}) — nenhum dump utilizável: $*"
  exit 2
}

write_result() {
  local status="$1"
  [ -d "$RUN_DIR" ] || return 0
  {
    echo "STATUS=$status"
    echo "TIMESTAMP=$TIMESTAMP"
    echo "OFFSITE=$OFFSITE_STATUS"
    echo "MINIO=$MINIO_STATUS"
    echo "ERROS=${#ERRORS[@]}"
    for e in "${ERRORS[@]}"; do echo "- $e"; done
  } > "$RUN_DIR/RESULT"
}

trap 'close_snapshot' EXIT

# Executa SQL (stdin) no snapshot exportado — mesma visão de dados do pg_dump.
run_in_snapshot() {
  { printf "BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY;\nSET TRANSACTION SNAPSHOT '%s';\n" "$SNAPSHOT_ID"
    cat
    printf 'COMMIT;\n'
  } | psql -X -q -At -F $'\t' -v ON_ERROR_STOP=1 "${PG_CONN[@]}" -d "$POSTGRES_DB"
}

# ── Início ────────────────────────────────────────────────────────────────────

T_START=$(now)
# mkdir sem -p no diretório da execução: se já existir (duas execuções no
# mesmo segundo), falha em vez de misturar arquivos de execuções diferentes.
if ! mkdir -p "$BACKUP_DIR/runs" 2>/dev/null || ! mkdir "$RUN_DIR" 2>/dev/null \
    || ! touch "$RUN_DIR/backup.log" 2>/dev/null; then
  echo "[$(date -u '+%H:%M:%S')] ERRO: não foi possível criar ${RUN_DIR} (permissão ou execução simultânea)" >&2
  _notify "🚨 Backup Mastersat FALHOU (${TIMESTAMP}): não foi possível criar ${RUN_DIR}"
  exit 2
fi
LOG_FILE="$RUN_DIR/backup.log"
log "===== Backup Mastersat — ${TIMESTAMP} UTC ====="

# ── 1. Banco: snapshot exportado + pg_dump + inventário ──────────────────────

log "→ Abrindo snapshot consistente do banco ${POSTGRES_DB}@${POSTGRES_HOST}..."
SNAP_FILE="$RUN_DIR/.snapshot"
exec {SNAP_FD}> >(psql -X -q -At -v ON_ERROR_STOP=1 "${PG_CONN[@]}" -d "$POSTGRES_DB" >/dev/null 2>>"$LOG_FILE")
SNAP_PID=$!
# Sem isto, escrever num psql que já morreu (senha errada, banco fora) mata
# o script por SIGPIPE antes de registrar o erro e alertar.
trap '' PIPE
SNAP_WRITE_OK=1
printf 'BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY;\n\\o %s\nSELECT pg_export_snapshot();\nSHOW server_version;\n\\o\n' "$SNAP_FILE" 1>&"$SNAP_FD" \
  || SNAP_WRITE_OK=0
trap - PIPE
[ "$SNAP_WRITE_OK" = "1" ] || fatal "não foi possível falar com o psql"

for _ in $(seq 1 300); do
  [ -f "$SNAP_FILE" ] && [ "$(wc -l < "$SNAP_FILE")" -ge 2 ] && break
  kill -0 "$SNAP_PID" 2>/dev/null || break
  sleep 0.2
done
SNAPSHOT_ID=$(sed -n 1p "$SNAP_FILE" 2>/dev/null || true)
SERVER_VERSION=$(sed -n 2p "$SNAP_FILE" 2>/dev/null || true)
rm -f "$SNAP_FILE"
[ -n "$SNAPSHOT_ID" ] || fatal "não foi possível abrir snapshot no banco (conexão/credenciais?)"
log "✓ Snapshot ${SNAPSHOT_ID} (PostgreSQL ${SERVER_VERSION})"

T_DB=$(now)
DB_FILE="$RUN_DIR/db.dump"
log "→ pg_dump (formato custom)..."
if ! pg_dump "${PG_CONN[@]}" -d "$POSTGRES_DB" \
    --format=custom --compress=6 --snapshot="$SNAPSHOT_ID" \
    --file="$DB_FILE" 2>>"$LOG_FILE"; then
  rm -f "$DB_FILE"
  fatal "pg_dump falhou"
fi

log "→ Inventário e referências de objetos no mesmo snapshot..."
inventory_sql | run_in_snapshot > "$RUN_DIR/inventory.tsv" 2>>"$LOG_FILE" \
  || fatal "inventário do banco falhou"
object_refs_sql | run_in_snapshot > "$RUN_DIR/object-refs.tsv" 2>>"$LOG_FILE" \
  || fatal "leitura das referências de objetos falhou"
close_snapshot

# O dump só vale se puder ser lido até o fim pelo leitor correto.
DB_FORMAT=$(detect_dump_format "$DB_FILE")
[ "$DB_FORMAT" = "custom" ] || fatal "dump gerado em formato inesperado: $DB_FORMAT"
validate_dump_readable "$DB_FILE" custom 2>>"$LOG_FILE" || fatal "dump gerado não pôde ser lido integralmente por pg_restore"
DB_PLAIN_SHA=$(sha256_of "$DB_FILE")
DB_PLAIN_BYTES=$(stat -c %s "$DB_FILE")
T_DB_END=$(now)
log "✓ Banco salvo: db.dump (${DB_PLAIN_BYTES} bytes, sha256 ${DB_PLAIN_SHA:0:16}…)"

DB_ARTIFACT="db.dump"
DB_ENCRYPTION="nenhuma"
if [ -n "${BACKUP_ENCRYPT_KEY:-}" ]; then
  log "→ Cifrando dump com GPG..."
  if [ -n "${BACKUP_GPG_PUBLIC_KEY_FILE:-}" ]; then
    gpg --batch --import "$BACKUP_GPG_PUBLIC_KEY_FILE" 2>>"$LOG_FILE" \
      || log "AVISO: não foi possível importar ${BACKUP_GPG_PUBLIC_KEY_FILE}"
  fi
  if gpg --batch --yes --trust-model always --recipient "$BACKUP_ENCRYPT_KEY" \
      --output "$DB_FILE.gpg" --encrypt "$DB_FILE" 2>>"$LOG_FILE"; then
    rm -f "$DB_FILE"
    DB_ARTIFACT="db.dump.gpg"
    DB_ENCRYPTION="gpg"
  else
    rm -f "$DB_FILE.gpg"
    # Antes o script mantinha o arquivo aberto e seguia enviando-o para a
    # nuvem. Agora: fica só local e o envio externo do dump é bloqueado.
    err "criptografia GPG falhou — dump mantido só localmente, sem envio externo"
    OFFSITE_BLOCKED=1
  fi
fi

# ── 2. Objetos (MinIO) ────────────────────────────────────────────────────────

T_MINIO=$(now)
MINIO_DIR="$RUN_DIR/minio"
if [ -n "${BACKUP_MINIO_SOURCE:-}" ]; then
  MINIO_METHOD="rclone"
  log "→ Copiando objetos de ${BACKUP_MINIO_SOURCE}..."
  if "$RCLONE_BIN" copy "$BACKUP_MINIO_SOURCE" "$MINIO_DIR" --transfers=4 2>>"$LOG_FILE"; then
    MINIO_STATUS="copiado"
  else
    err "cópia dos objetos (rclone) falhou"
    MINIO_STATUS="falhou"
  fi
elif [ -n "${MINIO_MC_ALIAS:-}" ] && command -v mc >/dev/null 2>&1; then
  MINIO_METHOD="mc"
  log "→ Espelhando objetos com mc (${MINIO_MC_ALIAS}/${MINIO_BUCKET})..."
  if mc mirror --preserve "${MINIO_MC_ALIAS}/${MINIO_BUCKET}" "$MINIO_DIR" >>"$LOG_FILE" 2>&1; then
    MINIO_STATUS="copiado"
  else
    err "espelhamento dos objetos (mc) falhou"
    MINIO_STATUS="falhou"
  fi
elif [ "${BACKUP_SKIP_MINIO:-0}" = "1" ]; then
  MINIO_STATUS="pulado"
  log "ℹ BACKUP_SKIP_MINIO=1 — objetos NÃO copiados (execução não serve para recuperação completa)"
else
  err "origem dos objetos não configurada (BACKUP_MINIO_SOURCE ou MINIO_MC_ALIAS)"
  MINIO_STATUS="nao_configurado"
fi
mkdir -p "$MINIO_DIR"

object_inventory "$MINIO_DIR" > "$RUN_DIR/minio-objects.tsv"
MINIO_COUNT=$(wc -l < "$RUN_DIR/minio-objects.tsv" | tr -d ' ')
MINIO_BYTES=$(awk -F'\t' '{s+=$2} END {printf "%d", s}' "$RUN_DIR/minio-objects.tsv")
REFS_REQUIRED=$(awk -F'\t' '$1=="obrigatorio"' "$RUN_DIR/object-refs.tsv" | wc -l | tr -d ' ')
REFS_MISSING=0
if [ "$MINIO_STATUS" != "pulado" ]; then
  if ! check_object_refs "$RUN_DIR/object-refs.tsv" "$RUN_DIR/minio-objects.tsv" "$RUN_DIR/objects-missing.tsv"; then
    REFS_MISSING=$(wc -l < "$RUN_DIR/objects-missing.tsv" | tr -d ' ')
    err "${REFS_MISSING} objeto(s) referenciado(s) pelo banco ausente(s) na cópia (ver objects-missing.tsv)"
    [ "$MINIO_STATUS" = "copiado" ] && MINIO_STATUS="incompleto"
  fi
fi
T_MINIO_END=$(now)
log "✓ Objetos: ${MINIO_COUNT} arquivo(s), ${MINIO_BYTES} bytes; referências obrigatórias: ${REFS_REQUIRED}, ausentes: ${REFS_MISSING}"

# ── 3. Manifest + checksums ──────────────────────────────────────────────────

ROWS_TOTAL=$(awk -F'\t' '$1=="rows" {s+=$3} END {printf "%d", s}' "$RUN_DIR/inventory.tsv")
TABLES_TOTAL=$(awk -F'\t' '$1=="rows"' "$RUN_DIR/inventory.tsv" | wc -l | tr -d ' ')
ALEMBIC_REV=$(awk -F'\t' '$1=="alembic" {print $3}' "$RUN_DIR/inventory.tsv")
PG_DUMP_VERSION=$(pg_dump --version | head -1)
FERNET_FP="${BACKUP_FERNET_FINGERPRINT:-}"
[ -z "$FERNET_FP" ] || [[ "$FERNET_FP" == sha256:* ]] || FERNET_FP="sha256:$FERNET_FP"
if [ -z "$FERNET_FP" ] && [ -n "${AILOS_TOKEN_ENCRYPTION_KEY:-}" ]; then
  FERNET_FP="sha256:$(printf '%s' "$AILOS_TOKEN_ENCRYPTION_KEY" | sha256sum | cut -c1-16)"
fi

file_entry() {
  local name="$1"
  [ -f "$RUN_DIR/$name" ] || return 0
  printf '    %s: {"bytes": %s, "sha256": "%s"}' "$(json_str "$name")" \
    "$(stat -c %s "$RUN_DIR/$name")" "$(sha256_of "$RUN_DIR/$name")"
}

{
  echo "{"
  echo "  \"schema_version\": $MANIFEST_SCHEMA_VERSION,"
  echo "  \"timestamp_utc\": $(json_str "$TIMESTAMP"),"
  echo "  \"app_git_sha\": $(json_str "${MASTERSAT_GIT_SHA:-desconhecido}"),"
  echo "  \"alembic_revision\": $(json_str "$ALEMBIC_REV"),"
  echo "  \"db_name\": $(json_str "$POSTGRES_DB"),"
  echo "  \"db_host\": $(json_str "$POSTGRES_HOST"),"
  echo "  \"server_version\": $(json_str "$SERVER_VERSION"),"
  echo "  \"pg_dump_version\": $(json_str "$PG_DUMP_VERSION"),"
  echo "  \"snapshot_id\": $(json_str "$SNAPSHOT_ID"),"
  echo "  \"db_artifact\": $(json_str "$DB_ARTIFACT"),"
  echo "  \"db_format\": \"custom\","
  echo "  \"db_encryption\": $(json_str "$DB_ENCRYPTION"),"
  echo "  \"db_plain_sha256\": $(json_str "$DB_PLAIN_SHA"),"
  echo "  \"db_plain_bytes\": $DB_PLAIN_BYTES,"
  echo "  \"gpg_recipient\": $(json_str "${BACKUP_ENCRYPT_KEY:-}"),"
  echo "  \"fernet_key_fingerprint\": $(json_str "$FERNET_FP"),"
  echo "  \"tables\": $TABLES_TOTAL,"
  echo "  \"rows_total\": $ROWS_TOTAL,"
  echo "  \"minio_method\": $(json_str "$MINIO_METHOD"),"
  echo "  \"minio_status\": $(json_str "$MINIO_STATUS"),"
  echo "  \"minio_source\": $(json_str "${BACKUP_MINIO_SOURCE:-${MINIO_MC_ALIAS:+${MINIO_MC_ALIAS}/${MINIO_BUCKET}}}"),"
  echo "  \"minio_objects\": $MINIO_COUNT,"
  echo "  \"minio_bytes\": $MINIO_BYTES,"
  echo "  \"object_refs_required\": $REFS_REQUIRED,"
  echo "  \"object_refs_missing\": $REFS_MISSING,"
  echo "  \"consistency\": \"banco e inventario no mesmo snapshot exportado; objetos copiados depois do snapshot (nao atomico) e conferidos contra as referencias do snapshot\","
  echo "  \"timings_seconds\": {\"db\": $((T_DB_END - T_DB)), \"minio\": $((T_MINIO_END - T_MINIO))},"
  echo "  \"files\": {"
  first=1
  for f in "$DB_ARTIFACT" inventory.tsv object-refs.tsv minio-objects.tsv objects-missing.tsv; do
    entry=$(file_entry "$f")
    [ -n "$entry" ] || continue
    [ $first -eq 1 ] || echo ","
    printf '%s' "$entry"
    first=0
  done
  echo ""
  echo "  }"
  echo "}"
} > "$RUN_DIR/manifest.json"

SUM_FILES=("$DB_ARTIFACT" inventory.tsv object-refs.tsv minio-objects.tsv manifest.json)
[ -f "$RUN_DIR/objects-missing.tsv" ] && SUM_FILES+=(objects-missing.tsv)
(cd "$RUN_DIR" && sha256sum -- "${SUM_FILES[@]}" > SHA256SUMS)
log "✓ manifest.json e SHA256SUMS gravados"

# ── 4. Envio externo ──────────────────────────────────────────────────────────

T_OFF=$(now)
RCLONE_FILTER=(--exclude "minio/**" --exclude "RESULT" --exclude "backup.log")
if [ -z "$RCLONE_REMOTE" ]; then
  if [ "$BACKUP_OFFSITE_REQUIRED" = "1" ]; then
    err "RCLONE_REMOTE não configurado — não há cópia fora do servidor"
  else
    log "ℹ RCLONE_REMOTE não configurado (BACKUP_OFFSITE_REQUIRED=0) — backup apenas local"
  fi
elif ! command -v "$RCLONE_BIN" >/dev/null 2>&1; then
  err "RCLONE_REMOTE configurado mas rclone não está instalado"
  OFFSITE_STATUS="falhou"
elif [ "$OFFSITE_BLOCKED" = "1" ]; then
  OFFSITE_STATUS="bloqueado"
else
  REMOTE_RUN="${RCLONE_REMOTE}/runs/${TIMESTAMP}"
  REMOTE_OBJECTS="${RCLONE_REMOTE}/minio/objects"
  log "→ Enviando execução para ${REMOTE_RUN}..."
  OFFSITE_STATUS="ok"
  if ! "$RCLONE_BIN" copy "$RUN_DIR" "$REMOTE_RUN" "${RCLONE_FILTER[@]}" 2>>"$LOG_FILE"; then
    err "envio do banco/manifest (rclone copy) falhou"
    OFFSITE_STATUS="falhou"
  # Código 0 do copy não basta: conferimos que tudo chegou íntegro.
  elif ! "$RCLONE_BIN" check "$RUN_DIR" "$REMOTE_RUN" --one-way "${RCLONE_FILTER[@]}" 2>>"$LOG_FILE"; then
    err "conferência do envio (rclone check) falhou — cópia externa incompleta ou divergente"
    OFFSITE_STATUS="falhou"
  fi
  if [ "$MINIO_STATUS" != "pulado" ] && [ "$MINIO_COUNT" -gt 0 ]; then
    log "→ Enviando objetos para ${REMOTE_OBJECTS}..."
    # copy (não sync): objetos apagados na origem continuam no destino.
    if ! "$RCLONE_BIN" copy "$MINIO_DIR" "$REMOTE_OBJECTS" --transfers=4 2>>"$LOG_FILE"; then
      err "envio dos objetos (rclone copy) falhou"
      OFFSITE_STATUS="falhou"
    elif ! "$RCLONE_BIN" check "$MINIO_DIR" "$REMOTE_OBJECTS" --one-way 2>>"$LOG_FILE"; then
      err "conferência dos objetos enviados (rclone check) falhou"
      OFFSITE_STATUS="falhou"
    fi
  fi
fi
T_OFF_END=$(now)

# ── 5. Retenção ───────────────────────────────────────────────────────────────
# Remove execuções mais antigas que KEEP_DAYS, mas NUNCA as BACKUP_MIN_KEEP
# execuções OK mais recentes — se o backup parar de funcionar por semanas,
# a retenção não pode apagar o último backup bom.

run_is_ok() { grep -qx 'STATUS=OK' "$1/RESULT" 2>/dev/null; }

log "→ Retenção local (${KEEP_DAYS} dias, mínimo ${BACKUP_MIN_KEEP} execuções OK)..."
CUTOFF=$(date -u -d "-${KEEP_DAYS} days" +%Y%m%d_%H%M%S)
mapfile -t ALL_RUNS < <(find "$BACKUP_DIR/runs" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r)
KEEP=("$TIMESTAMP")
ok_seen=0
for r in "${ALL_RUNS[@]}"; do
  if [ $ok_seen -lt "$BACKUP_MIN_KEEP" ] && run_is_ok "$BACKUP_DIR/runs/$r"; then
    KEEP+=("$r"); ok_seen=$((ok_seen + 1))
  fi
done
is_kept() { local x; for x in "${KEEP[@]}"; do [ "$x" = "$1" ] && return 0; done; return 1; }
for r in "${ALL_RUNS[@]}"; do
  [[ "$r" < "$CUTOFF" ]] || continue
  is_kept "$r" && continue
  rm -rf -- "${BACKUP_DIR:?}/runs/$r" || err "retenção: não foi possível remover runs/$r"
done
# A cópia local de objetos é a parte pesada: só as N execuções mais novas a mantêm.
minio_kept=0
for r in "${ALL_RUNS[@]}"; do
  [ -d "$BACKUP_DIR/runs/$r/minio" ] || continue
  minio_kept=$((minio_kept + 1))
  [ "$r" = "$TIMESTAMP" ] && continue
  if [ $minio_kept -gt "$BACKUP_MINIO_KEEP_RUNS" ]; then
    rm -rf -- "${BACKUP_DIR:?}/runs/$r/minio" || err "retenção: não foi possível remover runs/$r/minio"
  fi
done
# Dumps do formato antigo (db/*.sql.gz): só saem quando já existem execuções
# novas OK suficientes para substituí-los.
if [ -d "$BACKUP_DIR/db" ] && [ $ok_seen -ge "$BACKUP_MIN_KEEP" ]; then
  find "$BACKUP_DIR/db" -maxdepth 1 \( -name '*.sql.gz' -o -name '*.sql.gz.gpg' \) -mtime +"$KEEP_DAYS" -delete \
    || err "retenção: falha ao remover dumps legados"
fi

if [ "$OFFSITE_STATUS" = "ok" ]; then
  log "→ Retenção externa (${BACKUP_REMOTE_KEEP_DAYS} dias, mínimo ${BACKUP_MIN_KEEP} execuções)..."
  RCUTOFF=$(date -u -d "-${BACKUP_REMOTE_KEEP_DAYS} days" +%Y%m%d_%H%M%S)
  if REMOTE_LIST=$("$RCLONE_BIN" lsf --dirs-only "${RCLONE_REMOTE}/runs" 2>>"$LOG_FILE"); then
    mapfile -t REMOTE_RUNS < <(printf '%s\n' "$REMOTE_LIST" | tr -d '/' | grep -v '^$' | sort -r)
    idx=0
    for r in "${REMOTE_RUNS[@]}"; do
      idx=$((idx + 1))
      [ $idx -le "$BACKUP_MIN_KEEP" ] && continue
      [[ "$r" < "$RCUTOFF" ]] || continue
      "$RCLONE_BIN" purge "${RCLONE_REMOTE}/runs/$r" 2>>"$LOG_FILE" \
        || err "retenção externa: falha ao remover runs/$r"
    done
  else
    err "retenção externa: não foi possível listar ${RCLONE_REMOTE}/runs"
  fi
fi

# ── 6. Resultado ──────────────────────────────────────────────────────────────

T_END=$(now)
FINAL="OK"
[ ${#ERRORS[@]} -eq 0 ] || FINAL="FALHA"
log "===== Backup ${FINAL} em $((T_END - T_START))s (banco $((T_DB_END - T_DB))s, objetos $((T_MINIO_END - T_MINIO))s, envio $((T_OFF_END - T_OFF))s) ====="
write_result "$FINAL"

# RESULT e log sobem por último: execução remota sem RESULT = incompleta.
if [ "$OFFSITE_STATUS" = "ok" ]; then
  if ! "$RCLONE_BIN" copy "$RUN_DIR" "${RCLONE_REMOTE}/runs/${TIMESTAMP}" --include RESULT --include backup.log 2>>"$LOG_FILE"; then
    err "envio do RESULT falhou — execução remota ficará sem marcador de conclusão"
    OFFSITE_STATUS="falhou"
    FINAL="FALHA"
    write_result "$FINAL"
  fi
fi

echo "$TIMESTAMP $FINAL" > "$BACKUP_DIR/last_run"
if [ "$FINAL" = "OK" ]; then
  echo "$TIMESTAMP" > "$BACKUP_DIR/last_success"
  _notify "✅ Backup Mastersat OK (${TIMESTAMP}): ${ROWS_TOTAL} linhas, ${MINIO_COUNT} objetos, envio externo: ${OFFSITE_STATUS}"
  exit 0
fi
_notify "🚨 Backup Mastersat com FALHA (${TIMESTAMP}): $(printf '%s; ' "${ERRORS[@]}")"
exit 1
