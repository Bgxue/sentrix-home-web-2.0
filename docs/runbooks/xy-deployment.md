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
