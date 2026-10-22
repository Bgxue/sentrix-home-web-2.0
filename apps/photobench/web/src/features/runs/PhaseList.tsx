/**
 * 6 张 Pipeline 执行阶段卡 + 阶段错误明细。逐行照搬
 * 1.0 `services/photobench/frontend/src/App.vue:3097-3129`（只读权威；任务表给的是
 * `:3090-3118`，实测 phase-list 到 `</div>` 收在 `:3129`）。
 *
 * `phasePercent` 是 1.0 `App.vue:2195-2198` 的私有函数，2.0 lib 尚未收，照搬在本文件。
 * `phaseSummary` / `phaseErrorDetails` / `phaseErrorSummary` / `pipelineMetricRows` 已在
 * `lib/details.ts`，直接 import。
 *
 * `phaseSummary(key, phase, itemCount)` 的第三参是 1.0 闭包读的 `activeRun.item_count`
 * （1.0 `:2175` 的 `activeRun.value?.item_count`），模板里只传两个参数——这里补上。
 */
import type { Phase, Run } from "../../api/types.ts";
import { EXECUTION_PHASES } from "../../lib/constants.ts";
import { phaseErrorDetails, phaseErrorSummary, phaseSummary, pipelineMetricRows } from "../../lib/details.ts";
import { statusLabel } from "../../lib/format.ts";

/** 1.0 `App.vue:2195-2198`。 */
function phasePercent(phase: Phase | undefined): number {
  const progress = phase?.progress;
  return progress?.total
    ? Math.min(
        100,
        Math.round(
          (((Number(progress.processed) || 0) +
            (Number(progress.failed) || 0) +
            (Number(progress.skipped) || 0)) /
            progress.total) *
            100,
        ),
      )
    : 0;
}

export interface PhaseListProps {
  activeRun: Run;
}

export function PhaseList({ activeRun }: PhaseListProps) {
  return (
    <>
      <h3>Pipeline 执行阶段</h3>
      {activeRun.mode === "build" ? <p>仅构建记忆空间；QA、Judge 和评分有效性不适用。</p> : null}
      <div className="phase-list">
        {EXECUTION_PHASES.filter((p) => activeRun.mode !== "build" || p.key !== "qa_eval").map((phaseDef, index) => {
          const phase = activeRun.phases?.[phaseDef.key];
          const errors = phaseErrorDetails(phase);
          const metrics = pipelineMetricRows(phase);
          return (
            <article className="phase-card" key={phaseDef.key}>
              <div className="phase-title">
                <div className="phase-name">
                  <span className="phase-step">{index + 1}</span>
                  <b>{phaseDef.label}</b>
                </div>
                <span className={"phase-status " + (phase?.status || "pending")}>
                  {statusLabel(phase?.status || "pending")}
                </span>
              </div>
              <p className="phase-summary">{phaseSummary(phaseDef.key, phase, activeRun.item_count)}</p>
              {phaseDef.key === "photo_import" && phase?.progress?.current_directory ? (
                <p className="phase-summary" data-testid="import-waiting">
                  当前目录：{String(phase.progress.current_directory)}；
                  已完成目录：{Number(phase.progress.directories_done ?? 0)}/{Number(phase.progress.directories_total ?? 0)}；
                  等待同步导入响应：{Math.max(0, Math.floor((
                    (typeof phase.finished_at === "string" ? Date.parse(phase.finished_at) : Date.now()) -
                    Date.parse(String(phase.progress.request_started_at))
                  ) / 1000))} 秒（请求返回前无逐媒体进度）
                </p>
              ) : null}
              {errors.length > 0 ? (
                <details className="phase-errors">
                  <summary>{phaseErrorSummary(phase)}</summary>
                  <ul>
                    {errors.slice(0, 20).map((raw, detailIndex) => {
                      const detail = raw as Record<string, unknown>;
                      return (
                        <li key={`${String(detail.sample_id || "error")}-${detailIndex}`}>
                          <b>{String(detail.sample_id || "未知样本")}</b>
                          <span>{String(detail.reason || detail.error || "未提供原因")}</span>
                          <small>
                            {String(detail.error_type || detail.status || "错误")}
                            {detail.asset_id ? ` · ${String(detail.asset_id)}` : ""}
                          </small>
                        </li>
                      );
                    })}
                  </ul>
                  {errors.length > 20 ? <p>仅展示前 20 条，完整记录已保存在 run.json。</p> : null}
                </details>
              ) : null}
              {phaseDef.key === "pipeline_processing" && phase?.progress ? (
                <div className="phase-bar">
                  <div className="phase-bar-fill" style={{ width: `${phasePercent(phase)}%` }} />
                </div>
              ) : null}
              {phaseDef.key === "pipeline_processing" && metrics.length > 0 ? (
                <div className="phase-metrics pipeline-metrics">
                  {metrics.map((row) => (
                    <div className="phase-metric" key={row[0]}>
                      <span>{row[0]}</span>
                      <strong>{row[1]}</strong>
                      <small>{row[2]}</small>
                    </div>
                  ))}
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </>
  );
}
