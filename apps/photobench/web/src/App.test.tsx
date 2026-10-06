import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App.tsx";

// 骨架用例：证明 jsdom + JSX 转换 + testing-library 这一整套是通的。
// 它不证明任何业务行为——那种用例归 S10b-2 起各切片。
describe("工程骨架", () => {
  it("根组件能挂上并渲染出标题", () => {
    render(<App />);

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("PhotoBench");
  });
});
