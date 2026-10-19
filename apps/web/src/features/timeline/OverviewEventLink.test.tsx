/**
 * 概览页「最近事件」行 → 时间线的交接。
 *
 * 这条断言放在 timeline 目录下：它测的是时间线消费的 `?event=` 契约，而入口在
 * 概览页（本次唯一允许改动的外部文件）。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    listAssets: vi.fn(),
    listPeople: vi.fn(),
    listMemorySpaces: vi.fn(),
    fetchTimeline: vi.fn(),
    fetchHealth: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import { ScopeProvider } from "../../app/ScopeProvider";
import { OverviewPage } from "../overview/OverviewPage";

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("概览页事件行", () => {
  it("点击后带着事件 id 跳到时间线，而不是只到列表顶部", async () => {
    vi.mocked(client.listMemorySpaces).mockResolvedValue([
      { id: "album1", name: "第一册", kind: "benchmark", status: "active" },
    ]);
    vi.mocked(client.listAssets).mockResolvedValue([]);
    vi.mocked(client.listPeople).mockResolvedValue([]);
    vi.mocked(client.fetchHealth).mockResolvedValue({
      status: "ok",
      version: "0.1.0",
      database: "ok",
      embeddings: "ok",
    });
    vi.mocked(client.fetchTimeline).mockResolvedValue({
      scope_id: "album1",
      offset: 0,
      limit: 5,
      total: 1,
      days: [
        {
          date: "2026-01-02",
          events: [
            {
              id: "event1",
              title: "湖边散步",
              event_type: "outing",
              time_start: "2026-01-02T10:00:00+00:00",
              time_end: null,
              admin_district: "",
              place: "杭州市",
              activity: "",
              summary: "摘要",
              confidence: 0.8,
              photos: [],
            },
          ],
        },
      ],
    });

    render(
      <MemoryRouter initialEntries={["/"]}>
        <ScopeProvider>
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/timeline" element={<LocationProbe />} />
          </Routes>
        </ScopeProvider>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByText("湖边散步"));

    expect((await screen.findByTestId("location")).textContent).toBe(
      "/timeline?event=event1",
    );
  });
});
