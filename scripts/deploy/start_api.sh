#!/usr/bin/env bash
# 启动 2.0 主 API（默认 8191）。平台参数与线程上限在 exec 前生效。
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJ"

# shellcheck source=scripts/deploy/platform-profiles.sh
source scripts/deploy/platform-profiles.sh "${1:-auto}"

if [ -f deploy/thread-limits.env ]; then
  # shellcheck disable=SC1091
  . deploy/thread-limits.env
fi
export SENTRIX_LIB_THREADS="${SENTRIX_LIB_THREADS:-}"
if [ -n "$SENTRIX_LIB_THREADS" ] && [ "$SENTRIX_LIB_THREADS" != "0" ]; then
  export OMP_NUM_THREADS="$SENTRIX_LIB_THREADS"
  export MKL_NUM_THREADS="$SENTRIX_LIB_THREADS"
  export OPENBLAS_NUM_THREADS="$SENTRIX_LIB_THREADS"
  export NUMEXPR_NUM_THREADS="$SENTRIX_LIB_THREADS"
fi

exec .venv/bin/python -m uvicorn --factory sentrix.main:create_app \
  --host 0.0.0.0 --port "${SENTRIX_API_PORT:-8191}" --app-dir apps/api/src
