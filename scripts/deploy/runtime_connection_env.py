#!/usr/bin/env python3
"""把 runtime_connection.json 的服务拓扑导出成 API 环境变量。

只处理非密钥的主模型/嵌入连接；端口/进程归属由 PhotoBench 遥测直接读同一份文件。
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = Path(
    Path(os.environ["PHOTOBENCH_CONFIG_ROOT"]) / "runtime_connection.json"
    if os.environ.get("PHOTOBENCH_CONFIG_ROOT")
    else os.environ.get("PHOTOBENCH_RUNTIME_CONNECTION")
    or os.environ.get("PHOTOBENCH_RUNTIME_SERVICES")
    or ROOT / "apps/photobench/config/runtime_connection.json"
)


def main() -> None:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    embedding = data.get("embedding") or {}
    exports: dict[str, str] = {}
    if data.get("model_base_url"):
        exports["SENTRIX_VLM_BASE_URL"] = str(data["model_base_url"])
    if isinstance(embedding, dict):
        mode = str(embedding.get("mode") or "")
        if mode == "unified_multimodal":
            exports["SENTRIX_EMBEDDING_SCHEME"] = "wemm"
            if embedding.get("wemm_url"):
                exports["SENTRIX_WEMM_URL"] = str(embedding["wemm_url"])
        elif mode in {"split_text_image", "no_local_embedding"}:
            exports["SENTRIX_EMBEDDING_SCHEME"] = "legacy"
            if embedding.get("text_url"):
                exports["SENTRIX_TEXT_EMBEDDER_URL"] = str(embedding["text_url"])
            if embedding.get("image_url"):
                exports["SENTRIX_LEGACY_CLIP_URL"] = str(embedding["image_url"])
    for name, value in exports.items():
        print(f"export {name}={shlex.quote(value)}")


if __name__ == "__main__":
    main()
