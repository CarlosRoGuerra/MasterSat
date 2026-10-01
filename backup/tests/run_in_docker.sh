#!/usr/bin/env bash
# Roda os testes de backup/restore num container descartável SEM rede.
# Uso (da raiz do repositório):  bash backup/tests/run_in_docker.sh
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
IMAGE="${IMAGE:-mastersat-backup:teste}"
docker build -q -t "$IMAGE" "$ROOT/backup" >/dev/null
# pwd -W (Git Bash no Windows) devolve o caminho que o Docker Desktop entende.
HOST_DIR=$(cd "$ROOT/backup" && (pwd -W 2>/dev/null || pwd))
MSYS_NO_PATHCONV=1 docker run --rm --network none \
  -v "$HOST_DIR:/work/backup:ro" \
  "$IMAGE" bash /work/backup/tests/test_backup_restore.sh
