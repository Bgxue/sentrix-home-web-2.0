import { useState, type FormEvent } from "react";

import { searchAssets, type SearchResponse } from "../../shared/api/client";

/** 内容词检索入口。搜索是后端的事，这里只负责把词送过去、把结果交回去。 */
export function SearchBox({
  scopeId,
  onResults,
}: {
  scopeId: string;
  onResults: (response: SearchResponse) => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const query = text.trim();
    if (query === "") {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onResults(await searchAssets(query, scopeId));
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="search" onSubmit={submit}>
      <input
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder="搜内容词，例如 桥 / 海边 / 红色"
        aria-label="按内容词搜索照片"
      />
      <button type="submit" disabled={busy || text.trim() === ""}>
        {busy ? "搜索中…" : "搜索"}
      </button>
      {error ? <p className="error">{error}</p> : null}
    </form>
  );
}
