import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TOAST_DURATION_MS, ToastProvider, useToast } from "./Toast";

function Trigger({ message = "已保存", tone }: { readonly message?: string; readonly tone?: "error" }) {
  const { notify } = useToast();
  return (
    <button type="button" onClick={() => notify(message, tone)}>
      trigger
    </button>
  );
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Toast", () => {
  it("显示消息并在 2.6 秒后自动消失", () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByText("trigger"));
    expect(screen.getByRole("status").textContent).toContain("已保存");

    act(() => {
      vi.advanceTimersByTime(TOAST_DURATION_MS - 1);
    });
    expect(screen.getByRole("status").textContent).toContain("已保存");

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("失败提示带 error 色调，不会画成绿色对勾", () => {
    render(
      <ToastProvider>
        <Trigger message="切换失败" tone="error" />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByText("trigger"));
    expect(screen.getByRole("status").className).toContain("toast-error");
  });

  it("空消息不生成空壳提示", () => {
    render(
      <ToastProvider>
        <Trigger message="   " />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByText("trigger"));
    expect(screen.queryByRole("status")).toBeNull();
  });
});
