import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AUTO_REFRESH_MS, isUserEditing, shouldAutoRefresh, useAutoRefresh } from "./useAutoRefresh";

function Probe({ callback, enabled = true }: { readonly callback: () => void; readonly enabled?: boolean }) {
  useAutoRefresh(callback, 1000, enabled);
  return <input aria-label="probe" />;
}

function setVisibility(value: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => value,
  });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  setVisibility("visible");
  document.body.innerHTML = "";
});

describe("快照刷新节拍", () => {
  it("可见、未编辑时按间隔触发", () => {
    vi.useFakeTimers();
    const callback = vi.fn();
    render(<Probe callback={callback} />);

    act(() => {
      vi.advanceTimersByTime(999);
    });
    expect(callback).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(callback).toHaveBeenCalledTimes(1);
    expect(AUTO_REFRESH_MS).toBe(5000);
  });

  it("页面不可见时暂停", () => {
    vi.useFakeTimers();
    setVisibility("hidden");
    const callback = vi.fn();
    render(<Probe callback={callback} />);

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(callback).not.toHaveBeenCalled();
  });

  it("焦点在输入框时暂停", () => {
    vi.useFakeTimers();
    const callback = vi.fn();
    render(<Probe callback={callback} />);
    screen.getByLabelText("probe").focus();

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(callback).not.toHaveBeenCalled();
  });

  it("有模态（role=dialog）时暂停", () => {
    vi.useFakeTimers();
    const callback = vi.fn();
    render(<Probe callback={callback} />);
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    document.body.appendChild(dialog);

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(callback).not.toHaveBeenCalled();
  });

  it("enabled=false 不启动计时器", () => {
    vi.useFakeTimers();
    const callback = vi.fn();
    render(<Probe callback={callback} enabled={false} />);

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(callback).not.toHaveBeenCalled();
  });
});

describe("编辑判定", () => {
  it("默认页面没有输入焦点、没有模态时为 false", () => {
    expect(isUserEditing(document)).toBe(false);
    expect(shouldAutoRefresh(document)).toBe(true);
  });
});
