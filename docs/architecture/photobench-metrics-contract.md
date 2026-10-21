# PhotoBench metrics contract v2

本契约是 T006 指标正确性修复的兼容性基线；不补齐 vLLM 专项指标。

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
