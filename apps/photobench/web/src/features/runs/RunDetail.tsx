/**
 * 运行详情外壳。逐行照搬 1.0
 * `services/photobench/frontend/src/App.vue:3039-3058`（只读权威）：标题 / 状态 /
 * 导出轨迹控制（0/1/2 分勾选）/ `run-meta` / `fatal_error` 横幅，并装配
 * `RejudgeCard`、`PhaseList`。
 *
 * **`detailRef` 的落点说明**：`useRuns` 的 `runDetailRef` 在 1.0 对应的锚点是
 * `#qa-results`（`App.vue:2372`，`scrollToRunDetail` 的目标），而 `#qa-results` 是
 * 逐题结果切片的地盘，本切片不建。这里先把它挂在 `#detail-region` 上，让
 * `selectRun` 的滚动立即可用；逐题结果切片接手 `#qa-results` 时应把 ref 移过去。
 *
 * **「导出 SFT JSON」按钮**：当前 1.0 删了它、最老备份（`App.vue.bak.codex-macro-*`）
 * 有，后端端点一直在（见 `docs/refactor/s10-deviations.md` §4.3）。按裁决加回，与
 * 「导出轨迹 JSON」并排；URL 生成在 `useRunDetail.exportSftTraces`。备份里这个按钮
 * 没有 `title`（当前 `导出轨迹 JSON` 的 `title` 保留原样）。
 *
 * **阶段 3d 的三块不在这里实现**：见文末占位区块。
 */
import type { ReactNode, RefObject } from "react";

import type { Run } from "../../api/types.ts";
import { EXECUTION_PHASES } from "../../lib/constants.ts";
import { albumName, duration, fmtDate, modelName, qaName, statusLabel } from "../../lib/format.ts";
import { PhaseList } from "./PhaseList.tsx";
import { RejudgeCard } from "./RejudgeCard.tsx";
import type { RejudgeCardProps } from "./RejudgeCard.tsx";

/** 1.0 `:3057` 的 `<p class="run-meta">` 里 reuse/build 两段模板。 */
function runScopeMeta(run: Run): ReactNode {
  if (run.mode === "reuse") {
    const reused = Array.isArray(run.scope_reused_from_runs) ? (run.scope_reused_from_runs as unknown[]) : [];
    const album = run.scope_name || run.scope_id || (run.existing_scope_id ? String(run.existing_scope_id) : "");
    return (
      <>
        {" · 复用相册 "}
        {album}
        {reused.length > 0 ? (
          <span>（源自 run {reused.join("、")}）</span>
        ) : (
          <span>（外部创建，非编排器产物）</span>
        )}
      </>
    );
  }
  if (run.mode === "build") {
    return (
      <>
        {" · 产出相册 "}
        {run.scope_id || "-"}（已保留，可在复用测评中使用）
      </>
    );
  }
  return null;
}

/** 1.0 `:3049-3052` 的 checkbox group（值就是字符串 "0"/"1"/"2"）。 */
function toggleScore(current: string[], value: string, checked: boolean): string[] {
  const next = new Set(current);
  if (checked) next.add(value);
  else next.delete(value);
  return [...next];
}

export interface RunDetailProps {
  activeRun: Run | null;
  /** 1.0 `#qa-results` 的滚动落点（见文件头说明）。 */
  detailRef?: RefObject<HTMLElement | null>;
  exportScores: string[];
  onExportScoresChange: (scores: string[]) => void;
  onExportTraces: () => void;
  onExportSftTraces: () => void;
  rejudge: RejudgeCardProps;
}

export function RunDetail({
  activeRun,
  detailRef,
  exportScores,
  onExportScoresChange,
  onExportTraces,
  onExportSftTraces,
  rejudge,
}: RunDetailProps) {
  return (
    <section id="detail-region" ref={detailRef}>
      {!activeRun ? (
        <div className="section muted">点击上方列表中的某条记录查看详情</div>
      ) : (
        <section className="section detail-section">
          <div className="section-head detail-section-head">
            <h2>
              {modelName(activeRun)} · {albumName(activeRun)} · {qaName(activeRun)}
            </h2>
            <div className="detail-head-tools">
              <span className={"phase-status " + (activeRun.status || "")}>{statusLabel(activeRun.status)}</span>
              <div className="export-control">
                <span className="field-label">导出轨迹</span>
                <span className="export-score-filter">
                  {["0", "1", "2"].map((value) => (
                    <label className="checkbox-inline" key={value}>
                      <input
                        type="checkbox"
                        value={value}
                        checked={exportScores.includes(value)}
                        onChange={(event) => onExportScoresChange(toggleScore(exportScores, value, event.target.checked))}
                      />
                      {value} 分
                    </label>
                  ))}
                </span>
                <button className="btn compact" onClick={onExportTraces} title="每题导出 planner 完整输入/输出 + 完整轨迹">
                  导出轨迹 JSON
                </button>
                <button className="btn compact" onClick={onExportSftTraces}>
                  导出 SFT JSON
                </button>
              </div>
            </div>
          </div>
          <p className="run-meta">
            开始 {fmtDate(activeRun.started_at)} · 总耗时 {duration(activeRun)}
            {runScopeMeta(activeRun)}
          </p>
          {activeRun.fatal_error ? (
            <div className="run-error-banner">
              <b>任务终止原因</b>
              <span>{String(activeRun.fatal_error)}</span>
              {activeRun.failed_phase ? (
                <small>
                  失败阶段：
                  {EXECUTION_PHASES.find((item) => item.key === String(activeRun.failed_phase))?.label ||
                    String(activeRun.failed_phase)}
                </small>
              ) : null}
            </div>
          ) : null}
          {activeRun.mode !== "build" ? <RejudgeCard {...rejudge} /> : null}
          <PhaseList activeRun={activeRun} />
          {/* ── 3d 会替换这里 ────────────────────────────────────────────────
              阶段 3d 负责以下三块，本切片（3b）只留占位、不实现、不建对应文件：
                1. 记忆层级与有效证据链（1.0 `:3147-3284`：`memory-layer-panel` /
                   `memory-effectiveness-panel` / `keyframe-analysis-panel`）；
                2. 结果指标（1.0 `:3130-3136` 关键指标 + `:3285-3431` `result-phase-list`）；
                3. 实时资源遥测（1.0 `:3289` 起 `live-telemetry-card`）。
              另：1.0 的 QA 逐题结果 `#qa-results`（1.0 `:3432` 起）由逐题结果切片负责。 */}
          <div className="muted" data-testid="run-detail-3d-placeholder">
            记忆与证据链、结果指标、资源遥测由阶段 3d 接入。
          </div>
        </section>
      )}
    </section>
  );
}
