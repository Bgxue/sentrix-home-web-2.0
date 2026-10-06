import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Run } from "../../api/types.ts";

const { mockApi } = vi.hoisted(() => ({ mockApi: vi.fn() }));
vi.mock("../../api/client.ts", () => ({ api: mockApi, post: vi.fn() }));

import { QaResults } from "./QaResults.tsx";

afterEach(cleanup);

const RUN: Run = { run_id: "run1", album_id: "album3", status: "completed", rejudge: null };

const FACETS = {
  task_types: ["event_memory_qa"],
  tags: ["夜晚"],
  angles: ["object"],
  difficulties: ["hard"],
  answerabilities: ["answerable"],
  agent_statuses: ["completed"],
  attribution_layers: ["tool"],
};

/** 只有**全部 9 个条件**同时命中才返回那一条——少一个都不成立。 */
const REQUIRED: Record<string, string> = {
  search: "电池",
  score: "2",
  task_type: "event_memory_qa",
  tag: "夜晚",
  angle: "object",
  difficulty: "hard",
  answerability: "answerable",
  agent_status: "completed",
  primary: "tool",
};

beforeEach(() => {
  mockApi.mockReset();
  mockApi.mockImplementation((path: string) => {
    if (!path.includes("/items?")) return Promise.reject(new Error(path));
    const params = new URL(path, "http://localhost").searchParams;
    const all = Object.entries(REQUIRED).every(([key, value]) => params.get(key) === value);
    const items = all ? [{ index: 1, question: "电池续航够吗" }] : [];
    return Promise.resolve({
      items,
      page: 1,
      page_size: 20,
      total: items.length,
      pages: 1,
      unfiltered_total: 42,
      facets: FACETS,
    });
  });
});

function setSelect(placeholderOption: string, value: string): void {
  const select = screen.getByText(placeholderOption).closest("select");
  expect(select).not.toBeNull();
  fireEvent.change(select!, { target: { value } });
}

describe("QaResults · 筛选表单的 9 个条件", () => {
  it("只有 8 个下拉 + 搜索全部命中才出现结果；少一个就不出现", async () => {
    render(<QaResults activeRun={RUN} />);
    await screen.findByText("展开逐题结果（0 条）");

    fireEvent.change(screen.getByPlaceholderText("搜索题号或问题"), { target: { value: "电池" } });
    setSelect("全部 Judge 分数", "2");
    setSelect("全部任务类型", "event_memory_qa");
    setSelect("全部标签", "夜晚");
    setSelect("全部问题角度", "object");
    setSelect("全部难度", "hard");
    setSelect("全部可回答性", "answerable");
    setSelect("全部 Agent 状态", "completed");
    setSelect("全部归因层", "tool");

    const form = screen.getByText("筛选").closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);

    await screen.findByText("电池续航够吗");

    // 对照：撤回一个条件（难度），组合不再成立 → 结果消失。
    setSelect("全部难度", "");
    fireEvent.submit(form!);
    await waitFor(() => expect(screen.queryByText("电池续航够吗")).toBeNull());
  });
});
