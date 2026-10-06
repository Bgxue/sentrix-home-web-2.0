/**
 * 「评测运行」视图：把 `RunsTable`（1.0 `:2989-3037`）与 `RunDetail`（1.0 `:3039-` 起）
 * 组成完整视图。数据全部来自 `useRuns`（它内部已组装 `useRunDetail`，见其文件头注释），
 * 本组件不自己再调 `useRunDetail`、不发请求。
 *
 * 1.0 里这两块是 `<main class="app-shell">` 下的兄弟节点（中间没有包装层），所以这里
 * 返回 fragment，不加多余 DOM。
 */
import { useRuns } from "../../hooks/useRuns.ts";
import { RunDetail } from "./RunDetail.tsx";
import { RunsTable } from "./RunsTable.tsx";

export function RunsView() {
  const runs = useRuns();

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
        detailRef={runs.runDetailRef}
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
