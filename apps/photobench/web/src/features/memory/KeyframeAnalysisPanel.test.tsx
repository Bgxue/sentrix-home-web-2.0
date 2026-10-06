import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { KeyframeAnalysis } from "../../api/types.ts";

import { KeyframeAnalysisPanel } from "./KeyframeAnalysisPanel.tsx";

// 夹具口径：8 格**都有值且互不相同**——全同的夹具证明不了字段映射对不对。
// 1.0 参考 `App.vue:3261-3282`。
afterEach(cleanup);

const analysis: KeyframeAnalysis = {
  status: "ready",
  asset_source_note: "album3 本次导入",
  assets: { keyframes: 120, source_videos: 6, keyframes_per_video_mean: 20 },
  retrieval: {
    video_parent_recall: 0.75,
    video_gt_targets: 8,
    video_gt_targets_hit_by_keyframe: 6,
    candidate_precision: 0.5,
    candidate_relevant_keyframes: 30,
    candidate_keyframes: 60,
    predicted_precision: 0.9,
    predicted_relevant_keyframes: 9,
    predicted_keyframes: 10,
  },
  temporal: { mean_gap_sec: 2.5, mean_span_sec: 31, videos_sampled: 6, selection_reasons: { uniform: 4, event: 2 } },
  usefulness: {
    answer_quality_delta: 0.4,
    answer_quality_with_keyframe: 1.6,
    answer_quality_without_keyframe: 1.2,
    event_context_count: 3,
  },
  processing: {
    retained_frame_ratio: 0.02,
    compression_ratio: 0.98,
    raw_frame_count: 6000,
    keyframe_count: 120,
    realtime_factor: 0.123,
    processing_seconds_total: 42,
    processing_seconds_mean: 7,
  },
};

function renderPanel(overrides: Partial<KeyframeAnalysis> = {}, loading = false, error = "") {
  const { container } = render(
    <KeyframeAnalysisPanel keyframeAnalysis={{ ...analysis, ...overrides }} keyframeAnalysisLoading={loading} keyframeAnalysisError={error} />,
  );
  const cells = Array.from(container.querySelectorAll(".keyframe-analysis-grid > div > b")).map(
    (node) => node.textContent,
  );
  return { container, cells };
}

describe("KeyframeAnalysisPanel · 8 格指标（1.0 :3261-3282）", () => {
  it("8 格按字段各自取值，且至少两格不同（证明映射不是写死的常量）", () => {
    const { cells } = renderPanel();

    expect(cells).toEqual(["120", "75.0%", "50.0%", "90.0%", "2.5s", "+0.40 / 2", "2.0% / 98.0%", "0.123×"]);
    expect(new Set(cells).size).toBeGreaterThan(1);
  });

  it("规模与召回格的说明文案带上源视频数与分母", () => {
    renderPanel();

    expect(screen.getByText(/6 个源视频 · 平均 20 帧\/视频/)).not.toBeNull();
    expect(screen.getByText(/8 个 GT 视频目标中，6 个被关键帧覆盖/)).not.toBeNull();
    expect(screen.getByText(/30\/60 个候选与 GT 父视频一致/)).not.toBeNull();
    expect(screen.getByText(/9\/10 个返回关键帧有效/)).not.toBeNull();
    expect(screen.getByText(/有关键帧 1.6 · 无关键帧 1.2（非因果）/)).not.toBeNull();
    expect(screen.getByText(/120 \/ 6000 原始帧/)).not.toBeNull();
    expect(screen.getByText(/总耗时 42s · 均值 7s\/视频/)).not.toBeNull();
    expect(screen.getByText(/证据源：album3 本次导入/)).not.toBeNull();
  });

  it("改一个字段只影响对应那一格", () => {
    const { cells } = renderPanel({
      assets: { ...analysis.assets, keyframes: 240 },
      retrieval: { ...analysis.retrieval, predicted_precision: 0.6 },
    });

    expect(cells[0]).toBe("240");
    expect(cells[3]).toBe("60.0%");
    expect(cells[1]).toBe("75.0%");
    expect(cells[4]).toBe("2.5s");
  });

  it("未 ready 时没有网格与指标定义，只有提示句；loading 时显示「分析中」", () => {
    const { container } = render(
      <KeyframeAnalysisPanel keyframeAnalysis={null} keyframeAnalysisLoading={false} keyframeAnalysisError="" />,
    );
    expect(container.querySelector(".keyframe-analysis-grid")).toBeNull();
    expect(container.querySelector(".metric-definition-panel")).toBeNull();
    expect(screen.getByText("尚未取得完整 run 轨迹，暂不能计算关键帧有效性。")).not.toBeNull();

    cleanup();
    render(<KeyframeAnalysisPanel keyframeAnalysis={null} keyframeAnalysisLoading keyframeAnalysisError="" />);
    expect(screen.getByText("分析中")).not.toBeNull();
    expect(screen.queryByText("尚未取得完整 run 轨迹，暂不能计算关键帧有效性。")).toBeNull();
  });

  it("错误优先于网格（1.0 :3263 的 v-if / v-else-if）", () => {
    const { container } = render(
      <KeyframeAnalysisPanel keyframeAnalysis={analysis} keyframeAnalysisLoading={false} keyframeAnalysisError="关键帧分析失败" />,
    );
    expect(screen.getByText("关键帧分析失败")).not.toBeNull();
    expect(container.querySelector(".keyframe-analysis-grid")).toBeNull();
  });

  it("指标定义表逐条渲染（1.0 :2046-2054 七条）", () => {
    const { container } = render(
      <KeyframeAnalysisPanel keyframeAnalysis={analysis} keyframeAnalysisLoading={false} keyframeAnalysisError="" />,
    );
    const rows = container.querySelectorAll(".metric-definition-row");
    expect(rows).toHaveLength(7);
    expect(Array.from(rows).map((row) => row.querySelector("strong")?.textContent)).toEqual([
      "保留率 / 压缩率",
      "源视频父级召回",
      "候选关键帧精确率",
      "最终返回精确率",
      "时间间隔 / 覆盖跨度",
      "回答质量差值",
      "视频处理速度",
    ]);
  });
});
