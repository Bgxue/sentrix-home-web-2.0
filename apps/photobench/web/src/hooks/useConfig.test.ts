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

function happyFetch() {
  return vi.fn((url: string, _init?: RequestInit) => {
    if (url.endsWith("/api/config")) return Promise.resolve(jsonResponse(200, CONFIG));
    if (url.endsWith("/api/manifests")) return Promise.resolve(jsonResponse(200, { manifests: [MANIFEST] }));
    return Promise.reject(new Error(`unexpected url ${url}`));
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useConfig.initConfig", () => {
  it("拉 config + manifests，铺开配置字段，并把 config 对象交给调用方", async () => {
    vi.stubGlobal("fetch", happyFetch());
    const { result } = renderHook(() => useConfig());

    let returned: unknown = null;
    await act(async () => {
      returned = await result.current.initConfig();
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
    // 1.0 的连接状态：读完配置就是 `saved` / 「已读取配置文件」（不再是 idle）。
    expect(result.current.connectionConfigState).toBe("saved");
    expect(result.current.connectionConfigMessage).toBe("已读取配置文件");
    // config 对象不再恒为 null。
    expect(result.current.config?.judge_model).toBe("cfg-jm");
    expect(returned).toBe(result.current.config);
  });

  it("loadRuns 失败 → manifests **不被赋值**，但配置字段已铺开（1.0 :2751-2757）", async () => {
    vi.stubGlobal("fetch", happyFetch());
    const { result } = renderHook(() => useConfig());

    await act(async () => {
      await result.current.initConfig({ loadRuns: () => Promise.reject(new Error("runs boom")) });
    });

    // 失败语义：manifests 空。
    expect(result.current.manifests).toEqual([]);
    // 但配置字段先于 Promise.all 写好（1.0 原样）。
    expect(result.current.sentrixUrl).toBe("127.0.0.1:8091");
    expect(result.current.connectionConfigState).toBe("saved");
    expect(result.current.error).toBe("runs boom");
  });

  it("loadProfiles 失败 → 同样中止 manifests（与 /manifests 并发的那条）", async () => {
    vi.stubGlobal("fetch", happyFetch());
    const { result } = renderHook(() => useConfig());

    await act(async () => {
      await result.current.initConfig({ loadProfiles: () => Promise.reject(new Error("profiles boom")) });
    });

    expect(result.current.manifests).toEqual([]);
    expect(result.current.error).toBe("profiles boom");
  });

  it("onConfig 在 loadRuns 之前调用，拿到的是刚拉到的 config", async () => {
    vi.stubGlobal("fetch", happyFetch());
    const { result } = renderHook(() => useConfig());

    const seen: unknown[] = [];
    await act(async () => {
      await result.current.initConfig({
        onConfig: (cfg) => seen.push(cfg),
        loadRuns: () => {
          seen.push("runs");
          return Promise.resolve();
        },
      });
    });

    expect(seen[0]).toBe(result.current.config);
    expect(seen[1]).toBe("runs");
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

describe("useConfig.saveJudgePrompt（1.0 :2574-2582）", () => {
  it("空串拒绝：不提交，alert，返回 false", async () => {
    const fetchStub = vi.fn().mockResolvedValue(jsonResponse(200, { status: "ok" }));
    vi.stubGlobal("fetch", fetchStub);
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    const { result } = renderHook(() => useConfig());

    let ok = true;
    await act(async () => {
      ok = await result.current.saveJudgePrompt("answer_quality", "   ");
    });

    expect(ok).toBe(false);
    expect(alertSpy).toHaveBeenCalledWith("提示词不能为空");
    expect(fetchStub).not.toHaveBeenCalled();
  });

  // P1 的核心断言：提交的就是**调用方传入**的 (kind, prompt)，不是某个内部默认值。
  // 两组「kind + prompt」必须给出不同的请求体——若实现退回读 hook 内部状态（恒为
  // answer_quality + ""），两组都会红。
  it("提交的就是传入的 kind/system_prompt（两种组合给出两个不同请求体）", async () => {
    const fetchStub = vi.fn().mockResolvedValue(jsonResponse(200, { status: "ok" }));
    vi.stubGlobal("fetch", fetchStub);
    vi.spyOn(window, "alert").mockImplementation(() => {});
    const { result } = renderHook(() => useConfig());

    const cases: Array<[string, string]> = [
      ["answer_quality", "用户刚编辑的回答质量提示词"],
      ["evidence", "用户刚编辑的证据核验提示词"],
    ];
    const bodies: unknown[] = [];
    for (const [kind, prompt] of cases) {
      await act(async () => {
        const ok = await result.current.saveJudgePrompt(kind, prompt);
        expect(ok).toBe(true);
      });
      const [url, init] = fetchStub.mock.calls[fetchStub.mock.calls.length - 1]!;
      expect(String(url)).toBe("/api/judge-prompts");
      expect((init as RequestInit).method).toBe("POST");
      bodies.push(JSON.parse(String((init as RequestInit).body)));
    }
    expect(bodies).toEqual([
      { kind: "answer_quality", system_prompt: "用户刚编辑的回答质量提示词" },
      { kind: "evidence", system_prompt: "用户刚编辑的证据核验提示词" },
    ]);
    expect(fetchStub).toHaveBeenCalledTimes(2);
  });

  it("保存失败：alert 后端文案并返回 false", async () => {
    const fetchStub = vi.fn().mockResolvedValue(jsonResponse(400, { error: "boom" }));
    vi.stubGlobal("fetch", fetchStub);
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    const { result } = renderHook(() => useConfig());

    let ok = true;
    await act(async () => {
      ok = await result.current.saveJudgePrompt("answer_quality", "p");
    });

    expect(ok).toBe(false);
    expect(alertSpy).toHaveBeenCalledWith("保存失败：boom");
  });
});
