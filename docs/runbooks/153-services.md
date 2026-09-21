# 153 服务清单与重启 SOP

最后更新：2026-09-21

## 服务清单

| 端口 | 服务 | 托管方式 | 重启命令 |
|---|---|---|---|
| 8091 | **1.0 生产 API** | systemd `sentrix-api.service` | `sudo systemctl restart sentrix-api.service` |
| 4174 | 1.0 Web | 手动 `npm start` | `cd Sentrix-Home-Web && npm start` |
| 8100 | llama.cpp `llama-server`（VLM，OpenAI 兼容 `/v1`） | 手动 | 见部署脚本 |
| 8101 | bge-m3 文本嵌入 sidecar（CPU，`.venv-text`） | 由 8091 启动器按需拉起 | 见下 |
| 8500 | model-manager API | systemd --user `sentrix-vllm-api.service` | `systemctl --user restart sentrix-vllm-api.service` |
| 8771 | 1.0 PhotoBench | `nohup` + pid | `services/photobench/scripts/start.sh` |

**绝对不要碰**：
- `11434` ollama —— 属于其他项目
- `5173` FMA —— 无关项目

## 8091 的配置来源（重要）

2026-09-21 之前，8091 **不在 systemd 下**：它是一个 `setsid` 脱离的裸进程，systemd 里显示 `inactive (dead)` 但端口被占——**重启机器会静默失去生产环境**。

排查时发现**三份配置互不相同**：

| 来源 | 路径 | 会设什么 |
|---|---|---|
| systemd unit（旧） | `/home/asus/start_sentrix_api_8091.sh`（8月13） | ~30 个 env，含已废弃的 `SENTRIX_TEXT_EMBEDDER=clip` |
| 实际在跑的 | 无脚本可查（手工 `setsid` 启动） | 只有 5 个 `SENTRIX_*` |
| 仓库维护的 | `scripts/runtime/start_sentrix_api_8091.sh`（9月17） | 启 bge sidecar、AdaFace/NVIDIA 库初始化 |

**处置**：新建 `/home/asus/sentrix-8091-systemd.sh`，**精确复现「实际在跑且验证可用」的那 5 个变量**，unit 只改 `ExecStart` 指向它。

验证方式（切换后实测）：新旧进程的 `SENTRIX_*` env、命令行、cwd **逐字节一致**；`systemctl restart` 后 health 仍 200。

**为什么不用仓库那份脚本**：它带 `SENTRIX_VLLM_REGISTRY=configs/sentrix_vllm_registry_local_100.json`（指向 100 机器的 registry），且引入了未在本机验证过的 ~30 个变量。需要时另行评估。

## 备份与回滚

切换前的完整备份：`/home/asus/sentrix-task12-backup-20260921/`
（含旧 unit、三个启动脚本、运行进程的命令与 env 快照）

回滚：

```bash
sudo cp /home/asus/sentrix-task12-backup-20260921/sentrix-api.service /etc/systemd/system/sentrix-api.service
sudo systemctl daemon-reload
sudo systemctl restart sentrix-api.service
```

## bge-m3 sidecar（8101）

生产的文本嵌入依赖它。**它挂掉时检索会静默退化**，所以 `/home/asus/sentrix-8091-systemd.sh` 在启动前会检查 `8101/health`，不响应才拉起。

手动检查：

```bash
curl -s http://127.0.0.1:8101/health
# {"status": "ok", "model": "BAAI/bge-m3", "dimension": 1024}
```

## 已知行为

- **Qdrant 是嵌入式进程级文件锁**（`data/qdrant/.sentrix-qdrant.lock`）。第二个实例启动时不会崩，而是打印
  `qdrant dir lock is held by another process ...; vector search degrades to SQLite full scans`
  并降级运行。**因此 2.0 必须使用独立的 qdrant 目录。**
- `sentrix-vllm.service` 是 `Type=oneshot` + `RemainAfterExit=yes`，显示 `active (exited)` 是**假活**——8100 上实际跑的是 llama.cpp，不是 vLLM。
- 8091 的 `/api/arbiter/status` 端点**从来不存在**，被多个客户端持续轮询返回 404（曾占全部流量的 17%）。
