/**
 * PhotoBench 前端的组合层（组合根）。
 *
 * 视图切片（`features/config` / `features/runs` / `features/qa` / `features/qa-browser`）
 * 都是纯 props 驱动的展示层，数据层是 `hooks/` 下的七个 hook。这个文件把两者接起来，
 * 并复刻 1.0 `App.vue`（只读权威）里唯一一处横跨全部区块的逻辑：`init()`（`:2731-2767`）。
 *
 * 两处视图切换照搬 1.0：只有 `activeView` 一个状态（`"runs"` / `"qa-browser"`），
 * 没有 URL 语义（计划 §2 裁决），所以不引 react-router。
 *
 * ## init() 与 1.0 的三处已知出入（都有原因，逐条登记在报告里）
 *
 * 1. **未调用 `useConfig.initConfig()`**：1.0 的 `init()` 把「配置字段」与「`loadJudgePrompts`
 *    + `/manifests` + `loadRuns(1)` + `loadProfiles()`」放进**同一个** `try`，其中四条并发、
 *    任一条失败都会跳过 `manifests.value = ...`（`:2751-2757`）。而 `initConfig` 把
 *    `manifests` 的赋值封在自己的 `Promise.all` 里、且自己吞异常，组合层无法让 `loadRuns`
 *    的失败中止那次赋值；`initConfig` 也不返回拉到的 config 对象（`applyConfig` 需要它）。
 *    所以这里直接按 1.0 的四条重写：配置字段经 `useConfig` 暴露的 setter 写入，
 *    `manifests` 由组合层持有（这正是 1.0 的失败语义所在）。
 * 2. **`loadRuns(1)` 先 await，再并发其余三条**：`useModelEndpoint.loadProfiles` 读的是
 *    React state（1.0 读同步 `ref`），`applyConfig` 刚写入的模型字段在同一轮闭包里读不到。
 *    先 await 一次网络请求，让那次写入完成一次渲染，再用 `modelApiRef` 取最新闭包。
 * 3. **`connectionConfigState` / `connectionConfigMessage` 停在初值**：这两个字段只在
 *    `initConfig` 里被写成 `"saved"` / `"已读取配置文件"`，而 `useConfig` 没有暴露写入口。
 *    只是初始文案差异；`markConnectionConfigDirty` 的抑制窗口靠 `setLoading(false)` 恢复。
 */
import { useEffect, useRef, useState } from "react";

import { api, post } from "./api/client.ts";
import type { BenchConfig, ManifestPage, ManifestSummary } from "./api/types.ts";
import { Lightbox } from "./components/Lightbox.tsx";
import type { LightboxTarget } from "./components/Lightbox.tsx";
import { Loading } from "./components/Loading.tsx";
import { ViewTabs } from "./components/ViewTabs.tsx";
import type { ActiveView } from "./components/ViewTabs.tsx";
import { ConfigSection } from "./features/config/ConfigSection.tsx";
import { startDisabledReason } from "./features/config/rules.ts";
import { QaBrowserView } from "./features/qa-browser/QaBrowserView.tsx";
import { QaResults } from "./features/qa/QaResults.tsx";
import { RunsView } from "./features/runs/RunsView.tsx";
import { useConfig } from "./hooks/useConfig.ts";
import { useModelEndpoint } from "./hooks/useModelEndpoint.ts";
import { useReuseMode } from "./hooks/useReuseMode.ts";
import { useRuns } from "./hooks/useRuns.ts";

export interface AppProps {
  /**
   * 初始加载态。真实入口（`main.tsx`）传 `true`：1.0 的 `loading` 从 `true` 起、
   * `init()` 的 `finally` 置 `false`（`:267` / `:2759`）。组件测试要立刻断言视图时
   * 不传（默认 `false`）。
   */
  initialLoading?: boolean;
}

/** 1.0 的 `a || b || c || ""`：取第一个非空字符串。 */
function firstString(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string" && value) return value;
  }
  return "";
}

export function App({ initialLoading = false }: AppProps) {
  const configApi = useConfig();
  const [selectedModels, setSelectedModels] = useState<Set<string>>(() => new Set());
  const modelApi = useModelEndpoint({
    vllmTargetId: configApi.vllmTargetId,
    selectedModels,
    setSelectedModels,
    onDirty: configApi.markConnectionConfigDirty,
  });
  const runs = useRuns();
  /**
   * 1.0 `config` 与 `manifests`（`:22` 一带）。组合层持有：`init()` 里 `manifests` 的赋值
   * 必须是四条 `Promise.all` 之后才发生（1.0 `:2751-2757` 的失败语义），而 `useConfig.initConfig`
   * 把赋值封在自己那个吞异常的 `Promise.all` 里，无法被 `loadRuns` 的失败中止。
   */
  const [config, setConfig] = useState<BenchConfig | null>(null);
  const [manifests, setManifests] = useState<ManifestSummary[]>([]);
  const [selectedAlbum, setSelectedAlbum] = useState("album3-14");
  const [selectedQa, setSelectedQa] = useState("compact-10q");
  const reuseApi = useReuseMode({
    manifests,
    runs: runs.runs,
    sentrixUrl: configApi.sentrixUrl,
    selectedQa,
    setSelectedAlbum,
    setSelectedQa,
    judgeProviderId: configApi.judgeProviderId,
    judgeProviders: (config?.judge_providers || []) as Array<Record<string, unknown>>,
    judgeModel: configApi.judgeModel,
    setJudgeUrl: configApi.setJudgeUrl,
    setJudgeModel: configApi.setJudgeModel,
    onDirty: configApi.markConnectionConfigDirty,
  });

  const [deleteScopeAfterRun, setDeleteScopeAfterRun] = useState(false);
  const [suiteRunning, setSuiteRunning] = useState(false);
  const [lightbox, setLightbox] = useState<LightboxTarget | null>(null);
  const [activeView, setActiveView] = useState<ActiveView>("runs");
  const [loading, setLoading] = useState(initialLoading);
  /** init() 是否已跑完（`loading` 的初值可被 `initialLoading=false` 关掉，不能拿它当判据）。 */
  const [initDone, setInitDone] = useState(false);

  // `loadProfiles` 读的是 state；init 里 `applyConfig` 刚写的值在同一轮闭包读不到。
  // 用 ref 存最新一次渲染的 API，等一次 await（loadRuns 的网络往返）后再取。
  const modelApiRef = useRef(modelApi);
  useEffect(() => {
    modelApiRef.current = modelApi;
  });

  // ── init() — 1.0 `App.vue:2731-2767` ─────────────────────────────────────
  /* oxlint-disable react-hooks/exhaustive-deps -- init() 是 1.0 的 onMounted，只跑一次：
     依赖里的 hook 对象每次渲染换 identity（loadProfiles 还随 state 变），进依赖会让
     init 反复重放。只跑一次由 `initStarted` ref 保证（同时挡掉 StrictMode 的二次 effect）。 */
  const initStarted = useRef(false);
  useEffect(() => {
    if (initStarted.current) return;
    initStarted.current = true;
    void (async () => {
      let modelEndpoint = "";
      try {
        // 1.0 :2734-2750：配置字段。
        const cfg = await api<BenchConfig>("/config");
        setConfig(cfg);
        const targets = cfg.vllm_targets || {};
        const targetId = cfg.default_vllm_target_id || Object.keys(targets)[0] || "";
        configApi.setVllmTargetId(targetId);
        const target = targets[targetId] || {};
        const runtimeConfig = (cfg.runtime_config || {}) as Record<string, unknown>;
        configApi.setSentrixUrl(firstString(runtimeConfig.sentrix_url, cfg.default_sentrix_url));
        configApi.setJudgeUrl(firstString(runtimeConfig.judge_url, cfg.default_judge_url));
        configApi.setJudgeModel(firstString(runtimeConfig.judge_model, cfg.judge_model));
        configApi.setJudgeApiKey("");
        configApi.setJudgeApiKeyDirty(false);
        configApi.setRejudgePrompt(firstString(cfg.custom_judge_prompt, cfg.judge_prompt));
        const providers = (cfg.judge_providers || []) as Array<Record<string, unknown>>;
        configApi.setJudgeProviderId(
          firstString(runtimeConfig.judge_provider_id, cfg.default_judge_provider_id, providers[0]?.id),
        );
        // 1.0 :2738-2741：模型服务字段（归 useModelEndpoint）。
        modelApi.applyConfig(cfg);
        modelEndpoint = firstString(
          runtimeConfig.model_base_url,
          cfg.default_vllm_model_base_url,
          target.model_base_url,
        );

        // 1.0 :2751-2757 的四条。loadRuns 先 await（见文件头 2），其余三条并发。
        await runs.loadRuns(1);
        const managerUrl = firstString(
          runtimeConfig.vllm_manager_url,
          target.manager_url,
          cfg.default_vllm_api_url,
        );
        const [, manifestPayload] = await Promise.all([
          configApi.loadJudgePrompts(),
          api<ManifestPage>("/manifests"),
          managerUrl.trim() ? modelApiRef.current.loadProfiles() : Promise.resolve(),
        ]);
        setManifests(manifestPayload.manifests || []);
      } catch (e) {
        configApi.setError((e as Error).message || "读取配置失败");
      } finally {
        // 1.0 的 `loading` 是全局的；2.0 拆开后配置段那份也要落下，否则
        // `markConnectionConfigDirty` 的抑制窗口永不释放（useConfig 的 loading 从 true 起）。
        configApi.setLoading(false);
        setLoading(false);
        setInitDone(true);
      }
      // 1.0 :2760。
      if (modelEndpoint.trim()) void modelApi.loadCurrentModel({ openPopover: false, endpoint: modelEndpoint });
    })();
  }, []);
  /* oxlint-enable react-hooks/exhaustive-deps */

  // 1.0 :2758 / :2761-2765：init 结束后，若列表里有 running/pending 的 run，自动选中并开轮询。
  const autoSelected = useRef(false);
  useEffect(() => {
    if (!initDone || autoSelected.current) return;
    autoSelected.current = true;
    const current = runs.runs.find((run) => ["running", "pending"].includes(String(run.status)));
    if (!current) return;
    runs.selectRun(current);
    runs.startPolling();
  }, [initDone, runs, runs.runs, runs.selectRun, runs.startPolling]);

  // ── 启动 / 停止（1.0 `startSuite` :2674-2685 / `stopSuite` :2686-2690）────────
  // 两者只做 JSX 事件处理，不需要 memo：直接定义成普通函数，避免 useCallback 依赖
  // 每轮重建的 `startInput`（那只会让 identity 抖动，没有收益）。
  const startInput = {
    hasRunning: runs.hasRunning,
    suiteRunning,
    selectedModels,
    modelEndpoint: modelApi.modelEndpoint,
    vllmManagerUrl: modelApi.vllmManagerUrl,
    selectedEndpointModel: modelApi.selectedEndpointModel,
    runMode: reuseApi.runMode,
    existingScopeId: reuseApi.existingScopeId,
  };

  const startSuite = async () => {
    const blocked = startDisabledReason(startInput);
    if (blocked && !blocked.includes("运行中")) {
      window.alert(blocked);
      return;
    }
    if (blocked) return;
    if (
      reuseApi.runMode === "build" &&
      !window.confirm("构建相册模式只导入并处理数据，不做 QA 测评；产物相册会保留。确定开始？")
    )
      return;
    setSuiteRunning(true);
    try {
      const result = await post<{ run_ids?: string[] }>("/runs", {
        album_id: selectedAlbum,
        qa_set: reuseApi.runMode === "build" ? undefined : selectedQa,
        mode: reuseApi.runMode,
        existing_scope_id: reuseApi.runMode === "reuse" ? reuseApi.existingScopeId : undefined,
        models: [...selectedModels],
        sentrix_url: configApi.sentrixUrl.trim(),
        judge_url: configApi.judgeUrl.trim(),
        judge_model: configApi.judgeModel.trim(),
        judge_provider_id: configApi.judgeProviderId,
        ...(configApi.judgeApiKeyDirty ? { judge_api_key: configApi.judgeApiKey } : {}),
        vllm_target_id: configApi.vllmTargetId,
        vllm_manager_url: modelApi.vllmManagerUrl.trim(),
        model_base_url:
          selectedModels.has("__current__") || modelApi.modelEndpointUserEdited
            ? modelApi.modelEndpoint.trim()
            : "",
        endpoint_model: selectedModels.has("__current__") ? modelApi.selectedEndpointModel : "",
        delete_scope_after_run: reuseApi.runMode === "full" ? deleteScopeAfterRun : false,
      });
      // 1.0 :2682-2683：选中新 run。`useRuns` 没暴露 `setActiveRunId`，`selectRun` 是等价入口。
      await runs.loadRuns(1);
      const runId = result.run_ids?.[0];
      if (runId) runs.selectRun({ run_id: runId });
      runs.startPolling();
    } catch (e) {
      configApi.setError((e as Error).message);
    } finally {
      setSuiteRunning(false);
    }
  };

  const stopSuite = async () => {
    if (!window.confirm("确定停止当前所有评测任务？")) return;
    await api("/cancel-active", { method: "POST", body: "{}" });
    await runs.loadRuns();
    await runs.loadActiveRun();
  };

  if (loading) {
    return <Loading />;
  }

  return (
    <>
      <main className="app-shell">
        <ViewTabs activeView={activeView} onSelect={setActiveView} />
        {activeView === "runs" ? (
          <>
            <ConfigSection
              connection={{ ...configApi, config, manifests }}
              model={{ ...modelApi, selectedModels, setSelectedModels }}
              reuse={reuseApi}
              album={{ selectedAlbum, setSelectedAlbum, selectedQa, setSelectedQa }}
              start={{
                ...startInput,
                deleteScopeAfterRun,
                onDeleteScopeAfterRunChange: setDeleteScopeAfterRun,
                onStart: () => {
                  void startSuite();
                },
                onStop: () => {
                  void stopSuite();
                },
              }}
              error={configApi.error}
            />
            <RunsView
              runs={runs}
              judgeModel={configApi.judgeModel}
              saveJudgePrompt={() => {
                void configApi.saveJudgePrompt();
              }}
            />
            <QaResults
              activeRun={runs.activeRun}
              resultRef={runs.runDetailRef}
              reviewFor={runs.reviewFor}
              onReviewChange={(summary, patch) => runs.setReview(String(summary.qa_id || ""), patch)}
              reviewSaving={runs.reviewSaving}
              onSaveReviews={() => {
                void runs.saveReviews();
              }}
              onOpenMedia={setLightbox}
            />
          </>
        ) : (
          <QaBrowserView
            sentrixUrl={configApi.sentrixUrl}
            manifests={manifests}
            onOpenMedia={setLightbox}
          />
        )}
      </main>
      <Lightbox target={lightbox} onClose={() => setLightbox(null)} />
    </>
  );
}
