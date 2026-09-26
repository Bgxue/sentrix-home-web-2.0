/** 资料库。对应 1.0 `src/app.js:683` 的 `libraryView()`（pageHeader 在 `:687`）。 */

import { useEffect, useState } from "react";

import { useScope } from "../../app/ScopeProvider";
import { EmptyState } from "../../shared/ui/EmptyState";
import { PageHeader } from "../../shared/ui/PageHeader";
import { listAssets, type AssetSummary } from "../../shared/api/client";
import { AssetGrid } from "./AssetGrid";

export function LibraryPage() {
  const { scopeId } = useScope();
  const [assets, setAssets] = useState<AssetSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listAssets(scopeId)
      .then((items) => {
        if (cancelled) return;
        setAssets(items);
        setError(null);
      })
      .catch((cause: Error) => !cancelled && setError(cause.message));
    return () => {
      cancelled = true;
    };
  }, [scopeId]);

  return (
    <>
      <PageHeader
        kicker="家庭资料 / 全部内容"
        title="所有资料，都有回到记忆的路径。"
        description="这里展示真实导入的原始 Asset。点击任意条目可查看文件与处理状态。"
      />
      {error ? <p className="error">{error}</p> : null}
      {assets.length === 0 && error === null ? (
        <EmptyState
          symbol="▦"
          title="还没有资料"
          description="导入一份原始文件后，它会出现在这里。"
        />
      ) : (
        <AssetGrid assets={assets} />
      )}
    </>
  );
}
