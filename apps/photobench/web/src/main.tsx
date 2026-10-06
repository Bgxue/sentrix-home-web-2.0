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
