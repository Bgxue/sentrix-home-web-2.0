/**
 * 配置区的**纯视图逻辑** —— 1.0 `App.vue` 里散在 `<script setup>` 的几处常量与
 * 计算属性，单独成模块以便脱离 JSX 单测（`startDisabledReason` 的两份夹具要点名验
 * 「各条件满足」与「差一个条件」给出不同文案）。
 *
 *   - `RUN_MODES_UI` / `runModeMeta`（1.0 `:2491-2497`）
 *   - `startDisabledReason`（1.0 `:2555-2564`）
 *   - `albumCountLabel`（1.0 `:224-228`）
 *   - `qaOptionsFor`（1.0 `:250` 的 `qaOptions` 计算属性）
 *
 * 没有放进 `lib/constants.ts`：那一份是跨切片共享的常量，而这几项只服务配置区，
 * 且 `albumCountLabel` / `qaOptionsFor` 依赖 `ManifestSummary` 的具体形状。
 */
import type { ManifestSummary } from "../../api/types.ts";

/** 一种工作模式的 UI 元数据（1.0 `RUN_MODES_UI` 的一条）。 */
export interface RunModeMeta {
  id: string;
  label: string;
  hint: string;
  button: string;
}

/** 1.0 `:2491-2495`，文案逐字照搬。 */
export const RUN_MODES_UI: readonly RunModeMeta[] = [
  {
    id: "full",
    label: "全链路测试",
    hint: "新建相册 → 身份预置 → 导入图片和视频 → 流水线处理 → QA 测评",
    button: "启动评测",
  },
  {
    id: "reuse",
    label: "复用相册测评",
    hint: "选择后端已有相册，跳过导入与处理，直接 QA 测评（相册不会被删除）",
    button: "启动复用测评",
  },
  {
    id: "build",
    label: "构建相册",
    hint: "新建相册并导入图片/视频完成数据处理；产物相册保留供复用或在线测试，不做 QA 测评",
    button: "启动相册构建",
  },
];

/** 1.0 `:2497`：`RUN_MODES_UI.find(...) || RUN_MODES_UI[0]`。 */
export function runModeMeta(runMode: string): RunModeMeta {
  return RUN_MODES_UI.find((mode) => mode.id === runMode) || RUN_MODES_UI[0]!;
}

/** `startDisabledReason` 读到的全部输入。字段名对齐 hook / 模板里的来源。 */
export interface StartDisabledInput {
  hasRunning: boolean;
  suiteRunning: boolean;
  selectedModels: ReadonlySet<string>;
  modelEndpoint: string;
  vllmManagerUrl: string;
  selectedEndpointModel: string;
  runMode: string;
  existingScopeId: string;
}

/**
 * 1.0 `:2555-2564`。**判定顺序就是 1.0 的 `if` 顺序，不能重排或合并**——多条同时
 * 不满足时，1.0 固定报最靠前的那一条。返回空串表示可以启动。
 */
export function startDisabledReason(input: StartDisabledInput): string {
  if (input.hasRunning || input.suiteRunning) return "已有任务运行中";
  if (!input.selectedModels.size) return "请先选择模型";
  const cloudOnly = [...input.selectedModels].every((modelId) => modelId === "big_model");
  if (!cloudOnly && !input.modelEndpoint.trim()) return "请先填写模型服务地址";
  if (
    [...input.selectedModels].some((modelId) => !["__current__", "big_model"].includes(modelId)) &&
    !input.vllmManagerUrl.trim()
  )
    return "选择注册表模型需要模型管理器地址";
  if (input.selectedModels.has("__current__") && !input.selectedEndpointModel)
    return "请先从模型服务中选择要复用的模型";
  if (input.runMode === "reuse" && !input.existingScopeId) return "请先选择要复用的相册";
  return "";
}

/** 1.0 `:224-228`。相册下拉里的「N人 / N图 / N视频」。 */
export function albumCountLabel(manifest: ManifestSummary): string {
  const videos = Number(manifest.video_count || 0);
  const base = `${manifest.face_count}人 / ${manifest.photo_count}图`;
  return videos ? `${base} / ${videos}视频` : base;
}

/** 1.0 `:250`：`manifests.find(...)?.qa_sets || ["compact-10q"]`。 */
export function qaOptionsFor(manifests: ManifestSummary[], selectedAlbum: string): string[] {
  return manifests.find((manifest) => manifest.album_id === selectedAlbum)?.qa_sets || [
    "compact-10q",
  ];
}
