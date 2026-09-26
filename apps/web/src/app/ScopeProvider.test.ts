/**
 * 相册范围的两级回落。
 *
 * **这条是 1.0 `src/app.js:1168` 的原样行为，也是最容易漏的一条**——它的实际写法是
 *
 *     state.scopeId = storedSpace?.kind === "benchmark" ? storedSpace.id : "";
 *
 * 于是回落有**两种**：存的 id 在列表里找不到；**以及找得到但 `kind !== "benchmark"`**。
 * 第二种是 1.0 有意为之（它的注释写明是把旧的家庭选择迁到「全部相册」）。按字面实现
 * 「存在就用、不存在就回落」会漏掉第二种，而漏掉之后的表现是「打开就停在某个旧相册」——
 * 不报错，只是行为不对。
 */

import { describe, expect, it } from "vitest";

import { ALL_SCOPES, resolveStoredScope } from "./ScopeProvider";
import type { MemorySpace } from "../shared/api/client";

function space(id: string, kind: string): MemorySpace {
  return { id, name: id, kind, status: "active" };
}

const SPACES: readonly MemorySpace[] = [
  space("album1", "benchmark"),
  space("home", "household"),
];

describe("resolveStoredScope", () => {
  it("存的 id 找不到 → 全部相册", () => {
    expect(resolveStoredScope("已经删掉的那一册", SPACES)).toBe(ALL_SCOPES);
  });

  it("找得到但不是 benchmark → **也**回落全部相册", () => {
    // 这条就是最容易漏的那个分支：id 明明在列表里，仍然不保留。
    expect(resolveStoredScope("home", SPACES)).toBe(ALL_SCOPES);
  });

  it("找得到且是 benchmark → 用它", () => {
    expect(resolveStoredScope("album1", SPACES)).toBe("album1");
  });

  it("空列表 → 全部相册（空库是 2.0 的起点）", () => {
    expect(resolveStoredScope("album1", [])).toBe(ALL_SCOPES);
  });

  it("空串（用户选过全部相册）→ 全部相册", () => {
    expect(resolveStoredScope("", SPACES)).toBe(ALL_SCOPES);
  });
});
