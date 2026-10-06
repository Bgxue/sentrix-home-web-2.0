/**
 * PhotoBench 前端的根组件。
 *
 * **当前是 S10b-1 的骨架**：只证明工具链（tsc / oxlint / vitest / vite build）通了。
 * 真正的路由与页面由 S10b-2 起按核查结论替换——`apps/photobench/web` 的最终结构
 * 见 `docs/refactor/2026-10-05-s10-implementation-plan.md` §2 与 S10b-0 的核查报告。
 */
export function App() {
  return (
    <main>
      <h1>PhotoBench</h1>
      <p>评测控制台（2.0 版）</p>
    </main>
  );
}
