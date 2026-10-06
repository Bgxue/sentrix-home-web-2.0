// 样式**先于组件导入**：`photobench.css` 是 1.0 `styles.css` 的逐行移植（见该文件头部
// 注释），选择器名与 1.0 一致，组件直接复用同一批类名。
import "./styles/photobench.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App.tsx";

const root = document.getElementById("root");
if (!root) {
  // 明确报错而不是 `root!`：`index.html` 与这个文件的 id 一旦对不上，
  // 静默失败会表现成「白屏且控制台干净」，那是本项目最不想再见到的一类失败。
  throw new Error("index.html 里没有 #root");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
