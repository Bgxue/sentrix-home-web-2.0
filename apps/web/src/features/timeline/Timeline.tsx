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
  const [days, setDays] = useState<TimelineDay[]>([]);
  const [total, setTotal] = useState(0);
  const [place, setPlace] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<TimelinePhoto | null>(null);
  const sentinel = useRef<HTMLDivElement | null>(null);
  const loaded = days.reduce((sum, day) => sum + day.events.length, 0);

  const load = useCallback(
    async (offset: number, replace: boolean) => {
      setLoading(true);
      try {
        const page = await fetchTimeline(scopeId, offset, PAGE_SIZE, place);
        setTotal(page.total);
        setDays((current) => (replace ? page.days : mergeDays(current, page.days)));
        setError(null);
      } catch (cause) {
        setError((cause as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [scopeId, place],
  );

  useEffect(() => {
    void load(0, true);
  }, [load]);

  useEffect(() => {
    const node = sentinel.current;
    if (!node) {
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !loading && loaded < total) {
        void load(loaded, false);
      }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [load, loading, loaded, total]);

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
          {loaded} / {total} 个事件
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
        {loading ? "加载中…" : null}
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
