import { useEffect, useState } from "react";

import { AssistantPanel } from "./features/assistant/AssistantPanel";
import { AssetGrid } from "./features/library/AssetGrid";
import { SearchBox } from "./features/search/SearchBox";
import { SearchResults } from "./features/search/SearchResults";
import { listAssets, type AssetSummary, type SearchResponse } from "./shared/api/client";

const SCOPE_ID = "album1";

export default function App() {
  const [assets, setAssets] = useState<AssetSummary[]>([]);
  const [results, setResults] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listAssets(SCOPE_ID)
      .then(setAssets)
      .catch((cause: Error) => setError(cause.message));
  }, []);

  return (
    <main className="app">
      <h1>Sentrix Home 2.0</h1>
      <AssistantPanel scopeId={SCOPE_ID} />
      <SearchBox scopeId={SCOPE_ID} onResults={setResults} />
      {error ? <p className="error">{error}</p> : null}
      {results ? (
        <SearchResults response={results} onClose={() => setResults(null)} />
      ) : (
        <AssetGrid assets={assets} />
      )}
    </main>
  );
}
