/**
 * 加载态。1.0 是 `<main v-if="!loading">` 的 `v-else` 分支
 * （`App.vue:3766`：`<div class="loading">加载评测数据…</div>`），文案与 class 照搬。
 *
 * `role="status"` 是本次新加的：1.0 那个 div 没有语义，屏幕阅读器读不到「正在加载」。
 * 视觉仍由 `.loading` 这个 class 决定（样式另有人负责）。
 */
export function Loading() {
  return (
    <div className="loading" role="status">
      加载评测数据…
    </div>
  );
}
