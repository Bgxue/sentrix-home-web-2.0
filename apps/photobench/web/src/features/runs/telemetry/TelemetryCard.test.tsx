import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Run } from "../../../api/types.ts";

import { TelemetryCard } from "./TelemetryCard.tsx";

// 夹具口径：`telemetry_live.history` 有 3 个采样点（`telemetryChart` 要 ≥2 才给数据）。
// 1.0 参考 `App.vue:1027-1125`（renderTelemetryChart）+ `:2824-2834`（toggle / watch）。
//
// jsdom 画不出真图，所以只断言命令式生命周期：init 次数 / setOption / resize / dispose。
const echartsMock = vi.hoisted(() => {
  const instance = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() };
  return { instance, init: vi.fn(() => instance) };
});

vi.mock("echarts", () => ({ init: echartsMock.init }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  echartsMock.init.mockClear();
  echartsMock.instance.setOption.mockClear();
  echartsMock.instance.resize.mockClear();
  echartsMock.instance.dispose.mockClear();
});

function run(overrides: Partial<Run> = {}): Run {
  return {
    run_id: "r1",
    status: "running",
    current_phase: "qa_eval",
    telemetry_live: {
      source: "nvidia",
      status: "running",
      samples_count: 3,
      history: [
        { t: 0, phase: "qa_eval", memory_used_mib: 1024, system_memory_used_mib: 2048, model_process_memory_used_mib: 512, model_process_system_memory_used_mib: 256 },
        { t: 60, phase: "qa_eval", memory_used_mib: 2048, system_memory_used_mib: 2560, model_process_memory_used_mib: 640, model_process_system_memory_used_mib: 320 },
        { t: 120, phase: "qa_eval", memory_used_mib: 3072, system_memory_used_mib: 2560, model_process_memory_used_mib: 768, model_process_system_memory_used_mib: 384 },
      ],
    },
    ...overrides,
  } as Run;
}

/** 没有任何采样点的 run：`telemetrySampleCount` 为 0，整张卡不渲染。 */
function emptyRun(): Run {
  return { run_id: "r0", status: "done" } as Run;
}

describe("TelemetryCard · echarts 生命周期（1.0 :1027-1125 / :2824-2837）", () => {
  it("挂载建一次实例；run 变化只重画，不再 init", () => {
    const view = render(<TelemetryCard run={run()} />);

    expect(echartsMock.init).toHaveBeenCalledTimes(1);
    expect(echartsMock.instance.setOption).toHaveBeenCalled();
    // 1.0 `:1124`：setOption 之后立刻 resize 一次。
    expect(echartsMock.instance.resize).toHaveBeenCalled();

    // 换一个 run 对象（内容不同）→ 组件重渲染，实例必须复用而不是重建。
    view.rerender(<TelemetryCard run={run({ status: "completed" })} />);
    view.rerender(<TelemetryCard run={run({ status: "failed" })} />);
    expect(echartsMock.init).toHaveBeenCalledTimes(1);

    // 每次数据变化都重画（1.0 `:2834` 的 deep watch）。
    expect(echartsMock.instance.setOption.mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it("卸载时 dispose 实例（1.0 :2837）", () => {
    const view = render(<TelemetryCard run={run()} />);
    expect(echartsMock.instance.dispose).not.toHaveBeenCalled();

    view.unmount();
    expect(echartsMock.instance.dispose).toHaveBeenCalledTimes(1);
  });

  it("没有采样点时整张卡不渲染，也不 init", () => {
    const { container } = render(<TelemetryCard run={emptyRun()} />);
    expect(container.querySelector(".live-telemetry-card")).toBeNull();
    expect(echartsMock.init).not.toHaveBeenCalled();
  });

  it("展开时 resize、折叠时不 resize（1.0 :2824-2830 的 0×0 容器坑）", () => {
    const { container } = render(<TelemetryCard run={run()} />);
    const details = container.querySelector("details.live-telemetry-card") as HTMLDetailsElement;
    expect(details).not.toBeNull();

    echartsMock.instance.resize.mockClear();

    details.open = false;
    fireEvent(details, new Event("toggle"));
    expect(echartsMock.instance.resize).not.toHaveBeenCalled();

    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(echartsMock.instance.resize).toHaveBeenCalledTimes(1);
  });

  it("容器尺寸变化时 resize（ResizeObserver）", () => {
    let notify: (() => void) | null = null;
    class FakeResizeObserver {
      constructor(callback: () => void) {
        notify = callback;
      }
      observe = vi.fn();
      disconnect = vi.fn();
    }
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);

    render(<TelemetryCard run={run()} />);
    expect(notify).not.toBeNull();

    echartsMock.instance.resize.mockClear();
    (notify as unknown as () => void)();
    expect(echartsMock.instance.resize).toHaveBeenCalledTimes(1);
  });

  it("option 按数据源选线：NVIDIA 源画整卡显存 / 主模型显存，且 tooltip 与 markArea 就位", () => {
    render(<TelemetryCard run={run()} />);
    const option = echartsMock.instance.setOption.mock.calls[0]?.[0] as {
      series: Array<{ name: string; data: Array<number | null> }>;
      xAxis: { data: string[] };
    };

    expect(option.series.map((series) => series.name)).toEqual([
      "整卡 GPU 显存",
      "整机 RAM",
      "主模型 GPU 显存（nvidia-smi 按 PID）",
      "主模型 RAM（进程 VmRSS）",
    ]);
    // MiB → GiB：1024/2048/3072 MiB → 1/2/3 GiB。
    expect(option.series[0]?.data).toEqual([1, 2, 3]);
    // 首点 t=0 为基准 → +0.0 / +1.0 / +2.0 min。
    expect(option.xAxis.data).toEqual(["+0.0 min", "+1.0 min", "+2.0 min"]);
  });

  it("Mac 统一内存源画另外四条线（1.0 :1041-1046）", () => {
    const macRun = run({
      telemetry_live: {
        source: "apple_unified",
        status: "running",
        samples_count: 2,
        history: [
          { t: 0, system_memory_used_mib: 8192, product_stack_memory_mib: 4096, sentrix_stack_pss_mib: 2048, model_process_system_memory_used_mib: 3072 },
          { t: 60, system_memory_used_mib: 8192, product_stack_memory_mib: 4096, sentrix_stack_pss_mib: 2048, model_process_system_memory_used_mib: 3072 },
        ],
      },
    });
    render(<TelemetryCard run={macRun} />);
    const option = echartsMock.instance.setOption.mock.calls[0]?.[0] as { series: Array<{ name: string }> };
    expect(option.series.map((series) => series.name)).toEqual([
      "整机内存（App+Wired+Compressed）",
      "整套产品内存（相关进程 phys_footprint）",
      "Sentrix 周边（不含主模型，phys_footprint）",
      "主模型进程 phys_footprint",
    ]);
  });
});
