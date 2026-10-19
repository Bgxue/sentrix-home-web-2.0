import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { StoriesPage } from "./StoriesPage";

afterEach(cleanup);

describe("StoriesPage", () => {
  it("冻结状态明确说明后端故事端点尚未实现", () => {
    render(<StoriesPage />);

    expect(screen.getByText("后端故事端点尚未实现")).toBeTruthy();
    expect(screen.getByText(/2\.0 后端目前没有 stories 路由/)).toBeTruthy();
    // 不伪造可用入口：没有新建 / 生成按钮。
    expect(screen.queryByRole("button")).toBeNull();
  });
});
