import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ManifestSummary, QaRow } from "../../api/types.ts";

const { mockApi } = vi.hoisted(() => ({ mockApi: vi.fn() }));
vi.mock("../../api/client.ts", () => ({ api: mockApi, post: vi.fn(), BASE: "/api" }));

import { QaBrowserView } from "./QaBrowserView.tsx";

afterEach(cleanup);
beforeEach(() => {
  mockApi.mockReset();
});

const MANIFESTS: ManifestSummary[] = [
  {
    album_id: "album3",
    album_name: "相册 3",
    face_count: 1,
    photo_count: 2,
    video_count: 0,
    qa_sets: ["full-album3-38q", "empty-set"],
  },
  {
    album_id: "albumB",
    album_name: "相册 B",
    face_count: 1,
    photo_count: 1,
    video_count: 0,
    qa_sets: ["albumB-set"],
  },
];

const ITEMS: QaRow[] = [
  { qa_id: "q-night", question: "夜晚的灯", answer: "开灯", tags: ["夜晚"] },
  { qa_id: "q-day", question: "白天的车", answer: "行驶", tags: ["白天"] },
];

/**
 * 夹具的口径：**只有 album3 + full-album3-38q 这个组合才有数据**（相册 / 题集任一不符即为空），
 * 且返回的两条题里只有 `q-night` 同时命中「搜索=灯」与「标签=夜晚」（搜索 / 标签任一不符即为空）。
 * 四个条件缺一即 0 条。
 */
function respond(path: string) {
  if (path.includes("album_id=album3") && path.includes("qa_set=full-album3-38q")) {
    return {
      album_id: "album3",
      qa_set: "full-album3-38q",
      items: ITEMS,
      media_resolution: { status: "resolved", resolved_count: 2, missing_count: 0, ambiguous_count: 0 },
    };
  }
  return { album_id: "", qa_set: "", items: [], media_resolution: { status: "no_media", resolved_count: 0, missing_count: 0, ambiguous_count: 0 } };
}

describe("QaBrowserView · 四个筛选缺一不可（1.0 App.vue:3687-3695）", () => {
  it("相册 / 题集 / 搜索 / 标签同时满足才命中，去掉任一即为空", async () => {
    mockApi.mockImplementation((path: string) => Promise.resolve(respond(path)));
    const { container } = render(<QaBrowserView manifests={MANIFESTS} sentrixUrl="" />);
    const cards = () => container.querySelectorAll(".qa-review-card");

    await waitFor(() => expect(cards()).toHaveLength(2)); // 相册+题集默认值：两条都在

    const search = screen.getByPlaceholderText("QA ID、问题或答案");
    const selects = container.querySelectorAll(".qa-browser-toolbar select");
    const albumSelect = selects[0] as HTMLSelectElement;
    const setSelect = selects[1] as HTMLSelectElement;
    const tagSelect = selects[2] as HTMLSelectElement;

    fireEvent.change(search, { target: { value: "灯" } });
    await waitFor(() => expect(cards()).toHaveLength(1)); // 搜索 → 只剩 q-night
    fireEvent.change(tagSelect, { target: { value: "夜晚" } });
    await waitFor(() => expect(cards()).toHaveLength(1)); // + 标签 → 组合命中

    // 相册：换成 albumB → 该相册无数据
    fireEvent.change(albumSelect, { target: { value: "albumB" } });
    await waitFor(() => expect(cards()).toHaveLength(0));
    fireEvent.change(albumSelect, { target: { value: "album3" } });
    await waitFor(() => expect(cards()).toHaveLength(1));

    // 题集：换成 empty-set → 无数据
    fireEvent.change(setSelect, { target: { value: "empty-set" } });
    await waitFor(() => expect(cards()).toHaveLength(0));
    fireEvent.change(setSelect, { target: { value: "full-album3-38q" } });
    await waitFor(() => expect(cards()).toHaveLength(1));

    // 搜索：换成不命中词 → 空
    fireEvent.change(search, { target: { value: "不存在的词" } });
    await waitFor(() => expect(cards()).toHaveLength(0));
    fireEvent.change(search, { target: { value: "灯" } });
    await waitFor(() => expect(cards()).toHaveLength(1));

    // 标签：换成不命中标签 → 空
    fireEvent.change(tagSelect, { target: { value: "白天" } });
    await waitFor(() => expect(cards()).toHaveLength(0));
    fireEvent.change(tagSelect, { target: { value: "夜晚" } });
    await waitFor(() => expect(cards()).toHaveLength(1));
  });
});

describe("QaBrowserView · media_resolution 提示（1.0 App.vue:3698-3700）", () => {
  it("全部解析 / 部分缺失 / no_media 三种状态给出不同提示", async () => {
    const state: { resolution: unknown } = { resolution: null };
    mockApi.mockImplementation(() =>
      Promise.resolve({ album_id: "album3", qa_set: "full-album3-38q", items: [], media_resolution: state.resolution }),
    );
    const { container } = render(<QaBrowserView manifests={MANIFESTS} sentrixUrl="" />);
    const banner = () => container.querySelector(".qa-media-source-status");
    const refresh = () => screen.getByRole("button", { name: "刷新数据" });

    state.resolution = { status: "resolved", resolved_count: 5, missing_count: 0, ambiguous_count: 0 };
    fireEvent.click(refresh());
    await waitFor(() => expect(banner()?.textContent).toContain("已解析 5"));
    const allResolved = banner()?.textContent;

    state.resolution = { status: "resolved", resolved_count: 3, missing_count: 2, ambiguous_count: 1 };
    fireEvent.click(refresh());
    await waitFor(() => expect(banner()?.textContent).toContain("缺失 2"));
    const partial = banner()?.textContent;

    expect(partial).not.toBe(allResolved);

    state.resolution = { status: "no_media", resolved_count: 0, missing_count: 0, ambiguous_count: 0 };
    fireEvent.click(refresh());
    await waitFor(() => expect(banner()).toBeNull());
  });
});
