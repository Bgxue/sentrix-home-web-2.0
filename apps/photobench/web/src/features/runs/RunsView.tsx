/**
 * 「评测运行」视图：把 `RunsTable`（1.0 `:2989-3037`）与 `RunDetail`（1.0 `:3039-` 起）
 * 组成完整视图。
 *
 * **数据层由组合层注入**：`useRuns`（内含 `useRunDetail`）在 `App.tsx` 里调用一次——
 * 1.0 `init()`（`:2731-2767`）要在挂载时 `loadRuns(1)`、发现运行中的 run 就自动选中并开
 * 轮询，这些都需要 runs 的数据层；若本组件自己再调一次 `useRuns`，就会有两个实例、
 * 两条轮询。所以这里只接 `runs`（外加配置切片的 `judgeModel` / `saveJudgePrompt`，
 * 经 `RejudgeCard` 的接缝透传），**不发请求**。
 *
 * 1.0 里 RunsTable 与 RunDetail 是 `<main class="app-shell">` 下的兄弟节点（中间没有包装层），
 * 所以这里返回 fragment，不加多余 DOM。
 */
import type { UseRunsApi } from "../../hooks/useRuns.ts";
import { RunDetail } from "./RunDetail.tsx";
import { RunsTable } from "./RunsTable.tsx";

export interface RunsViewProps {
  /** 组合层持有的 runs 数据层（`useRuns`，已内含 `useRunDetail`）。 */
  runs: UseRunsApi;
  /** 配置切片的 `judgeModel`；`RejudgeCard` 缺省显示 `-`（1.0 `judgeModel || '-'`）。 */
  judgeModel: string;
  /** 配置切片的 `saveJudgePrompt()`；不接则「保存提示词」按钮无作用。 */
  saveJudgePrompt: () => void;
}

export function RunsView({ runs, judgeModel, saveJudgePrompt }: RunsViewProps) {
  return (
    <>
      <RunsTable
        runs={runs.runs}
        runPage={runs.runPage}
        activeRunId={runs.activeRunId}
        onSelectRun={runs.selectRun}
        onDeleteRun={(run) => {
          void runs.deleteRun(run);
        }}
        onChangeRunPage={runs.changeRunPage}
        regionRef={runs.runsRegionRef}
      />
      <RunDetail
        activeRun={runs.activeRun}
        exportScores={runs.exportScores}
        onExportScoresChange={runs.setExportScores}
        onExportTraces={runs.exportTraces}
        onExportSftTraces={runs.exportSftTraces}
        rejudge={{
          activeRejudge: runs.activeRejudge,
          judgePromptKinds: runs.judgePromptKinds,
          activePromptKind: runs.activePromptKind,
          onSwitchPromptKind: runs.switchPromptKind,
          rejudgePrompt: runs.rejudgePrompt,
          onRejudgePromptChange: runs.setRejudgePrompt,
          judgeModel,
          onSavePrompt: saveJudgePrompt,
          onResetPrompt: runs.resetJudgePrompt,
          canRejudge: runs.canRejudge,
          rejudgeSubmitting: runs.rejudgeSubmitting,
          rejudgeConfirmation: runs.rejudgeConfirmation,
          onRequestRejudge: runs.requestRejudge,
          onConfirmRejudge: () => {
            void runs.confirmRejudge();
          },
          onCancelRejudge: runs.cancelRejudge,
          rejudgePercent: runs.rejudgePercent,
        }}
      />
    </>
  );
}
