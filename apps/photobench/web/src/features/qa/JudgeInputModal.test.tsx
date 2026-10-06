import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { RunItemDetail } from "../../api/types.ts";

import { JudgeInputModal } from "./JudgeInputModal.tsx";

afterEach(cleanup);

// 1.0 `:3796` 是 `<Teleport to="body">`：弹窗必须挂在 body 下、**不在组件树内**，
// 否则 `.judge-modal-backdrop` 的 position:fixed 会被祖先的 transform/overflow 影响。
describe("JudgeInputModal · portal 到 document.body", () => {
  it("弹窗渲染在 body 下，而不是组件的容器里", () => {
    const item: RunItemDetail = {
      index: 0,
      qa_id: "qa-7",
      judge: { input: { messages: [{ role: "user", content: "请评分" }] } },
    };

    const { container } = render(
      <div id="host">
        <JudgeInputModal item={item} onClose={() => {}} />
      </div>,
    );

    // 组件自己的容器里没有弹窗。
    expect(container.querySelector(".judge-modal")).toBeNull();
    expect(container.querySelector("#host .judge-modal")).toBeNull();

    // body 下有弹窗，且不在 #host 子树里。
    const modal = document.body.querySelector(".judge-modal");
    expect(modal).not.toBeNull();
    expect(modal!.closest("#host")).toBeNull();
    expect(modal!.textContent).toContain("JUDGE 模型原始输入");
    expect(modal!.textContent).toContain("qa-7");
  });

  it("没有保存 messages 时显示「无法恢复」提示", () => {
    render(<JudgeInputModal item={{ index: 1, qa_id: "qa-8" }} onClose={() => {}} />);

    expect(screen.getByText("该历史结果在运行时未保存 Judge 原始请求 JSON，无法恢复。")).not.toBeNull();
  });
});
