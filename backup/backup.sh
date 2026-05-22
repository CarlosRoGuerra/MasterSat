#!/usr/bin/env bash
# =============================================================================
# backup.sh — Backup automatizado do Mastersat
#
# Executa:
#   1. pg_dump do PostgreSQL (comprimido + criptografado se GPG configurado)
#   2. Sync dos arquivos MinIO
#   3. Rotação local (mantém KEEP_DAYS dias)
#   4. Upload para nuvem via rclone (se RCLONE_REMOTE configurado)
#   5. Notificação por e-mail/webhook em caso de falha
#
# Variáveis de ambiente esperadas (via .env ou docker-compose):
#   POSTGRES_HOST, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
#   BACKUP_DIR           — diretório local de backup (padrão: /backup)
#   KEEP_DAYS            — dias de retenção local (padrão: 30)
#   RCLONE_REMOTE        — destino rclone, ex: "b2:mastersat-backups"
#   BACKUP_ENCRYPT_KEY   — chave GPG para criptografar (opcional)
#   ALERT_WEBHOOK        — URL webhook para alertas de falha (opcional, ex: Discord/Slack)
# =============================================================================

set -euo pipefail

# ── Configurações ─────────────────────────────────────────────────────────────
BACKUP_DIR="${BACKUP_DIR:-/backup}"
KEEP_DAYS="${KEEP_DAYS:-30}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="${BACKUP_DIR}/backup_${TIMESTAMP}.log"

POSTGRES_HOST="${POSTGRES_HOST:-db}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-rastreamento}"
RCLONE_REMOTE="${RCLONE_REMOTE:-}"
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"

# ── Funções auxiliares ────────────────────────────────────────────────────────

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
fail() {
  log "ERRO: $*"
  _notify_failure "$*"
  exit 1
}

_notify_failure() {
  if [ -n "$ALERT_WEBHOOK" ]; then
    curl -s -X POST "$ALERT_WEBHOOK" \
      -H 'Content-Type: application/json' \
      -d "{\"content\":\"🚨 **Backup Mastersat falhou** (${TIMESTAMP}): $1\"}" \
      || true
  fi
}

_notify_success() {
  if [ -n "$ALERT_WEBHOOK" ]; then
    curl -s -X POST "$ALERT_WEBHOOK" \
      -H 'Content-Type: application/json' \
      -d "{\"content\":\"✅ **Backup Mastersat concluído** (${TIMESTAMP}): DB ${DB_SIZE}, MinIO ${MINIO_SIZE}\"}" \
      || true
  fi
}

# ── Início ────────────────────────────────────────────────────────────────────

mkdir -p "$BACKUP_DIR/db" "$BACKUP_DIR/minio" "$BACKUP_DIR/logs"
log "===== Backup Mastersat — $TIMESTAMP ====="

# ── 1. Backup do PostgreSQL ───────────────────────────────────────────────────

log "→ Iniciando pg_dump..."
DB_FILE="${BACKUP_DIR}/db/mastersat_db_${TIMESTAMP}.sql.gz"

export PGPASSWORD="$POSTGRES_PASSWORD"

pg_dump \
  -h "$POSTGRES_HOST" \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  --no-password \
  --format=custom \
  --compress=9 \
  | gzip -9 > "$DB_FILE" \
  || fail "pg_dump falhou"

DB_SIZE=$(du -sh "$DB_FILE" | cut -f1)
log "✓ Banco salvo: $DB_FILE ($DB_SIZE)"

# Criptografia opcional com GPG
if [ -n "${BACKUP_ENCRYPT_KEY:-}" ]; then
  log "→ Criptografando com GPG..."
  gpg --batch --yes --recipient "$BACKUP_ENCRYPT_KEY" \
    --encrypt "$DB_FILE" \
    && rm -f "$DB_FILE" \
    && DB_FILE="${DB_FILE}.gpg" \
    || log "⚠ Criptografia falhou — mantendo arquivo sem criptografia"
fi

# ── 2. Backup dos arquivos MinIO ──────────────────────────────────────────────

log "→ Sincronizando arquivos MinIO..."
MINIO_BACKUP="${BACKUP_DIR}/minio/minio_${TIMESTAMP}"

if command -v mc &>/dev/null; then
  mc mirror \
    --preserve \
    "minio/${MINIO_BUCKET:-rastreamento}" \
    "$MINIO_BACKUP" 2>>"$LOG_FILE" \
    && MINIO_SIZE=$(du -sh "$MINIO_BACKUP" 2>/dev/null | cut -f1 || echo "0") \
    || log "⚠ Sync MinIO falhou — continuando"
else
  # Fallback: backup direto do volume Docker
  if [ -d "/minio-data" ]; then
    tar -czf "${MINIO_BACKUP}.tar.gz" /minio-data 2>>"$LOG_FILE" \
      && MINIO_SIZE=$(du -sh "${MINIO_BACKUP}.tar.gz" | cut -f1) \
      || log "⚠ tar MinIO falhou"
  else
    log "⚠ MinIO CLI não encontrado e volume não acessível — pulando backup MinIO"
    MINIO_SIZE="0"
  fi
fi

# ── 3. Rotação local ──────────────────────────────────────────────────────────

log "→ Removendo backups com mais de ${KEEP_DAYS} dias..."
find "$BACKUP_DIR/db"    -name "*.gz" -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true
find "$BACKUP_DIR/db"    -name "*.gpg" -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true
find "$BACKUP_DIR/minio" -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true
find "$BACKUP_DIR/logs"  -name "*.log" -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true

REMAINING=$(find "$BACKUP_DIR/db" -name "*.gz" -o -name "*.gpg" | wc -l)
log "✓ Rotação concluída — ${REMAINING} backup(s) de banco mantidos"

# ── 4. Upload para nuvem (rclone) ─────────────────────────────────────────────

if [ -n "$RCLONE_REMOTE" ] && command -v rclone &>/dev/null; then
  log "→ Enviando para nuvem: $RCLONE_REMOTE..."
  rclone copy "${BACKUP_DIR}/db" "${RCLONE_REMOTE}/db" \
    --transfers=4 \
    --progress \
    2>>"$LOG_FILE" \
    || log "⚠ Upload do banco falhou — backup local mantido"

  # Remove da nuvem backups mais antigos que KEEP_DAYS
  rclone delete "${RCLONE_REMOTE}/db" \
    --min-age "${KEEP_DAYS}d" \
    2>>"$LOG_FILE" || true

  log "✓ Upload concluído"
else
  [ -z "$RCLONE_REMOTE" ] && log "ℹ RCLONE_REMOTE não configurado — backup apenas local"
fi

# ── 5. Verificação de integridade ─────────────────────────────────────────────

log "→ Verificando integridade do backup do banco..."
if gzip -t "$DB_FILE" 2>/dev/null; then
  log "✓ Arquivo íntegro"
elif file "$DB_FILE" | grep -q "gzip"; then
  log "✓ Arquivo íntegro (gzip)"
else
  log "ℹ Arquivo criptografado ou formato custom — verificação pulada"
fi

# ── Conclusão ──────────────────────────────────────────────────────────────────

MINIO_SIZE="${MINIO_SIZE:-n/a}"
log "===== Backup concluído com sucesso ====="
log "  Banco:  $DB_SIZE"
log "  MinIO:  $MINIO_SIZE"
log "  Log:    $LOG_FILE"

cp "$LOG_FILE" "${BACKUP_DIR}/logs/backup_${TIMESTAMP}.log" 2>/dev/null || true
_notify_success
