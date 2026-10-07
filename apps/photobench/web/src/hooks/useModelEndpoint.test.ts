import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useModelEndpoint } from "./useModelEndpoint.ts";

function jsonResponse(status: number, body: unknown) {
  const text = JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => text,
    json: async () => JSON.parse(text),
  };
}

/** 用一个真实 `useState<Set>` 托住 `selectedModels`，验 Set 写回语义。 */
function renderModelEndpoint() {
  const onDirty = vi.fn();
  const rendered = renderHook(() => {
    const [selectedModels, setSelectedModels] = useState<Set<string>>(
      new Set(["__current__", "reg1"]),
    );
    const endpoint = useModelEndpoint({ vllmTargetId: "", selectedModels, setSelectedModels, onDirty });
    return { endpoint, selectedModels };
  });
  return { ...rendered, onDirty };
}

function bodyOf(call: unknown[]): Record<string, unknown> {
  return JSON.parse(String((call[1] as RequestInit).body));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useModelEndpoint.loadCurrentModel", () => {
  it("先带 model 失败 → 清空选择再裸调一次（去重试就会红）", async () => {
    const fetchStub = vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/current-model")) {
        const body = JSON.parse(String(init?.body));
        if (body.model) return Promise.resolve(jsonResponse(400, { error: "model not found" }));
        return Promise.resolve(jsonResponse(200, { served_models: ["m1"], served_model_name: "m1" }));
      }
      return Promise.reject(new Error(`unexpected url ${url}`));
    });
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderModelEndpoint();

    await act(async () => {
      await result.current.endpoint.loadCurrentModel({
        endpoint: "127.0.0.1:8100",
        requestedModel: "old-model",
      });
    });

    expect(fetchStub).toHaveBeenCalledTimes(2);
    expect(bodyOf(fetchStub.mock.calls[0]!).model).toBe("old-model");
    expect(Object.prototype.hasOwnProperty.call(bodyOf(fetchStub.mock.calls[1]!), "model")).toBe(false);
    expect(bodyOf(fetchStub.mock.calls[1]!)).toMatchObject({
      model_base_url: "127.0.0.1:8100",
      vllm_target_id: "",
    });
    expect(result.current.endpoint.selectedEndpointModel).toBe("m1");
    expect(result.current.endpoint.endpointModels).toEqual(["m1"]);
    // 成功后这条「已清除…」提示**不被抹掉**（1.0 :2623 原样）。
    expect(result.current.endpoint.currentModelError).toContain("已清除不可用的已保存模型：model not found");
  });

  it("没选过型号时失败不重试，直接进错误分支", async () => {
    const fetchStub = vi.fn().mockResolvedValue(jsonResponse(400, { error: "boom" }));
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderModelEndpoint();

    await act(async () => {
      await result.current.endpoint.loadCurrentModel({ endpoint: "ep:1" });
    });

    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(result.current.endpoint.currentModelInfo).toBeNull();
    expect(result.current.endpoint.endpointModels).toEqual([]);
    expect(result.current.endpoint.currentModelError).toBe("boom");
  });

  it("地址为空时只写提示，不发请求", async () => {
    const fetchStub = vi.fn();
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderModelEndpoint();

    await act(async () => {
      await result.current.endpoint.loadCurrentModel();
    });

    expect(fetchStub).not.toHaveBeenCalled();
    expect(result.current.endpoint.currentModelError).toBe("请先填写模型服务地址，例如 127.0.0.1:8100");
  });

  it("首次成功时按 openPopover 打开 popover，并把 served_model_name 写进选择", async () => {
    const fetchStub = vi.fn().mockResolvedValue(jsonResponse(200, { served_models: ["a", "b"], served_model_name: "b" }));
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderModelEndpoint();

    await act(async () => {
      await result.current.endpoint.loadCurrentModel({ endpoint: "ep:1" });
    });

    expect(result.current.endpoint.selectedEndpointModel).toBe("b");
    expect(result.current.endpoint.endpointModels).toEqual(["a", "b"]);
    expect(result.current.endpoint.currentModelPopoverOpen).toBe(true);
  });

  it("没有 served_model_name 且请求的型号不在列表里时，清空选择", async () => {
    const fetchStub = vi.fn().mockResolvedValue(jsonResponse(200, { served_models: ["a"] }));
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderModelEndpoint();

    await act(async () => {
      await result.current.endpoint.loadCurrentModel({ endpoint: "ep:1", requestedModel: "zzz" });
    });

    expect(result.current.endpoint.selectedEndpointModel).toBe("");
  });
});

describe("useModelEndpoint.testEndpointModel", () => {
  it("成功 / 失败文案不同", async () => {
    const fetchStub = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { latency_ms: 42 }))
      .mockResolvedValueOnce(jsonResponse(500, { error: "no completion choices" }));
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderModelEndpoint();

    act(() => result.current.endpoint.setSelectedEndpointModel("m1"));
    await act(async () => {
      await result.current.endpoint.testEndpointModel();
    });
    expect(result.current.endpoint.modelTestState).toBe("success");
    expect(result.current.endpoint.modelTestMessage).toBe("POST 可用 · 42 ms");

    await act(async () => {
      await result.current.endpoint.testEndpointModel();
    });
    expect(result.current.endpoint.modelTestState).toBe("error");
    expect(result.current.endpoint.modelTestMessage).toBe("POST 测试失败：no completion choices");
  });

  it("没选型号时不发请求", async () => {
    const fetchStub = vi.fn();
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderModelEndpoint();

    await act(async () => {
      await result.current.endpoint.testEndpointModel();
    });
    expect(fetchStub).not.toHaveBeenCalled();
  });
});

describe("useModelEndpoint 输入与选择", () => {
  it("onModelEndpointInput 重置探活状态、清掉 __current__、标脏", async () => {
    const { result, onDirty } = renderModelEndpoint();
    act(() => result.current.endpoint.setSelectedEndpointModel("keep-me"));
    act(() => result.current.endpoint.setVllmManagerUrl("mgr:1"));

    act(() => result.current.endpoint.onModelEndpointInput());

    expect(result.current.endpoint.modelEndpointUserEdited).toBe(true);
    expect(result.current.endpoint.selectedEndpointModel).toBe("");
    expect(result.current.endpoint.endpointModels).toEqual([]);
    expect(result.current.endpoint.currentModelInfo).toBeNull();
    expect(result.current.endpoint.modelTestState).toBe("idle");
    expect([...result.current.selectedModels]).toEqual(["reg1"]);
    expect(onDirty).toHaveBeenCalledTimes(1);
    // 只清 __current__，不清 profiles 所依赖的 manager 地址。
    expect(result.current.endpoint.vllmManagerUrl).toBe("mgr:1");
  });

  it("onModelManagerInput 清空所有选择并重置模型区", async () => {
    const { result, onDirty } = renderModelEndpoint();

    act(() => result.current.endpoint.onModelManagerInput());

    expect([...result.current.selectedModels]).toEqual([]);
    expect(result.current.endpoint.profiles).toEqual([]);
    expect(result.current.endpoint.currentModelInfo).toBeNull();
    expect(result.current.endpoint.modelTestState).toBe("idle");
    expect(onDirty).toHaveBeenCalledTimes(1);
  });

  it("onEndpointModelChange 只删 __current__ 并重新探活", async () => {
    const fetchStub = vi.fn().mockResolvedValue(jsonResponse(200, { served_models: ["a"] }));
    vi.stubGlobal("fetch", fetchStub);
    const { result, onDirty } = renderModelEndpoint();

    act(() => result.current.endpoint.setModelEndpoint("ep:1"));
    await act(async () => {
      await result.current.endpoint.onEndpointModelChange("a");
    });

    expect([...result.current.selectedModels]).toEqual(["reg1"]);
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(bodyOf(fetchStub.mock.calls[0]!).model).toBe("a");
    expect(onDirty).toHaveBeenCalledTimes(1);
  });
});

describe("useModelEndpoint.loadProfiles", () => {
  it("manager 为空时清空 profiles，只留 __current__，标脏与否由调用方决定", async () => {
    const fetchStub = vi.fn();
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderModelEndpoint();

    await act(async () => {
      await result.current.endpoint.loadProfiles();
    });

    expect(fetchStub).not.toHaveBeenCalled();
    expect(result.current.endpoint.profiles).toEqual([]);
    expect([...result.current.selectedModels]).toEqual(["__current__"]);
  });

  it("有 manager 时按 1.0 的 body 形状请求注册表", async () => {
    const fetchStub = vi.fn().mockResolvedValue(jsonResponse(200, { profiles: [{ id: "p1", available: true }] }));
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderModelEndpoint();

    act(() => result.current.endpoint.onModelEndpointInput()); // 置 modelEndpointUserEdited
    act(() => result.current.endpoint.setModelEndpoint("ep:9"));
    act(() => result.current.endpoint.setVllmManagerUrl("mgr:9"));

    await act(async () => {
      await result.current.endpoint.loadProfiles();
    });

    expect(bodyOf(fetchStub.mock.calls[0]!)).toEqual({
      vllm_target_id: "",
      vllm_manager_url: "mgr:9",
      model_base_url: "ep:9",
    });
    expect(result.current.endpoint.profiles.map((p) => p.id)).toEqual(["p1"]);
  });
});

describe("useModelEndpoint 配置应用", () => {
  it("applyConfig 用 runtime_config 优先，其次 default_* 与 vllm_targets", () => {
    const { result } = renderModelEndpoint();

    act(() =>
      result.current.endpoint.applyConfig({
        runtime_config: { model_base_url: "runtime-ep", vllm_manager_url: "runtime-mgr", endpoint_model: "runtime-em" },
        default_vllm_model_base_url: "default-ep",
        default_vllm_api_url: "default-mgr",
        vllm_targets: { t1: { model_base_url: "target-ep", manager_url: "target-mgr" } },
        default_vllm_target_id: "t1",
      }),
    );

    expect(result.current.endpoint.modelEndpoint).toBe("runtime-ep");
    expect(result.current.endpoint.vllmManagerUrl).toBe("runtime-mgr");
    expect(result.current.endpoint.selectedEndpointModel).toBe("runtime-em");
    expect(result.current.endpoint.modelEndpointUserEdited).toBe(true);
  });

  it("applyConfig 缺 runtime_config 时回退到 vllm_targets[targetId]", () => {
    const { result } = renderModelEndpoint();

    act(() =>
      result.current.endpoint.applyConfig({
        default_vllm_api_url: "default-mgr",
        vllm_targets: { t1: { model_base_url: "target-ep", manager_url: "target-mgr" } },
        default_vllm_target_id: "t1",
      }),
    );

    expect(result.current.endpoint.vllmManagerUrl).toBe("target-mgr");
    expect(result.current.endpoint.modelEndpoint).toBe("target-ep");
    expect(result.current.endpoint.modelEndpointUserEdited).toBe(false);
  });

  it("applySavedConnection 回写字段，并在有地址时按保存后的值再探活", async () => {
    const fetchStub = vi.fn().mockResolvedValue(jsonResponse(200, { served_models: ["a"] }));
    vi.stubGlobal("fetch", fetchStub);
    const { result } = renderModelEndpoint();

    await act(async () => {
      await result.current.endpoint.applySavedConnection({
        vllm_manager_url: "saved-mgr",
        model_base_url: "saved-ep",
        endpoint_model: "",
      });
    });

    expect(result.current.endpoint.vllmManagerUrl).toBe("saved-mgr");
    expect(result.current.endpoint.modelEndpoint).toBe("saved-ep");
    expect(result.current.endpoint.modelEndpointUserEdited).toBe(true);
    expect(fetchStub).toHaveBeenCalledTimes(1);
    expect(bodyOf(fetchStub.mock.calls[0]!)).toMatchObject({ model_base_url: "saved-ep" });
    // `endpoint_model` 存的是空串：1.0 也照样把 `model: ""` 发出去（只有内层重试那次才不带）。
    expect(bodyOf(fetchStub.mock.calls[0]!).model).toBe("");
  });
});
