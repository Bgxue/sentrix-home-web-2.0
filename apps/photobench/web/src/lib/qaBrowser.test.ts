import { describe, expect, it } from "vitest";

import {
  modeBadgeClass,
  modeLabel,
  qaActionBadge,
  qaAnswerabilityLabel,
  qaReferenceLabel,
  qaTypeLabel,
} from "./qaBrowser.ts";

// 三个映射函数的「未命中回退」互不相同：qaTypeLabel -> 未分类，
// qaActionBadge / qaAnswerabilityLabel -> 原值或 `-`。夹具必须把这几种区分开。

describe("qaTypeLabel (:2789-2791)", () => {
  it("映射命中回中文", () => {
    expect(qaTypeLabel("event_memory_qa")).toBe("事件记忆");
    expect(qaTypeLabel("jailbreak_attempt")).toBe("越狱尝试");
  });
  it("未命中回原值；空/缺省回 `未分类`", () => {
    expect(qaTypeLabel("custom_type")).toBe("custom_type");
    expect(qaTypeLabel("")).toBe("未分类");
    expect(qaTypeLabel(undefined)).toBe("未分类");
  });
});

describe("qaActionBadge (:2792-2794)", () => {
  it("映射命中回中文", () => {
    expect(qaActionBadge("answer")).toBe("回答");
    expect(qaActionBadge("refuse")).toBe("拒答");
    expect(qaActionBadge("clarify")).toBe("澄清");
  });
  it("未命中回原值；空/缺省回 `-`（不是 `未分类`）", () => {
    expect(qaActionBadge("custom")).toBe("custom");
    expect(qaActionBadge("")).toBe("-");
    expect(qaActionBadge(undefined)).toBe("-");
  });
});

describe("qaAnswerabilityLabel (:2795-2797)", () => {
  it("映射命中回中文", () => {
    expect(qaAnswerabilityLabel("answerable")).toBe("可回答");
    expect(qaAnswerabilityLabel("answerable_after_clarification")).toBe("澄清后可回答");
  });
  it("未命中回原值；空/缺省回 `-`", () => {
    expect(qaAnswerabilityLabel("custom")).toBe("custom");
    expect(qaAnswerabilityLabel(undefined)).toBe("-");
  });
});

describe("qaReferenceLabel (:2821-2823)", () => {
  it("只有 clarify 走澄清示例，其余都是参考回答", () => {
    expect(qaReferenceLabel({ expected_action: "clarify" })).toBe("参考澄清示例");
    expect(qaReferenceLabel({ expected_action: "answer" })).toBe("参考回答");
    expect(qaReferenceLabel({})).toBe("参考回答");
    expect(qaReferenceLabel(null)).toBe("参考回答");
  });
});

describe("modeLabel (:2565)", () => {
  it("三种模式各有中文", () => {
    expect(modeLabel("full")).toBe("全链路");
    expect(modeLabel("reuse")).toBe("复用测评");
    expect(modeLabel("build")).toBe("构建相册");
  });
  it("缺省 / 空串都当 full；未命中回原值", () => {
    expect(modeLabel(undefined)).toBe("全链路");
    expect(modeLabel(null)).toBe("全链路");
    expect(modeLabel("")).toBe("全链路");
    expect(modeLabel("weird")).toBe("weird");
  });
});

describe("modeBadgeClass (:2566)", () => {
  it("三种模式各有类名", () => {
    expect(modeBadgeClass("full")).toBe("mode-full");
    expect(modeBadgeClass("reuse")).toBe("mode-reuse");
    expect(modeBadgeClass("build")).toBe("mode-build");
  });
  it("缺省 / 空串 / 未命中都回 `mode-full`", () => {
    expect(modeBadgeClass(undefined)).toBe("mode-full");
    expect(modeBadgeClass("")).toBe("mode-full");
    expect(modeBadgeClass("weird")).toBe("mode-full");
  });
});
