import { describe, expect, it } from "vitest";

import type { AssetSummary } from "../../shared/api/client";
import { countByScope } from "./scopeCounts";

function asset(id: string, scopeId?: string): AssetSummary {
  return {
    id,
    file_name: `${id}.jpg`,
    media_type: "image",
    mime_type: "image/jpeg",
    size_bytes: 1,
    status: "processed",
    captured_at: null,
    gps: null,
    ...(scopeId === undefined ? {} : { scope_id: scopeId }),
  };
}

describe("相册维度数量汇总", () => {
  it("按 scope_id 计数", () => {
    expect(countByScope([asset("a", "album1"), asset("b", "album1"), asset("c", "album2")])).toEqual({
      album1: 2,
      album2: 1,
    });
  });

  it("后端没给 scope_id 时返回空对象——字段缺失不等于 0 个相册", () => {
    expect(countByScope([asset("a"), asset("b")])).toEqual({});
  });
});
