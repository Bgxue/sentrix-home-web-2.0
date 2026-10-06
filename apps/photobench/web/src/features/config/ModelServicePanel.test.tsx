import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CurrentModelInfo } from "../../api/types.ts";
import { ModelServicePanel, type ModelServicePanelProps } from "./ModelServicePanel.tsx";

function makeProps(overrides: Partial<ModelServicePanelProps> = {}): ModelServicePanelProps {
  return {
    profiles: [],
    vllmManagerUrl: "",
    modelEndpoint: "127.0.0.1:8100",
    selectedEndpointModel: "m1",
    endpointModels: ["m1", "m2"],
    currentModelInfo: null,
    currentModelLoading: false,
    currentModelError: "",
    currentModelPopoverOpen: false,
    modelTestState: "idle",
    modelTestMessage: "",
    setVllmManagerUrl: vi.fn(),
    setModelEndpoint: vi.fn(),
    setSelectedEndpointModel: vi.fn(),
    setCurrentModelPopoverOpen: vi.fn(),
    loadProfiles: vi.fn(),
    loadCurrentModel: vi.fn(),
    onEndpointModelChange: vi.fn(),
    testEndpointModel: vi.fn(),
    onModelManagerInput: vi.fn(),
    onModelEndpointInput: vi.fn(),
    applySavedConnection: vi.fn(),
    selectedModels: new Set<string>(),
    setSelectedModels: vi.fn(),
    cloudModelProfile: {
      id: "big_model",
      label: "big_model（云端 API）",
      model: "deepseek-v4.1-flash",
      available: true,
    },
    ...overrides,
  };
}

afterEach(cleanup);

describe("ModelServicePanel 测试模型端点三态", () => {
  it("未测：按钮是「测试 POST」、可点，没有反馈行", () => {
    render(<ModelServicePanel {...makeProps()} />);
    const button = screen.getByRole("button", { name: "测试 POST" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(screen.queryByText(/正在发送最小 POST 请求/)).toBeNull();
    expect(screen.queryByText(/POST 可用/)).toBeNull();
  });

  it("测试中：按钮变「测试中…」且禁用，反馈行带 state-testing", () => {
    render(
      <ModelServicePanel
        {...makeProps({ modelTestState: "testing", modelTestMessage: "正在发送最小 POST 请求…" })}
      />,
    );
    const button = screen.getByRole("button", { name: "测试中…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    const feedback = screen.getByText("正在发送最小 POST 请求…");
    expect(feedback.className).toContain("state-testing");
  });

  it("成功：反馈行带 state-success；失败：带 state-error", () => {
    const { rerender } = render(
      <ModelServicePanel
        {...makeProps({ modelTestState: "success", modelTestMessage: "POST 可用 · 12 ms" })}
      />,
    );
    expect(screen.getByText("POST 可用 · 12 ms").className).toContain("state-success");

    rerender(
      <ModelServicePanel
        {...makeProps({ modelTestState: "error", modelTestMessage: "POST 测试失败：boom" })}
      />,
    );
    expect(screen.getByText("POST 测试失败：boom").className).toContain("state-error");
  });

  it("点击「测试 POST」调的是 hook 的探活动作（组件自己不请求）", () => {
    const testEndpointModel = vi.fn();
    render(<ModelServicePanel {...makeProps({ testEndpointModel })} />);
    fireEvent.click(screen.getByRole("button", { name: "测试 POST" }));
    expect(testEndpointModel).toHaveBeenCalledTimes(1);
  });
});

describe("ModelServicePanel 换模型的新语义", () => {
  it("模型服务地址旁说明「改地址需重启 2.0、不热切换」", () => {
    render(<ModelServicePanel {...makeProps()} />);
    expect(
      screen.getByText(
        "模型服务地址在 2.0 启动时绑定：改地址不会热切换，启动评测时会用它重启 2.0；重启失败会中止本次评测。",
      ),
    ).toBeTruthy();
  });

  it("popover 是只读快照：展示当前模型，并标注需重启、不热切换", () => {
    const info: CurrentModelInfo = {
      served_model_name: "qwen3",
      served_models: ["qwen3"],
      model_base_url: "http://127.0.0.1:8100/v1",
      manager_available: false,
      state: { profile: "p1", max_model_len: 8192, dtype: "bfloat16" },
    };
    render(
      <ModelServicePanel
        {...makeProps({ currentModelInfo: info, currentModelPopoverOpen: true })}
      />,
    );
    expect(screen.getByText("qwen3")).toBeTruthy();
    expect(screen.getByText("已连接 OpenAI-compatible 端点")).toBeTruthy();
    expect(screen.getByText("该模型为只读状态；更换模型服务地址需重启 2.0，不会热切换。")).toBeTruthy();
  });
});

describe("ModelServicePanel 勾选集合", () => {
  it("模型注册表在有管理器地址时才出现，勾选走 setSelectedModels(new Set)", () => {
    const setSelectedModels = vi.fn();
    render(
      <ModelServicePanel
        {...makeProps({
          vllmManagerUrl: "127.0.0.1:8500",
          profiles: [{ id: "reg1", available: true }],
          setSelectedModels,
        })}
      />,
    );
    expect(screen.getByText("模型注册表（可多选，串行测试）")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /reg1/ }));
    expect(setSelectedModels).toHaveBeenCalledTimes(1);
    expect([...(setSelectedModels.mock.calls[0]![0] as Set<string>)]).toContain("reg1");
  });

  it("没有管理器地址时不显示注册表，改显示 model-manager-empty 说明", () => {
    render(<ModelServicePanel {...makeProps()} />);
    expect(screen.queryByText("模型注册表（可多选，串行测试）")).toBeNull();
    expect(screen.getByText(/未配置模型管理器/)).toBeTruthy();
  });
});
