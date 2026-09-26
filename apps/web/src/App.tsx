/**
 * 应用入口：路由宿主。
 *
 * 1.0 用 `useState` 切视图（`src/app.js` 的 `state.view`），刷新会回到首页、也没法把
 * 链接分享到具体页面。2.0 改为 URL 路由（用户 2026-09-25 裁决③）。
 *
 * 旧的 `styles.css` **暂时保留**：现有的 Timeline / PeopleView / AssistantPanel /
 * AssetGrid / SearchBox 五个组件还在用它里面的类名，它们会在 F2–F5 各自迁进新骨架时
 * 一起换掉，届时删掉那份。
 */

import { BrowserRouter, Route, Routes } from "react-router";

import { ROUTES, SHELL } from "./app/router";
import { ScopeProvider } from "./app/ScopeProvider";

export default function App() {
  return (
    <BrowserRouter>
      <ScopeProvider>
        <Routes>
          <Route element={<SHELL />}>
            {Object.entries(ROUTES).map(([path, Page]) => (
              <Route key={path} path={path} element={<Page />} />
            ))}
          </Route>
        </Routes>
      </ScopeProvider>
    </BrowserRouter>
  );
}
