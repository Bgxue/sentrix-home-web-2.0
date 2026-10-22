# PhotoBench 四机固定版本部署与 10QA 验收

适用 T006；先本地完成 `make ci`、提交并固定 SHA，再发布该 SHA。四机使用相同源码和前端，保留各自配置、权重、数据、历史结果和 Python 环境。指标契约见 [photobench-metrics-contract.md](../architecture/photobench-metrics-contract.md)。本轮不补 vLLM 专项指标。

2026-10-22用户临时调整：NX暂停适配并释放本轮8291/8872、序列及reader占用，共享模型保留；仅接收独立release代码，不激活、不启动、不测试。截图相关前端修复（未知导入计数、阶段墙钟与后端内部计时分列、阶段耗时null保留）按用户明确要求不跑测试，只做构建、固定提交及制品hash核验后同步。此例外不等于新SHA通过完整CI；旧de14真实运行、后端CI与新前端产物分开追踪，不改标旧run，也不据此放宽3秒采样门槛。其余三机既有回归继续，避免为前端改动中断活跃run。

## 当前暂停边界与采样修复验证

2026-10-22用户进一步要求先修可控制的代码、硬件驱动暂缓、暂不启动测评。本次只在本地开发/测试/提交；不激活设备release、不做模型探针、不重启服务，NX继续保持暂停。前文较早“其余三机回归继续”描述仅适用于已结束的de14轮。

恢复验证时，共用采样/发布语义变更须固定新SHA并验证受影响设备；当前不执行。新的Jetson样本必须同时核验memory_observations原始起止/TTL、memory_dependencies及最老必要输入；辅助不存在的0需有新鲜topology。查看sampling_diagnostics分辨设备、进程、系统及回调耗时，查看sampling_integrity的写盘/发布/采集异常和stop_pending_threads。未退出线程不能认定释放成功，不应与旧任务并发同scope。

3秒门槛不变；单次内核smaps不能硬抢占，不通过扩大TTL或沿用旧值掩盖缺口。本地门禁与真实采样分别报告，旧run版本和原始文件保留。

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

preflight要求版本一致、完整diagnostics正常、没有活跃孤儿；核对有效产品环境与PhotoBench配置；检查实际本机监听进程，拒绝代理/重定向/远程卸载；分别执行主文本、主视觉、文本嵌入、图像嵌入真实请求并校验向量维度及有效值。核对显式人脸provider，在独立进程实际推理本地RetinaFace、SCRFD、ArcFace权重，记录哈希和profile里的实际kernel provider；请求CUDA而实得CPU必须失败，明确CPU合法。存储使用本机数据路径，不下载权重。Judge采用独立配置，不把密钥写入报告。

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

Judge一致性v3进一步限定为明确“模型/助手的回答/答案/结论”或分句开头针对回答的肯定断言；参考答案已回答正确地点而模型未能作答的合法0分不重问。真正模型回答正确却给0仍重问一次；v1/v2历史语义保留，新执行consistency_version=3，标准化v1不变。


### Jetson 核心采样关键路径（2026-10-22）

9dad22b 实际 AGX 导入仍出现3.493秒缺口。继续定位发现gpu_stats先读主模型PSS，
process_memory又对同PID读完整PSS/RSS；设备探针不再重复遍历smaps，唯一的新鲜进程
观测归process_memory。可选llama.cpp /metrics各IO超时从2秒收紧至0.2秒，繁忙时KV为
不可用，不保留前次值；不改变主模型HTTP请求超时、内存公式、源TTL或严格3秒覆盖门槛。
这是Jetson适配器内部修复，无公开schema、端口、装配点或部署配置变化。真实HTTP繁忙
测试确认在服务端任务结束前返回不可用；完整采样测试确认每PID仅一次smaps遍历。
影响范围仅NX/AGX；固定新提交、完整CI后这两机新scope full10QA。3090/Mac继续使用
9dad22b正式证据及该版本前端产物，不将旧run改标新SHA，版本分别追踪。


## 2026-10-22 新轮验证与硬件环境

固定新SHA后四机full10QA；NX/Mac再各执行build→reuse10QA，所有scope独立，目标60条QA终态/有效评分，任务能力失败另报。Mac使用集中mac预设（Agent600、Chat180、QA2、导入86400），显式配置优先，QA0仍表示自动；server保持原预算。保留原run及适用版本，不能改标旧证据。

配置源为runtime_connection.json；白名单product_settings可指定deployment_profile、agent_wall_time_s、chat_timeout_s、face_providers、retinaface_providers。配置API更新仅影响新run，队列和resume保持快照。不同产品URL不授予按同端口杀本机服务的权限，只有登记本机API允许自动重启。

发布器回滚到没有`Settings.runtime_configuration()`的旧release时，预检读取旧版实际Settings的Agent/Chat预算与已有provider环境变量，不要求旧版实现新增诊断方法，也不伪报其支持部署预设。仍在停止任一监听之前核验Python≥3.11、源码导入路径及配置可构造性；旧环境损坏时先绑定独立验证过的兼容环境。此兼容修复仅影响发布/回滚工具，不改变应用执行、指标、接口或前端；真实测评证据沿用其实际应用SHA，工具提交单独追踪。

硬件环境先运行当前解释器下的runtime_acceleration_probe.py，保存实际包路径/版本/RECORD哈希、用户site、动态库和真实算子结果。可用`--torch-device cuda|mps|cpu`和`--onnx <本地权重> --provider CUDAExecutionProvider|CPUExecutionProvider`；每个探针有总超时。生产预检逐个人脸权重做实际推理并检查profile kernel provider。

新安装指定全新SENTRIX_INSTALL_VENV；Jetson还必须提供SENTRIX_DEPENDENCY_LOCK完整带哈希锁（含匹配JetPack/Python的原始wheel及SHA256）。安装不覆盖现存环境，pip check后保存runtime-inventory.json与requirements.observed.txt，后者只表示已安装事实，不能单独充当带来源哈希的重建锁。CUDA候选侧车可用自己的Python版本；不能用Python3.10运行要求≥3.11的产品包。

CUDA/cuDNN大版本必须匹配。LD_LIBRARY_PATH只允许候选进程指向已核验匹配的真实库；不能把lib*.so.12软链成.so.13。不要将两个ORT发行包共用命名空间或用户site污染视为正常加速证据；独立环境选择一个实际运行版本，并核验import与kernel。隔离候选未通过前现用服务和模型不切换。

官方依据：https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html 、 https://docs.nvidia.com/deeplearning/frameworks/install-pytorch-jetson-platform/index.html 。PyPI ORT≥1.27默认CUDA13，不适合直接套用CUDA12.6 Jetson。已核验的设备事实与候选测试记录另见本轮独立报告。

前端内存说明补核版只改变标签/说明及accounting元数据投影，后端生产文件与3cb480a逐字节相同。完整CI、固定提交与统一制品后，等待各机当前验证序列终态再切release；四机重新预检，并用保留的真实API结果验证新前端的内存标签、公式和null显示。保留3cb实际运行版本与原始证据，不将其改标为新SHA，也不因此再调用60题模型。切换前先保存PID/资源连续性和日志，切换本身作为授权部署记录，不报意外重启。


#### 实时详情轮询负载回归

轮询等待必须从上一轮完成时计起，正常2秒、异常5秒；渲染或QA loader引用变化不重启计时器。
同run重叠详情读取共享在途计算；完成后下一批重新观测，错误也传播给全部等待者并清理。
验证时保留有实时详情访问的运行证据，并检查遥测最大间隔、首尾缺口、读取响应和内存压力。
若发现采样停滞，保全raw/API/日志后按严格门槛判定，不能删除异常采样或扩大3秒阈值。
本次共用读侧修正固定提交后四机full10QA，NX/Mac再build→reuse；旧失败轮保留，不按质量筛选。

T006终态归档：新版必须先结束采样与末尾观测，再同时发布终态/finished_at。
收集证据时还需确认current_phase为空、末尾快照存在、执行套件已退出并且原始文件稳定；不能只凭status=completed开始下一轮。
旧版曾存在提前终态，若两份自然收尾文件不同，保留两个哈希和逐字段差异，不能覆盖原采集证据或伪造版本。


### 2026-10-22 Jetson监听缓存按端口选择隔离

NX新版实测发现同一采样周期先查询主模型端口、再查询完整产品端口集合；旧单槽缓存相互覆盖，持续重复全量扫描/proc/fd。改为按排序后的端口集合保存独立条目，每次仍核验监听socket和PID启动标识，TTL保持2秒；清除过期条目且最多保留32份，避免配置变化造成无界缓存。不改变端口、公式、null/TTL或3秒验收阈值。回归测试交替查询单端口/多端口并复用PID，先红后绿。仅Jetson基础设施路径受影响；发布后NX重新full→build→reuse，AGX待主模型恢复再回归。153/Mac不调用此实现，原实际SHA证据继续保留。真实时序效果须由新版本运行确认，不能由缓存测试提前记为通过。


### 2026-10-23 测试分层调整

固定提交发布前仍须 `make ci` 全绿；真实资源测试独立为 `make test-real`，
保留原断言，缺依赖/skip不算通过。按影响范围运行专项和真机10QA，详见 [testing.md](testing.md)。
本次Jetson缓存只影响NX/AGX，不因测试编排提交要求153/Mac重复10QA。
旧4项WeMM依赖失败及3项地点fixture错误保留，不由拆分CI改写成成功。


### 2026-10-23 Jetson进程发现缩小读取范围

NX c3dbd09实测拓扑最慢2.295秒，核心内存有效缺口6.078秒，未通过3秒门槛；同时存在严重内存/交换压力，不能将全部延迟归因代码。端口回退发现改为只核验实际监听候选；进程清单每PID先单读stat建父子图，再仅给选中监听及当前子孙读取RSS/命令行并复核start_ticks。新子进程每轮发现、共享子孙去重；PID复用或消失不沿用旧值。无scope的旧关键词规则、本机显式PID或无端口关键词配置仍全量发现，远端local=false不触发本机扩展。公式、预算、null、缓存TTL、接口及3秒阈值不变；无新增模块、端口或装配点。专项覆盖候选过滤、新子进程、PID复用及兼容分支，真实改善须由新固定版本NX full→build→reuse验证；AGX仍等待模型恢复，153/Mac不调用该路径。
