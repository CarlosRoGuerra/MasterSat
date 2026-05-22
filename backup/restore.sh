#!/usr/bin/env bash
# =============================================================================
# restore.sh — Restauração do Mastersat
#
# Uso:
#   ./restore.sh                          # usa o backup mais recente
#   ./restore.sh db/mastersat_db_XXXX.sql.gz  # usa arquivo específico
#
# ATENÇÃO: Este script substitui o banco atual. Pare o backend antes.
#   docker compose stop backend
#   ./restore.sh
#   docker compose start backend
# =============================================================================

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backup}"
POSTGRES_HOST="${POSTGRES_HOST:-db}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-rastreamento}"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ── Localiza o arquivo de backup ──────────────────────────────────────────────

if [ -n "${1:-}" ]; then
  BACKUP_FILE="$1"
  [ -f "$BACKUP_DIR/$BACKUP_FILE" ] && BACKUP_FILE="$BACKUP_DIR/$BACKUP_FILE"
else
  BACKUP_FILE=$(find "$BACKUP_DIR/db" -name "*.sql.gz" | sort -r | head -1)
  [ -z "$BACKUP_FILE" ] && { log "ERRO: Nenhum backup encontrado em $BACKUP_DIR/db"; exit 1; }
fi

[ -f "$BACKUP_FILE" ] || { log "ERRO: Arquivo não encontrado: $BACKUP_FILE"; exit 1; }

log "Arquivo selecionado: $BACKUP_FILE"
log "Banco de destino:    $POSTGRES_DB em $POSTGRES_HOST"
echo ""
read -p "Confirma a restauração? O banco atual será SOBRESCRITO. (s/N) " CONFIRM
[ "${CONFIRM:-N}" = "s" ] || { log "Restauração cancelada."; exit 0; }

# ── Descriptografa se necessário ─────────────────────────────────────────────

if [[ "$BACKUP_FILE" == *.gpg ]]; then
  log "→ Descriptografando..."
  DECRYPTED="${BACKUP_FILE%.gpg}"
  gpg --batch --yes --decrypt "$BACKUP_FILE" > "$DECRYPTED" \
    || { log "ERRO: Falha na descriptografia"; exit 1; }
  BACKUP_FILE="$DECRYPTED"
  CLEANUP_DECRYPT=true
fi

# ── Restaura o banco ──────────────────────────────────────────────────────────

export PGPASSWORD="$POSTGRES_PASSWORD"

log "→ Encerrando conexões ativas ao banco..."
psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d postgres \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$POSTGRES_DB' AND pid <> pg_backend_pid();" \
  2>/dev/null || true

log "→ Recriando banco de dados..."
psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d postgres \
  -c "DROP DATABASE IF EXISTS $POSTGRES_DB;" \
  -c "CREATE DATABASE $POSTGRES_DB;" \
  || { log "ERRO: Falha ao recriar banco"; exit 1; }

log "→ Restaurando dados (pode demorar)..."
if [[ "$BACKUP_FILE" == *.sql.gz ]]; then
  gunzip -c "$BACKUP_FILE" | psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    || { log "ERRO: Falha na restauração"; exit 1; }
else
  # formato custom do pg_dump
  pg_restore -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --no-password --verbose "$BACKUP_FILE" 2>&1 \
    | tail -20 \
    || { log "ERRO: Falha na restauração"; exit 1; }
fi

[ "${CLEANUP_DECRYPT:-false}" = "true" ] && rm -f "$BACKUP_FILE"

log "✓ Restauração concluída com sucesso!"
log ""
log "Próximos passos:"
log "  1. Verifique os dados no sistema"
log "  2. Reinicie o backend: docker compose restart backend"
