# ninfer（18100）的启动命令留档

**2026-10-07 停用。** 用户裁决「不用这个 ninfer 了，把模型换回 qwen3.5-4b」。
它当时是 153 上除 8100 之外唯一的 VLM 端点，所有服务都指着它；停用是因为显存只剩
5233 MiB，装不下 8100 的模型，而 8100 的 qwen35-q4km 才是要用的那个。

留档的是从 `/proc/<pid>/cmdline` 逐字读出来的完整命令——**nexus 是第三方项目**
（`~/Downloads/GoQuark/`，独立于 1.0 与 2.0 两个仓库），起停方式不在本仓库的任何
脚本里，丢了就只能从进程现场或者它的作者那里找回来。

```sh
/home/asus/Downloads/GoQuark/ninfer-master-shensanshu-kvmem/src-tree/fusion-engine-src/build-linux/apps/ninfer-serve \
  /home/asus/Downloads/GoQuark/q1_with_vison/bonsai2_27b_ptq1_native_mtp_vision.ninfer \
  --model-id qwen3.8-27b-graft-8k-k8v4-c4-cpuvision \
  --host 0.0.0.0 --port 18100 --stats-port 18101 \
  --max-context 8192 --kv-capacity 32768 --kv-dtype k8v4 \
  --max-concurrency 4 --prefill-chunk 512 \
  --concurrent-prefill --no-prefix-reuse --recover-invariant-failures \
  --default-max-tokens 800 --default-thinking-budget 4096 --default-reasoning-effort xhigh \
  --presence-penalty 1.0 --gdn-state-fp16 --cuda-graph-allowance-mib 512 \
  --vision --vision-residency cpu --vision-max-merged 512 \
  --media-cache-mib 128 --media-live-mib 256 \
  --structured-output --max-pending-requests 16 --pending-timeout-ms 600000
```

**要起回时**：显存得先腾出来（它占 7150 MiB）。跑评测前把 2.0 的端点指过去要走两处，
见 `docs/runbooks/2.0-services.md` §3.2/§3.3（drop-in + `runtime_connection.json`，缺一
不可）。

**性能参照**（2026-10-07 实测，同一个「请只回复两个字：收到」）：prompt eval 341 tok/s、
decode 43 tok/s；对照 8100 的 qwen35-q4km 是 5742 / 142-172。
