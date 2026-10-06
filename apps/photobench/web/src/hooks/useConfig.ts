/**
 * PhotoBench 评测配置 hook —— 逐行移植 1.0 `App.vue` 的配置区：
 *
 *   - `init()`（`:2731-2767`）的**配置部分**（`GET /api/config`、`GET /api/manifests`，
 *     以及各连接字段的初值）
 *   - `saveConnectionConfig`（`:2419-2464`）
 *   - `markConnectionConfigDirty`（`:2414-2418`）
 *   - `saveJudgePrompt`（`:2574-2582`）
 *
 * **提示词状态不在这里**：1.0 只有一份 `rejudgePrompt` / `activePromptKind` /
 * `promptDrafts`（`:253` / `:252` / `:254`）。2.0 拆 hook 时这份状态的唯一真相落在
 * `useRunDetail`（`RejudgeCard` 绑的就是它）。本 hook 只保留 `saveJudgePrompt`，
 * 且**显式收参** `(kind, prompt)`，不再自己持有一份恒为默认值的副本。
 *
 * **边界**（与并行 hook 的分工，1.0 里它们全在一个组件里）：
 *   - 换模型 / 模型服务探活（`loadProfiles` / `loadCurrentModel` / `test-model`）在
 *     `useModelEndpoint`；`init()` 里对模型字段的赋值由那边的 `applyConfig()` 负责。
 *   - run 列表、`init()` 末尾的 run 选中与轮询在 `useRuns`；`loadJudgePrompts` 在
 *     `useRunDetail`（提示词那份状态所在）。
 *   - 复用的三个 `watch` 在 `useReuseMode`。
 *
 * 因此 1.0 `:2751` 那个把 `loadRuns` / `loadProfiles` 一起拉进来的 `Promise.all` 在此
 * **只保留配置自己那一条**（`/manifests`）；其余 loader 由组合层以 options 注入，
 * 好让 `loadRuns` 的失败仍能中止 `manifests` 赋值（1.0 `:2751-2757` 的失败语义）。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { api, post } from "../api/client.ts";
import type { BenchConfig, ManifestPage, ManifestSummary } from "../api/types.ts";

/** `config.judge_providers[]` 的一条（1.0 :256 只读 `id` / `url` / `model`）。 */
export interface JudgeProvider {
  id?: string;
  url?: string;
  model?: string;
  [key: string]: unknown;
}

/** `POST /api/config` 回来、前端要回写的 `runtime_config` 字段（1.0 :2437-2451）。 */
interface SavedRuntimeConfig {
  sentrix_url?: string;
  judge_url?: string;
  judge_model?: string;
  judge_provider_id?: string;
  vllm_manager_url?: string;
  model_base_url?: string;
  endpoint_model?: string;
  [key: string]: unknown;
}

/** 保存时要一并提交、但状态归 `useModelEndpoint` 的模型服务字段。 */
export interface ConnectionModelFields {
  vllmManagerUrl: string;
  modelEndpoint: string;
  endpointModel: string;
}

export interface SaveConnectionHooks {
  /** 保存成功后回写模型字段（1.0 `:2448-2459`，那里还顺带 `loadCurrentModel`）。 */
  onModelConfigSaved: (runtimeConfig: Record<string, unknown>) => void | Promise<void>;
}

/**
 * `initConfig` 里那三条不属于本 hook 的 loader / 回调（都是 1.0 `init()` 的一部分），
 * 由组合层注入：
 *   - `onConfig`：拿到 config、铺开配置字段之后、`loadRuns` 之前调用（组合层在这里
 *     `applyConfig` 并算出 model endpoint）；
 *   - `loadRuns`：1.0 `:2751` 的四条之一；**它的失败必须跳过 `manifests` 赋值**
 *     （1.0 把它们放进同一个 `Promise.all`）；
 *   - `loadProfiles`：1.0 `:2751` 的四条之一；与 `/manifests` 并发，失败同样跳过赋值。
 */
export interface InitConfigOptions {
  onConfig?: (config: BenchConfig) => void;
  loadRuns?: () => Promise<unknown>;
  loadProfiles?: () => Promise<unknown>;
}

/** 保存状态机的取值（1.0 是一个自由字符串 ref，这里收窄成联合类型）。 */
export type ConnectionState = "idle" | "saving" | "saved" | "dirty" | "error";

/** 提示词三类的中文名（1.0 `:2465`，只在保存成功的 alert 文案里用）。 */
const PROMPT_KIND_LABELS: Record<string, string> = {
  answer_quality: "回答质量",
  task_decision: "任务判断",
  evidence: "证据核验",
};

export function useConfig() {
  const [config, setConfig] = useState<BenchConfig | null>(null);
  const [manifests, setManifests] = useState<ManifestSummary[]>([]);
  const [vllmTargets, setVllmTargets] = useState<Record<string, Record<string, unknown>>>({});
  const [vllmTargetId, setVllmTargetId] = useState("");
  const [sentrixUrl, setSentrixUrl] = useState("");
  const [judgeUrl, setJudgeUrl] = useState("");
  const [judgeModel, setJudgeModel] = useState("");
  const [judgeApiKey, setJudgeApiKey] = useState("");
  const [judgeApiKeyDirty, setJudgeApiKeyDirty] = useState(false);
  const [judgeProviderId, setJudgeProviderId] = useState("");
  const [connectionConfigState, setConnectionConfigState] = useState<ConnectionState>("idle");
  const [connectionConfigMessage, setConnectionConfigMessage] = useState("");
  // 1.0 `:267` 的 `loading` 从 `true` 起，`init()` 的 `finally` 置 `false`。它是全局
  // 加载态，这里只覆盖配置这一段；`markConnectionConfigDirty` 的抑制窗口按它算。
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // `loadingRef` 用 effect 同步（1.0 的 `loading` 是同步 ref，React 的 state 不是）。
  const loadingRef = useRef(loading);
  const savingRef = useRef(false);
  useEffect(() => {
    loadingRef.current = loading;
  }, [loading]);

  /** 1.0 `:2414-2418`。 */
  const markConnectionConfigDirty = useCallback(() => {
    if (loadingRef.current) return;
    setConnectionConfigState("dirty");
    setConnectionConfigMessage("有未保存修改");
  }, []);

  /**
   * 1.0 `:2574-2582`。**显式收参**：唯一真相是运行详情侧（`useRunDetail`）的
   * `rejudgePrompt` / `activePromptKind`，组合层用那边的值调它。
   * 返回是否保存成功——组合层据此把新值写回运行详情侧的草稿
   * （1.0 的 `promptDrafts[activePromptKind] = prompt`）。
   */
  const saveJudgePrompt = useCallback(async (kind: string, prompt: string): Promise<boolean> => {
    const trimmed = prompt.trim();
    if (!trimmed) {
      window.alert("提示词不能为空");
      return false;
    }
    try {
      await post("/judge-prompts", { kind, system_prompt: trimmed });
      window.alert(`已保存「${PROMPT_KIND_LABELS[kind] || kind}」提示词，重新评分与后续评测将使用它`);
      return true;
    } catch (e) {
      window.alert("保存失败：" + (e as Error).message);
      return false;
    }
  }, []);

  /**
   * 1.0 `:2419-2464`。模型服务字段（`vllm_manager_url` / `model_base_url` /
   * `endpoint_model`）由 `useModelEndpoint` 持有，这里以参数收、以回调写回。
   */
  const saveConnectionConfig = useCallback(
    async (model: ConnectionModelFields, hooks: SaveConnectionHooks) => {
      if (savingRef.current) return;
      savingRef.current = true;
      setConnectionConfigState("saving");
      setConnectionConfigMessage("正在保存…");
      try {
        const result = await api<{ runtime_config?: SavedRuntimeConfig }>("/config", {
          method: "POST",
          body: JSON.stringify({
            sentrix_url: sentrixUrl.trim(),
            judge_url: judgeUrl.trim(),
            judge_model: judgeModel.trim(),
            vllm_manager_url: model.vllmManagerUrl.trim(),
            model_base_url: model.modelEndpoint.trim(),
            endpoint_model: model.endpointModel,
            judge_provider_id: judgeProviderId,
            ...(judgeApiKeyDirty ? { judge_api_key: judgeApiKey } : {}),
          }),
        });
        const saved = result.runtime_config || {};
        setConfig((prev) =>
          prev
            ? {
                ...prev,
                runtime_config: saved as Record<string, unknown>,
                default_sentrix_url: saved.sentrix_url,
                default_judge_url: saved.judge_url,
                default_vllm_api_url: saved.vllm_manager_url,
                default_vllm_model_base_url: saved.model_base_url,
              }
            : prev,
        );
        setSentrixUrl(saved.sentrix_url || sentrixUrl);
        setJudgeUrl(saved.judge_url || judgeUrl);
        setJudgeModel(saved.judge_model || judgeModel);
        setJudgeApiKey("");
        setJudgeApiKeyDirty(false);
        setConnectionConfigState("saved");
        setConnectionConfigMessage(
          `已保存 · ${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`,
        );
        await hooks.onModelConfigSaved(saved as Record<string, unknown>);
      } catch (e) {
        setConnectionConfigState("error");
        setConnectionConfigMessage(`保存失败：${(e as Error).message}`);
      } finally {
        savingRef.current = false;
      }
    },
    [sentrixUrl, judgeUrl, judgeModel, judgeApiKey, judgeApiKeyDirty, judgeProviderId],
  );

  /**
   * 1.0 `:2731-2767` 的配置部分（run 选中 / 轮询 / 模型探活不在 `useConfig`，由组合层
   * 经 options 注入）。**保留 1.0 的失败语义**：`loadRuns`（1.0 与 `/manifests` 同在一个
   * `Promise.all`）一失败，`manifests` 就不被赋值。返回拉到的 config（组合层拿它做
   * `applyConfig` 与 model endpoint —— 1.0 里它们是同一个函数作用域内的局部变量）。
   */
  const initConfig = useCallback(async (options: InitConfigOptions = {}): Promise<BenchConfig | null> => {
    setLoading(true);
    try {
      const cfg = await api<BenchConfig>("/config");
      setConfig(cfg);
      const targets = cfg.vllm_targets || {};
      setVllmTargets(targets);
      const targetId = cfg.default_vllm_target_id || Object.keys(targets)[0] || "";
      setVllmTargetId(targetId);
      const runtimeConfig = (cfg.runtime_config || {}) as SavedRuntimeConfig;
      setSentrixUrl(runtimeConfig.sentrix_url || cfg.default_sentrix_url || "");
      setJudgeUrl(runtimeConfig.judge_url || cfg.default_judge_url || "");
      setJudgeModel(runtimeConfig.judge_model || cfg.judge_model || "");
      setJudgeApiKey("");
      setJudgeApiKeyDirty(false);
      const providers = (cfg.judge_providers || []) as JudgeProvider[];
      setJudgeProviderId(
        runtimeConfig.judge_provider_id || cfg.default_judge_provider_id || providers[0]?.id || "",
      );
      setConnectionConfigState("saved");
      setConnectionConfigMessage("已读取配置文件");
      options.onConfig?.(cfg);
      // 1.0 `:2751-2757`：这几条任一失败都跳过 `manifests.value = ...`。`loadRuns` 先串行
      // await（2.0 的一处时序适配，见 App.tsx 文件头），`loadProfiles` 与 `/manifests` 并发。
      await options.loadRuns?.();
      const [manifestPayload] = (await Promise.all([
        api<ManifestPage>("/manifests"),
        options.loadProfiles?.() ?? Promise.resolve(),
      ])) as [ManifestPage, unknown];
      setManifests(manifestPayload.manifests || []);
      return cfg;
    } catch (e) {
      setError((e as Error).message || "读取配置失败");
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  return {
    config,
    manifests,
    vllmTargets,
    vllmTargetId,
    setVllmTargetId,
    sentrixUrl,
    setSentrixUrl,
    judgeUrl,
    setJudgeUrl,
    judgeModel,
    setJudgeModel,
    judgeApiKey,
    setJudgeApiKey,
    judgeApiKeyDirty,
    setJudgeApiKeyDirty,
    judgeProviderId,
    setJudgeProviderId,
    connectionConfigState,
    connectionConfigMessage,
    loading,
    setLoading,
    error,
    setError,
    markConnectionConfigDirty,
    saveJudgePrompt,
    saveConnectionConfig,
    initConfig,
  };
}
