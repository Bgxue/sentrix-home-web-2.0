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
  // 刷新：把 nonce 并进 key，首屏那个 effect 的依赖跟着变，自然重跑。
  // 比另写一条 reload 路径好——两条路径迟早会漂开。
  const [nonce, setNonce] = useState(0);
  const sentinel = useRef<HTMLDivElement | null>(null);

  const key = `${scopeId}\u0000${place}\u0000${nonce}`;
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
      <div className="filter-row">
        {/* 地点筛选走后端：前端只把用户输入原样传过去，不做任何地名计算。 */}
        <input
          className="filter-chip"
          aria-label="按地点筛选"
          placeholder="按地点筛选（后端匹配）"
          value={place}
          onChange={(event) => setPlace(event.target.value)}
        />
        {place ? (
          <button type="button" className="text-button" onClick={() => setPlace("")}>
            清除筛选
          </button>
        ) : null}
        <span className="filter-spacer" />
        <span className="timeline-count">{shown} / {total} 个事件</span>
        <button
          type="button"
          className="icon-button bordered"
          aria-label="刷新时间线"
          onClick={() => setNonce((value) => value + 1)}
        >
          ↻
        </button>
      </div>
      {/* 1.0 的筛选行还有「有人物 / 有地点」两片，那是它在**一次全量拉回的列表**上做
          客户端筛选。2.0 的 /timeline 是分页的，照搬会变成「只在当前这一页里筛」——
          结果看起来对、实际漏掉后面所有页，而且不报错。后端目前只有 place 一个
          筛选参数，所以那两片等后端出了布尔参数再加。 */}
      <p className="timeline-filter-note">
        按人物或有无地点筛选需要后端加参数，当前只支持按地点文字匹配。
      </p>
      {error ? <p className="error">{error}</p> : null}
      {days.length === 0 && !loading ? (
        <div className="empty-queue">
          <span aria-hidden="true">↕</span>
          <strong>{place ? "这个地点还没有事件" : "还没有事件"}</strong>
          <p>{place ? "换一个地点词，或清除筛选查看全部。" : "导入并处理资料后，记忆会自动出现在这里。"}</p>
        </div>
      ) : null}
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
