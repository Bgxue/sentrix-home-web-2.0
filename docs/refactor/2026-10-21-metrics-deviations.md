# T006 指标 v2 对历史行为的更正

- 修复3090端口选定后扩大同名模型归属，以及NVIDIA误用Orin公式/标签。RAM和VRAM分别统计。
- 修复unknown被转0、缺分量仍报完整合计；无辅助服务必须明确确认，探测失败不可视为无服务。
- 配置从运行期反复读取改为run创建时冻结，新增按快照装配入口；旧入口保留一个发布周期。
- 2.0 Agent时延不再重复扣Judge；阶段吞吐用单调墙钟。产品记录失败调用时长及工具JSON解析事实；不再无条件标成功。
- NX功耗解析所有实际VDD轨，VDD_IN为板级，其他轨标明范围；Mac分拆容量估计，平台整机口径不等价Linux。
- 流退出/超期失效、各来源失败独立；Jetson进程/系统内存不依赖GPU探针，PID消失时清空旧分量；NVIDIA驱动失败仍采PSS。历史读取同样验证异常并保留计数，原数据不改写。统一nearest-rank P95，先完整统计后降采样。
- 轻量health异步不占线程池；直接ASGI入口同样在数值库导入前设置自动线程；人脸CPU部署。单实例租约下备份恢复孤儿run，不自动接续工作。
- 前端同一时间点读数修正也须重画，null保持断线；词法融合测试的两份语义向量改为确定性区分，提示词测试等待异步加载完成，保持原断言。
- 全量CI的macOS锁持有子进程允许posix_spawn，避免已加载数值库后fork崩溃；不削减锁降级检索断言。既有planning/video摘要夹具同步真实终止及摘要事实。
- 会话详情契约测试等待后台回合终态，再断言用户/助手完整消息序列，消除“线程还没完成”的时序假设。
- 独立验收额外核对监听PID后代与显存归属、typed media微平均算术，并实际加载本地三份人脸ONNX权重确认CPU provider；不修改生产失败语义。
- 实际四机预检发现legacy文本探针路由有别于WeMM，按既有适配器契约使用`/embed`；不将预检404误报成模型不可用。
- NX实际BGE-M3是GGUF q8_0、llama.cpp pooling=none；产品原先只会调用Python侧车`/embed`，健康200仍无法编码。适配器增加有界HTTP协议协商；明确pooling=none时原生token矩阵按官方CLS+L2生成1024维dense向量。错误维度/非有限/零CLS/多输入拒绝；普通HTTP错误不切换协议，模型进程保持原样。新scope避免与旧查询轨迹混用。

  依据：[BGE-M3官方模型说明](https://huggingface.co/BAAI/bge-m3)、
  [FlagEmbedding M3默认CLS及归一化](https://github.com/FlagOpen/FlagEmbedding/blob/master/FlagEmbedding/inference/embedder/encoder_only/m3.py)、
  [dense首token实现](https://github.com/FlagOpen/FlagEmbedding/blob/master/FlagEmbedding/finetune/embedder/encoder_only/m3/modeling.py)。
  同时核实NX已部署llama.cpp的`send_embedding`：pooling=none返回原始token，忽略`embd_normalize`。
- AGX主模型只绑定192.168.0.118:8103，用户批准保留同机LAN地址以遵守不重启模型约束；发布显式登记、校验本机接口及端口、加入NO_PROXY，验收拒绝未登记或非本机例外。
- 早期3090 WeMM恢复入口可按原权重CPU运行，Transformers5.2.0禁用CUDA FLA入口以选择原生PyTorch后备实现；最终现场实际侧车为CUDA，以每轮health/PID/原始显存记录为准，不从该入口推断现场设备。无假向量、无权重/算法更换。
- 四机真实run发现Mac进程快照包含采集器刚启动的`ps`，它退出后将产品总量持续判为不完整。精确排除本次观察进程PID，保留同名真实工作进程及其他PID消失的不可用语义。
- 发布新API后未登记进程控制记录，首次full run会将“无记录”误当端点变化而重启8291。发布登记已启动的API PID与端点，并显式绑定控制记录/日志到runtime；端点一致时保持API PID，预检可验证全过程连续性。

兼容：新增字段均可选，两份OpenAPI和前端同步；TelemetryProvider四方法不变。旧结果只允许基于完整事实的带版本派生。排除vLLM专项补齐，严格验收不改变生产fail-open语义。


运行拓扑冻结同样覆盖产品重启：bootstrap按run快照构造subprocess/systemd控制器，
以内容哈希寻址、原子发布的只读runtime_connection.json作为既有PHOTOBENCH_CONFIG_ROOT。
实际启动脚本不能重新读取后续全局配置。控制记录同时保留无密钥拓扑；同模型但嵌入拓扑不同
不得视为already_pointing_here。旧未注入快照入口保留；端口方法和公开schema不变。

独立验收器同样保留unknown：API重启时benchmark_processes缺席不能把仅有的nvmap分量相加当成产品总量。缺进程PSS时null合计正确，伪造0或部分和仍拒绝；必需阶段的缺失/超过3秒缺口继续严格失败。


2026-10-22真实10QA进一步确认预算事实位于runtime_turns[*].agent2_trace.budget_outcome，
题目顶层无该字段。步数内完成率必须按实际回合读取；多轮题全部回合满足终止原因、非空答案、
状态与预算才算完成，已知失败优先于其他回合的缺失。只有事实缺失时保留null，不回填预算。
独立验收从回合原始事实复核完成率、工具参数JSON分母及阶段墙钟吞吐，防止顶层夹具掩盖真实结构差异。

2026-10-22补齐前端实际投影：阶段快照的峰值回退只接受有限数值，null/布尔/空串不转0；
降采样历史不得冒充缺失的完整阶段峰值。实时行与阶段行优先使用memory_accounting，
Jetson显示RSS/nvmap/PSS各分量及板级/轨级功耗，Mac显示容量估计与active+wired+compressor；
旧无指标定义的历史source仅用于兼容。生产schema与失败语义不变。

2026-10-22最终原始数据审计发现：gpu_stats返回后，进程/系统采集可能阻塞，
设备原观测65.6秒前但旧age仍为0.53秒。采样持久化改按同一单调时钟重算age，
流源传递三个实际源周期的TTL；过期只清空设备读数并保留进程/系统内存。
历史投影从已有时间事实派生、标device_freshness_version=1，不改原文；
缺TTL旧记录采用显式3秒兼容门槛，不补造真实刷新频率。验收器独立从时间戳核验。
此修复须形成新固定SHA，四机统一发布后重新full 10QA，旧轮次全部保留。

2026-10-22四机运行追加暴露Judge一致性误判：旧正则跨“问题可回答，正确地点”命中，
把合法0分丢成None。新judge.consistency_version=2仅在同一标点分句判断，排除可回答/可以回答，
否定短语不能删除后拼接出新的褒义。真正矛盾仍只重问一次、失败保留None；评分rubric不变。
未带版本的旧Judge保留v1语义，不根据早先raw_text猜测未保存的重问结果，不改历史原文。
API列表与详情、前端类型及OpenAPI同步添加可选版本字段。新SHA必须四机重新完整回归。

2026-10-22用户明确替代旧零error验收：中间受控异常不直接否定服务；要求有界恢复和真实终态证据。新增score_normalization_version=1、score_normalization、attempts及只读score_derivation。原始历史不改；显式原score/无歧义raw_text允许确定性派生；缺失重问、失败原执行不补造。共用Judge解析变更须四机固定新SHA回归，纯展示按影响验证。

AGX实测单次进程采集1.431秒，其中1.228秒是同6个PID分别读PSS和RSS导致12次内核smaps_rollup遍历。改为每PID单次读取两字段，保留缺失null并避免跨观测拼接；不缓存旧值、不修改公式/TTL/3秒覆盖门槛，负载效果待真机回归。

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
