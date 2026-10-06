import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Run } from "../../api/types.ts";
import { KeyMetrics } from "./KeyMetrics.tsx";

// 1.0 参考 `App.vue:3130-3146`。两种口径必须给出不同答案：
//   - 有 `image_ranking.question_count` → 「召回指标」折叠块出现；
//   - 没有（该 run 未落盘候选顺序）→ `recallMetricRows` 回空数组 → 整块不渲染。
afterEach(cleanup);

const RUN: Run = {
  run_id: "r1",
  mode: "full",
  status: "done",
  summary: {
    answer_quality_mean: 1.5,
    total: 4,
    judge_distribution: { "0": 0, "1": 2, "2": 2 },
    image_ranking: {
      question_count: 3,
      r_at_1: 0.33,
      r_at_5: 0.66,
      r_at_10: 1,
      hit_at_1: 0.33,
      hit_at_5: 0.66,
      hit_at_10: 1,
      average_precision: 0.5,
      mrr: 0.6,
      p_at_5: 0.4,
      r_at_1_question_count: 1,
      r_at_5_question_count: 3,
      r_at_10_question_count: 3,
      p_at_5_question_count: 2,
      multi_positive_count: 1,
      short_candidate_count: 0,
      coverage_short_count: 0,
      no_candidate_count: 0,
    },
  },
} as Run;

describe("KeyMetrics（1.0 :3130-3146）", () => {
  it("渲染关键指标卡的标题/值/note，以及折叠的召回指标", () => {
    const { container } = render(<KeyMetrics run={RUN} />);

    expect(screen.getByText("关键指标")).not.toBeNull();
    expect(container.querySelector(".key-metric-list")).not.toBeNull();
    expect(screen.getByText("回答质量均分")).not.toBeNull();
    expect(screen.getByText("1.5 / 2")).not.toBeNull();
    // 召回指标是**折叠**的（<details>），不是平铺。
    const recall = container.querySelector("details.recall-metric-card");
    expect(recall).not.toBeNull();
    expect(recall?.querySelector("summary")?.textContent).toBe("召回指标");
    expect(screen.getByText("R@1")).not.toBeNull();
  });

  it("没有候选顺序（question_count 缺失）时，整块召回指标不渲染", () => {
    const { container } = render(<KeyMetrics run={{ ...RUN, summary: { answer_quality_mean: 1 } }} />);

    expect(container.querySelector(".key-metric-list")).not.toBeNull();
    expect(container.querySelector(".recall-metric-card")).toBeNull();
    expect(screen.queryByText("召回指标")).toBeNull();
  });
});
