import { useEffect, useState } from "react";

import { AssetGrid } from "./features/library/AssetGrid";
import { listAssets, type AssetSummary } from "./shared/api/client";

export default function App() {
  const [assets, setAssets] = useState<AssetSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listAssets("album1")
      .then(setAssets)
      .catch((cause: Error) => setError(cause.message));
  }, []);

  return (
    <main className="app">
      <h1>Sentrix Home 2.0</h1>
      {error ? <p className="error">{error}</p> : null}
      <AssetGrid assets={assets} />
    </main>
  );
}
