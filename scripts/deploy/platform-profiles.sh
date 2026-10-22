#!/usr/bin/env bash
# 平台参数（2.0 版，对齐 1.0 scripts/deploy/platform-profiles.sh 的用途）。
#
# 用法：
#   source scripts/deploy/platform-profiles.sh [auto|153|jetson|mac|legacy]
#
# 只导出环境变量，不启动任何服务。auto 的判据是「能力信号」而不是主机名：
#   Darwin arm64        → mac
#   Linux aarch64       → jetson
#   Linux x86_64 + nvidia-smi → 153
#   其余                → legacy（CPU 人脸 + legacy 嵌入）
set -u

_profile="${1:-auto}"
if [ "$_profile" = "auto" ]; then
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) _profile=mac ;;
    Linux-aarch64) _profile=jetson ;;
    Linux-x86_64)
      if command -v nvidia-smi >/dev/null 2>&1; then _profile=153; else _profile=legacy; fi
      ;;
    *) _profile=legacy ;;
  esac
fi

export SENTRIX_PIPELINE_MAX_WORKERS="${SENTRIX_PIPELINE_MAX_WORKERS:-4}"
export SENTRIX_VLM_BASE_URL="${SENTRIX_VLM_BASE_URL:-http://127.0.0.1:8100/v1}"

case "$_profile" in
  153)
    export SENTRIX_DEPLOYMENT_PROFILE="${SENTRIX_DEPLOYMENT_PROFILE:-server}"
    # 153: reserve the device for its local main/embedding models; faces run on CPU.
    export FACE_PROVIDERS="${FACE_PROVIDERS:-CPUExecutionProvider}"
    export RETINAFACE_PROVIDERS="${RETINAFACE_PROVIDERS:-CPUExecutionProvider}"
    export SENTRIX_EMBEDDING_SCHEME="${SENTRIX_EMBEDDING_SCHEME:-wemm}"
    export SENTRIX_WEMM_URL="${SENTRIX_WEMM_URL:-http://127.0.0.1:8102}"
    ;;
  jetson)
    export SENTRIX_DEPLOYMENT_PROFILE="${SENTRIX_DEPLOYMENT_PROFILE:-server}"
    # 46/118：16–64 GB 统一内存，WeMM 2B 与主模型抢内存；用 1.0 的 legacy 方案。
    export SENTRIX_EMBEDDING_SCHEME="${SENTRIX_EMBEDDING_SCHEME:-legacy}"
    export SENTRIX_LEGACY_CLIP_URL="${SENTRIX_LEGACY_CLIP_URL:-http://127.0.0.1:8103}"
    export SENTRIX_TEXT_EMBEDDER_URL="${SENTRIX_TEXT_EMBEDDER_URL:-http://127.0.0.1:8101}"
    export FACE_PROVIDERS="${FACE_PROVIDERS:-CPUExecutionProvider}"
    export RETINAFACE_PROVIDERS="${RETINAFACE_PROVIDERS:-CPUExecutionProvider}"
    ;;
  mac)
    export SENTRIX_DEPLOYMENT_PROFILE="${SENTRIX_DEPLOYMENT_PROFILE:-mac}"
    export SENTRIX_EMBEDDING_SCHEME="${SENTRIX_EMBEDDING_SCHEME:-legacy}"
    export SENTRIX_LEGACY_CLIP_URL="${SENTRIX_LEGACY_CLIP_URL:-http://127.0.0.1:8103}"
    export SENTRIX_TEXT_EMBEDDER_URL="${SENTRIX_TEXT_EMBEDDER_URL:-http://127.0.0.1:8101}"
    export FACE_PROVIDERS="${FACE_PROVIDERS:-CPUExecutionProvider}"
    export RETINAFACE_PROVIDERS="${RETINAFACE_PROVIDERS:-CPUExecutionProvider}"
    ;;
  legacy)
    export SENTRIX_EMBEDDING_SCHEME="${SENTRIX_EMBEDDING_SCHEME:-legacy}"
    export FACE_PROVIDERS="${FACE_PROVIDERS:-CPUExecutionProvider}"
    export RETINAFACE_PROVIDERS="${RETINAFACE_PROVIDERS:-CPUExecutionProvider}"
    ;;
  *)
    printf 'Unknown platform profile: %s\n' "$_profile" >&2
    return 2 2>/dev/null || exit 2
    ;;
esac

printf 'platform profile: %s (scheme=%s)\n' "$_profile" "${SENTRIX_EMBEDDING_SCHEME:-}" >&2
