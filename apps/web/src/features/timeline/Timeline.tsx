import { useCallback, useEffect, useRef, useState } from "react";

import {
  assetPreviewUrl,
  assetFileUrl,
  fetchTimeline,
  type TimelineDay,
  type TimelinePhoto,
} from "../../shared/api/client";

const PAGE_SIZE = 20;
const HEIC_SUFFIXES = [".heic", ".heif"];

/** 浏览器直接渲染不了的格式走服务端转码预览，其余走原图（与库视图同一条判断）。 */
function photoUrl(photo: TimelinePhoto): string {
  const lowered = photo.file_name.toLowerCase();
  return HEIC_SUFFIXES.some((suffix) => lowered.endsWith(suffix))
    ? assetPreviewUrl(photo.asset_id)
    : assetFileUrl(photo.asset_id);
}

/** 相邻两页可能落在同一天，合并而不是让同一天出现两次。 */
function mergeDays(current: TimelineDay[], incoming: TimelineDay[]): TimelineDay[] {
  const merged = current.map((day) => ({ ...day, events: [...day.events] }));
  for (const day of incoming) {
    const last = merged[merged.length - 1];
    if (last && last.date === day.date) {
      last.events.push(...day.events);
    } else {
      merged.push({ ...day, events: [...day.events] });
    }
  }
  return merged;
}

export function Timeline({ scopeId }: { scopeId: string }) {
  const [place, setPlace] = useState("");
  const [loaded, setLoaded] = useState<{ key: string; days: TimelineDay[]; total: number } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<TimelinePhoto | null>(null);
  const sentinel = useRef<HTMLDivElement | null>(null);

  const key = `${scopeId}\u0000${place}`;
  // 「首屏还在加载」是**推导**出来的：当前这组筛选条件的页面还没到，就是加载中。
  // 用一个 state 去记它就得在 effect 里同步 setState，那会级联渲染
  // （oxlint 的 react(set-state-in-effect) 报的就是这件事，它给的解法正是
  // 「derive the value during render」）。
  const loading = loaded === null || loaded.key !== key;
  const days = loaded && loaded.key === key ? loaded.days : [];
  const total = loaded && loaded.key === key ? loaded.total : 0;
  const shown = days.reduce((sum, day) => sum + day.events.length, 0);

  // 首屏与筛选变化：整体替换。setState 只发生在 then/catch 回调里。
  useEffect(() => {
    let alive = true;
    fetchTimeline(scopeId, 0, PAGE_SIZE, place)
      .then((response) => {
        if (alive) {
          setLoaded({ key, days: response.days, total: response.total });
          setError(null);
        }
      })
      .catch((cause: Error) => {
        if (alive) {
          setError(cause.message);
        }
      });
    return () => {
      alive = false;
    };
  }, [scopeId, place, key]);

  // 无限滚动：只追加下一页。这个回调由 IntersectionObserver 触发，不是 effect 体。
  const loadMore = useCallback(async () => {
    if (loaded === null || loaded.key !== key) {
      return;
    }
    setLoadingMore(true);
    try {
      const response = await fetchTimeline(scopeId, shown, PAGE_SIZE, place);
      setLoaded({ key, days: mergeDays(loaded.days, response.days), total: response.total });
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setLoadingMore(false);
    }
  }, [key, loaded, place, scopeId, shown]);

  useEffect(() => {
    const node = sentinel.current;
    if (!node) {
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !loading && !loadingMore && shown < total) {
        void loadMore();
      }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [loadMore, loading, loadingMore, shown, total]);

  return (
    <section className="timeline">
      <div className="timeline-bar">
        <h2>时间线</h2>
        {/* 地点筛选走后端：前端只把用户输入原样传过去，不做任何地名计算。 */}
        <input
          aria-label="按地点筛选"
          placeholder="按地点筛选（后端匹配）"
          value={place}
          onChange={(event) => setPlace(event.target.value)}
        />
        <span className="timeline-count">
          {shown} / {total} 个事件
        </span>
      </div>
      {error ? <p className="error">{error}</p> : null}
      {days.length === 0 && !loading ? <p>还没有事件。先导入一批照片。</p> : null}
      {days.map((day) => (
        <div className="timeline-day" key={day.date}>
          <h3>{day.date}</h3>
          {day.events.map((event) => (
            <article className="timeline-event" key={event.id}>
              <header>
                <strong>{event.title}</strong>
                <span>{event.place}</span>
              </header>
              <p>{event.summary}</p>
              <div className="timeline-photos">
                {event.photos.map((photo) => (
                  <button type="button" key={photo.asset_id} onClick={() => setOpen(photo)}>
                    <img src={photoUrl(photo)} alt={photo.file_name} loading="lazy" />
                    {photo.place ? <small>{photo.place}</small> : null}
                  </button>
                ))}
              </div>
            </article>
          ))}
        </div>
      ))}
      <div ref={sentinel} className="timeline-sentinel">
        {loading || loadingMore ? "加载中…" : null}
      </div>
      {open ? (
        <div className="timeline-overlay" role="presentation" onClick={() => setOpen(null)}>
          <img src={photoUrl(open)} alt={open.file_name} />
          <p>
            {open.file_name} · {open.place || "（未解析出地点）"}
          </p>
        </div>
      ) : null}
    </section>
  );
}
