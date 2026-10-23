#!/usr/bin/env bash
# 启动 2.0 PhotoBench（默认 8772）。它通过 HTTP 访问 Sentrix，不直接 import 主产品。
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJ"

exec .venv/bin/python -m uvicorn --factory photobench.main:create_app \
  --host 0.0.0.0 --port "${PHOTOBENCH_PORT:-8772}" --app-dir apps/photobench/src
