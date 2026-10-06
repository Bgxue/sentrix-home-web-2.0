import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useConfig } from "./useConfig.ts";

// 手写 stub，不引 msw：这里验的是 hook 的行为，夹具越少越不容易把「stub 的行为」
// 当成「hook 的行为」。同时给 text()（api() 用）与 json()（裸 fetch 用）。
function jsonResponse(status: number, body: unknown) {
  const text = JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => text,
    json: async () => JSON.parse(text),
  };
}

const CONFIG = {
  runtime_config: {
    sentrix_url: "127.0.0.1:8091",
    judge_url: "j:1234/v1",
    judge_model: "runtime-jm",
    judge_provider_id: "p2",
    vllm_manager_url: "mgr:8500",
    model_base_url: "ep:8100",
    endpoint_model: "em",
  },
  vllm_targets: { t1: { manager_url: "tmgr", model_base_url: "tep" } },
  default_vllm_target_id: "t1",
  default_judge_url: "fallback-j",
  judge_model: "cfg-jm",
  custom_judge_prompt: "CUSTOM-PROMPT",
  judge_providers: [
    { id: "p1", url: "u1", model: "m1" },
    { id: "p2", url: "u2", model: "m2" },
  ],
};

const MANIFEST = { album_id: "album3", album_name: "A3", face_count: 1, photo_count: 2, video_count: 0, qa_sets: ["compact-10q"] };

const PROMPT_KINDS = {
  kinds: [
    { kind: "answer_quality", default: "DEFAULT-AQ", custom: "" },
    { kind: "task_decision", default: "DEFAULT-TD", custom: "CUSTOM-TD" },
    { kind: "evidence", default: "DEFAULT-EV", custom: "" },
  ],
};

function happyFetch() {
  return vi.fn((url: string, _init?: RequestInit) => {
    if (url.endsWith("/api/config")) return Promise.resolve(jsonResponse(200, CONFIG));
    if (url.endsWith("/api/manifests")) return Promise.resolve(jsonResponse(200, { manifests: [MANIFEST] }));
    if (url.endsWith("/api/judge-prompts")) return Promise.resolve(jsonResponse(200, PROMPT_KINDS));
    return Promise.reject(new Error(`unexpected url ${url}`));
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useConfig.initConfig", () => {
  it("拉 config + manifests + judge-prompts，并把配置字段铺开", async () => {
    vi.stubGlobal("fetch", happyFetch());
    const { result } = renderHook(() => useConfig());

    await act(async () => {
      await result.current.initConfig();
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBe("");
    expect(result.current.sentrixUrl).toBe("127.0.0.1:8091");
    expect(result.current.judgeUrl).toBe("j:1234/v1");
    expect(result.current.judgeModel).toBe("runtime-jm");
    expect(result.current.judgeProviderId).toBe("p2");
    expect(result.current.vllmTargetId).toBe("t1");
    expect(Object.keys(result.current.vllmTargets)).toEqual(["t1"]);
    expect(result.current.manifests.map((m) => m.album_id)).toEqual(["album3"]);
    expect(result.current.connectionConfigState).toBe("saved");
    expect(result.current.connectionConfigMessage).toBe("已读取配置文件");
    // loadJudgePrompts 在 Promise.all 里跑，会把 rejudgePrompt 覆盖成 answer_quality 的草稿。
    expect(result.current.promptDrafts.task_decision).toBe("CUSTOM-TD");
    expect(result.current.rejudgePrompt).toBe("DEFAULT-AQ");
  });

  it("config 拉取失败时写 error 并结束 loading（GET 会重试一次）", async () => {
    const fetchStub = vi.fn().mockResolvedValue(jsonResponse(500, { error: "boom" }));
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderHook(() => useConfig());

    await act(async () => {
      await result.current.initConfig();
    });

    expect(result.current.error).toBe("boom");
    expect(result.current.loading).toBe(false);
    expect(result.current.manifests).toEqual([]);
    // GET 的 500 可重试：第一次 + 重试一次。
    expect(fetchStub).toHaveBeenCalledTimes(2);
  });

  it("loading 期间 markConnectionConfigDirty 被抑制，结束后才置 dirty", async () => {
    vi.stubGlobal("fetch", happyFetch());
    const { result } = renderHook(() => useConfig());

    // 1.0 :267 的 loading 初值是 true。
    expect(result.current.loading).toBe(true);
    act(() => result.current.markConnectionConfigDirty());
    expect(result.current.connectionConfigState).toBe("idle");

    await act(async () => {
      await result.current.initConfig();
    });
    act(() => result.current.markConnectionConfigDirty());
    expect(result.current.connectionConfigState).toBe("dirty");
    expect(result.current.connectionConfigMessage).toBe("有未保存修改");
  });
});

describe("useConfig.saveConnectionConfig", () => {
  it("提交连接配置；api key 脏了才带 judge_api_key，并回写 runtime_config", async () => {
    const fetchStub = happyFetch();
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderHook(() => useConfig());

    act(() => result.current.setSentrixUrl("127.0.0.1:8091"));
    act(() => result.current.setJudgeUrl("j:1/v1"));
    act(() => result.current.setJudgeModel("jm"));
    act(() => result.current.setJudgeProviderId("p1"));
    act(() => result.current.setJudgeApiKey("SECRET"));
    act(() => result.current.setJudgeApiKeyDirty(true));

    const onModelConfigSaved = vi.fn();
    const savedRuntime = { sentrix_url: "saved-s", judge_url: "saved-j", judge_model: "saved-m", vllm_manager_url: "saved-vm", model_base_url: "saved-ep", endpoint_model: "saved-em" };
    fetchStub.mockImplementation((url: string, _init?: RequestInit) => {
      if (url.endsWith("/api/config")) return Promise.resolve(jsonResponse(200, { saved: true, runtime_config: savedRuntime }));
      return Promise.reject(new Error(`unexpected url ${url}`));
    });

    await act(async () => {
      await result.current.saveConnectionConfig(
        { vllmManagerUrl: " mgr:9 ", modelEndpoint: " ep:2 ", endpointModel: "em" },
        { onModelConfigSaved },
      );
    });

    const postCall = fetchStub.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(postCall).toBeTruthy();
    const body = JSON.parse(String((postCall![1] as RequestInit).body));
    expect(body).toEqual({
      sentrix_url: "127.0.0.1:8091",
      judge_url: "j:1/v1",
      judge_model: "jm",
      vllm_manager_url: "mgr:9",
      model_base_url: "ep:2",
      endpoint_model: "em",
      judge_provider_id: "p1",
      judge_api_key: "SECRET",
    });

    expect(onModelConfigSaved).toHaveBeenCalledWith(savedRuntime);
    expect(result.current.sentrixUrl).toBe("saved-s");
    expect(result.current.judgeApiKey).toBe("");
    expect(result.current.judgeApiKeyDirty).toBe(false);
    expect(result.current.connectionConfigState).toBe("saved");
    expect(result.current.connectionConfigMessage.startsWith("已保存 · ")).toBe(true);
  });

  it("api key 未改动时不提交 judge_api_key", async () => {
    const fetchStub = vi.fn().mockResolvedValue(jsonResponse(200, { runtime_config: {} }));
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderHook(() => useConfig());

    await act(async () => {
      await result.current.saveConnectionConfig(
        { vllmManagerUrl: "", modelEndpoint: "", endpointModel: "" },
        { onModelConfigSaved: vi.fn() },
      );
    });

    const init = (fetchStub.mock.calls[0]![1] as RequestInit);
    const body = JSON.parse(String(init.body));
    expect(Object.prototype.hasOwnProperty.call(body, "judge_api_key")).toBe(false);
  });

  it("保存失败把状态置 error 并带出后端文案", async () => {
    const fetchStub = vi.fn().mockResolvedValue(jsonResponse(400, { error: "boom" }));
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderHook(() => useConfig());

    await act(async () => {
      await result.current.saveConnectionConfig(
        { vllmManagerUrl: "", modelEndpoint: "", endpointModel: "" },
        { onModelConfigSaved: vi.fn() },
      );
    });

    expect(result.current.connectionConfigState).toBe("error");
    expect(result.current.connectionConfigMessage).toBe("保存失败：boom");
    expect(fetchStub).toHaveBeenCalledTimes(1); // POST 不重试
  });
});

describe("useConfig 提示词", () => {
  it("loadJudgePrompts 是裸 fetch：失败时静默保留默认（与 api() 的失败处理不同）", async () => {
    const fetchStub = vi.fn().mockRejectedValue(new Error("network down"));
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderHook(() => useConfig());

    await act(async () => {
      await result.current.loadJudgePrompts();
    });

    expect(result.current.judgePromptKinds).toEqual([]);
    expect(result.current.promptDrafts).toEqual({ answer_quality: "", task_decision: "", evidence: "" });
    expect(result.current.error).toBe("");
  });

  it("switchPromptKind 换草稿、resetJudgePrompt 回默认值", async () => {
    vi.stubGlobal("fetch", happyFetch());
    const { result } = renderHook(() => useConfig());

    await act(async () => {
      await result.current.loadJudgePrompts();
    });

    act(() => result.current.switchPromptKind("evidence"));
    expect(result.current.rejudgePrompt).toBe("DEFAULT-EV");
    expect(result.current.activePromptKind).toBe("evidence");

    act(() => result.current.setRejudgePrompt("我改过的"));
    act(() => result.current.resetJudgePrompt());
    expect(result.current.rejudgePrompt).toBe("DEFAULT-EV");
    expect(result.current.promptDrafts.evidence).toBe("DEFAULT-EV");

    // 切走再切回读的是草稿（不是 rejudgePrompt 的临时值）。
    act(() => result.current.switchPromptKind("task_decision"));
    act(() => result.current.switchPromptKind("evidence"));
    expect(result.current.rejudgePrompt).toBe("DEFAULT-EV");
  });

  it("saveJudgePrompt：空串拒绝；非空提交并写进草稿", async () => {
    const fetchStub = vi.fn().mockResolvedValue(jsonResponse(200, { status: "ok" }));
    vi.stubGlobal("fetch", fetchStub);
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    const { result } = renderHook(() => useConfig());

    await act(async () => {
      await result.current.saveJudgePrompt();
    });
    expect(alertSpy).toHaveBeenCalledWith("提示词不能为空");
    expect(fetchStub).not.toHaveBeenCalled();

    act(() => result.current.setRejudgePrompt("新提示词"));
    await act(async () => {
      await result.current.saveJudgePrompt();
    });
    expect(fetchStub).toHaveBeenCalledTimes(1);
    const [url, init] = fetchStub.mock.calls[0]!;
    expect(String(url)).toBe("/api/judge-prompts");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      kind: "answer_quality",
      system_prompt: "新提示词",
    });
    expect(result.current.promptDrafts.answer_quality).toBe("新提示词");
  });
});
