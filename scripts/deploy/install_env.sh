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

# Candidate environments are separate from the service venv; never upgrade it in place.
install_venv="${SENTRIX_INSTALL_VENV:-$PROJ/.venv}"
install_lock="${SENTRIX_DEPENDENCY_LOCK:-}"
if [ -e "$install_venv" ]; then
  echo "目标环境已存在，拒绝原地覆盖：$install_venv；请指定新的 SENTRIX_INSTALL_VENV。" >&2
  exit 1
fi
if [ "$(uname -s)-$(uname -m)" = "Linux-aarch64" ] && [ -z "$install_lock" ]; then
  echo "Jetson 需要匹配 JetPack/Python/CUDA 的完整带哈希依赖锁；设置 SENTRIX_DEPENDENCY_LOCK。" >&2
  exit 1
fi
uv python install 3.11
uv venv --python 3.11 --python-preference only-managed --seed "$install_venv"
export PYTHONNOUSERSITE=1
if [ -n "$install_lock" ]; then
  "$install_venv/bin/python" -m pip install --require-hashes -r "$install_lock"
  "$install_venv/bin/python" -m pip install --no-deps -e apps/api -e apps/photobench
else
  "$install_venv/bin/python" -m pip install -e "apps/api[dev]" -e apps/photobench
fi
"$install_venv/bin/python" -m pip check
"$install_venv/bin/python" scripts/deploy/runtime_acceleration_probe.py > "$install_venv/runtime-inventory.json"
"$install_venv/bin/python" -m pip freeze --all > "$install_venv/requirements.observed.txt"

if command -v npm >/dev/null 2>&1; then
  (cd apps/web && npm ci)
  (cd apps/photobench/web && npm ci)
else
  echo "未找到 npm：跳过前端依赖。部署时把别处构建好的 dist 同步到 apps/*/web/dist。" >&2
fi

cat <<EOF
环境就绪：$install_venv
下一步：
  source scripts/deploy/platform-profiles.sh $profile
  bash scripts/deploy/start_legacy_mac.sh   # 仅 Mac legacy
  make dev                                  # API 8191
  make photobench-dev                       # PhotoBench 8772
EOF
