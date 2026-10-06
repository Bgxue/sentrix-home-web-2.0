/**
 * 复用相册 / 工作模式 hook —— 移植 1.0 `App.vue` 的：
 *
 *   - `loadMemorySpaces`（`:2504-2512`，**裸 `fetch`**）
 *   - `runMode`（`:2496`）/ `onModeChange`（`:2549-2554`）
 *   - `existingScopeId` / `selectedReuseBaseId` / `scopeAlbumHint`（`:2501-2503`）
 *   - `selectedSpace` / `selectedReuseBase`（`:2513-2514`）
 *   - `inferAlbumForScope`（`:2517-2525`）
 *   - 三个跨状态 `watch`：
 *       `watch(existingScopeId)`（`:2526-2535`）
 *       `watch(selectedReuseBaseId)`（`:2536-2548`）
 *       `watch(judgeProviderId)`（`:255-262`）
 *
 * **React 没有 `watch`**，这里用 `useEffect` + **上一次值 ref** 模拟。用 ref 而不是靠
 * 依赖数组，是为了同时拿到两件 1.0 有、naive useEffect 没有的性质：
 *   1. **不在挂载时触发**（`watch` 只在值变化时触发；`useEffect` 挂载即跑）；
 *   2. **只在被观察的那个值变化时触发**，依赖里其它值（`manifests` / `runs` / `reuseBases`
 *      / `judgeProviders`）变化只是让回调读到最新值，不额外触发。
 *
 * **防循环**：三个 effect 写入的都是**被观察值之外的**状态（`selectedAlbum` /
 * `selectedQa` / `scopeAlbumHint` / `existingScopeId` / `judgeUrl` / `judgeModel`），
 * 而每个 effect 都由「上一次值 ref」把关——写回不会让同一个 effect 再跑。
 * `selectedReuseBaseId` 的 effect 会写 `existingScopeId`，从而触发 `existingScopeId`
 * 的 effect 一次；后者只写 `scopeAlbumHint` / `selectedAlbum`，**不回写 `existingScopeId`**，
 * 所以链路到此为止（这正是 1.0 的触发顺序：基座 watch 先设 hint，随后 scope watch 再覆盖）。
 * 所有写回都是字符串，重复写同值 React 会 bail out，不会因为父组件重渲染而无限循环。
 *
 * `selectedAlbum` / `selectedQa` 在 1.0 是别的区块的 ref，这里由调用方通过
 * `setSelectedAlbum` / `setSelectedQa` 注入。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ManifestSummary, MemorySpace, MemorySpacesResponse, ReuseBase, Run } from "../api/types.ts";

/* oxlint-disable react/set-state-in-effect -- 下面三个 effect 是刻意复刻 1.0 的 Vue `watch`（跨状态联动，React 无等价物）；React Compiler 的这条建议在此不适用，防循环见文件头 */

export interface UseReuseModeOptions {
  /** `useConfig.manifests`（`inferAlbumForScope` 的相册清单）。 */
  manifests: ManifestSummary[];
  /** run 列表（按 `scope_id → album_id` 反查）。 */
  runs: Run[];
  /** Sentrix 地址；`loadMemorySpaces` 的查询参数（1.0 `:2507` 用未 trim 的原值）。 */
  sentrixUrl: string;
  /** 当前 QA 数据集选择（1.0 `selectedQa`，两个 watch 只读它）。 */
  selectedQa: string;
  /** 1.0 的 watch 会写这两个外部 ref。 */
  setSelectedAlbum: (album: string) => void;
  setSelectedQa: (qa: string) => void;
  /** `watch(judgeProviderId)` 的输入：候选 provider 列表、当前 id 与当前 judgeModel（回退值）。 */
  judgeProviderId: string;
  judgeProviders: Array<Record<string, unknown>>;
  judgeModel: string;
  setJudgeUrl: (url: string) => void;
  setJudgeModel: (model: string) => void;
  /** `useConfig.markConnectionConfigDirty`。 */
  onDirty: () => void;
}

export function useReuseMode(options: UseReuseModeOptions) {
  const {
    manifests,
    runs,
    sentrixUrl,
    selectedQa,
    setSelectedAlbum,
    setSelectedQa,
    judgeProviderId,
    judgeProviders,
    judgeModel,
    setJudgeUrl,
    setJudgeModel,
    onDirty,
  } = options;

  const [runMode, setRunMode] = useState("full");
  const [memorySpaces, setMemorySpaces] = useState<MemorySpace[]>([]);
  const [reuseBases, setReuseBases] = useState<ReuseBase[]>([]);
  const [memorySpacesLoading, setMemorySpacesLoading] = useState(false);
  const [existingScopeId, setExistingScopeId] = useState("");
  const [selectedReuseBaseId, setSelectedReuseBaseId] = useState("");
  const [scopeAlbumHint, setScopeAlbumHint] = useState("");

  const selectedSpace = useMemo(
    () => memorySpaces.find((space) => space.id === existingScopeId) || null,
    [memorySpaces, existingScopeId],
  );
  const selectedReuseBase = useMemo(
    () => reuseBases.find((base) => base.base_id === selectedReuseBaseId) || null,
    [reuseBases, selectedReuseBaseId],
  );

  /** 1.0 `:2504-2512`。 */
  const loadMemorySpaces = useCallback(async () => {
    setMemorySpacesLoading(true);
    try {
      // 1.0 `:2507` 是**裸 `fetch`**，不走统一错误处理——照搬（与 `loadJudgePrompts` 同，
      // 是 1.0 的一处已知不一致）。
      const data = (await fetch(
        `/api/memory-spaces?sentrix_url=${encodeURIComponent(sentrixUrl)}`,
      ).then((r) => r.json())) as MemorySpacesResponse;
      setMemorySpaces(data.spaces || []);
      setReuseBases(data.reuse_bases || []);
    } catch {
      setMemorySpaces([]);
      setReuseBases([]);
    } finally {
      setMemorySpacesLoading(false);
    }
  }, [sentrixUrl]);

  /** 1.0 `:2517-2525`。 */
  const inferAlbumForScope = useCallback(
    (space: MemorySpace | null): string | null => {
      if (!space) return null;
      const run = runs.find(
        (item) => item.scope_id === space.id && (item.mode === "build" || item.mode === "full" || !item.mode),
      );
      if (run?.album_id && manifests.some((m) => m.album_id === run.album_id)) return run.album_id;
      const name = String(space.name || "");
      const candidates = manifests.map((m) => m.album_id).filter((id) => name.includes(id));
      if (candidates.length) return candidates.sort((a, b) => b.length - a.length)[0];
      return null;
    },
    [runs, manifests],
  );

  /** 1.0 `:2549-2554`。`nextRunMode` 用于「调用方刚 setState，state 还未刷新」。 */
  const onModeChange = useCallback(
    (nextRunMode?: string) => {
      const mode = nextRunMode ?? runMode;
      if (nextRunMode !== undefined) setRunMode(nextRunMode);
      setExistingScopeId("");
      setSelectedReuseBaseId("");
      setScopeAlbumHint("");
      if (mode === "reuse" && memorySpaces.length === 0) void loadMemorySpaces();
    },
    [runMode, memorySpaces.length, loadMemorySpaces],
  );

  // watch(existingScopeId) — 1.0 `:2526-2535`。
  const previousScopeId = useRef(existingScopeId);
  useEffect(() => {
    if (previousScopeId.current === existingScopeId) return;
    previousScopeId.current = existingScopeId;
    if (runMode !== "reuse") return;
    const space = memorySpaces.find((item) => item.id === existingScopeId) || null;
    const album = inferAlbumForScope(space);
    if (album) {
      setSelectedAlbum(album);
      setScopeAlbumHint(`题目对照数据集：${album}`);
    } else {
      setScopeAlbumHint("未能自动匹配数据集，题目对照沿用当前 QA 数据集所属数据");
    }
  }, [existingScopeId, runMode, memorySpaces, inferAlbumForScope, setSelectedAlbum]);

  // watch(selectedReuseBaseId) — 1.0 `:2536-2548`。
  const previousReuseBaseId = useRef(selectedReuseBaseId);
  useEffect(() => {
    if (previousReuseBaseId.current === selectedReuseBaseId) return;
    previousReuseBaseId.current = selectedReuseBaseId;
    if (runMode !== "reuse") return;
    const base = reuseBases.find((item) => item.base_id === selectedReuseBaseId) || null;
    if (!base) return;
    setExistingScopeId(base.scope_id || "");
    if (base.album_id) {
      setSelectedAlbum(base.album_id);
      const manifest = manifests.find((item) => item.album_id === base.album_id);
      // ⚠️ 照搬 1.0 `:2544` 的**已知缺陷**：1.0 的 manifest.qa_sets 是对象，而 2.0
      // `/api/manifests` 返回的是**数组**（types.ts `ManifestSummary.qa_sets: string[]`）。
      // `Object.keys([...])` 得到 `["0","1",…]`，于是 `options.includes(selectedQa)`
      // 永远匹配不到真实题集名，`selectedQa` 会被写成 `"0"`。**不修**——只在报告里登记。
      const qaSetOptions = Object.keys(
        (manifest?.qa_sets as unknown as Record<string, unknown>) || {},
      );
      if (qaSetOptions.length && !qaSetOptions.includes(selectedQa)) {
        setSelectedQa(qaSetOptions[0]);
      }
    }
    setScopeAlbumHint(
      `复用基座：${base.album_id} · ${base.model_profile}；QA 数据集：${base.album_id}`,
    );
  }, [
    selectedReuseBaseId,
    runMode,
    reuseBases,
    manifests,
    selectedQa,
    setExistingScopeId,
    setSelectedAlbum,
    setSelectedQa,
  ]);

  // watch(judgeProviderId) — 1.0 `:255-262`。
  const previousJudgeProviderId = useRef(judgeProviderId);
  useEffect(() => {
    if (previousJudgeProviderId.current === judgeProviderId) return;
    previousJudgeProviderId.current = judgeProviderId;
    const provider = judgeProviders.find((item) => item.id === judgeProviderId);
    if (!provider?.url) return;
    setJudgeUrl(String(provider.url));
    setJudgeModel(typeof provider.model === "string" && provider.model ? provider.model : judgeModel);
    onDirty();
  }, [judgeProviderId, judgeProviders, judgeModel, setJudgeUrl, setJudgeModel, onDirty]);

  /* oxlint-enable react/set-state-in-effect */

  return {
    runMode,
    setRunMode,
    onModeChange,
    memorySpaces,
    reuseBases,
    memorySpacesLoading,
    existingScopeId,
    setExistingScopeId,
    selectedReuseBaseId,
    setSelectedReuseBaseId,
    scopeAlbumHint,
    setScopeAlbumHint,
    selectedSpace,
    selectedReuseBase,
    loadMemorySpaces,
    inferAlbumForScope,
  };
}
