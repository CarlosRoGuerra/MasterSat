#!/usr/bin/env bash
# =============================================================================
# restore.sh — Restauração verificável do Mastersat
#
# Princípio: nada é apagado. O banco em uso nunca é alvo de DROP; a cópia é
# restaurada num banco NOVO, validada contra o manifest e só então — em passo
# separado, com aprovação explícita — pode trocar de lugar com o atual
# (renomeando; o antigo fica preservado como <banco>_pre_restore_<data>).
#
# Uso:
#   restore.sh verificar <execução|arquivo>
#       Só confere: checksums, formato real e leitura integral do dump.
#       Não conecta em banco nenhum.
#
#   restore.sh banco <execução|arquivo> [--destino NOME]
#       Preflight completo e, se tudo passar, cria o banco NOME (padrão
#       <POSTGRES_DB>_restore_<data>), restaura com o leitor correto e compara
#       o inventário (linhas, somas monetárias, Alembic) com o manifest.
#       Recusa destino que já existe ou que seja o banco em uso.
#
#   restore.sh objetos <execução> <diretório-destino> [--para-bucket REMOTO:bucket]
#       Copia os objetos da execução para um diretório novo e confere hash de
#       cada um e as referências do banco. Com --para-bucket, envia também a
#       um bucket VAZIO (rclone) e confere.
#
#   restore.sh baixar <REMOTO/runs/AAAAMMDD_HHMMSS> <diretório-destino>
#       Traz uma execução do destino externo (inclusive objetos) e confere.
#
#   restore.sh promover --de NOME_RESTAURADO
#       Troca controlada: <POSTGRES_DB> → <POSTGRES_DB>_pre_restore_<data> e
#       NOME_RESTAURADO → <POSTGRES_DB>. Exige RESTORE_APROVACAO (quem aprovou /
#       chamado) e confirmação digitada. Pare o backend antes.
#
#   restore.sh reverter-troca --anterior NOME_PRESERVADO
#       Desfaz a troca: o banco atual volta a ter o nome de onde veio e
#       NOME_PRESERVADO volta a ser <POSTGRES_DB>. Mesmas exigências.
#
# Forma antiga: `restore.sh <arquivo>` equivale a `restore.sh banco <arquivo>`
# — agora restaura num banco novo, nunca sobre o atual.
#
# Códigos de saída: 0 ok; 1 uso/pré-condição; 2 arquivo inválido (nada foi
# criado); 3 restaurado mas divergente do manifest; 4 falha durante restauração.
# =============================================================================

set -Eeuo pipefail
umask 077

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

BACKUP_DIR="${BACKUP_DIR:-/backup}"
POSTGRES_HOST="${POSTGRES_HOST:-db}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-rastreamento}"
RCLONE_BIN="${RCLONE_BIN:-rclone}"
export PGPASSWORD="${POSTGRES_PASSWORD:-}"
PG_CONN=(-h "$POSTGRES_HOST" -U "$POSTGRES_USER" --no-password)
STAMP=$(date -u +%Y%m%d_%H%M%S)
WORK_DIR=""

log() { echo "[$(date -u '+%H:%M:%S')] $*" >&2; }
die() { local code="$1"; shift; log "ERRO: $*"; exit "$code"; }

cleanup() { [ -n "$WORK_DIR" ] && rm -rf -- "$WORK_DIR"; return 0; }
trap cleanup EXIT

psql_admin() { psql -X -q -At -v ON_ERROR_STOP=1 "${PG_CONN[@]}" -d postgres "$@"; }

db_exists() {
  [ "$(psql_admin -c "SELECT 1 FROM pg_database WHERE datname = '$1'")" = "1" ]
}

usage() { sed -n '2,/^# =====/p' "$0" | sed 's/^# \{0,1\}//' | head -n -1; }

# ── Resolução e preflight do artefato ────────────────────────────────────────
# Define: ART_DIR (execução, se houver), MANIFEST, DUMP_FILE (legível, já
# decifrado), DUMP_FORMAT. Não toca em banco algum.

resolve_artifact() {
  local arg="$1"
  [ -e "$arg" ] || [ ! -e "$BACKUP_DIR/$arg" ] || arg="$BACKUP_DIR/$arg"
  [ -e "$arg" ] || die 1 "não encontrado: $arg"
  ART_DIR=""; MANIFEST=""
  if [ -d "$arg" ]; then
    ART_DIR=$(cd "$arg" && pwd)
    [ -f "$ART_DIR/manifest.json" ] || die 2 "diretório sem manifest.json: $ART_DIR"
    MANIFEST="$ART_DIR/manifest.json"
    local art
    art=$(manifest_get "$MANIFEST" db_artifact)
    [ -n "$art" ] || die 2 "manifest sem db_artifact"
    RAW_FILE="$ART_DIR/$art"
  else
    RAW_FILE=$(cd "$(dirname "$arg")" && pwd)/$(basename "$arg")
    if [ -f "$(dirname "$RAW_FILE")/manifest.json" ]; then
      ART_DIR=$(dirname "$RAW_FILE")
      MANIFEST="$ART_DIR/manifest.json"
    fi
  fi
  [ -r "$RAW_FILE" ] || die 2 "sem permissão de leitura ou arquivo ausente: $RAW_FILE"
}

preflight() {
  resolve_artifact "$1"
  log "Artefato: $RAW_FILE"

  if [ -n "$ART_DIR" ]; then
    [ -f "$ART_DIR/SHA256SUMS" ] || die 2 "execução sem SHA256SUMS: $ART_DIR"
    log "→ Conferindo checksums (SHA256SUMS)..."
    (cd "$ART_DIR" && sha256sum --check --strict --quiet SHA256SUMS) >&2 \
      || die 2 "checksum não confere — arquivo corrompido, truncado ou alterado"
    if [ -f "$ART_DIR/RESULT" ] && ! grep -qx 'STATUS=OK' "$ART_DIR/RESULT"; then
      log "AVISO: esta execução terminou com falha (ver RESULT) — confira antes de usar:"
      sed 's/^/    /' "$ART_DIR/RESULT" >&2
    fi
  else
    log "AVISO: arquivo avulso sem manifest — integridade será conferida só pela leitura completa"
  fi

  DUMP_FILE="$RAW_FILE"
  DUMP_FORMAT=$(detect_dump_format "$DUMP_FILE")
  if [ "$DUMP_FORMAT" = "gpg" ]; then
    WORK_DIR=$(mktemp -d)
    log "→ Decifrando com GPG para diretório temporário..."
    gpg --batch --yes --output "$WORK_DIR/db.dump" --decrypt "$DUMP_FILE" \
      || die 2 "falha ao decifrar (chave GPG disponível?)"
    DUMP_FILE="$WORK_DIR/db.dump"
    DUMP_FORMAT=$(detect_dump_format "$DUMP_FILE")
  fi

  if [ -n "$MANIFEST" ]; then
    local expected_fmt expected_sha
    expected_fmt=$(manifest_get "$MANIFEST" db_format)
    [ "$DUMP_FORMAT" = "$expected_fmt" ] \
      || die 2 "formato detectado ($DUMP_FORMAT) difere do manifest ($expected_fmt)"
    expected_sha=$(manifest_get "$MANIFEST" db_plain_sha256)
    [ -z "$expected_sha" ] || [ "$(sha256_of "$DUMP_FILE")" = "$expected_sha" ] \
      || die 2 "sha256 do dump decifrado difere do manifest"
  fi

  case "$DUMP_FORMAT" in
    custom|custom+gzip|sql|sql+gzip) ;;
    *) die 2 "formato não reconhecido — não é dump do PostgreSQL: $RAW_FILE" ;;
  esac
  log "→ Formato: $DUMP_FORMAT. Lendo o arquivo inteiro com o leitor correto..."
  validate_dump_readable "$DUMP_FILE" "$DUMP_FORMAT" >&2 \
    || die 2 "dump ilegível ou truncado ($DUMP_FORMAT) — nenhum banco foi criado nem alterado"
  log "✓ Preflight aprovado"
}

# ── Subcomandos ───────────────────────────────────────────────────────────────

cmd_verificar() {
  [ $# -eq 1 ] || die 1 "uso: restore.sh verificar <execução|arquivo>"
  preflight "$1"
}

cmd_banco() {
  local src="" target=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --destino) target="${2:-}"; shift 2 ;;
      *) [ -z "$src" ] || die 1 "argumento inesperado: $1"; src="$1"; shift ;;
    esac
  done
  [ -n "$src" ] || die 1 "uso: restore.sh banco <execução|arquivo> [--destino NOME]"
  target="${target:-${POSTGRES_DB}_restore_${STAMP}}"
  valid_db_name "$target" || die 1 "nome de banco inválido: $target"
  [ "$target" != "$POSTGRES_DB" ] \
    || die 1 "destino é o banco em uso ($POSTGRES_DB). Restaure em outro nome e use 'promover'."

  # Tudo que pode reprovar o arquivo acontece ANTES de criar o destino.
  preflight "$src"

  db_exists "$target" && die 1 "o banco $target já existe — escolha outro --destino (nada foi alterado)"

  local t0; t0=$(date -u +%s)
  log "→ Criando banco novo $target..."
  psql_admin -c "CREATE DATABASE \"$target\" TEMPLATE template0" || die 4 "não foi possível criar $target"

  log "→ Restaurando ($DUMP_FORMAT) em transação única..."
  local ok=1
  case "$DUMP_FORMAT" in
    custom)
      pg_restore "${PG_CONN[@]}" -d "$target" --exit-on-error --single-transaction "$DUMP_FILE" || ok=0 ;;
    custom+gzip)
      gzip -dc "$DUMP_FILE" | pg_restore "${PG_CONN[@]}" -d "$target" --exit-on-error --single-transaction || ok=0 ;;
    sql)
      psql -X -q -v ON_ERROR_STOP=1 --single-transaction "${PG_CONN[@]}" -d "$target" -f "$DUMP_FILE" >/dev/null || ok=0 ;;
    sql+gzip)
      gzip -dc "$DUMP_FILE" | psql -X -q -v ON_ERROR_STOP=1 --single-transaction "${PG_CONN[@]}" -d "$target" >/dev/null || ok=0 ;;
  esac
  [ $ok -eq 1 ] || die 4 "restauração falhou; $target ficou vazio (transação desfeita) e pode ser inspecionado/removido. O banco em uso não foi tocado."

  local verdict="sem manifest para comparar"
  if [ -n "$ART_DIR" ] && [ -f "$ART_DIR/inventory.tsv" ]; then
    log "→ Comparando inventário restaurado com o do backup..."
    WORK_DIR=${WORK_DIR:-$(mktemp -d)}
    local got="$WORK_DIR/inventory.tsv"
    inventory_sql | psql -X -q -At -F $'\t' -v ON_ERROR_STOP=1 "${PG_CONN[@]}" -d "$target" > "$got" \
      || die 3 "não foi possível calcular inventário em $target"
    if ! diff -u "$ART_DIR/inventory.tsv" "$got" >&2; then
      die 3 "inventário de $target DIFERE do backup (linhas/somas/Alembic acima). Não promova este banco."
    fi
    verdict="inventário idêntico ao do backup ($(awk -F'\t' '$1=="rows"' "$got" | wc -l | tr -d ' ') tabelas, $(awk -F'\t' '$1=="rows"{s+=$3} END{printf "%d", s}' "$got") linhas)"
  fi

  # Marca de validação: 'promover' só aceita bancos com esta marca.
  local sha=""
  [ -z "$MANIFEST" ] || sha=$(manifest_get "$MANIFEST" db_plain_sha256)
  psql_admin -c "COMMENT ON DATABASE \"$target\" IS 'mastersat-restore validado $STAMP sha256=${sha:-sem-manifest}'" \
    || die 4 "não foi possível marcar $target como validado"

  log "✓ Restaurado em $target em $(( $(date -u +%s) - t0 ))s — $verdict"
  log "Próximos passos (nenhum deles é automático):"
  log "  1. Verificar documentos e segredos: scripts/verificar_restauracao.py (ver backup/README.md)"
  log "  2. Com aprovação operacional: RESTORE_APROVACAO='...' restore.sh promover --de $target"
  echo "$target"
}

cmd_objetos() {
  local run="" dest="" bucket=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --para-bucket) bucket="${2:-}"; shift 2 ;;
      *) if [ -z "$run" ]; then run="$1"; elif [ -z "$dest" ]; then dest="$1"; else die 1 "argumento inesperado: $1"; fi; shift ;;
    esac
  done
  [ -n "$run" ] && [ -n "$dest" ] || die 1 "uso: restore.sh objetos <execução> <diretório-destino> [--para-bucket REMOTO:bucket]"
  [ -d "$run" ] || [ ! -d "$BACKUP_DIR/$run" ] || run="$BACKUP_DIR/$run"
  [ -d "$run" ] || run="$BACKUP_DIR/runs/$run"
  [ -f "$run/minio-objects.tsv" ] || die 2 "execução sem minio-objects.tsv: $run"
  [ -f "$run/SHA256SUMS" ] || die 2 "execução sem SHA256SUMS: $run"
  (cd "$run" && sha256sum --check --strict --quiet --ignore-missing SHA256SUMS) >&2 \
    || die 2 "checksum não confere na execução $run"
  [ -d "$run/minio" ] || die 2 "execução sem cópia local de objetos ($run/minio) — use 'baixar' para trazer do destino externo"
  if [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ]; then
    die 1 "destino $dest não está vazio — nada foi copiado"
  fi

  mkdir -p "$dest"
  log "→ Copiando objetos para $dest..."
  cp -a "$run/minio/." "$dest/" || die 4 "cópia dos objetos falhou"

  log "→ Conferindo hash de cada objeto..."
  WORK_DIR=${WORK_DIR:-$(mktemp -d)}
  object_inventory "$dest" > "$WORK_DIR/objetos.tsv"
  if ! diff -q "$run/minio-objects.tsv" "$WORK_DIR/objetos.tsv" >/dev/null; then
    { diff "$run/minio-objects.tsv" "$WORK_DIR/objetos.tsv" || true; } | head -20 >&2
    die 3 "objetos restaurados divergem do inventário do backup (ausentes ou alterados)"
  fi
  if ! check_object_refs "$run/object-refs.tsv" "$WORK_DIR/objetos.tsv" "$WORK_DIR/faltando.tsv"; then
    head -20 "$WORK_DIR/faltando.tsv" >&2
    die 3 "$(wc -l < "$WORK_DIR/faltando.tsv" | tr -d ' ') objeto(s) referenciado(s) pelo banco não existem na cópia"
  fi
  local n; n=$(wc -l < "$WORK_DIR/objetos.tsv" | tr -d ' ')
  log "✓ $n objeto(s) conferidos; todas as referências obrigatórias do banco presentes"

  if [ -n "$bucket" ]; then
    local existing
    existing=$("$RCLONE_BIN" lsf --max-depth 1 "$bucket" 2>/dev/null | head -1 || true)
    [ -z "$existing" ] || die 1 "bucket $bucket não está vazio — não sobrescrevo objetos existentes"
    log "→ Enviando para $bucket..."
    "$RCLONE_BIN" copy "$dest" "$bucket" || die 4 "envio ao bucket falhou"
    "$RCLONE_BIN" check "$dest" "$bucket" --one-way || die 3 "conferência do bucket falhou"
    log "✓ Bucket $bucket conferido"
  fi
}

cmd_baixar() {
  [ $# -eq 2 ] || die 1 "uso: restore.sh baixar <REMOTO/runs/AAAAMMDD_HHMMSS> <diretório-destino>"
  local remote_run="${1%/}" dest="$2"
  if [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ]; then
    die 1 "destino $dest não está vazio"
  fi
  mkdir -p "$dest"
  log "→ Baixando $remote_run..."
  "$RCLONE_BIN" copy "$remote_run" "$dest" || die 4 "download falhou"
  [ -f "$dest/RESULT" ] || log "AVISO: execução remota sem RESULT — o envio original não chegou ao fim"
  (cd "$dest" && sha256sum --check --strict --quiet SHA256SUMS) >&2 \
    || die 2 "checksum não confere após download"
  local objects_remote="${remote_run%/runs/*}/minio/objects"
  local n
  n=$(wc -l < "$dest/minio-objects.tsv" | tr -d ' ')
  if [ "$n" -gt 0 ]; then
    log "→ Baixando $n objeto(s) de $objects_remote..."
    cut -f1 "$dest/minio-objects.tsv" > "$dest/.lista-objetos"
    "$RCLONE_BIN" copy "$objects_remote" "$dest/minio" --files-from "$dest/.lista-objetos" \
      || die 4 "download dos objetos falhou"
    rm -f "$dest/.lista-objetos"
    WORK_DIR=${WORK_DIR:-$(mktemp -d)}
    object_inventory "$dest/minio" > "$WORK_DIR/objetos.tsv"
    diff -q "$dest/minio-objects.tsv" "$WORK_DIR/objetos.tsv" >/dev/null \
      || die 3 "objetos baixados divergem do inventário da execução"
  fi
  log "✓ Execução baixada e conferida em $dest"
}

require_approval() {
  [ -n "${RESTORE_APROVACAO:-}" ] \
    || die 1 "defina RESTORE_APROVACAO com quem aprovou e o chamado/registro da decisão"
  local typed
  if [ "${RESTORE_CONFIRMAR:-}" = "$POSTGRES_DB" ]; then
    typed="$POSTGRES_DB"
  else
    read -r -p "Digite o nome do banco em uso ($POSTGRES_DB) para confirmar a troca: " typed
  fi
  [ "$typed" = "$POSTGRES_DB" ] || die 1 "confirmação não confere — nada foi alterado"
  log "Aprovação registrada: $RESTORE_APROVACAO"
}

terminate_connections() {
  psql_admin -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$1' AND pid <> pg_backend_pid()" >/dev/null
}

swap_databases() {
  # $1 = nome que o banco atual passa a ter; $2 = banco que assume $POSTGRES_DB
  local keep_as="$1" incoming="$2"
  terminate_connections "$POSTGRES_DB"
  terminate_connections "$incoming"
  psql_admin -c "ALTER DATABASE \"$POSTGRES_DB\" RENAME TO \"$keep_as\"" \
    || die 4 "falha ao renomear $POSTGRES_DB — nada foi alterado"
  if ! psql_admin -c "ALTER DATABASE \"$incoming\" RENAME TO \"$POSTGRES_DB\""; then
    log "ERRO ao renomear $incoming; devolvendo o nome original ao banco anterior..."
    psql_admin -c "ALTER DATABASE \"$keep_as\" RENAME TO \"$POSTGRES_DB\"" \
      || die 4 "ATENÇÃO: $POSTGRES_DB está como $keep_as — renomeie manualmente"
    die 4 "troca abortada; estado original restabelecido"
  fi
}

cmd_promover() {
  local from=""
  [ "${1:-}" = "--de" ] && from="${2:-}"
  [ -n "$from" ] || die 1 "uso: restore.sh promover --de NOME_RESTAURADO"
  valid_db_name "$from" || die 1 "nome inválido: $from"
  [ "$from" != "$POSTGRES_DB" ] || die 1 "origem e destino são o mesmo banco"
  db_exists "$from" || die 1 "banco $from não existe"
  db_exists "$POSTGRES_DB" || die 1 "banco em uso $POSTGRES_DB não existe"
  local mark
  mark=$(psql_admin -c "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = '$from'")
  [[ "$mark" == "mastersat-restore validado"* ]] \
    || die 1 "$from não passou por 'restore.sh banco' com validação — não promovo"
  require_approval
  local keep_as="${POSTGRES_DB}_pre_restore_${STAMP}"
  db_exists "$keep_as" && die 1 "$keep_as já existe"
  swap_databases "$keep_as" "$from"
  log "✓ $from agora é $POSTGRES_DB. O banco anterior foi PRESERVADO como $keep_as."
  log "  Para desfazer: RESTORE_APROVACAO='...' restore.sh reverter-troca --anterior $keep_as"
}

cmd_reverter_troca() {
  local prev=""
  [ "${1:-}" = "--anterior" ] && prev="${2:-}"
  [ -n "$prev" ] || die 1 "uso: restore.sh reverter-troca --anterior NOME_PRESERVADO"
  valid_db_name "$prev" || die 1 "nome inválido: $prev"
  [[ "$prev" == "${POSTGRES_DB}_pre_restore_"* ]] || die 1 "$prev não é um banco preservado por 'promover'"
  db_exists "$prev" || die 1 "banco $prev não existe"
  require_approval
  local keep_as="${POSTGRES_DB}_revertido_${STAMP}"
  swap_databases "$keep_as" "$prev"
  log "✓ $prev voltou a ser $POSTGRES_DB. O banco que estava em uso ficou como $keep_as."
}

# ── Despacho ──────────────────────────────────────────────────────────────────

sub="${1:-}"
case "$sub" in
  verificar) shift; cmd_verificar "$@" ;;
  banco) shift; cmd_banco "$@" ;;
  objetos) shift; cmd_objetos "$@" ;;
  baixar) shift; cmd_baixar "$@" ;;
  promover) shift; cmd_promover "$@" ;;
  reverter-troca) shift; cmd_reverter_troca "$@" ;;
  ""|-h|--help|ajuda) usage; [ -n "$sub" ] || exit 1 ;;
  *)
    if [ -e "$sub" ] || [ -e "$BACKUP_DIR/$sub" ]; then
      log "Forma antiga detectada: restaurando em banco NOVO (o banco em uso não é alterado)."
      cmd_banco "$@"
    else
      usage; exit 1
    fi
    ;;
esac
