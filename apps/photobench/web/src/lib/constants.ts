// 跨切片的共享常量。此前 `EXECUTION_PHASES` 只在 `lib/telemetry.ts` 有一份私有副本
// （`telemetry.ts:41`，1.0 `App.vue:5`）；2.0 现在建公共常量模块，改为 import。
//
// 只放**确实是全局共用**的常量，避免变成杂物箱。

/** 1.0 `App.vue:5`。执行阶段的固定顺序与中文标签。 */
export interface ExecutionPhase {
  key: string;
  label: string;
}

export const EXECUTION_PHASES: readonly ExecutionPhase[] = [
  { key: "model_deploy", label: "模型部署" },
  { key: "scope_setup", label: "创建相册" },
  { key: "identity_seed", label: "预置身份" },
  { key: "photo_import", label: "照片/视频导入" },
  { key: "pipeline_processing", label: "流水线处理" },
  { key: "qa_eval", label: "QA 评测" },
];
