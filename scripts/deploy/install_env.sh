#!/usr/bin/env bash
# 在全新机器上建 2.0 运行环境（1.0 scripts/deploy/install_env.sh 的 2.0 对应物）。
#
# 用法：
#   bash scripts/deploy/install_env.sh [auto|153|jetson|mac|legacy]
#
# 做的事：uv 装 Python 3.11 → 建 .venv → 装 apps/api[dev] 与 apps/photobench →
# 有 npm 时装两个前端；没有 npm 的机器（46）跳过，部署时复用别处构建的 dist。
# 不启动服务、不下载模型、不写 .env.local。
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJ"
profile="${1:-auto}"

# shellcheck source=scripts/deploy/platform-profiles.sh
source scripts/deploy/platform-profiles.sh "$profile"

command -v uv >/dev/null 2>&1 || {
  echo "缺少 uv：先安装 https://docs.astral.sh/uv/ （或把 uv 放进 PATH）" >&2
  exit 1
}

uv python install 3.11
if [ ! -x .venv/bin/python ]; then
  uv venv --python 3.11 --python-preference only-managed --seed .venv
fi

.venv/bin/python -m pip install -q -e "apps/api[dev]" -e apps/photobench

if command -v npm >/dev/null 2>&1; then
  (cd apps/web && npm ci)
  (cd apps/photobench/web && npm ci)
else
  echo "未找到 npm：跳过前端依赖。部署时把别处构建好的 dist 同步到 apps/*/web/dist。" >&2
fi

cat <<EOF
环境就绪：$PROJ/.venv
下一步：
  source scripts/deploy/platform-profiles.sh $profile
  bash scripts/deploy/start_legacy_mac.sh   # 仅 Mac legacy
  make dev                                  # API 8191
  make photobench-dev                       # PhotoBench 8772
EOF
