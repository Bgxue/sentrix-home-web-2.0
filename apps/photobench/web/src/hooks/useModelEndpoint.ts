/**
 * 模型服务 hook —— 逐行移植 1.0 `App.vue` 的模型服务区：
 *
 *   - `loadProfiles`（`:2387-2400`）
 *   - `onModelManagerInput`（`:2401-2413`）
 *   - `loadCurrentModel`（`:2602-2638`，注意「先带 `model` 失败 → 再裸调」）
 *   - `onEndpointModelChange`（`:2639-2645`）
 *   - `testEndpointModel`（`:2646-2661`）
 *   - `onModelEndpointInput`（`:2662-2673`）
 *   - `init()`（`:2738-2741`）与 `saveConnectionConfig`（`:2448-2459`）里对模型服务字段的赋值
 *   - `markConnectionConfigDirty` 的调用点（脏标记本身在 `useConfig`，通过 `onDirty` 注入）
 *
 * **语义边界（用户 2026-10-05 裁决③）**：1.0 的 popover 是「不重启切端点」，2.0 换模型
 * = **重启 2.0**。本 hook **只搬数据获取与探活**（`/profiles`、`/current-model`、
 * `/test-model` 三条都是纯查询，S10a 的后端注释也确认探活无副作用）；
 * 「切换端点」这个**动作**属于视图层要按新语义重新设计，**不在本文件**。
 *
 * 状态归属：模型服务表单字段（`vllmManagerUrl` / `modelEndpoint` / `modelEndpointUserEdited`
 * / `selectedEndpointModel`）与探活结果都在这里；`selectedModels`（勾选的模型集合，供
 * run 配置使用）与 `vllmTargetId`（配置）在外部，通过 options 传入。
 */
import { useCallback, useRef, useState } from "react";

import { post } from "../api/client.ts";
import type { BenchConfig, CurrentModelInfo } from "../api/types.ts";

/** `POST /api/profiles` 返回的一条（1.0 :2973-2974 只读 `id` / `available` / `source`）。 */
export interface ModelProfile {
  id: string;
  available?: boolean;
  source?: string;
  [key: string]: unknown;
}

/** `POST /api/config` 回来的 `runtime_config` 里模型服务字段（1.0 :2448-2451）。 */
interface SavedRuntimeConfig {
  vllm_manager_url?: string;
  model_base_url?: string;
  endpoint_model?: string;
  [key: string]: unknown;
}

export interface UseModelEndpointOptions {
  /** `useConfig.vllmTargetId`。 */
  vllmTargetId: string;
  /** 勾选的模型集合（1.0 `selectedModels`，`reactive(new Set())`）。 */
  selectedModels: ReadonlySet<string>;
  /** 必须用 `new Set(...)` 写回（1.0 的 `reactive` Set 直接 add 会重渲染；React 不会）。 */
  setSelectedModels: (next: Set<string>) => void;
  /** `useConfig.markConnectionConfigDirty`。 */
  onDirty: () => void;
}

export interface LoadCurrentModelOptions {
  /** 1.0 `loadCurrentModel({ openPopover })`。 */
  openPopover?: boolean;
  /**
   * React 里调用方可能刚 `setState`（如保存成功后），state 尚未刷新，1.0 读 ref 无此问题。
   * 显式传入可保住 1.0 的语义；不传则读当前 `modelEndpoint`。
   */
  endpoint?: string;
  /** 同上：`onEndpointModelChange` 刚改了 `selectedEndpointModel`。 */
  requestedModel?: string;
}

/** 1.0 的值链是 `a || b || c || ""`，这里等价：取第一个非空字符串。 */
function pick(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string" && value) return value;
  }
  return "";
}

export function useModelEndpoint(options: UseModelEndpointOptions) {
  const { vllmTargetId, selectedModels, setSelectedModels, onDirty } = options;

  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [vllmManagerUrl, setVllmManagerUrl] = useState("");
  const [modelEndpoint, setModelEndpoint] = useState("");
  const [modelEndpointUserEdited, setModelEndpointUserEdited] = useState(false);
  const [selectedEndpointModel, setSelectedEndpointModel] = useState("");
  const [endpointModels, setEndpointModels] = useState<string[]>([]);
  const [currentModelInfo, setCurrentModelInfo] = useState<CurrentModelInfo | null>(null);
  const [currentModelLoading, setCurrentModelLoading] = useState(false);
  const [currentModelError, setCurrentModelError] = useState("");
  const [currentModelPopoverOpen, setCurrentModelPopoverOpen] = useState(false);
  const [modelTestState, setModelTestState] = useState("idle");
  const [modelTestMessage, setModelTestMessage] = useState("");

  // 1.0 的 `testEndpointModel` 用 `modelTestState.value === "testing"` 防重入；React 的
  // state 是异步的，用 ref 才挡得住同一 tick 内的第二次调用。
  const testingRef = useRef(false);

  /** 1.0 `:2387-2400`。 */
  const loadProfiles = useCallback(async () => {
    if (!vllmManagerUrl.trim()) {
      setProfiles([]);
      const next = new Set<string>();
      for (const modelId of selectedModels) {
        if (modelId === "__current__") next.add(modelId);
      }
      setSelectedModels(next);
      return;
    }
    const data = await post<{ profiles?: ModelProfile[] }>("/profiles", {
      vllm_target_id: vllmTargetId,
      vllm_manager_url: vllmManagerUrl.trim(),
      model_base_url: modelEndpointUserEdited ? modelEndpoint.trim() : "",
    });
    setProfiles(data.profiles || []);
  }, [vllmManagerUrl, vllmTargetId, modelEndpointUserEdited, modelEndpoint, selectedModels, setSelectedModels]);

  /** 1.0 `:2401-2413`。 */
  const onModelManagerInput = useCallback(() => {
    onDirty();
    setProfiles([]);
    setCurrentModelInfo(null);
    setCurrentModelPopoverOpen(false);
    setCurrentModelError("");
    setModelTestState("idle");
    setModelTestMessage("");
    // 1.0 先 `delete("__current__")` 再 `forEach` 删光，净效果是清空。
    setSelectedModels(new Set());
  }, [onDirty, setSelectedModels]);

  /**
   * 1.0 `:2602-2638`。内层 `try` 的语义：带 `model` 的那次失败且选过型号时，
   * **清掉选择再裸调一次**，并保留一条「已清除…」的提示（成功也不抹掉）。
   */
  const loadCurrentModel = useCallback(
    async (loadOptions: LoadCurrentModelOptions = {}) => {
      const openPopover = loadOptions.openPopover ?? true;
      const endpoint = (loadOptions.endpoint ?? modelEndpoint).trim();
      if (!endpoint) {
        setCurrentModelError("请先填写模型服务地址，例如 127.0.0.1:8100");
        return;
      }
      setCurrentModelLoading(true);
      setCurrentModelError("");
      try {
        const requestedModel = loadOptions.requestedModel ?? selectedEndpointModel;
        const requestBody = { model_base_url: endpoint, vllm_target_id: vllmTargetId };
        let result: CurrentModelInfo;
        try {
          result = await post<CurrentModelInfo>("/current-model", {
            ...requestBody,
            model: requestedModel,
          });
        } catch (selectionError) {
          if (!requestedModel) throw selectionError;
          setSelectedEndpointModel("");
          result = await post<CurrentModelInfo>("/current-model", requestBody);
          setCurrentModelError(`已清除不可用的已保存模型：${(selectionError as Error).message}`);
        }
        const servedModels = result.served_models || [];
        setEndpointModels(servedModels);
        if (result.served_model_name) setSelectedEndpointModel(result.served_model_name);
        else if (!servedModels.includes(requestedModel)) setSelectedEndpointModel("");
        setCurrentModelInfo(result);
        setCurrentModelPopoverOpen(openPopover);
      } catch (e) {
        setCurrentModelInfo(null);
        setEndpointModels([]);
        setCurrentModelPopoverOpen(false);
        setCurrentModelError((e as Error).message);
      } finally {
        setCurrentModelLoading(false);
      }
    },
    [modelEndpoint, selectedEndpointModel, vllmTargetId],
  );

  /** 1.0 `:2639-2645`。 */
  const onEndpointModelChange = useCallback(
    async (requestedModel?: string) => {
      const next = new Set(selectedModels);
      next.delete("__current__");
      setSelectedModels(next);
      setModelTestState("idle");
      setModelTestMessage("");
      onDirty();
      await loadCurrentModel({ openPopover: false, requestedModel });
    },
    [selectedModels, setSelectedModels, onDirty, loadCurrentModel],
  );

  /** 1.0 `:2646-2661`。 */
  const testEndpointModel = useCallback(async () => {
    if (!selectedEndpointModel || testingRef.current) return;
    testingRef.current = true;
    setModelTestState("testing");
    setModelTestMessage("正在发送最小 POST 请求…");
    try {
      const result = await post<{ latency_ms?: number }>("/test-model", {
        model_base_url: modelEndpoint.trim(),
        model: selectedEndpointModel,
      });
      setModelTestState("success");
      setModelTestMessage(`POST 可用 · ${result.latency_ms} ms`);
    } catch (e) {
      setModelTestState("error");
      setModelTestMessage(`POST 测试失败：${(e as Error).message}`);
    } finally {
      testingRef.current = false;
    }
  }, [selectedEndpointModel, modelEndpoint]);

  /** 1.0 `:2662-2673`。 */
  const onModelEndpointInput = useCallback(() => {
    setModelEndpointUserEdited(true);
    setEndpointModels([]);
    setSelectedEndpointModel("");
    setCurrentModelInfo(null);
    setCurrentModelPopoverOpen(false);
    setCurrentModelError("");
    setModelTestState("idle");
    setModelTestMessage("");
    const next = new Set(selectedModels);
    next.delete("__current__");
    setSelectedModels(next);
    onDirty();
  }, [selectedModels, setSelectedModels, onDirty]);

  /** 1.0 `init()` `:2738-2741` 对模型服务字段的赋值。 */
  const applyConfig = useCallback((config: BenchConfig) => {
    const runtimeConfig = (config.runtime_config || {}) as SavedRuntimeConfig;
    const targets = config.vllm_targets || {};
    const targetId = config.default_vllm_target_id || Object.keys(targets)[0] || "";
    const target = targets[targetId] || {};
    setVllmManagerUrl(
      pick(runtimeConfig.vllm_manager_url, target.manager_url, config.default_vllm_api_url),
    );
    setModelEndpoint(
      pick(runtimeConfig.model_base_url, config.default_vllm_model_base_url, target.model_base_url),
    );
    setModelEndpointUserEdited(Boolean(runtimeConfig.model_base_url));
    setSelectedEndpointModel(pick(runtimeConfig.endpoint_model));
  }, []);

  /** 1.0 `saveConnectionConfig` `:2448-2459` 对模型服务字段的回写。 */
  const applySavedConnection = useCallback(
    async (runtimeConfig: Record<string, unknown>) => {
      const saved = runtimeConfig as SavedRuntimeConfig;
      const endpoint = pick(saved.model_base_url);
      setVllmManagerUrl(pick(saved.vllm_manager_url));
      setModelEndpoint(endpoint);
      setModelEndpointUserEdited(Boolean(saved.model_base_url));
      const savedEndpointModel = pick(saved.endpoint_model);
      setSelectedEndpointModel(savedEndpointModel);
      setEndpointModels([]);
      setCurrentModelInfo(null);
      setCurrentModelError("");
      setModelTestState("idle");
      setModelTestMessage("");
      // 1.0 `:2459`：`if (modelEndpoint.value.trim()) await loadCurrentModel(...)`。那次
      // 调用读的 `selectedEndpointModel` 正是上面刚写的 `saved.endpoint_model`——React 里
      // state 未刷新，显式传进去才等价。
      if (endpoint.trim()) {
        await loadCurrentModel({ openPopover: false, endpoint, requestedModel: savedEndpointModel });
      }
    },
    [loadCurrentModel],
  );

  return {
    profiles,
    vllmManagerUrl,
    setVllmManagerUrl,
    modelEndpoint,
    setModelEndpoint,
    modelEndpointUserEdited,
    selectedEndpointModel,
    setSelectedEndpointModel,
    endpointModels,
    currentModelInfo,
    currentModelLoading,
    currentModelError,
    currentModelPopoverOpen,
    setCurrentModelPopoverOpen,
    modelTestState,
    modelTestMessage,
    loadProfiles,
    onModelManagerInput,
    loadCurrentModel,
    onEndpointModelChange,
    testEndpointModel,
    onModelEndpointInput,
    applyConfig,
    applySavedConnection,
  };
}
