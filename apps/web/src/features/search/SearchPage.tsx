/**
 * 资料检索。
 *
 * **1.0 没有这一页**：它的提问框直接提交进助手对话（`searchBar()` → assistant turn）。
 * 而 2.0 的 `GET /api/v1/search` 是直检索（视觉 + 词法 + 元数据），与助手的 agent
 * 链路是两种能力，所以单列成页（用户 2026-09-25 裁决⑤）。
 */

import { useState } from "react";

import { useScope } from "../../app/ScopeProvider";
import { PageHeader } from "../../shared/ui/PageHeader";
import type { SearchResponse } from "../../shared/api/client";
import { SearchBox } from "./SearchBox";
import { SearchResults } from "./SearchResults";

export function SearchPage() {
  const { scopeId } = useScope();
  const [results, setResults] = useState<SearchResponse | null>(null);

  return (
    <>
      <PageHeader
        kicker="家庭资料 / 直接检索"
        title="按内容找照片，不走对话。"
        description="视觉、词法与元数据三条通道并行召回。每一条结果都会列出它在各通道上的相似度。"
      />
      <SearchBox scopeId={scopeId} onResults={setResults} />
      {results ? <SearchResults response={results} onClose={() => setResults(null)} /> : null}
    </>
  );
}
