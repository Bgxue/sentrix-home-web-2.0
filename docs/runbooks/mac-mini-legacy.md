# Mac mini（16 GB）legacy 检索部署

目标机器：`saas@192.168.0.158`（`saass-Mac-mini.local`，arm64，macOS 27.0.1，16 GB）。
用途：在没有 10 GB 显存跑 WeMM 的机器上，用 1.0 的 chinese-clip + bge-m3 方案验证 2.0。

## 1. 依赖

| 能力 | 来源 | 端口 |
|---|---|---|
| 对话 / VLM | llama-server `qwen35-q4km` + mmproj | 8100 |
| 文本嵌入 | 1.0 `scripts/maintenance/text_embedder_sidecar.py` | 8101 |
| 视觉嵌入 | 本仓库 `scripts/maintenance/legacy_clip_sidecar.py` | 8103 |
| 人脸 | onnxruntime + insightface + RetinaFace（CPU） | 进程内 |
| 模型权重 | `~/.cache/clip/clip_cn_vit-l-14.pt`、`~/.cache/huggingface/.../bge-m3`、`~/.insightface`、`~/benchmarks/retinaface` | — |

2.0 的 `.venv` 可以从 1.0 的 `.venv` 复制（已含 cn_clip / torch / onnxruntime / insightface），
再补 `pydantic-settings`：

```bash
cp -R ~/GitHub/Sentrix-Home-Web/.venv ~/GitHub/sentrix-home-web2.0/.venv
.venv/bin/python -m pip install pydantic-settings
```

## 2. 启动

```bash
cd ~/GitHub/sentrix-home-web2.0
./scripts/deploy/start_legacy_mac.sh
```

脚本会：

1. 设置 `SENTRIX_EMBEDDING_SCHEME=legacy`；
2. 显式设置 `FACE_PROVIDERS=CPUExecutionProvider`、`RETINAFACE_PROVIDERS=CPUExecutionProvider`
   （Mac 没有 CUDA；不设会被人脸模块的 CUDA 防线按设计拒启）；
3. 拉起 8103 chinese-clip 侧车；
4. 拉起 8191 API；
5. 等 `/api/v1/health` 返回。

### 2.1 PhotoBench 迁移（必做）

从 153 复制 PhotoBench 配置后，**必须**改 `apps/photobench/config/runtime_connection.json`：

```json
{"sentrix_url": "http://127.0.0.1:8191", "model_base_url": "http://127.0.0.1:8100/v1", ...}
```

PhotoBench 的 `SentrixClient` 读的是这个文件，不是 `PHOTOBENCH_SENTRIX_API_URL`。
不改的话，评测会把导入请求发回 153，表现为 `photo_import` 立即失败
`directory not found`，且 130 的 2.0 日志里看不到 `POST /api/v1/imports`。

## 3. 验证

```bash
curl -s http://127.0.0.1:8191/api/v1/health
# {"status":"ok",...,"embeddings":"ok"}

curl -s "http://127.0.0.1:8191/api/v1/search?q=街道&scope_id=<scope>&limit=5"
# channel_trace 里 visual_ann / text_ann 应为 ok，backend=qdrant / sqlite_exact
```

向量落库应看到：

```
visual   chinese-clip-ViT-L-14   768d
semantic BAAI/bge-m3             1024d
episodic BAAI/bge-m3             1024d
```

## 4. 与 153 的差异

| 项 | 153 | 130 |
|---|---|---|
| 方案 | wemm（默认） | legacy |
| 视觉 | WeMM 2048d / 8102 | chinese-clip 768d / 8103 |
| 文本 | WeMM 2048d / 8102 | bge-m3 1024d / 8101 |
| 人脸 | CUDA | CPU |
| 显存/内存 | 24 GB GPU | 16 GB 统一内存 |

## 5. 已知限制

- 130 没有 2.0 的 GeoNames dump（`data/geo`），最近地点解析为空；行政区走 PyGeoCN
  （venv 里已可用）。
- 首次导入每张图要等 VLM 观察（实测 2 张约 45 秒），随后 finish/summarize 约 16 秒。
- 切换方案（wemm ↔ legacy）必须重建向量；旧 `model_name` 的行不会命中。
- 测试数据：`~/tmp/mac-smoke`，scope `mac-legacy-smoke2`（2 张图、2 个事件、
  visual/text 两类向量均已落库）。
