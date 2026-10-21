# 2.0 跨平台 PhotoBench 部署（xy 分支）

> 2026-10-21 ｜ 适用：`xy` 分支的四机 10QA 回归  
> 目标：同一份 commit 在 153（RTX 3090）、118（Orin AGX）、46（Orin NX）、158（Mac mini M4）跑通 benchmark；复用现有 8100 主模型，不启第二个模型服务。

## 1. 设备矩阵

| 设备 | 2.0 目录 | Sentrix API | PhotoBench | 嵌入方案 | 备注 |
|---|---|---|---|---|---|
| 153 | `/home/asus/xy/sentrix-home-web-2.0` | 8291 | 8872 | wemm（复用 8102） | 现有 2.0 在 8191/8772，1.0 在 8091/8771 |
| 118 | `/home/orin/xy/sentrix-home-web-2.0` | 8291 | 8872 | legacy（8101 + 8103） | 现有 1.0 在 8091/8771/11001；无 2.0 |
| 46 | `/home/nx/xy/sentrix-home-web-2.0` | 8291 | 8872 | legacy（8101 + 8103） | 现有 2.0 在 8191/8772；内存 15.6 GB |
| 158 | `/Users/saas/xy/sentrix-home-web-2.0` | 8291 | 8872 | legacy（8101 + 8103） | 现有 2.0 在 8191/8772；目录非 git |

主模型一律复用 `http://127.0.0.1:8100/v1`；PhotoBench 的 `runtime_connection.json` 必须把 `sentrix_url` 指到本机 8291，不能指回 153。

## 2. 安装

```bash
git clone git@github.com:Sheepspeare/sentrix-home-web-2.0.git ~/xy/sentrix-home-web-2.0
cd ~/xy/sentrix-home-web-2.0
git checkout xy
bash scripts/deploy/install_env.sh auto
```

- `auto` 按 `uname`/`nvidia-smi` 判平台：Darwin arm64 → mac，Linux aarch64 → jetson，Linux x86_64 + NVIDIA → 153。
- 没有 npm 的机器（46）会跳过前端依赖；把别处构建好的 `apps/*/web/dist` 同步过来即可。
- macOS 与 Linux aarch64 装 CPU 版 `onnxruntime`；153 装 `onnxruntime-gpu`。人脸 provider 必须显式声明 CPU（legacy 方案脚本已设）。

## 3. 启动

```bash
# 只加载平台参数（不启动）
source scripts/deploy/platform-profiles.sh auto

# 153：wemm 方案（8102 侧车须在跑）
bash scripts/deploy/start_api.sh 153
bash scripts/deploy/start_photobench.sh

# 118/46/158：legacy 方案（8101 + 8103 侧车须在跑）
bash scripts/deploy/start_api.sh jetson   # 158 用 mac
bash scripts/deploy/start_photobench.sh
```

Mac 的 legacy 一键启动仍是 `bash scripts/deploy/start_legacy_mac.sh`（会顺带拉起 8103 chinese-clip）。

线程上限：`deploy/thread-limits.env` 的 `SENTRIX_LIB_THREADS` 留空表示自动推导；`start_api.sh` / `start_legacy_mac.sh` 会在 Python 启动前导出 OMP/MKL/OpenBLAS/NumExpr。

## 4. 10QA 验收

```bash
POST http://127.0.0.1:8872/api/v1/runs
{
  "album_id": "album3-14",
  "qa_set": "compact-10q",
  "mode": "full",
  "models": ["__current__"],
  "model_base_url": "http://127.0.0.1:8100/v1",
  "sentrix_url": "http://127.0.0.1:8291"
}
```

每台必须满足：

- `status=completed`、`run_valid=true`、10/10 item completed；
- `tool_execution_error=0`，无 planner/model call error；
- Judge 10/10 completed，有效分数 10/10（异常项必须记录原因，不补造）；
- 遥测：153 `host_nvidia_smi` 有样本；118/46 `model_process_host_memory_mib`、`_gpu_memory_mib`、`_gpu_memory_source` 非空；158 `memory_profile.method=apple_phys_footprint_plus_mapped_weights_v1`；
- 无 OOM、无 watchdog 重启；1.0 端点不受影响。

## 5. 资源与回退

- 不启动第二个 8100；2.0 的 CLIP/人脸一律 CPU，显存留给主模型。
- 46 的 `MemAvailable < 4 GiB` 时不启动 2.0 API；必要时只暂停现有 2.0（8191/8772），不动 1.0 与 8100。
- 158 若有在跑的 run，不要重启它的 8191/8772；新栈用 8291/8872。
- 任一新栈失败：只停新栈，原服务不动；记录 PID、命令行、日志路径。
- 46 的 2.0 若 health 报 `embeddings=unavailable`，先起 8101/8103（legacy）或 8102（wemm）再评测。

## 6. 遥测口径（2.0 现行）

- **Orin 本机（46）**：`memory_unit=host_smaps_rss_plus_gpu_mib`、
  `memory_profile.method=orin_smaps_rss_plus_gpu_uma_v1`、
  `scope=host_smaps_rss_plus_gpu_uma`。主模型 = 主机 `smaps Rss` + 设备侧 GPU/UMA 页；
  GPU 侧优先 `nvmap` 按 PID 求和，账本不可读时才用 `VmRSS − smaps Rss` 兜底。
  历史 run 没有拆分字段时仍按 `orin_process_pss_uma_v1`（PSS/VmRSS）解释。
- **Mac（158）**：`memory_unit=apple_phys_footprint_plus_mapped_weights_mib`、
  `method=apple_phys_footprint_plus_mapped_weights_v1`。主模型 =
  `phys_footprint + 已映射的模型权重文件`；不是 GPU 专属显存。
- **整套产品**：周边 PSS + 主模型 PSS + 主模型 GPU/UMA 页；不是
  `VmRSS − smaps` 的“补偿”。只有 `scope=all_related_pss_plus_model_uma_extra`
  的 run 才按此公式解释。

## 7. 遥测服务范围（run 级归因）

整机 RAM / 整卡 GPU 仍全量采样；细分归因只算当前 run 实际使用的本机服务。
每台设备只维护 `apps/photobench/config/runtime_connection.json`（不进 git），
主模型、嵌入、Sentrix、product 都在这一份里；PhotoBench 启动时读取，改配置后重启 8872。

```json
{
  "model_base_url": "http://127.0.0.1:8100/v1",
  "embedding": {
    "mode": "unified_multimodal",
    "profiles": {
      "unified_multimodal": {"local": true},
      "split_text_image": {"local": true},
      "no_local_embedding": {"local": false}
    }
  },
  "product": [
    {"id": "sentrix_api", "ports": [8291]},
    {"id": "photobench", "ports": [8872]},
    {"id": "qdrant", "ports": [6333]}
  ]
}
```

- `unified_multimodal`：一个多模态嵌入侧车（WeMM 8102）。
- `split_text_image`：文本 + 图像两个侧车（8101/8103 或 18101/18103）。
- 嵌入端口只从 `wemm_url` / `text_url` / `image_url` 推导，`profiles` 不再写 `ports`；避免两份端口漂移。
- `no_local_embedding`：嵌入在远程，本机不记。
- `main_model.local=false`：模型在远程，本机不记主模型。
- 未列出的旧栈、另一套嵌入方案、旧 2.0/1.0 进程不进入 product/auxiliary。
- 主模型 / product / auxiliary 按 PID 互斥；产品总量只加一次，再加主模型 GPU 页。
