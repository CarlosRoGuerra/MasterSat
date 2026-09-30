#!/usr/bin/env bash
# =============================================================================
# Testes de regressão de backup.sh / restore.sh (OPS-01, OPS-02).
#
# Roda DENTRO da imagem de backup (backup/Dockerfile), como root, sem rede:
# sobe um PostgreSQL descartável no próprio container (socket em /tmp),
# cria dados sintéticos, um "MinIO" de mentira (diretório lido pelo rclone)
# e um destino externo local. Nunca aponta para banco ou bucket reais.
#
#   backup/tests/run_in_docker.sh      (a partir da raiz do repositório)
# =============================================================================
set -Eeuo pipefail

SRC="${SRC:-/work/backup}"
T=/tmp/t
rm -rf "$T"; mkdir -p "$T"
OUT="$T/out.txt"
PASS=0; FAIL=0

ok()  { PASS=$((PASS + 1)); echo "ok   - $1"; }
nok() { FAIL=$((FAIL + 1)); echo "FAIL - $1"; }
check() { local d="$1"; shift; if "$@"; then ok "$d"; else nok "$d"; fi; }
# expect_code CÓDIGO DESCRIÇÃO comando...  (saída em $OUT)
expect_code() {
  local want="$1" d="$2"; shift 2
  set +e; "$@" >"$OUT" 2>&1; local got=$?; set -e
  if [ "$got" -eq "$want" ]; then ok "$d (saída $got)"
  else nok "$d (esperado $want, obtido $got)"; sed 's/^/      | /' "$OUT" | tail -25; fi
}
out_has() { grep -q -- "$1" "$OUT"; }

# ── PostgreSQL descartável ────────────────────────────────────────────────────
export PGDATA="$T/pgdata"
mkdir -p "$PGDATA"; chown postgres "$PGDATA" "$T"
gosu postgres initdb -A trust -U postgres >/dev/null
gosu postgres pg_ctl -o "-c listen_addresses='' -k $T" -l "$T/pg.log" -w start >/dev/null
trap 'gosu postgres pg_ctl -m immediate stop >/dev/null 2>&1 || true' EXIT

export POSTGRES_HOST="$T" POSTGRES_USER=postgres POSTGRES_DB=rastreamento
# Segredos falsos: não podem aparecer em manifest, log nem destino externo.
export POSTGRES_PASSWORD="senha-sintetica-NAO-VAZAR-7f3a"
export AILOS_TOKEN_ENCRYPTION_KEY="fernet-sintetica-NAO-VAZAR-9c1b"
export MASTERSAT_GIT_SHA="0123456789abcdef0123456789abcdef01234567"
PSQL=(psql -X -q -At -v ON_ERROR_STOP=1 -h "$T" -U postgres)

"${PSQL[@]}" -d postgres -c "CREATE DATABASE rastreamento"
"${PSQL[@]}" -d rastreamento <<'SQL'
CREATE TYPE billingstatus AS ENUM ('PENDING', 'PAID', 'OVERDUE', 'CANCELED');
CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY);
INSERT INTO alembic_version VALUES ('f7c1a2b3d4e5');
CREATE TABLE clients (id serial PRIMARY KEY, name text NOT NULL);
CREATE TABLE billings (
  id serial PRIMARY KEY, client_id int NOT NULL REFERENCES clients(id),
  amount numeric(10,2) NOT NULL, paid_amount numeric(10,2),
  status billingstatus NOT NULL, is_deleted boolean NOT NULL DEFAULT false);
CREATE TABLE documents (id serial PRIMARY KEY, object_key varchar(500) UNIQUE NOT NULL, active boolean NOT NULL DEFAULT true);
CREATE TABLE ailos_retorno_arquivos (id serial PRIMARY KEY, storage_object_key varchar(255));
INSERT INTO clients(name) SELECT 'Cliente sintético ' || g FROM generate_series(1, 200) g;
INSERT INTO billings(client_id, amount, paid_amount, status, is_deleted)
SELECT (g % 200) + 1, (g * 7 % 1000) / 10.0 + 0.01,
       CASE WHEN g % 3 = 0 THEN (g * 7 % 1000) / 10.0 + 0.01 END,
       (ARRAY['PENDING','PAID','OVERDUE','CANCELED'])[(g % 4) + 1]::billingstatus, g % 17 = 0
  FROM generate_series(1, 1000) g;
INSERT INTO documents(object_key, active) VALUES
  ('clients/1/documents/a.pdf', true), ('clients/2/documents/b.pdf', true),
  ('clients/2/documents/excluido.pdf', false);
INSERT INTO ailos_retorno_arquivos(storage_object_key) VALUES ('ailos/retorno/r1.ret'), (NULL);
SQL

# Fingerprint do banco "em uso": não pode mudar em nenhum teste de restore.
source "$SRC/lib.sh"
fp_of() { inventory_sql | psql -X -q -At -F $'\t' -h "$T" -U postgres -d "$1" | sha256sum | cut -c1-64; }
live_fp() { fp_of rastreamento; }
LIVE_FP=$(live_fp)
db_count() { "${PSQL[@]}" -d postgres -c "SELECT count(*) FROM pg_database WHERE datname = '$1'"; }

# ── MinIO de mentira e destino externo ───────────────────────────────────────
MINIO="$T/minio-src/rastreamento"
mkdir -p "$MINIO/clients/1/documents" "$MINIO/clients/2/documents" "$MINIO/ailos/retorno"
echo "%PDF-sintetico-a" > "$MINIO/clients/1/documents/a.pdf"
echo "%PDF-sintetico-b" > "$MINIO/clients/2/documents/b.pdf"
echo "retorno sintetico" > "$MINIO/ailos/retorno/r1.ret"
echo "objeto sem referência" > "$MINIO/orfao.bin"

mkdir -p "$T/stubs"
cat > "$T/stubs/curl" <<EOF
#!/bin/sh
echo "\$@" >> "$T/alertas.log"
EOF
chmod +x "$T/stubs/curl"
export PATH="$T/stubs:$PATH"

export BACKUP_DIR="$T/backup" BACKUP_MINIO_SOURCE="$MINIO" RCLONE_REMOTE="$T/offsite"
export ALERT_WEBHOOK="http://alerta.invalid/webhook"
BACKUP=(bash "$SRC/backup.sh")
RESTORE=(bash "$SRC/restore.sh")
latest_run() { find "$1/runs" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort | tail -1; }
next_second() { local s; s=$(date -u +%s); while [ "$(date -u +%s)" = "$s" ]; do sleep 0.1; done; }

echo "== Backup completo =="
expect_code 0 "backup com banco, objetos e envio externo" "${BACKUP[@]}"
RUN=$(latest_run "$BACKUP_DIR"); R="$BACKUP_DIR/runs/$RUN"
check "RESULT=OK" grep -qx 'STATUS=OK' "$R/RESULT"
check "SHA256SUMS confere" sh -c "cd '$R' && sha256sum -c --quiet SHA256SUMS"
check "manifest: formato custom" grep -q '"db_format": "custom"' "$R/manifest.json"
check "manifest: commit da aplicação" grep -q "$MASTERSAT_GIT_SHA" "$R/manifest.json"
check "manifest: revisão Alembic" grep -q '"alembic_revision": "f7c1a2b3d4e5"' "$R/manifest.json"
check "manifest: 1000 cobranças no inventário" grep -qP '^rows\tbillings\t1000$' "$R/inventory.tsv"
check "manifest: impressão digital da chave Fernet" grep -q '"fernet_key_fingerprint": "sha256:' "$R/manifest.json"
check "dump é custom legível (PGDMP)" sh -c "head -c5 '$R/db.dump' | grep -q PGDMP"
check "objetos inventariados (4)" test "$(wc -l < "$R/minio-objects.tsv")" -eq 4
check "destino externo tem RESULT" test -f "$T/offsite/runs/$RUN/RESULT"
check "destino externo tem objetos" test -f "$T/offsite/minio/objects/clients/1/documents/a.pdf"
check "last_success atualizado" grep -qx "$RUN" "$BACKUP_DIR/last_success"
check "segredos não vazam em backup local/externo" sh -c "! grep -rqF -e '$POSTGRES_PASSWORD' -e '$AILOS_TOKEN_ENCRYPTION_KEY' '$BACKUP_DIR' '$T/offsite'"
check "alerta de sucesso enviado" grep -q 'Backup Mastersat OK' "$T/alertas.log"

echo "== Restore do formato atual =="
expect_code 0 "verificar não toca em banco" "${RESTORE[@]}" verificar "$R"
expect_code 0 "restaurar em banco novo e conferir inventário" "${RESTORE[@]}" banco "$R" --destino ensaio_atual
check "inventário idêntico reportado" out_has "inventário idêntico"
check "banco restaurado tem 1000 cobranças" test "$("${PSQL[@]}" -d ensaio_atual -c 'SELECT count(*) FROM billings')" = 1000
check "soma monetária idêntica" test "$("${PSQL[@]}" -d ensaio_atual -c 'SELECT sum(amount) FROM billings')" = "$("${PSQL[@]}" -d rastreamento -c 'SELECT sum(amount) FROM billings')"
check "banco em uso intacto" test "$(live_fp)" = "$LIVE_FP"

echo "== Destino protegido =="
expect_code 1 "recusa restaurar sobre o banco em uso" "${RESTORE[@]}" banco "$R" --destino rastreamento
expect_code 1 "recusa destino que já existe" "${RESTORE[@]}" banco "$R" --destino ensaio_atual
check "banco em uso intacto" test "$(live_fp)" = "$LIVE_FP"

echo "== Formatos legados e SQL =="
L="$T/legado"; mkdir -p "$L"
pg_dump -h "$T" -U postgres -d rastreamento --format=custom | gzip -9 > "$L/mastersat_db_legado.sql.gz"
pg_dump -h "$T" -U postgres -d rastreamento --format=plain | gzip -9 > "$L/plain.sql.gz"
pg_dump -h "$T" -U postgres -d rastreamento --format=plain > "$L/plain.sql"
expect_code 0 "legado custom+gzip (*.sql.gz) restaura com pg_restore" "${RESTORE[@]}" banco "$L/mastersat_db_legado.sql.gz" --destino ensaio_legado
check "formato legado detectado como custom+gzip" out_has "Formato: custom+gzip"
check "legado: 1000 cobranças" test "$("${PSQL[@]}" -d ensaio_legado -c 'SELECT count(*) FROM billings')" = 1000
expect_code 0 "SQL comprimido restaura com psql" "${RESTORE[@]}" banco "$L/plain.sql.gz" --destino ensaio_sqlgz
check "SQL comprimido: 1000 cobranças" test "$("${PSQL[@]}" -d ensaio_sqlgz -c 'SELECT count(*) FROM billings')" = 1000
expect_code 0 "SQL puro restaura com psql" "${RESTORE[@]}" banco "$L/plain.sql" --destino ensaio_sql
expect_code 0 "forma antiga 'restore.sh <arquivo>' vai para banco novo" "${RESTORE[@]}" "$L/mastersat_db_legado.sql.gz"
check "forma antiga avisou" out_has "Forma antiga detectada"
check "banco em uso intacto" test "$(live_fp)" = "$LIVE_FP"

echo "== Arquivo inválido nunca cria nem altera banco =="
head -c 4000 "$L/mastersat_db_legado.sql.gz" > "$L/truncado.sql.gz"
expect_code 2 "gzip truncado reprovado" "${RESTORE[@]}" banco "$L/truncado.sql.gz" --destino nao_deve_existir_1
check "nenhum banco criado" test "$(db_count nao_deve_existir_1)" = 0
pg_dump -h "$T" -U postgres -d rastreamento --format=custom > "$L/inteiro.dump"
# Corte que atinge os blocos de dados (metade do arquivo). Um corte só nos
# bytes finais pode passar na leitura sem perder conteúdo — o pg_restore
# gera SQL idêntico —; por isso backups novos dependem do SHA-256 do manifest.
head -c $(( $(stat -c %s "$L/inteiro.dump") / 2 )) "$L/inteiro.dump" > "$L/truncado.dump"
expect_code 2 "custom truncado reprovado pela leitura integral" "${RESTORE[@]}" banco "$L/truncado.dump" --destino nao_deve_existir_2
check "nenhum banco criado" test "$(db_count nao_deve_existir_2)" = 0
TR="$T/copia-truncada"; cp -a "$R" "$TR"
head -c $(( $(stat -c %s "$R/db.dump") - 100 )) "$R/db.dump" > "$TR/db.dump"
expect_code 2 "dump truncado dentro de execução reprovado pelo checksum" "${RESTORE[@]}" banco "$TR" --destino nao_deve_existir_8
check "nenhum banco criado" test "$(db_count nao_deve_existir_8)" = 0
head -c $(( $(stat -c %s "$L/plain.sql") - 200 )) "$L/plain.sql" > "$L/truncado.sql"
expect_code 2 "SQL truncado reprovado" "${RESTORE[@]}" banco "$L/truncado.sql" --destino nao_deve_existir_3
head -c 2048 /dev/urandom > "$L/lixo.bin"
expect_code 2 "arquivo que não é dump reprovado" "${RESTORE[@]}" banco "$L/lixo.bin" --destino nao_deve_existir_4
C="$T/copia-hash"; cp -a "$R" "$C"
printf '\x00' | dd of="$C/db.dump" bs=1 seek=200 conv=notrunc status=none
expect_code 2 "hash inválido reprovado antes de qualquer operação" "${RESTORE[@]}" banco "$C" --destino nao_deve_existir_5
check "mensagem cita checksum" out_has "checksum não confere"
check "nenhum banco criado" test "$(db_count nao_deve_existir_5)" = 0
check "banco em uso intacto" test "$(live_fp)" = "$LIVE_FP"

echo "== Inventário divergente é detectado após restaurar =="
D="$T/copia-inventario"; cp -a "$R" "$D"
sed -i 's/^rows\tbillings\t1000$/rows\tbillings\t999/' "$D/inventory.tsv"
(cd "$D" && sha256sum db.dump inventory.tsv object-refs.tsv minio-objects.tsv manifest.json > SHA256SUMS)
expect_code 3 "restauração divergente do manifest reprovada" "${RESTORE[@]}" banco "$D" --destino ensaio_divergente
check "banco divergente não recebe marca de validado" test -z "$("${PSQL[@]}" -d postgres -c "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname='ensaio_divergente'")"

echo "== Permissões =="
P="$T/sem-permissao"; mkdir -p "$P"; chmod 0555 "$P"
expect_code 2 "backup em diretório sem permissão falha" env BACKUP_DIR="$P" gosu postgres bash "$SRC/backup.sh"
check "mensagem de permissão" out_has "não foi possível criar"
cp "$L/inteiro.dump" "$T/ilegivel.dump"; chmod 000 "$T/ilegivel.dump"
expect_code 2 "restore de arquivo ilegível falha antes de criar banco" gosu postgres bash "$SRC/restore.sh" banco "$T/ilegivel.dump" --destino nao_deve_existir_6
check "nenhum banco criado" test "$(db_count nao_deve_existir_6)" = 0

echo "== Objetos =="
expect_code 0 "restaurar objetos em diretório novo e conferir hashes" "${RESTORE[@]}" objetos "$R" "$T/objetos-restaurados"
check "objeto restaurado idêntico" cmp -s "$MINIO/clients/1/documents/a.pdf" "$T/objetos-restaurados/clients/1/documents/a.pdf"
expect_code 1 "recusa destino de objetos não vazio" "${RESTORE[@]}" objetos "$R" "$T/objetos-restaurados"
O="$T/copia-objetos"; cp -a "$R" "$O"; rm "$O/minio/clients/2/documents/b.pdf"
expect_code 3 "objeto ausente na cópia é detectado" "${RESTORE[@]}" objetos "$O" "$T/objetos-2"

next_second
mv "$MINIO/clients/2/documents/b.pdf" "$T/b.pdf.fora"
expect_code 1 "backup com objeto referenciado ausente na origem falha" "${BACKUP[@]}"
RUN_M=$(latest_run "$BACKUP_DIR")
check "objects-missing.tsv lista o objeto" grep -q 'clients/2/documents/b.pdf' "$BACKUP_DIR/runs/$RUN_M/objects-missing.tsv"
check "RESULT=FALHA" grep -qx 'STATUS=FALHA' "$BACKUP_DIR/runs/$RUN_M/RESULT"
check "alerta de falha enviado" grep -q 'FALHA' "$T/alertas.log"
check "last_success não avança" grep -qx "$RUN" "$BACKUP_DIR/last_success"
mv "$T/b.pdf.fora" "$MINIO/clients/2/documents/b.pdf"

echo "== Destino externo (rclone) =="
cat > "$T/rclone-falha" <<EOF
#!/bin/sh
case "\$1 \$3" in "copy $T/offsite"*) echo "falha simulada" >&2; exit 1 ;; esac
exec rclone "\$@"
EOF
cat > "$T/rclone-mentiroso" <<EOF
#!/bin/sh
# copy para o destino externo "dá certo" sem copiar nada.
case "\$1 \$3" in "copy $T/offsite"*) exit 0 ;; esac
exec rclone "\$@"
EOF
chmod +x "$T/rclone-falha" "$T/rclone-mentiroso"
next_second
expect_code 1 "rclone com erro gera falha" env RCLONE_BIN="$T/rclone-falha" "${BACKUP[@]}"
check "RESULT registra envio falho" grep -qx 'OFFSITE=falhou' "$BACKUP_DIR/runs/$(latest_run "$BACKUP_DIR")/RESULT"
next_second
expect_code 1 "rclone que retorna 0 sem copiar é pego pela conferência" env RCLONE_BIN="$T/rclone-mentiroso" "${BACKUP[@]}"
check "mensagem de conferência" out_has "rclone check"
next_second
expect_code 1 "sem destino externo configurado conta como falha" env RCLONE_REMOTE= "${BACKUP[@]}"
next_second
expect_code 0 "sem destino externo com BACKUP_OFFSITE_REQUIRED=0" env RCLONE_REMOTE= BACKUP_OFFSITE_REQUIRED=0 "${BACKUP[@]}"
next_second
expect_code 1 "origem de objetos não configurada falha" env BACKUP_MINIO_SOURCE= "${BACKUP[@]}"
next_second
expect_code 2 "pg_dump sem banco falha de forma fatal" env POSTGRES_DB=nao_existe "${BACKUP[@]}"

echo "== Restauração a partir do destino externo =="
expect_code 0 "baixar execução do destino externo com objetos" "${RESTORE[@]}" baixar "$T/offsite/runs/$RUN" "$T/baixado"
expect_code 0 "restaurar banco a partir da cópia baixada" "${RESTORE[@]}" banco "$T/baixado" --destino ensaio_offsite
expect_code 0 "restaurar objetos a partir da cópia baixada" "${RESTORE[@]}" objetos "$T/baixado" "$T/objetos-offsite"

echo "== GPG =="
export GNUPGHOME="$T/gnupg"; mkdir -p "$GNUPGHOME"; chmod 700 "$GNUPGHOME"
gpg --batch --passphrase '' --quick-gen-key 'Backup Teste <backup-teste@example.invalid>' default default never 2>/dev/null
next_second
expect_code 1 "GPG com destinatário inexistente falha" env BACKUP_ENCRYPT_KEY=ninguem@example.invalid "${BACKUP[@]}"
RUN_G=$(latest_run "$BACKUP_DIR")
check "dump aberto NÃO vai para o destino externo" test ! -e "$T/offsite/runs/$RUN_G"
check "RESULT registra envio bloqueado" grep -qx 'OFFSITE=bloqueado' "$BACKUP_DIR/runs/$RUN_G/RESULT"
next_second
expect_code 0 "backup cifrado com GPG" env BACKUP_ENCRYPT_KEY=backup-teste@example.invalid "${BACKUP[@]}"
RUN_E=$(latest_run "$BACKUP_DIR")
check "artefato cifrado" test -f "$BACKUP_DIR/runs/$RUN_E/db.dump.gpg" -a ! -f "$BACKUP_DIR/runs/$RUN_E/db.dump"
expect_code 0 "restaurar backup cifrado" "${RESTORE[@]}" banco "$BACKUP_DIR/runs/$RUN_E" --destino ensaio_gpg
expect_code 2 "sem a chave privada GPG o restore reprova antes de criar banco" env GNUPGHOME="$T/gnupg-vazio" "${RESTORE[@]}" banco "$BACKUP_DIR/runs/$RUN_E" --destino nao_deve_existir_7
check "nenhum banco criado" test "$(db_count nao_deve_existir_7)" = 0

echo "== Retenção =="
RB="$T/retencao"; mkdir -p "$RB/runs"
for r in 20200101_000000 20200102_000000 20200103_000000; do mkdir "$RB/runs/$r"; echo STATUS=OK > "$RB/runs/$r/RESULT"; done
mkdir "$RB/runs/20200104_000000"; echo STATUS=FALHA > "$RB/runs/20200104_000000/RESULT"
next_second
expect_code 0 "backup com retenção" env BACKUP_DIR="$RB" BACKUP_MIN_KEEP=2 RCLONE_REMOTE= BACKUP_OFFSITE_REQUIRED=0 "${BACKUP[@]}"
check "mantém as 2 execuções OK mais novas mesmo antigas" test -d "$RB/runs/20200103_000000" -a -d "$RB/runs/20200102_000000"
check "remove execução OK além do mínimo" test ! -e "$RB/runs/20200101_000000"
check "remove execução com falha antiga" test ! -e "$RB/runs/20200104_000000"

echo "== Troca controlada e reversão =="
mk_count() { "${PSQL[@]}" -d postgres -c "SELECT count(*) FROM pg_database"; }
expect_code 1 "promover sem aprovação é recusado" "${RESTORE[@]}" promover --de ensaio_atual
"${PSQL[@]}" -d postgres -c "CREATE DATABASE nao_validado"
N_BEFORE=$(mk_count)
expect_code 1 "promover banco não validado é recusado" env RESTORE_APROVACAO=teste RESTORE_CONFIRMAR=rastreamento "${RESTORE[@]}" promover --de nao_validado
"${PSQL[@]}" -d ensaio_atual -c "INSERT INTO clients(name) VALUES ('marca-do-restaurado')"
expect_code 0 "promover com aprovação" env RESTORE_APROVACAO="ensaio automatizado" RESTORE_CONFIRMAR=rastreamento "${RESTORE[@]}" promover --de ensaio_atual
PRE=$("${PSQL[@]}" -d postgres -c "SELECT datname FROM pg_database WHERE datname LIKE 'rastreamento_pre_restore_%'")
check "banco anterior preservado com os dados originais" test "$(fp_of "$PRE")" = "$LIVE_FP"
check "restaurado assumiu o nome em uso" test "$("${PSQL[@]}" -d rastreamento -c "SELECT count(*) FROM clients WHERE name='marca-do-restaurado'")" = 1
expect_code 0 "reverter troca" env RESTORE_APROVACAO="ensaio automatizado" RESTORE_CONFIRMAR=rastreamento "${RESTORE[@]}" reverter-troca --anterior "$PRE"
check "banco original de volta ao nome em uso" test "$(live_fp)" = "$LIVE_FP"
check "nenhum banco foi apagado na troca/reversão" test "$(mk_count)" = "$N_BEFORE"

echo
echo "Resultado: $PASS ok, $FAIL falha(s)"
[ "$FAIL" -eq 0 ]
