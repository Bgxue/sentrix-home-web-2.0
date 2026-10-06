/**
 * PhotoBench 前端的根组件：两个视图的外壳。
 *
 * 移植 1.0 `App.vue:2839-2843` 的 `<main v-if="!loading" class="app-shell">` +
 * `<nav class="view-tabs">`。视图切换用 `useState`，**不引 react-router**：
 * 1.0 只有一个 `activeView` ref，没有 URL 语义（计划 §2 裁决）。
 *
 * 两个视图现在是占位组件，由后续切片逐个替换；`activeView` 的口径（字符串
 * `"runs"` / `"qa-browser"`）与 1.0 一致，替换时不用改切换逻辑。
 */
import { useState } from "react";

import { Loading } from "./components/Loading.tsx";
import { ViewTabs } from "./components/ViewTabs.tsx";
import type { ActiveView } from "./components/ViewTabs.tsx";
import { QaBrowserView } from "./features/qa-browser/QaBrowserView.tsx";
import { RunsView } from "./features/runs/RunsView.tsx";

export interface AppProps {
  /**
   * 初始加载态。1.0 的 `loading` 由 `init()`（拉 config / manifests / runs）驱动，
   * 到 S10b-2 接数据时才有值；现在默认 `false`，接上后由 `useState` 的 setter 驱动。
   */
  initialLoading?: boolean;
}

export function App({ initialLoading = false }: AppProps) {
  const [activeView, setActiveView] = useState<ActiveView>("runs");
  const [loading] = useState(initialLoading);

  if (loading) {
    return <Loading />;
  }

  return (
    <main className="app-shell">
      <ViewTabs activeView={activeView} onSelect={setActiveView} />
      {activeView === "runs" ? <RunsView /> : <QaBrowserView />}
    </main>
  );
}
