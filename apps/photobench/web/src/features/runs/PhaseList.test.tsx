import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Run } from "../../api/types.ts";
import { PhaseList } from "./PhaseList.tsx";

// 1.0 参考 `App.vue:3097-3129`。夹具口径：6 张卡固定；status / error_details
// 有与无必须给出不同答案。

afterEach(cleanup);

function activeRun(phases: Run["phases"]): Run {
  return { run_id: "r1", phases };
}

describe("阶段卡（1.0 :3099-3129）", () => {
  it("固定渲染 6 张卡，状态文案随 phase.status 变化", () => {
    const { container } = render(
      <PhaseList activeRun={activeRun({ model_deploy: { status: "done" }, qa_eval: { status: "running" } })} />,
    );

    expect(container.querySelectorAll(".phase-card")).toHaveLength(6);
    const statuses = container.querySelectorAll(".phase-card .phase-status");
    expect(statuses[0]?.textContent).toBe("完成");
    expect(statuses[5]?.textContent).toBe("进行中");
    expect(statuses[1]?.textContent).toBe("等待"); // 未提供 → pending
  });

  it("错误明细有/无两种口径", () => {
    const absent = render(<PhaseList activeRun={activeRun({ model_deploy: { status: "done" } })} />);
    expect(absent.container.querySelector(".phase-errors")).toBeNull();
    cleanup();

    const present = render(
      <PhaseList
        activeRun={activeRun({
          model_deploy: {
            status: "failed",
            error_details: [{ sample_id: "s1", reason: "原因", status: "failed", asset_id: "a1" }],
          },
        })}
      />,
    );
    const errors = present.container.querySelector(".phase-errors");
    expect(errors).not.toBeNull();
    expect(errors?.querySelector("summary")?.textContent).toBe("1 条错误记录");
    expect(errors?.textContent).toContain("s1");
    expect(errors?.textContent).toContain("原因");
    expect(errors?.textContent).toContain("a1");
  });

  it("pipeline_processing 进度条宽度 = (processed+failed+skipped)/total", () => {
    const { container } = render(
      <PhaseList
        activeRun={activeRun({
          pipeline_processing: {
            status: "running",
            progress: { total: 10, processed: 3, failed: 1, skipped: 1 },
          },
        })}
      />,
    );

    // EXECUTION_PHASES 第 5 个（index 4）是 pipeline_processing。
    const phase = container.querySelectorAll(".phase-card")[4];
    const fill = phase?.querySelector(".phase-bar-fill") as HTMLElement | null;
    expect(fill?.style.width).toBe("50%");
  });
});
