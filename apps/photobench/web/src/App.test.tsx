import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "./App.tsx";

// 这个工程没开 vitest `globals`，testing-library 的自动 cleanup 不会注册——不显式
// 清理的话上一个用例挂上的 DOM 还在，`getByRole` 会因为找到两个按钮而抛错。
afterEach(cleanup);

// 夹具的口径：**切换后旧视图必须消失**。只断言「新视图在」是不够的——两个视图
// 同时挂上（比如把 v-if 写成恒真）也能让那种断言变绿。所以每个方向都断言「在」与
// 「不在」两半，两个口径给出不同答案。
describe("视图外壳", () => {
  it("默认显示评测运行；切到 QA 数据集浏览后评测运行消失", () => {
    render(<App />);

    expect(screen.queryByTestId("runs-view")).not.toBeNull();
    expect(screen.queryByTestId("qa-browser-view")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "QA 数据集浏览" }));

    expect(screen.queryByTestId("qa-browser-view")).not.toBeNull();
    expect(screen.queryByTestId("runs-view")).toBeNull();
  });

  it("能切回评测运行", () => {
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "QA 数据集浏览" }));
    fireEvent.click(screen.getByRole("button", { name: "评测运行" }));

    expect(screen.queryByTestId("runs-view")).not.toBeNull();
    expect(screen.queryByTestId("qa-browser-view")).toBeNull();
  });

  it("加载态渲染 Loading，而不是任何一个视图", () => {
    render(<App initialLoading />);

    expect(screen.getByRole("status")).not.toBeNull();
    expect(screen.queryByTestId("runs-view")).toBeNull();
    expect(screen.queryByTestId("qa-browser-view")).toBeNull();
  });
});
