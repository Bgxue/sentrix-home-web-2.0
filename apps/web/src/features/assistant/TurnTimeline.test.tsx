import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { AssistantProgressEvent } from "../../shared/api/client";
import { buildCards, TurnTimeline } from "./TurnTimeline";

function event(overrides: Partial<AssistantProgressEvent>): AssistantProgressEvent {
  return {
    text: "",
    status: "ok",
    stage: "thinking",
    step_index: 1,
    timestamp: "2026-07-01T10:00:00+00:00",
    kind: "status",
    payload: {},
    ...overrides,
  };
}

const EVENTS: AssistantProgressEvent[] = [
  event({
    step_index: 1,
    stage: "planning",
    kind: "plan",
    status: "complete",
    text: "已确认需要哪些证据。",
    payload: {
      requirements: [
        {
          id: "req1",
          evidence_type: "time",
          description: "照片的时间范围",
          status: "open",
          satisfied_by: [],
          unmet_reason: "",
        },
      ],
    },
  }),
  event({
    step_index: 2,
    stage: "action",
    kind: "model_action",
    status: "running",
    text: "正在执行 search_memories…",
    payload: {
      tool: "search_memories",
      tool_call_id: "call1",
      arguments: { query: "海边" },
    },
  }),
  event({
    step_index: 3,
    stage: "tool_result",
    kind: "tool_result",
    status: "ok",
    text: "结果集里共有 7 张候选照片。",
    payload: {
      tool: "search_memories",
      tool_call_id: "call1",
      duration_ms: 12,
      result: {
        result_set_id: "rs_1",
        total: 7,
        has_more: true,
        asset_ids: ["a1", "a2"],
      },
    },
  }),
  event({
    step_index: 4,
    stage: "terminal",
    kind: "terminal",
    status: "complete",
    text: "已完成本轮回答。",
    payload: { status: "complete", termination_reason: "answered" },
  }),
];

afterEach(() => cleanup());

describe("推理时间线", () => {
  it("把 action 与同一次 tool_result 合成一张卡片", () => {
    const cards = buildCards(EVENTS);
    expect(cards.map((card) => card.kind)).toEqual([
      "planning",
      "tool",
      "final",
    ]);
    expect(cards[1].events.map((item) => item.step_index)).toEqual([2, 3]);
  });

  it("阶段卡片可折叠，展开后显示参数与返回摘要", () => {
    render(<TurnTimeline events={EVENTS} />);

    const toggle = screen.getByRole("button", { name: /记忆推理 · 4 个节点/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("工具调用")).toBeNull();

    fireEvent.click(toggle);

    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("规划")).toBeTruthy();
    expect(screen.getByText("工具调用")).toBeTruthy();
    expect(screen.getByText("收尾")).toBeTruthy();
    expect(screen.getByText(/照片的时间范围/)).toBeTruthy();
    expect(screen.getByText(/"query": "海边"/)).toBeTruthy();
    expect(screen.getByText("结果集 rs_1")).toBeTruthy();
    expect(screen.getByText("共 7 张")).toBeTruthy();
    expect(screen.getByText("耗时 12 ms")).toBeTruthy();
  });
});
