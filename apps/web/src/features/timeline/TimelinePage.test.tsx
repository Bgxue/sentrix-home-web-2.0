import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    fetchTimeline: vi.fn(),
    fetchEvent: vi.fn(),
    listMemorySpaces: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import type { TimelineEvent } from "../../shared/api/client";
import { ScopeProvider } from "../../app/ScopeProvider";
import { TimelinePage } from "./TimelinePage";

const NoopIntersectionObserver = class {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): never[] {
    return [];
  }
};

const EVENT: TimelineEvent = {
  id: "event1",
  title: "湖边散步",
  event_type: "outing",
  time_start: "2026-01-02T10:00:00+00:00",
  time_end: null,
  admin_district: "浙江省杭州市",
  place: "杭州市",
  activity: "散步",
  summary: "摘要",
  confidence: 0.8,
  photos: [],
};

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

function renderPage(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <ScopeProvider>
        <TimelinePage />
        <LocationProbe />
      </ScopeProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.stubGlobal("IntersectionObserver", NoopIntersectionObserver);
  vi.mocked(client.listMemorySpaces).mockResolvedValue([
    { id: "album1", name: "第一册", kind: "benchmark", status: "active" },
  ]);
  vi.mocked(client.fetchTimeline).mockResolvedValue({
    scope_id: "album1",
    offset: 0,
    limit: 20,
    total: 1,
    days: [{ date: "2026-01-02", events: [EVENT] }],
  });
  vi.mocked(client.fetchEvent).mockResolvedValue(EVENT);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("时间线页面", () => {
  it("带 ?event= 进入时直接展开该事件详情并高亮卡片", async () => {
    renderPage("/timeline?event=event1");

    const dialog = await screen.findByRole("dialog");
    expect(await screen.findByRole("heading", { name: "湖边散步" })).not.toBeNull();
    await waitFor(() =>
      expect(document.getElementById("timeline-event-event1")?.className).toContain("focused"),
    );
    expect(document.getElementById("timeline-event-event1")?.closest("details")?.hasAttribute("open")).toBe(
      true,
    );
    expect(dialog).not.toBeNull();
  });

  it("关闭详情会从 URL 里删掉 event 参数", async () => {
    renderPage("/timeline?event=event1");
    await screen.findByRole("dialog");

    fireEvent.click(screen.getByRole("button", { name: "关闭事件详情" }));

    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toBe("/timeline"),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("点击卡片会把事件写进 URL，再按 URL 打开详情", async () => {
    renderPage("/timeline");
    await screen.findByText("湖边散步");
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "查看详情 →" }));

    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toBe("/timeline?event=event1"),
    );
    expect(await screen.findByRole("dialog")).not.toBeNull();
  });
});
