/**
 * 相册范围（scope）的单一来源。
 *
 * **语义逐条移植 1.0 `src/app.js:1162-1172`（初始化）与 `:1247`（切换写回）**，
 * localStorage 的键名沿用 `sentrix.scopeId`——从 1.0 迁过来的浏览器不该丢选择。
 *
 * **回落是两级，不是一句「不存在就回落」。** `:1168` 的实际写法是
 * `state.scopeId = storedSpace?.kind === "benchmark" ? storedSpace.id : ""`：
 *
 * 1. 存的 id 在列表里找不到 → `""`（全部相册）；
 * 2. **找得到但 `kind !== "benchmark"` → 也回落 `""`**。
 *
 * 第二种是 1.0 有意为之（注释写明是把旧的家庭选择迁到「全部相册」）。按字面实现
 * 「只判存在性」会漏掉它，所以那一条单独有用例骑住。
 *
 * 2.0 多一样：范围同步到 `?scope=` query，链接因此可分享、刷新保持。
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { listMemorySpaces, type MemorySpace } from "../shared/api/client";

const STORAGE_KEY = "sentrix.scopeId";
/** 「全部相册」的表示。1.0 用空串，2.0 沿用——它同时是 query 省略与 API 不传参的约定。 */
export const ALL_SCOPES = "";

interface ScopeValue {
  readonly scopeId: string;
  readonly spaces: readonly MemorySpace[];
  readonly setScopeId: (next: string) => void;
  /** 相册列表读失败的原因（后端还没起、或路由缺失）；非 null 时切换器只显示「全部相册」。 */
  readonly spacesError: string | null;
}

const ScopeContext = createContext<ScopeValue | null>(null);

/** 1.0 `:1168` 的判定，原样搬来并单独成函数——它有用例，不该埋在组件里。 */
export function resolveStoredScope(storedId: string, spaces: readonly MemorySpace[]): string {
  const stored = spaces.find((space) => space.id === storedId);
  return stored?.kind === "benchmark" ? stored.id : ALL_SCOPES;
}

function readQueryScope(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("scope");
}

export function ScopeProvider({ children }: { children: ReactNode }) {
  const [spaces, setSpaces] = useState<readonly MemorySpace[]>([]);
  const [spacesError, setSpacesError] = useState<string | null>(null);
  // `?scope=` 优先于 localStorage：分享出去的链接必须打开就是那一册。
  const [scopeId, setScopeIdState] = useState<string>(() => readQueryScope() ?? "");

  useEffect(() => {
    let cancelled = false;
    listMemorySpaces()
      .then((items) => {
        if (cancelled) return;
        setSpaces(items);
        setSpacesError(null);
        // 只在没有 `?scope=` 时才从 localStorage 恢复——否则会覆盖链接里的选择。
        if (readQueryScope() === null) {
          const stored = window.localStorage.getItem(STORAGE_KEY) ?? "";
          setScopeIdState(stored ? resolveStoredScope(stored, items) : ALL_SCOPES);
        }
      })
      .catch((cause: Error) => {
        if (cancelled) return;
        setSpacesError(cause.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const setScopeId = useCallback((next: string) => {
    setScopeIdState(next);
    window.localStorage.setItem(STORAGE_KEY, next);
    const url = new URL(window.location.href);
    if (next === ALL_SCOPES) url.searchParams.delete("scope");
    else url.searchParams.set("scope", next);
    // replaceState：切相册是筛选，不该往浏览器历史里塞一条。
    window.history.replaceState(null, "", url);
  }, []);

  const value = useMemo<ScopeValue>(
    () => ({ scopeId, spaces, setScopeId, spacesError }),
    [scopeId, spaces, setScopeId, spacesError],
  );
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}

export function useScope(): ScopeValue {
  const value = useContext(ScopeContext);
  if (value === null) throw new Error("useScope 必须在 ScopeProvider 内使用");
  return value;
}
