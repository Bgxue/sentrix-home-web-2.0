import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client.ts", () => ({ api: vi.fn(), post: vi.fn() }));

import { api, post } from "../api/client.ts";
import type { Run } from "../api/types.ts";
import { useRunDetail } from "./useRunDetail.ts";
import type { ActiveRunIdRef } from "./useRunDetail.ts";

// 1.0 参考：`services/photobench/frontend/src/App.vue`（只读权威）。
// 夹具原则：每组的两种口径必须给出不同答案（例如「并发」与「串行」、「可重评」与「不可重评」）。

const apiMock = vi.mocked(api);
const postMock = vi.mocked(post);

function runDetail(overrides: Partial<Run> = {}): Run {
  return { run_id: "r1", item_count: 3, status: "done", ...overrides };
}

function mount(activeRunId: string | null = "r1") {
  const ref: ActiveRunIdRef = { current: activeRunId };
  const hook = renderHook(() =>
    useRunDetail({ activeRunId, activeRunIdRef: ref, setError: () => {} }),
  );
  return { ...hook, ref };
}

beforeEach(() => {
  apiMock.mockReset();
  postMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("loadActiveRun 的并发关系（1.0 :2310-2332）", () => {
  it("run 详情**串行**在前：详情失败时 keyframe / memory 根本不发", async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path === "/runs/r2") throw new Error("detail boom");
      throw new Error(`不该请求：${path}`);
    });
    const { result, ref } = mount("r1");
    ref.current = "r2";

    await act(async () => {
      await expect(result.current.loadActiveRun()).rejects.toThrow("detail boom");
    });

    // 只有一条请求：串行闸门挡住了后面的并发集。若改成 Promise.all 全并发，这里会是 3 条。
    expect(apiMock).toHaveBeenCalledTimes(1);
    expect(apiMock.mock.calls[0]?.[0]).toBe("/runs/r2");
  });

  it("keyframe 失败**不影响** memory（两者是 Promise.all，各自吞异常）", async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path === "/runs/r1") return runDetail();
      if (path.endsWith("/keyframe-analysis")) throw new Error("kf boom");
      if (path.endsWith("/memory-effectiveness")) return { status: "ready", levels: [] };
      throw new Error(`不该请求：${path}`);
    });
    const { result } = mount("r1");

    await act(async () => {
      await result.current.loadActiveRun();
    });

    expect(result.current.keyframeAnalysisError).toBe("kf boom");
    expect(result.current.keyframeAnalysis).toBeNull();
    // 若 keyframe 的异常决定性地中断了流程，memory 就不会被写上。
    expect(result.current.memoryEffectiveness).toEqual({ status: "ready", levels: [] });
    // 并发集确实都发了。
    expect(apiMock.mock.calls.map((call) => call[0])).toEqual([
      "/runs/r1",
      "/runs/r1/keyframe-analysis",
      "/runs/r1/memory-effectiveness",
    ]);
  });

  it("memory 失败也不影响 keyframe（反方向，且失败落成 unavailable 包体）", async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path === "/runs/r1") return runDetail();
      if (path.endsWith("/keyframe-analysis")) return { status: "ready" };
      if (path.endsWith("/memory-effectiveness")) throw new Error("mem boom");
      throw new Error(`不该请求：${path}`);
    });
    const { result } = mount("r1");

    await act(async () => {
      await result.current.loadActiveRun();
    });

    expect(result.current.keyframeAnalysis).toEqual({ status: "ready" });
    expect(result.current.memoryEffectiveness).toEqual({
      status: "unavailable",
      error: "mem boom",
    });
  });

  it("run 详情走 60s 超时（1.0 :2313）", async () => {
    apiMock.mockImplementation(async () => runDetail());
    const { result } = mount("r1");
    await act(async () => {
      await result.current.loadActiveRun();
    });
    expect(apiMock.mock.calls[0]?.[1]).toEqual({ timeoutMs: 60000 });
  });

  it("resetPage 串行拉 reviews 并填充 reviewDrafts", async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path === "/runs/r1") return runDetail();
      if (path === "/runs/r1/reviews") return { reviews: { q1: { verdict: "correct" } } };
      if (path.endsWith("/keyframe-analysis")) return {};
      if (path.endsWith("/memory-effectiveness")) return {};
      throw new Error(`不该请求：${path}`);
    });
    const { result } = mount("r1");

    await act(async () => {
      await result.current.loadActiveRun({ resetPage: true });
    });

    expect(result.current.reviewFor("q1")).toEqual({ verdict: "correct" });
    // reviews 在并发集之前（串行）。
    expect(apiMock.mock.calls[1]?.[0]).toBe("/runs/r1/reviews");
  });
});

describe("exportTraces / exportSftTraces（1.0 :2567 / backup :1401）", () => {
  it("window.open 带正确的 scores 参数", () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    const { result } = mount("r1");

    act(() => {
      result.current.setExportScores(["1", "2"]);
    });
    act(() => {
      result.current.exportTraces();
    });
    expect(openSpy).toHaveBeenCalledWith("/api/runs/r1/export-trace?scores=1,2", "_blank");

    openSpy.mockClear();
    act(() => {
      result.current.exportSftTraces();
    });
    expect(openSpy).toHaveBeenCalledWith("/api/runs/r1/export-sft?scores=1,2", "_blank");
  });

  it("默认评分是 0,1,2（1.0 :2487）", () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    const { result } = mount("r1");
    act(() => {
      result.current.exportTraces();
    });
    expect(openSpy).toHaveBeenCalledWith("/api/runs/r1/export-trace?scores=0,1,2", "_blank");
  });

  it("一个都没勾选时不下载，改为提示", () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    const { result } = mount("r1");
    act(() => {
      result.current.setExportScores([]);
    });
    act(() => {
      result.current.exportTraces();
    });
    expect(openSpy).not.toHaveBeenCalled();
    expect(alertSpy).toHaveBeenCalledWith("请至少勾选一个评分再导出");
  });

  it("没有 activeRunId 时什么都不做", () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    const { result } = mount(null);
    act(() => {
      result.current.exportTraces();
      result.current.exportSftTraces();
    });
    expect(openSpy).not.toHaveBeenCalled();
  });
});

describe("canRejudge / rejudgePercent（1.0 :365-374）", () => {
  it("可重评：有 item_count + 非 running + 提示词非空", () => {
    const { result } = mount("r1");
    act(() => {
      result.current.setActiveRun(
        runDetail({ item_count: 5, status: "done", rejudge: { status: "completed", total: 4, completed: 1 } }),
      );
      result.current.setRejudgePrompt("你是 judge");
    });
    expect(result.current.canRejudge).toBe(true);
    expect(result.current.rejudgePercent).toBe(25);
  });

  it("不可重评：run 正在 running", () => {
    const { result } = mount("r1");
    act(() => {
      result.current.setActiveRun(runDetail({ item_count: 5, status: "running" }));
      result.current.setRejudgePrompt("你是 judge");
    });
    expect(result.current.canRejudge).toBe(false);
  });

  it("不可重评：重评任务正在 running（即便 run 本身 done）", () => {
    const { result } = mount("r1");
    act(() => {
      result.current.setActiveRun(
        runDetail({ item_count: 5, status: "done", rejudge: { status: "running", total: 4, completed: 2 } }),
      );
      result.current.setRejudgePrompt("你是 judge");
    });
    expect(result.current.canRejudge).toBe(false);
    expect(result.current.rejudgePercent).toBe(50);
  });

  it("不可重评：提示词为空（这一条单独就能否掉）", () => {
    const { result } = mount("r1");
    act(() => {
      result.current.setActiveRun(runDetail({ item_count: 5, status: "done" }));
      result.current.setRejudgePrompt("");
    });
    expect(result.current.canRejudge).toBe(false);
  });

  it("requestRejudge 只置「待确认动作」，confirmRejudge 才 POST", async () => {
    postMock.mockResolvedValue({});
    apiMock.mockImplementation(async () => runDetail());
    const { result } = mount("r1");
    act(() => {
      result.current.setActiveRun(runDetail({ item_count: 7, status: "done" }));
      result.current.setRejudgePrompt("p");
    });

    act(() => {
      result.current.requestRejudge();
    });
    expect(result.current.rejudgeConfirmation?.message).toContain("7 条 Agent 回答");
    expect(postMock).not.toHaveBeenCalled();

    await act(async () => {
      await result.current.confirmRejudge({ judge_url: " u ", judge_model: "m" });
    });
    expect(result.current.rejudgeConfirmation).toBeNull();
    expect(postMock).toHaveBeenCalledWith(
      "/runs/r1/rejudge",
      expect.objectContaining({ judge_url: "u", judge_model: "m", system_prompt: "p" }),
    );
  });
});

describe("reviewFor / saveReviews（1.0 :2333-2345）", () => {
  it("reviewFor 缺省返回空草稿；saveReviews 只提交有 verdict 的", async () => {
    postMock.mockResolvedValue({});
    apiMock.mockImplementation(async () => runDetail());
    const { result } = mount("r1");

    expect(result.current.reviewFor({ qa_id: "q9" })).toEqual({ verdict: "", note: "" });

    act(() => {
      result.current.setReview("q1", { verdict: "correct" });
      result.current.setReview("q2", { note: "只写备注" });
    });

    await act(async () => {
      await result.current.saveReviews();
    });

    expect(postMock).toHaveBeenCalledWith("/runs/r1/reviews", {
      reviews: { q1: { verdict: "correct", note: "" } },
    });
  });
});
