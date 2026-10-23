import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ManifestSummary } from "../api/types.ts";

const { mockApi } = vi.hoisted(() => ({ mockApi: vi.fn() }));
vi.mock("../api/client.ts", () => ({ api: mockApi, post: vi.fn() }));

import { useQaBrowser } from "./useQaBrowser.ts";

const MANIFESTS: ManifestSummary[] = [
  {
    album_id: "album3",
    album_name: "相册 3",
    face_count: 1,
    photo_count: 1,
    video_count: 0,
    qa_sets: ["full-album3-38q", "compact-10q"],
  },
];

beforeEach(() => {
  mockApi.mockReset();
});

describe("useQaBrowser · loadQaBrowser（1.0 App.vue:2779-2788）", () => {
  it("拼出编码后的查询串，并消费 items 与 media_resolution", async () => {
    const calls: string[] = [];
    mockApi.mockImplementation((path: string) => {
      calls.push(path);
      return Promise.resolve({
        album_id: "album3",
        qa_set: "full-album3-38q",
        items: [{ qa_id: "q1", question: "?", tags: ["夜晚"] }],
        media_resolution: { status: "resolved", resolved_count: 3, missing_count: 1, ambiguous_count: 0 },
      });
    });
    const { result } = renderHook(() => useQaBrowser({ sentrixUrl: "  http://sx/api  ", manifests: MANIFESTS }));

    await act(async () => {
      await result.current.loadQaBrowser();
    });

    expect(calls[0]).toBe(
      "/qa-dataset?album_id=album3&qa_set=full-album3-38q&sentrix_url=http%3A%2F%2Fsx%2Fapi",
    );
    expect(result.current.qaBrowserItems).toHaveLength(1);
    expect(result.current.qaBrowserMediaResolution).toEqual({
      status: "resolved",
      resolved_count: 3,
      missing_count: 1,
      ambiguous_count: 0,
    });
    expect(result.current.qaBrowserLoading).toBe(false);
    expect(result.current.qaBrowserError).toBe("");
  });

  it("media_resolution 缺省时落成 null（1.0 `data.media_resolution || null`）", async () => {
    mockApi.mockImplementation(() => Promise.resolve({ items: [] }));
    const { result } = renderHook(() => useQaBrowser());
    await act(async () => {
      await result.current.loadQaBrowser();
    });
    expect(result.current.qaBrowserMediaResolution).toBeNull();
  });

  it("失败时写 error、清空 items 与 media_resolution", async () => {
    mockApi.mockImplementation(() => Promise.reject(new Error("boom")));
    const { result } = renderHook(() => useQaBrowser());
    await act(async () => {
      await result.current.loadQaBrowser();
    });
    expect(result.current.qaBrowserError).toBe("boom");
    expect(result.current.qaBrowserItems).toEqual([]);
    expect(result.current.qaBrowserMediaResolution).toBeNull();
    expect(result.current.qaBrowserLoading).toBe(false);
  });
});

describe("useQaBrowser · 相册默认值", () => {
  it("只装了 album3-14 时自动落到该相册及其第一个题集", () => {
    const only14: ManifestSummary[] = [
      {
        album_id: "album3-14",
        album_name: "album3 小子集",
        face_count: 8,
        photo_count: 14,
        video_count: 0,
        qa_sets: ["compact-10q", "behavior-6q"],
      },
    ];
    const { result } = renderHook(() => useQaBrowser({ manifests: only14 }));
    expect(result.current.qaBrowserAlbum).toBe("album3-14");
    expect(result.current.qaBrowserSet).toBe("compact-10q");
  });
});

describe("useQaBrowser · 派生值", () => {
  it("qaBrowserOptions 取自对应 album 的 qa_sets", () => {
    const { result } = renderHook(() => useQaBrowser({ manifests: MANIFESTS }));
    expect(result.current.qaBrowserOptions).toEqual(["full-album3-38q", "compact-10q"]);
  });

  it("qaBrowserTags 去重后排序（1.0 :2769）", async () => {
    mockApi.mockImplementation(() =>
      Promise.resolve({
        items: [
          { qa_id: "a1", tags: ["夜晚", "室内"] },
          { qa_id: "a2", tags: ["白天", "夜晚"] },
          { qa_id: "a3" }, // 无 tags，不能炸
        ],
      }),
    );
    const { result } = renderHook(() => useQaBrowser());
    await act(async () => {
      await result.current.loadQaBrowser();
    });
    expect(result.current.qaBrowserTags).toEqual(["夜晚", "室内", "白天"]);
  });

  it("visibleQaBrowserItems：标签与关键词同时生效（组合才命中）", async () => {
    mockApi.mockImplementation(() =>
      Promise.resolve({
        items: [
          { qa_id: "a1", question: "夜晚的灯", answer: "开灯", tags: ["夜晚", "室内"] },
          { qa_id: "a2", question: "白天的车", answer: "行驶", tags: ["白天"] },
          { qa_id: "a3", question: "夜晚的车", answer: "行驶", tags: ["夜晚"] },
        ],
      }),
    );
    const { result } = renderHook(() => useQaBrowser());
    await act(async () => {
      await result.current.loadQaBrowser();
    });
    expect(result.current.visibleQaBrowserItems).toHaveLength(3);

    act(() => {
      result.current.setQaBrowserTag("夜晚");
    });
    expect(result.current.visibleQaBrowserItems.map((item) => item.qa_id)).toEqual(["a1", "a3"]);

    act(() => {
      result.current.setQaBrowserSearch("车");
    });
    expect(result.current.visibleQaBrowserItems.map((item) => item.qa_id)).toEqual(["a3"]);
  });

  it("搜索覆盖 qa_id / 问题 / 答案 / 标签，且大小写不敏感", async () => {
    mockApi.mockImplementation(() =>
      Promise.resolve({
        items: [
          { qa_id: "Alpha-1", question: "问题", answer: "答案", tags: ["TagX"] },
          { qa_id: "b2", question: "其它", answer: "其它", tags: [] },
        ],
      }),
    );
    const { result } = renderHook(() => useQaBrowser());
    await act(async () => {
      await result.current.loadQaBrowser();
    });

    act(() => {
      result.current.setQaBrowserSearch("alpha");
    });
    expect(result.current.visibleQaBrowserItems.map((item) => item.qa_id)).toEqual(["Alpha-1"]);

    act(() => {
      result.current.setQaBrowserSearch("tagx");
    });
    expect(result.current.visibleQaBrowserItems.map((item) => item.qa_id)).toEqual(["Alpha-1"]);

    act(() => {
      result.current.setQaBrowserSearch(" 答案 ");
    });
    expect(result.current.visibleQaBrowserItems.map((item) => item.qa_id)).toEqual(["Alpha-1"]);
  });
});
