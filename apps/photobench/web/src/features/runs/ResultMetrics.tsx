/**
 * 「结果指标」卡片组（不含实时遥测卡）。逐行照搬 1.0 `App.vue:3331-3430`：
 * GPU 指标 / 可比较显存 / 指标汇总 + token 分布 / 确定性交付与 OCR partial /
 * 主 Agent 工具性能 / 运行可追溯信息。
 *
 * 1.0 的三处组件内助手（`resultPhaseStatus` `:2154-2157`、`gpuMetricsStatusLabel`
 * `:2158-2162`、`isOrinPssRun` `:2274-2284`）没有落进 `lib/`，只服务这几张卡，
 * 因此留在本文件内；它们读的全局 `activeRun` 改成显式入参 `run`。
 *
 * `<h3 class="result-heading">结果指标</h3>` 与 `<div class="result-phase-list">`
 * 由组合层（`RunDetail`）提供，因为 1.0 的第一张卡是 `TelemetryCard`，它排在本组件之前。
 */
import type { Run } from "../../api/types.ts";
import { deliveryBreakdown, shortHash, snapshotSummary, toolPerformanceRows } from "../../lib/details.ts";
import { fmtMs, fmtPct, fmtSeconds, phaseSeconds, statusLabel } from "../../lib/format.ts";
import { effectiveRunSummary } from "../../lib/runSummary.ts";
import {
  aggregateMetricRows,
  comparableMemoryProfile,
  gpuMetricRows,
  gpuMetricsView,
  memoryProfileRows,
  tokenDistributionCount,
  tokenDistributionRows,
} from "../../lib/telemetry.ts";
import type { MetricRow } from "../../lib/telemetry.ts";

type Dict = Record<string, any>;

/** 1.0 的可选链语义：非对象一律当空对象（`Run` 的索引签名是 `unknown`）。 */
function dict(value: unknown): Dict {
  return typeof value === "object" && value !== null ? (value as Dict) : {};
}

/** Vue `{{ }}` 的等价：`null` / `undefined` 渲染空串，其余 `String()`。 */
function vtext(value: unknown): string {
  return value == null ? "" : String(value);
}

/** 1.0 `:2154-2157`。`phase.status` 缺省时按 run 终态给 `not_run` / `pending`。 */
function resultPhaseStatus(phase: unknown, run: Run | null | undefined): string {
  const p = dict(phase);
  if (p.status) return String(p.status);
  return ["cancelled", "interrupted", "failed"].includes(String(run?.status)) ? "not_run" : "pending";
}

/** 1.0 `:2158-2162`。 */
function gpuMetricsStatusLabel(run: Run | null | undefined): string {
  const phase = gpuMetricsView(run);
  if (phase.partial && phase.status === "running") return "实时更新中";
  return statusLabel(resultPhaseStatus(phase, run));
}

/** 1.0 `:2274-2284`。 */
function isOrinPssRun(run: Run | null | undefined): boolean {
  const sources: unknown[] = ["orin_ssh_pss", "jetson_local_pss", "apple_unified"];
  const r = dict(run);
  const gpu = dict(dict(r.phases).gpu_metrics);
  const method = dict(gpu.memory_profile).method || dict(comparableMemoryProfile(run)?.memory_profile).method;
  return (
    sources.includes(r.telemetry_source) ||
    sources.includes(dict(r.telemetry_live).source) ||
    sources.includes(gpu.source) ||
    method === "orin_process_pss_uma_v1" ||
    method === "apple_phys_footprint_v1"
  );
}

/** 1.0 `:3338` 的 `:class="['phase-metric', { 'priority-metric': row[3] }]"`。 */
function metricClass(row: MetricRow): string {
  return row[3] ? "phase-metric priority-metric" : "phase-metric";
}

export interface ResultMetricsProps {
  run: Run | null | undefined;
}

export function ResultMetrics({ run }: ResultMetricsProps) {
  // 1.0 在每张卡里现算 `effectiveRunSummary(activeRun.value)`（`:426`）；纯函数、不发请求。
  const summary = effectiveRunSummary(run);
  const gpu = gpuMetricsView(run);
  const comparable = comparableMemoryProfile(run);
  const delivery = deliveryBreakdown(summary);
  const tools = toolPerformanceRows(summary);
  const integrity = dict(run?.input_integrity);
  const snapshots = dict(run?.hardware_snapshots);
  const start = snapshotSummary(snapshots.start);
  const end = snapshotSummary(snapshots.end);
  const concurrency = dict(run).qa_concurrency;

  /** 1.0 `:3336`。 */
  const gpuTime = gpu.partial
    ? "已按当前已采样数据滚动汇总；全部阶段结束后更新为全程均值/峰值。"
    : "指标计算耗时 " + fmtSeconds(phaseSeconds(run?.phases?.gpu_metrics));
  const gpuSource =
    gpu.source === "apple_unified"
      ? "Mac：主指标是主模型 phys_footprint"
      : ["orin_ssh_pss", "jetson_local_pss"].includes(String(gpu.source))
        ? "Orin：主指标是 llama-server 的 VmRSS"
        : gpu.memory_pressure
          ? "macOS 统一内存系统级采样（含模型 Metal 分配）"
          : "模型进程显存为 NVML 按 PID 汇总的实际占用，KV Cache 为 vLLM 逻辑使用率";
  return (
    <>
      <details className="phase-card result-phase-card gpu-result-card">
        <summary className="phase-title">
          <b>GPU 指标</b>
          <span className={`phase-status ${resultPhaseStatus(gpu, run)}`}>{gpuMetricsStatusLabel(run)}</span>
        </summary>
        <p className="metric-calc-time">
          {gpuTime} · {gpuSource}
          {Number(concurrency) > 1
            ? ` · QA 并发 ${concurrency}（时延含排队，勿与串行 run 直接对比）`
            : ""}
        </p>
        <div className="phase-metrics">
          {gpuMetricRows(gpu).map((row) => (
            <div key={row[0]} className={metricClass(row)}>
              <span>{row[0]}</span>
              <strong>{row[1]}</strong>
              <small>{row[2]}</small>
            </div>
          ))}
        </div>
      </details>

      {!isOrinPssRun(run) ? (
        <details className="phase-card result-phase-card gpu-result-card">
          <summary className="phase-title">
            <b>{comparable?.source === "replay" ? "可比较显存复测" : "可比较显存"}</b>
            <span className={`phase-status ${comparable?.status || "pending"}`}>
              {statusLabel(comparable?.status || "pending")}
            </span>
          </summary>
          <p className="metric-calc-time">
            {(dict(comparable?.memory_profile).method === "orin_process_pss_uma_v1"
              ? "Orin 统一内存按进程 PSS 记录；KV 字节数尚不可用，不与独立显存直接比较。"
              : comparable?.source === "gpu_metrics"
                ? "来自本次正式评测 GPU 采样；"
                : comparable?.source === "replay"
                  ? "复用现有相册与问题，不运行 Benchmark/Judge，不保存本次回答；"
                  : "本次 run 的 GPU 采样结束后生成；") + "工作负载显存为扣除预留 KV 后的估算值，不是硬件实测。"}
          </p>
          {comparable?.error ? <p className="error">{vtext(comparable.error)}</p> : null}
          <div className="phase-metrics">
            {memoryProfileRows(comparable || {}).map((row) => (
              <div key={row[0]} className={metricClass(row)}>
                <span>{row[0]}</span>
                <strong>{row[1]}</strong>
                <small>{row[2]}</small>
              </div>
            ))}
          </div>
        </details>
      ) : null}

      <details className="phase-card result-phase-card aggregate-result-card">
        <summary className="phase-title">
          <b>指标汇总</b>
          <span className={`phase-status ${resultPhaseStatus(run?.phases?.aggregate, run)}`}>
            {statusLabel(resultPhaseStatus(run?.phases?.aggregate, run))}
          </span>
        </summary>
        <p className="metric-calc-time">指标计算耗时 {fmtSeconds(phaseSeconds(run?.phases?.aggregate))}</p>
        <div className="phase-metrics">
          {aggregateMetricRows(run, summary).map((row) => (
            <div key={row[0]} className={metricClass(row)}>
              <span>{row[0]}</span>
              <strong>{row[1]}</strong>
              <small>{row[2]}</small>
            </div>
          ))}
        </div>
        <div className="token-distribution-section">
          <div className="phase-title">
            <b>主 Agent 单次调用 Token 分布</b>
            <span className="muted small">共 {tokenDistributionCount(summary)} 次模型调用</span>
          </div>
          <p className="metric-calc-time">输入、输出和总上下文均按每次主 Agent 模型调用独立统计</p>
          <div className="token-distribution-grid">
            {tokenDistributionRows(summary).map((row) => (
              <div key={row[0]} className="phase-metric">
                <span>{row[0]}</span>
                <strong>{row[1]}</strong>
                <small>{row[2]}</small>
              </div>
            ))}
          </div>
        </div>
      </details>

      {delivery ? (
        <details className="phase-card result-phase-card">
          <summary className="phase-title">
            <b>确定性交付与 OCR partial</b>
            <span className="muted small">结构层诊断</span>
          </summary>
          <div className="tool-performance-grid">
            {delivery.detCount ? (
              <div className="tool-performance-row">
                <strong>确定性渲染</strong>
                <span>{delivery.detCount} 题直接渲染，未走模型生成</span>
                {delivery.detKinds.length ? (
                  <span>类型 {delivery.detKinds.map(([k, v]) => `${k}×${v}`).join("、")}</span>
                ) : null}
              </div>
            ) : null}
            {delivery.ocrPartial ? (
              <div className="tool-performance-row">
                <strong>OCR partial</strong>
                <span>{delivery.ocrPartial} 题因 OCR 失败以 partial 语义收尾</span>
                {delivery.ocrReasons.length ? (
                  <span>原因 {delivery.ocrReasons.map(([k, v]) => `${k}×${v}`).join("、")}</span>
                ) : null}
              </div>
            ) : null}
          </div>
        </details>
      ) : null}

      <details className="phase-card result-phase-card tool-result-card">
        <summary className="phase-title">
          <b>主 Agent 工具性能</b>
          <span className="muted small">{tools.length} 类工具</span>
        </summary>
        <p className="metric-calc-time">按工具调用次数、成功率和耗时汇总；不展示具体后端实现与内部推理细节</p>
        {tools.length ? (
          <div className="tool-performance-grid">
            {tools.map((tool) => (
              <div className="tool-performance-row" key={tool.name}>
                <strong>{tool.name}</strong>
                <span>
                  调用 {vtext(tool.calls)} · 成功 {tool.ok_rate == null ? "-" : fmtPct(tool.ok_rate)}
                </span>
                <span>
                  P50 {fmtMs(tool.p50_ms)} · P95 {fmtMs(tool.p95_ms)} · max {fmtMs(tool.max_ms)}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="qa-performance-empty">该运行没有记录工具调用。</p>
        )}
      </details>

      <article className="phase-card result-phase-card traceability-card">
        <details>
          <summary>
            <strong>运行可追溯信息</strong>
            <span>数据集完整性与模型运行时起止快照</span>
          </summary>
          <div className="traceability-grid">
            <div>
              <b>输入数据校验</b>
              <span>数据集 {shortHash(integrity.dataset_sha256)}</span>
              <span>
                Manifest {shortHash(integrity.manifest_sha256)} · QA {shortHash(integrity.qa_sha256)}
              </span>
              <span>
                {run?.input_integrity
                  ? `${vtext(integrity.files_checked)} 个文件 · 缺失 ${
                      Array.isArray(integrity.missing_files) ? integrity.missing_files.length : 0
                    }`
                  : "该历史运行未记录"}
              </span>
            </div>
            <div>
              <b>运行开始</b>
              <span>
                {start.time} · {start.model}
              </span>
              <span>{start.gpu}</span>
              <span>
                整卡 {start.memory} · 模型进程 {start.processMemory}
              </span>
            </div>
            <div>
              <b>运行结束</b>
              <span>
                {end.time} · {end.model}
              </span>
              <span>{end.gpu}</span>
              <span>
                整卡 {end.memory} · 模型进程 {end.processMemory}
              </span>
            </div>
          </div>
        </details>
      </article>
    </>
  );
}
