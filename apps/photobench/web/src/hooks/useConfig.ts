/**
 * PhotoBench 评测配置 hook —— 逐行移植 1.0 `App.vue` 的配置区：
 *
 *   - `init()`（`:2731-2767`）的**配置部分**（`GET /api/config`、`GET /api/manifests`、
 *     `loadJudgePrompts`，以及各连接字段的初值）
 *   - `saveConnectionConfig`（`:2419-2464`）
 *   - `markConnectionConfigDirty`（`:2414-2418`）
 *   - `loadJudgePrompts`（`:2467-2476`）/ `switchPromptKind`（`:2477`）/
 *     `resetJudgePrompt`（`:2481`）/ `saveJudgePrompt`（`:2574`）
 *
 * **边界**（与并行 hook 的分工，1.0 里它们全在一个组件里）：
 *   - 换模型 / 模型服务探活（`loadProfiles` / `loadCurrentModel` / `test-model`）在
 *     `useModelEndpoint`；`init()` 里对模型字段的赋值由那边的 `applyConfig()` 负责。
 *   - run 列表、`init()` 末尾的 run 选中与轮询在 `useRuns`。
 *   - 复用的三个 `watch` 在 `useReuseMode`。
 *
 * 因此 1.0 `:2751` 那个把 `loadRuns` / `loadProfiles` 一起拉进来的 `Promise.all` 在此
 * **只保留配置自己那两条**；组合层要按 1.0 的并行关系把其余 loader 一起发起。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { api, post } from "../api/client.ts";
import type { BenchConfig, ManifestPage, ManifestSummary } from "../api/types.ts";

/** `GET /api/judge-prompts` 的一条（`config.public_judge_prompts()`，1.0 :2465 `promptKindMeta`）。 */
export interface JudgePromptKind {
  kind: string;
  custom?: string;
  default?: string;
  [key: string]: unknown;
}

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
  const [rejudgePrompt, setRejudgePrompt] = useState("");
  const [judgePromptKinds, setJudgePromptKinds] = useState<JudgePromptKind[]>([]);
  const [activePromptKind, setActivePromptKind] = useState("answer_quality");
  const [promptDrafts, setPromptDrafts] = useState<Record<string, string>>({
    answer_quality: "",
    task_decision: "",
    evidence: "",
  });
  const [connectionConfigState, setConnectionConfigState] = useState<ConnectionState>("idle");
  const [connectionConfigMessage, setConnectionConfigMessage] = useState("");
  // 1.0 `:267` 的 `loading` 从 `true` 起，`init()` 的 `finally` 置 `false`。它是全局
  // 加载态，这里只覆盖配置这一段；`markConnectionConfigDirty` 的抑制窗口按它算。
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // 回调要读最新值又不想进依赖（会传给 useModelEndpoint / useReuseMode），用 ref 镜像。
  // `loadingRef` 用 effect 同步（1.0 的 `loading` 是同步 ref，React 的 state 不是）；
  // `draftsRef` 由 `writeDrafts` 单点维护，无需再在渲染期同步。
  const loadingRef = useRef(loading);
  const draftsRef = useRef(promptDrafts);
  const savingRef = useRef(false);
  useEffect(() => {
    loadingRef.current = loading;
  }, [loading]);

  const writeDrafts = useCallback((next: Record<string, string>) => {
    draftsRef.current = next;
    setPromptDrafts(next);
  }, []);

  /** 1.0 `:2414-2418`。 */
  const markConnectionConfigDirty = useCallback(() => {
    if (loadingRef.current) return;
    setConnectionConfigState("dirty");
    setConnectionConfigMessage("有未保存修改");
  }, []);

  /** 1.0 `:2467-2476`。 */
  const loadJudgePrompts = useCallback(async () => {
    try {
      // 1.0 `:2469` 是**裸 `fetch`**，不走统一错误处理（没有超时 / 重试 / `{error}` 解析）
      // ——照搬，这是 1.0 一处已知的不一致（`loadMemorySpaces` 同样）。
      const data = (await fetch("/api/judge-prompts").then((r) => r.json())) as {
        kinds?: JudgePromptKind[];
      };
      const kinds = data.kinds || [];
      setJudgePromptKinds(kinds);
      const next = { ...draftsRef.current };
      for (const meta of kinds) next[meta.kind] = meta.custom || meta.default || "";
      writeDrafts(next);
      setRejudgePrompt(next.answer_quality || "");
    } catch {
      /* keep defaults from config（1.0 `:2475` 原样） */
    }
  }, [writeDrafts]);

  /** 1.0 `:2477-2480`。 */
  const switchPromptKind = useCallback((kind: string) => {
    setActivePromptKind(kind);
    setRejudgePrompt(draftsRef.current[kind] || "");
  }, []);

  /** 1.0 `:2481-2485`。 */
  const resetJudgePrompt = useCallback(() => {
    const meta = judgePromptKinds.find((k) => k.kind === activePromptKind) || null;
    const value = meta?.default || config?.judge_prompt || "";
    writeDrafts({ ...draftsRef.current, [activePromptKind]: value });
    setRejudgePrompt(value);
  }, [judgePromptKinds, activePromptKind, config, writeDrafts]);

  /** 1.0 `:2574-2582`。 */
  const saveJudgePrompt = useCallback(async () => {
    const prompt = rejudgePrompt.trim();
    if (!prompt) {
      window.alert("提示词不能为空");
      return;
    }
    try {
      await post("/judge-prompts", { kind: activePromptKind, system_prompt: prompt });
      writeDrafts({ ...draftsRef.current, [activePromptKind]: prompt });
      window.alert(
        `已保存「${PROMPT_KIND_LABELS[activePromptKind] || activePromptKind}」提示词，重新评分与后续评测将使用它`,
      );
    } catch (e) {
      window.alert("保存失败：" + (e as Error).message);
    }
  }, [rejudgePrompt, activePromptKind, writeDrafts]);

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

  /** 1.0 `:2731-2767` 的配置部分（run 选中 / 轮询 / 模型探活不在这里）。 */
  const initConfig = useCallback(async () => {
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
      setRejudgePrompt(cfg.custom_judge_prompt || cfg.judge_prompt || "");
      const providers = (cfg.judge_providers || []) as JudgeProvider[];
      setJudgeProviderId(
        runtimeConfig.judge_provider_id || cfg.default_judge_provider_id || providers[0]?.id || "",
      );
      setConnectionConfigState("saved");
      setConnectionConfigMessage("已读取配置文件");
      const [, manifestPayload] = await Promise.all([
        loadJudgePrompts(),
        api<ManifestPage>("/manifests"),
      ]);
      setManifests(manifestPayload.manifests || []);
    } catch (e) {
      setError((e as Error).message || "读取配置失败");
    } finally {
      setLoading(false);
    }
  }, [loadJudgePrompts]);

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
    rejudgePrompt,
    setRejudgePrompt,
    judgePromptKinds,
    activePromptKind,
    promptDrafts,
    connectionConfigState,
    connectionConfigMessage,
    loading,
    setLoading,
    error,
    setError,
    markConnectionConfigDirty,
    loadJudgePrompts,
    switchPromptKind,
    resetJudgePrompt,
    saveJudgePrompt,
    saveConnectionConfig,
    initConfig,
  };
}
