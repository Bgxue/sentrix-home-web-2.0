import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { RunItemDetail } from "../../api/types.ts";

import { AgentLoopTree } from "./AgentLoopTree.tsx";

afterEach(cleanup);

/** 一条成功的 agent 调用；`agentLoopGroups` 会把它归到第 0 轮。 */
const DETAIL: RunItemDetail = {
  index: 0,
  question: "车停在哪",
  model_call_metrics: [
    {
      call_type: "agent",
      step_id: "s1",
      role: "assistant",
      model: "qwen2.5-vl",
      total_ms: 1200,
      ttft_ms: 200,
      prompt_tokens: 500,
      completion_tokens: 40,
      tokens_per_second: 30,
      streamed: true,
    },
  ],
};

// 1.0 用原生 `<details>` 折叠调用体（:3561）。jsdom 会把子节点一直留在 DOM 里，
// 所以「展开 / 折叠」的可观测真值是 details 的 `open`——夹具必须两边给出不同答案。
describe("AgentLoopTree · 调用体展开 / 折叠", () => {
  it("点 summary 展开（open=true），再点折叠（open=false）；子节点一直在 details 内", () => {
    render(<AgentLoopTree detail={DETAIL} modelLabel="qwen" />);

    const summary = screen.getByText("Agent 决策 / 回答").closest("summary");
    expect(summary).not.toBeNull();
    const details = summary!.closest("details");
    expect(details).not.toBeNull();

    expect(details!.open).toBe(false);

    fireEvent.click(summary!);
    expect(details!.open).toBe(true);
    // 展开后子节点（延迟组成 / 用途栏）确实挂在 details 内部。
    expect(details!.contains(screen.getByText("用途"))).toBe(true);

    fireEvent.click(summary!);
    expect(details!.open).toBe(false);
  });
});
