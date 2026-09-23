import { useEffect, useState } from "react";

import { AssistantPanel } from "./features/assistant/AssistantPanel";
import { AssetGrid } from "./features/library/AssetGrid";
import { PeopleView } from "./features/people/PeopleView";
import { SearchBox } from "./features/search/SearchBox";
import { SearchResults } from "./features/search/SearchResults";
import { Timeline } from "./features/timeline/Timeline";
import { listAssets, type AssetSummary, type SearchResponse } from "./shared/api/client";

const SCOPE_ID = "album1";

type View = "timeline" | "library" | "people";

export default function App() {
  const [view, setView] = useState<View>("timeline");
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
      <nav className="views">
        <button type="button" onClick={() => setView("timeline")} aria-pressed={view === "timeline"}>
          时间线
        </button>
        <button type="button" onClick={() => setView("library")} aria-pressed={view === "library"}>
          全部照片
        </button>
        <button type="button" onClick={() => setView("people")} aria-pressed={view === "people"}>
          人物
        </button>
      </nav>
      <AssistantPanel scopeId={SCOPE_ID} />
      <SearchBox scopeId={SCOPE_ID} onResults={setResults} />
      {error ? <p className="error">{error}</p> : null}
      {results ? (
        <SearchResults response={results} onClose={() => setResults(null)} />
      ) : view === "timeline" ? (
        <Timeline scopeId={SCOPE_ID} />
      ) : view === "library" ? (
        <AssetGrid assets={assets} />
      ) : (
        <PeopleView scopeId={SCOPE_ID} />
      )}
    </main>
  );
}
