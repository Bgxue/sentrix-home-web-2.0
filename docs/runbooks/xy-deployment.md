# PhotoBench 四机固定版本部署与 10QA 验收

适用 T006；先本地完成 `make ci`、提交并固定 SHA，再发布该 SHA。四机使用相同源码和前端，保留各自配置、权重、数据、历史结果和 Python 环境。指标契约见 [photobench-metrics-contract.md](../architecture/photobench-metrics-contract.md)。本轮不补 vLLM 专项指标。

## 设备与依赖

默认端点指设备自己的 `127.0.0.1`；产品8291、PhotoBench8872。仅云端 Judge 可访问外部模型。2026-10-21现场核验AGX主模型仅绑定本机192.168.0.118，用户明确批准保留该同机LAN端点，不重启模型。

| SSH | 主模型 | 文本嵌入 | 图片嵌入 | 旧新栈根目录 |
|---|---|---|---|---|
| 153 | 8100/v1 | WeMM8102 | WeMM8102 | /home/asus/xy/sentrix-home-web-2.0 |
| nx46 | 8100/v1 | 8101 | 8103 | /home/nx/xy/sentrix-home-web-2.0 |
| orin118 | **192.168.0.118:8103/v1（同机）** | **18101** | **18103** | /home/orin/xy/sentrix-home-web-2.0 |
| mac158 | 8100/v1 | 8101 | 8103 | /Users/saas/xy/sentrix-home-web-2.0 |

模型不同，不能解释为同模型性能横评。四机没有 Git 元数据，运行版本由源码及前端 SHA256 清单、实际 Python 导入路径、服务回显和 run 快照共同证明。

## 本地开发与打包

```bash
# 仓库根；包括全量测试、≥85%覆盖率、两个前端构建、两份OpenAPI
make ci
git add <已检查的代码、测试、文档>
git commit -m 'fix(photobench): correct cross-platform metric accounting'
# 在最终提交上再次通过门禁；不得用跳过失败测试代替
make ci
.venv/bin/python scripts/deploy/photobench_release.py package --destination /absolute/release-output
```

输出 `<SHA>.tar.gz`、包校验文件和解包目录。包内包含 `build-info.json`、逐文件 `release-manifest.json` 和统一构建的两个 dist。打包要求工作树干净且 dist 存在。不将配置、密钥、venv、权重或历史结果打包。

本地磁盘不足时，真实嵌入CI fixture可通过临时测试桥访问已登记的真实模型，须在验证报告明确说明。正式四机回归禁止沿用此桥。

## 发布与回滚

在远端使用独立目录 `~/xy/releases/<SHA>`，运行绑定保存在 `~/xy/runtime/<SHA>`，不要覆盖旧新栈目录。先校验包SHA256，再解包。首次激活要求旧8291/8872仍在运行以捕获有效环境。

```bash
# 远端现有venv内的Python；以下路径需替换为实际绝对路径
<old>/.venv/bin/python <release>/scripts/deploy/photobench_release.py verify --root <release>
<old>/.venv/bin/python <release>/scripts/deploy/photobench_release.py activate \
  --root <release> --previous <old> --runtime <runtime>
```

激活器复制配置到受保护的运行目录；原配置不改。仅已确认属本机的LAN地址可规范为loopback；真实远程依赖拒绝。旧data、datasets、results、venv显式绑定，两个dist和PYTHONPATH指新release。所有相对数据路径均相对旧根解析。

AGX激活额外指定`--local-model-url http://192.168.0.118:8103/v1`。该显式例外必须通过本机接口地址核验，不能换端口；记录于topology-adjustments并加入NO_PROXY。preflight仅对登记的主模型地址接受此例外，其他依赖仍要求回环。该地址不能改成其他设备的LAN地址。

发布前检查 `product` 清单：独立Qdrant必须有本机监听；进程内嵌入式Qdrant无需6333服务，确认实现后从新配置副本移除该独立服务声明，不能把探测失败当不存在。主模型和文本/图片嵌入端口均从URL推导，不能再维护一份遥测端口文件。

激活器只核验并停止8291/8872的uvicorn，活跃run拒绝发布；不管理模型服务，不动8091/8191/8771/8772。人脸固定CPU，自动线程上限在数值库导入前生效；0表示不限制。权重必须预先本地存在，正式运行设离线模式。启动后等待两个服务回显同一SHA，保存日志、PID及activation记录。

API启动后将其真实PID、模型端点和无密钥runtime_connection登记于`runtime/sentrix-process.json`；PhotoBench通过既有
`PHOTOBENCH_SENTRIX_PID_FILE`读取该记录，`PHOTOBENCH_SENTRIX_LOG_PATH`指向同一runtime的`api.log`。
full run模型和嵌入拓扑一致时不能再次重启API；源码release内不保存这些可变控制文件。
如需重启，按run快照在runtime/startup-snapshots生成内容寻址只读配置，并传给启动脚本；全局配置后改不影响本run。systemd同样绑定只读快照，旧入口兼容。

2026-10-21用户批准的第二个现场例外：NX既有CLIP8103的CUDA内核不兼容，健康200但编码500。
原权重离线CPU验证图片及文本编码通过后，仅该侧车按显式授权重启为CPU；8100主模型及8101文本嵌入不重启。
该变更不由产品应用管理，部署证据另存，四机同版本重新执行完整测评。

```bash
<old>/.venv/bin/python <release>/scripts/deploy/photobench_release.py rollback \
  --root <release> --runtime <runtime>
```

回滚切回bindings中记录的previous源码并重启8291/8872，保留当前release、激活记录和全部结果。启动失败先读运行目录的api.log/photobench.log；不修改远端源码修问题，回本地修复、提交新SHA、重新通过CI和四机完整回归。

## 启动前门禁与正式执行

```bash
<release>/.venv/bin/python <release>/scripts/deploy/photobench_acceptance.py preflight \
  --root <release> --runtime <runtime>
# 汇总四机preflight，确认dataset/QA/GT哈希完全一致后再执行
<release>/.venv/bin/python <release>/scripts/deploy/photobench_acceptance.py run \
  --root <release> --runtime <runtime>
```

preflight要求版本一致、完整diagnostics正常、没有活跃孤儿；核对有效产品环境与PhotoBench配置；检查实际本机监听进程，拒绝代理/重定向/远程卸载；分别执行主文本、主视觉、文本嵌入、图像嵌入真实请求并校验向量维度及有效值。核对CPU人脸环境，并在独立进程实际加载本地RetinaFace、SCRFD、ArcFace权重，记录哈希与实际CPU provider。存储使用本机数据路径，不下载权重。Judge采用独立配置，不把密钥写入报告。

NX文本8101现为BGE-M3 q8_0 GGUF、llama.cpp pooling=none；产品和独立预检均须实际执行有界协议协商，不能用`/health`200作为编码成功。原生token矩阵使用官方CLS+L2，报告`text_embedding_protocol=llama_cpp_bge_m3_cls_l2`；禁止mean pooling或重启模型规避协议问题。Mac/AGX原Python侧车继续使用`/embed`。

run保存唯一请求和响应，`album3-14 / compact-10q / mode=full / models=["__current__"]`，创建新scope，QA并发1、turn_timeout900秒、import_timeout14400秒。每次尝试使用独立runtime记录，旧请求与结果不得覆盖。需要重试时明确记录原因和前一run，不能挑选或删除失败题。

完成后：

```bash
<release>/.venv/bin/python <release>/scripts/deploy/photobench_acceptance.py audit \
  --root <release> --runtime <runtime>
```

独立报告分别给出`service_pass`（兼容`chain_pass`）、`metrics_pass`、`tasks`、`scoring_complete/scoring`、`infrastructure_errors`，不以run_valid替代。10题全部调度且终态可核查；受控恢复须有既定类别、压缩事件和同一步后续成功调用，明确预算/解析/模型失败收尾单列任务失败，未知终态/丢记录/错误计分属测评程序缺陷。恢复和任务失败不因中间error自动否定服务；OOM/断连/意外重启单报基础设施，不宣称健康。任务收尾与GT质量分开，正常需要Judge的10题仍以10/10有效评分为评分完整目标；不足注明覆盖分母，不补0。

指标须验证原始样本→保存统计→API/前端一致，分量可复算、未知/过期保持null、功耗轨带范围；导入/处理/QA均有新鲜核心内存，报告样本数/间隔/首尾与最大缺口。既定单次/首尾超过3秒门槛不变。调用错误率报告分子/分母及固定版本历史基线；不设事后任务质量容忍阈值，10QA只验证流程，不能证明低概率故障消除。

共用公式/有效性/快照/计时/Judge/主流程变更：固定新版本四机完整回归。仅平台变更说明范围后验证该机及共用逻辑；纯文档样式运行相关检查/前端构建，null/单位/指标投影需真实数据核验。旧证据注明适用SHA，不改旧run构建版本，源码/后端/前端均可追踪。

旧运行无法验证新版本时先保全结果/日志/版本，通过取消接口有界停止，确认线程池已join、active_count=0、无同scope子任务，再切换新scope；必要时仅重启8291/8872。人工取消记录替代原因并保留旧run，不能报为崩溃或最终验收。AGX保留已批准同机LAN8103，NX已批准8103 CPU侧车例外不代表可任意重启模型。

Judge标准化v1的score规则见指标契约；每次原响应及转换事实保存。历史只读派生带source/hash/original_score，不覆盖原文、不补猜分、不伪装旧执行成功。

## 口径与历史

- NX/AGX一致：整机MemTotal−MemAvailable，主模型smaps RSS+nvmap，产品相关PID去重PSS+主/辅助nvmap。分配/归属估计；nvmap不可读时正残差可作标明来源的估计，非正残差不可用。
- NVIDIA：主模型RAM按选定端口的监听PID及后代PSS；VRAM按同一PID归属。其他实例只进整卡/其他进程统计。
- Mac：整机active+wired+compressor为平台特定统计；footprint、映射权重和容量估计合计分别展示，不宣称精确物理驻留。
- source退出或超过三个刷新周期后读数失效；单来源失败保留其他有效来源。null不是0，必要分量未知时总量为空。
- P95用nearest-rank，完整有效样本先统计后降采样；均值为样本均值、峰值为采样峰值，保留首尾及阶段边界。
- 历史原始结果不改写；仅从已记录完整事实做带版本派生。历史归属错误、缺失解析事实或采样不能补造。

2026-10-22最终原始数据审计发现：gpu_stats返回后，进程/系统采集可能阻塞，
设备原观测65.6秒前但旧age仍为0.53秒。采样持久化改按同一单调时钟重算age，
流源传递三个实际源周期的TTL；过期只清空设备读数并保留进程/系统内存。
历史投影从已有时间事实派生、标device_freshness_version=1，不改原文；
缺TTL旧记录采用显式3秒兼容门槛，不补造真实刷新频率。验收器独立从时间戳核验。
此修复须形成新固定SHA，四机统一发布后重新full 10QA，旧轮次全部保留。

Judge一致性规则核验：新正式run的回答质量judge必须记录consistency_version=2；
合法0分计入有效分母，缺分保持null。旧无版本原文继续v1；禁止从不完整重问轨迹补分。
任何该规则代码修复也必须新SHA、完整make ci及四机新scope full10QA，不能重判旧run代替。
