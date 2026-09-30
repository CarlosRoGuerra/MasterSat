#!/usr/bin/env bash
# =============================================================================
# lib.sh — funções compartilhadas por backup.sh e restore.sh
#
# Não execute diretamente: é carregado com `source`. Nada aqui é destrutivo;
# as funções só leem arquivos/bancos e escrevem em diretórios informados.
# =============================================================================

# Versão do formato do manifest. Subir quando o layout de uma execução mudar
# de forma que um restore.sh antigo não saiba ler.
# shellcheck disable=SC2034  # usada por backup.sh, que carrega este arquivo
MANIFEST_SCHEMA_VERSION=1

# Cabeçalho do formato custom do pg_dump ("PGDMP").
PGDMP_MAGIC_HEX=5047444d50

_hex_head() {
  # Primeiros $2 bytes de stdin em hexadecimal, sem espaços.
  head -c "$1" | od -An -tx1 | tr -d ' \n'
}

# Identifica o formato real do arquivo pelo conteúdo — nunca pela extensão.
# Os backups gerados até 09/2026 chamam-se *.sql.gz mas são pg_dump custom
# dentro de gzip; o restore antigo usava psql neles e falhava depois do DROP.
#   custom        pg_dump --format=custom
#   custom+gzip   pg_dump --format=custom | gzip   (legado)
#   sql           pg_dump --format=plain
#   sql+gzip      pg_dump --format=plain | gzip
#   gpg           cifrado com GPG (decifrar antes de detectar de novo)
#   desconhecido  qualquer outra coisa — nunca restaurar
detect_dump_format() {
  local f="$1" head5 inner first2
  [ -r "$f" ] || { echo "desconhecido"; return 0; }
  case "$f" in
    *.gpg|*.pgp) echo "gpg"; return 0 ;;
  esac
  head5=$(_hex_head 5 < "$f")
  if [ "$head5" = "$PGDMP_MAGIC_HEX" ]; then
    echo "custom"; return 0
  fi
  # "-----" = GPG em ASCII armor
  if [ "$head5" = "2d2d2d2d2d" ] && head -c 27 "$f" | grep -q 'BEGIN PGP'; then
    echo "gpg"; return 0
  fi
  first2=${head5:0:4}
  if [ "$first2" = "1f8b" ]; then
    # `|| true` isola o SIGPIPE do gzip quando o head fecha o pipe.
    inner=$({ gzip -dc "$f" 2>/dev/null || true; } | _hex_head 5)
    if [ "$inner" = "$PGDMP_MAGIC_HEX" ]; then
      echo "custom+gzip"
    elif { gzip -dc "$f" 2>/dev/null || true; } | head -c 64 | grep -q -- '^--'; then
      echo "sql+gzip"
    else
      echo "desconhecido"
    fi
    return 0
  fi
  if head -c 64 "$f" | grep -q -- '^--'; then
    echo "sql"; return 0
  fi
  echo "desconhecido"
}

# Lê o arquivo INTEIRO com o leitor adequado, sem tocar em banco algum.
# Detecta truncamento e corrupção antes de qualquer operação no destino.
# Retorna 0 se o arquivo é legível até o fim.
validate_dump_readable() {
  local f="$1" fmt="$2" tail_txt
  case "$fmt" in
    custom)
      pg_restore --list "$f" >/dev/null || return 1
      pg_restore --file=/dev/null "$f" || return 1
      ;;
    custom+gzip)
      gzip -t "$f" || return 1
      gzip -dc "$f" | pg_restore --file=/dev/null || return 1
      ;;
    sql)
      tail_txt=$(tail -c 4096 "$f")
      grep -q 'PostgreSQL database dump complete' <<<"$tail_txt" || return 1
      ;;
    sql+gzip)
      gzip -t "$f" || return 1
      tail_txt=$(gzip -dc "$f" | tail -c 4096)
      grep -q 'PostgreSQL database dump complete' <<<"$tail_txt" || return 1
      ;;
    *)
      return 1
      ;;
  esac
}

sha256_of() { sha256sum "$1" | cut -d' ' -f1; }

# Escapa uma string para uso dentro de aspas em JSON.
json_str() {
  local s="$1"
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\t'/\\t}
  s=${s//$'\r'/}
  s=${s//$'\n'/\\n}
  printf '"%s"' "$s"
}

# Lê um campo escalar de nível 1 do manifest (gerado por backup.sh com uma
# chave por linha). Evita depender de jq na imagem.
manifest_get() {
  local manifest="$1" key="$2"
  sed -n "s/^  \"${key}\": \"\\{0,1\\}\\([^\"]*\\)\"\\{0,1\\},\\{0,1\\}\$/\\1/p" "$manifest" | head -1
}

# Valida um nome de banco antes de interpolar em SQL.
valid_db_name() {
  [[ "$1" =~ ^[a-z_][a-z0-9_]{0,62}$ ]]
}

# SQL do inventário: contagem de linhas de toda tabela de public, soma exata
# de toda coluna numeric (valores monetários), quebra de cobranças por
# situação e revisão Alembic. Roda igual na origem (dentro do snapshot do
# pg_dump) e no banco restaurado — as duas saídas precisam ser idênticas.
# Saída: tipo<TAB>chave<TAB>valor, ordenada.
inventory_sql() {
  cat <<'SQL'
SELECT to_regclass('public.billings') IS NOT NULL AS inv_has_billings,
       to_regclass('public.alembic_version') IS NOT NULL AS inv_has_alembic \gset
SELECT 'rows' AS tipo, c.relname AS chave,
       (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM public.%I', c.relname), false, true, '')))[1]::text AS valor
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
UNION ALL
SELECT 'sum', col.table_name || '.' || col.column_name,
       (xpath('/row/c/text()', query_to_xml(format('SELECT coalesce(sum(%I), 0) AS c FROM public.%I', col.column_name, col.table_name), false, true, '')))[1]::text
  FROM information_schema.columns col
  JOIN information_schema.tables t ON t.table_schema = col.table_schema AND t.table_name = col.table_name AND t.table_type = 'BASE TABLE'
 WHERE col.table_schema = 'public' AND col.data_type = 'numeric'
ORDER BY 1, 2;
\if :inv_has_billings
SELECT 'billings', status::text || '|excluida=' || is_deleted::text,
       count(*) || '|' || coalesce(sum(amount), 0) || '|' || coalesce(sum(paid_amount), 0)
  FROM public.billings GROUP BY status, is_deleted ORDER BY 2;
\endif
\if :inv_has_alembic
SELECT 'alembic', 'version_num', coalesce(string_agg(version_num, ',' ORDER BY version_num), '') FROM public.alembic_version;
\endif
SQL
}

# Chaves de objeto referenciadas pelo banco. `obrigatorio` = documento ativo,
# cujo objeto precisa existir; `opcional` = versão substituída/excluída
# (excluir documento remove o objeto e mantém a linha com active=false).
# Saída: obrigatorio|opcional<TAB>origem<TAB>chave
object_refs_sql() {
  cat <<'SQL'
SELECT to_regclass('public.documents') IS NOT NULL AS refs_has_documents,
       to_regclass('public.ailos_retorno_arquivos') IS NOT NULL AS refs_has_retorno \gset
\if :refs_has_documents
SELECT CASE WHEN active THEN 'obrigatorio' ELSE 'opcional' END, 'documents', object_key
  FROM public.documents ORDER BY 3;
\endif
\if :refs_has_retorno
SELECT 'obrigatorio', 'ailos_retorno_arquivos', storage_object_key
  FROM public.ailos_retorno_arquivos WHERE storage_object_key IS NOT NULL ORDER BY 3;
\endif
SQL
}

# Lista os objetos de um diretório espelho: chave<TAB>bytes<TAB>sha256.
object_inventory() {
  local dir="$1"
  [ -d "$dir" ] || return 0
  (cd "$dir" && find . -type f -print0 | sort -z | while IFS= read -r -d '' p; do
    k=${p#./}
    printf '%s\t%s\t%s\n' "$k" "$(stat -c %s "$p")" "$(sha256_of "$p")"
  done)
}

# Compara referências do banco com o inventário de objetos.
# Escreve em $3 as referências obrigatórias sem objeto; retorna 1 se houver.
check_object_refs() {
  local refs="$1" objects="$2" missing_out="$3"
  : > "$missing_out"
  [ -s "$refs" ] || return 0
  # getline em vez de NR==FNR: com inventário vazio o truque NR==FNR
  # trataria o arquivo de referências como se fosse o de objetos.
  awk -F'\t' -v objs="$objects" '
    BEGIN { while ((getline l < objs) > 0) { split(l, a, "\t"); have[a[1]] = 1 } }
    $1 == "obrigatorio" && !($3 in have) { print }
  ' "$refs" > "$missing_out"
  [ ! -s "$missing_out" ]
}
