# PhotoBench metrics contract v2

本契约是 T006 指标正确性修复的兼容性基线；不补齐 vLLM 专项指标。

## 2026-10-22 采样与分量观测补充（memory_observation_version=1）

新增可选 `memory_observations`（来源ID到观测事实）、`memory_dependencies`（指标到必要来源ID列表）、
`memory_observation_version=1` 与 `sampling_diagnostics`。来源事实包含 `source/status/reason`、
`observed_at_monotonic`（开始读取时刻，保守计龄）、`finished_at_monotonic`、`ttl_seconds`、
`cache_hit`，进程来源另含PID及启动标识。nvmap缓存复用原观测时间，不以返回时间刷新。
合计依赖最老的必要分量；过期/缺失/进程身份变化使该分量及其依赖合计为null，其他有效分量保留。
明确不存在的辅助分量可为0，但仍依赖新鲜topology观测；空依赖不可证明有效；历史无version不补造时间，新采集严格验证在domain集中执行，
写入、读取及汇总共用。系统内存独立观测，不因GPU来源失效而清空。

`sampling_diagnostics`记录锁等待、三个provider耗时、采集总耗时、计划延迟、错过周期及上次回调耗时。
采样按单调时钟计划，超预算跳过错过的周期，不忙等追赶；所有实际点及超时事实保留。
阶段内滚动聚合由单个可停止的发布线程读取全样本快照执行，不在采样锁中计算，不建立无界队列。
原始JSONL仍逐点写入；写回调失败记录原因，不能静默宣称保存完整。`gpu_metrics.sampling_integrity`记录callback/publisher/collection失败及原因、missed_intervals和stop_pending_threads；正常停止后才发布任务终态，若共同等待10秒仍有线程则明确记录停止未确认（telemetry_live.status=stop_pending），严格验收失败，不把遥测增强改为终止生产任务。成功停止为stopped。
可选探针缩短预算，进程扫描利用冻结拓扑；内核单次不可抢占IO仍如实记录超预算，不宣称硬实时保证。
失败/取消未走完采样阶段时以gpu_metrics.status=partial保留已有统计，前端不得清掉partial标记。四方法端口和生产fail-open语义不变，无新部署开关；3秒覆盖门槛不放宽。本轮仅本地修复，不启动真实测评。

## Additive interfaces

- New runs persist `metrics_contract_version=2`, `build_revision` and an immutable,
  secret-free `runtime_snapshot`. Model, Sentrix and telemetry clients use this
  snapshot; changes to saved configuration affect subsequent runs only.
- TelemetryProvider retains its four existing methods and status envelope.
  Optional data describes `memory_accounting`, `memory_complete`, raw memory
  components, power rail/scope, observation age and freshness. Unknown numeric
  components remain null; incomplete totals remain null. Explicit absence is 0.
- Samples preserve independent sources when another source fails. Stream values
  expire after three source periods and are never re-stamped as new observations.
- Product model-call traces add optional tool-argument parse counts and outcomes.
  They contain execution facts, not benchmark judgments or raw prompts.
- Native tool-argument JSON parsing has its own denominator; plain-text answers
  are not JSON attempts. Legacy JSON-action metrics remain available for legacy
  structured-action traces. Missing observations are not inferred successes.
- v2 item `wall_clock_ms` is Agent elapsed time excluding Judge. Phase throughput
  uses measured monotonic wall time, including overlap, never sums item latency.
- Optional build identifiers are exposed through existing config/diagnostics
  responses. Existing URLs, error envelopes and required fields remain unchanged.
- New answer-quality Judge results add optional `judge.consistency_version=2`.
  Consistency checks must stay within punctuation-delimited clauses and must not
  mistake question answerability or the reference answer for praise of the model.
  A legitimate zero remains zero; contradictory praise still triggers one retry.
  Unversioned historical Judge records retain version 1 semantics. Missing scores
  cannot be recovered from an earlier raw response when retry facts are absent.

## Accounting and historical compatibility

Jetson: system=MemTotal-MemAvailable; model=smaps RSS+nvmap; product=related PSS+
main/auxiliary nvmap. These are attribution/allocation estimates, not a proof of
physical-page deduplication. Positive VmRSS residual is a labeled fallback only.
NVIDIA host RAM and device VRAM remain distinct. Apple footprint and mapped
weights are separately retained and their sum is a capacity estimate; system
active+wired+compressor is platform-specific.

Historical files are not rewritten. Deterministic read projections may be
recomputed from recorded facts, using the declared contract version. Missing
attribution, parse observations, timing or samples cannot be reconstructed.
P95 uses nearest-rank; mean is sample mean and peak is sampled peak. Statistics
use valid full-resolution observations before display downsampling. Read and
write projections apply the same value validation.

## Acceptance

The regression report distinguishes metric correctness from full-chain success;
it does not redefine `run_valid`. Four devices run the same committed release,
dataset and 10 QA in full mode. Every service is local except cloud Judge. Each
required dependency is exercised before starting; all recorded failures and
Judge denominators remain visible. General telemetry remains optional.


运行拓扑冻结同样覆盖产品重启：bootstrap按run快照构造subprocess/systemd控制器，
以内容哈希寻址、原子发布的只读runtime_connection.json作为既有PHOTOBENCH_CONFIG_ROOT。
实际启动脚本不能重新读取后续全局配置。控制记录同时保留无密钥拓扑；同模型但嵌入拓扑不同
不得视为already_pointing_here。旧未注入快照入口保留；端口方法和公开schema不变。


2026-10-22真实10QA进一步确认预算事实位于runtime_turns[*].agent2_trace.budget_outcome，
题目顶层无该字段。步数内完成率必须按实际回合读取；多轮题全部回合满足终止原因、非空答案、
状态与预算才算完成，已知失败优先于其他回合的缺失。只有事实缺失时保留null，不回填预算。
独立验收从回合原始事实复核完成率、工具参数JSON分母及阶段墙钟吞吐，防止顶层夹具掩盖真实结构差异。

2026-10-22补齐前端实际投影：阶段快照的峰值回退只接受有限数值，null/布尔/空串不转0；
降采样历史不得冒充缺失的完整阶段峰值。实时行与阶段行优先使用memory_accounting，
Jetson显示RSS/nvmap/PSS各分量及板级/轨级功耗，Mac显示容量估计与active+wired+compressor；
旧无指标定义的历史source仅用于兼容。生产schema与失败语义不变。

2026-10-22设备源新鲜度补充（device_freshness_version=1，可选投影）：
设备读数在采样持久化时按同一单调时钟重新计算age，不能沿用gpu_stats返回时的age。
新增可选device_observation_ttl_seconds（流源为三个真实刷新周期），并保留device_observed_at_monotonic。
TTL失效仅清空设备数值及轨道读数并标source_status.device=unavailable，保留有效进程/系统内存。
历史已有完整单调时间戳时允许按记录TTL派生；旧缺TTL用明确标注的3秒兼容门槛，
派生结果标device_freshness_version=1，原始文件不改写。没有时间事实时不补造新鲜度。
独立验收从采样时间减源观测时间复核age和TTL，禁止仅信任已保存的age。

## Judge score标准化v1与验收分维（2026-10-22）

新增可选字段`score_normalization_version=1`、`score_normalization`（rule_version/input_type/status/converted/score）、`attempts`（每次input/raw_response/raw_text/解析及一致性事实）。只从score严格解析ASCII十进制数字字符串，接受有限整数0/1/2；bool/null/空串/非有限/小数/越界/混合文本拒绝。reason不用于猜分。真正矛盾最多重问一次；失败保留全部已收到响应并无分，不能用首次分数回退。

历史原文不改。只凭明确保存的score或无歧义raw_text生成`score_derivation`（规则版本、源、源文本SHA256及派生分），磁盘原score/status不改；API派生投影score为标准分并附original_score，缺失重问事实或错误收尾不补造。API派生/汇总/前端采用domain同一规则，派生不代表原执行评分成功。

独立验收分服务/指标、任务表现、评分证据：受控恢复须有既定异常类别和后续成功轨迹；真实任务失败单列，未知终态、漏题、丢记录、算分错误归服务缺陷；OOM/断连/重启单报基础设施。10QA全部有终态，评分完整仍要求10/10，不补0；均值注明有效覆盖分母。错误率报告分子/分母和对照版本，不为通过调整阈值。3秒内存覆盖及TTL门槛保持。

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


## 2026-10-22 执行与构建契约（新增可选字段）

创建时冻结拓扑与预算，实际 HTTP 与遥测共用同一快照，排队后全局配置修改只影响新 run。reuse 也执行产品同步与诊断核验；resume 使用原运行依赖事实并记录 `snapshot_source=resumed_run`，兼容旧记录时明确 `legacy_resume_current_config`。自动重启限部署登记的本机API。

build 不解析题库或 Judge 配置，题集为空、题数0、结果空列表；评分和运行评分有效性未适用，`run_valid=null`（保存、读取、列表、前端一致）。`scope_build={status,embedding,source_run_id}` 为新增来源事实，只有身份种子/导入/处理全部完成才 ready；已知嵌入配置变更或构建未完成的 scope 不得 reuse。历史无该字段的 scope 保留兼容语义，不补造构建证据。

`model_identity={status,raw_id,display_name,candidates,reason,observed_at}` 保存实际端点身份；只去除明确权重文件扩展名，不截断 Qwen3.5 等型号。多候选且无明确 ID 时保留未知，不选第一项。

`execution_budgets`、`product_settings` 和部署诊断中的有效预算/provider 都可追溯。POST 发送后的结果不确定会记录 `fatal_error_details`/相位 `request_failure`，不自动重放；同步导入未返回期间只展示目录、目录计数和等待时长，媒体完成数与失败数未知时保持 `?`，不得用 `0` 伪造进度。

导入阶段墙钟以 `photo_import.total_seconds` 为准，兼容历史 `upload_seconds` 或完整起止时间；`timings.total` 只作为“后端计时”单列，不能替代请求墙钟。真实 Mac 记录为墙钟1208秒、内部计时738.1秒，二者范围不同。前端阶段耗时缺少完整起止事实时，`null`、布尔、空字符串和负数保持不可用；真实数字0保留原有起止时间回退语义。本次只改前端投影，不改原始记录、后端公式、执行或公开schema。

本轮共用执行语义变化，固定版本后四机 full10QA，并在 NX/Mac 各做独立 build→reuse10QA。build 的严格遥测验收只覆盖导入/处理；reuse 只覆盖 QA，未适用阶段不补点。单次及首尾3秒门槛、null与TTL、三类验收结论、历史保全均沿用。

输入初始化失败：`failed_phase="initialization"`，`qa_count=null`、`input_integrity=null`表示无法获得输入事实；保存真实failed记录且不调用产品。build正常初始化的题目数仍为0，验收不得要求10题；评分不适用不豁免遥测覆盖。
