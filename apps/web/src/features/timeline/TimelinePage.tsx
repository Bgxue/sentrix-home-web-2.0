/**
 * 事件时间线。对应 1.0 `src/app.js:626` 的 `timelineView()`（pageHeader 在 `:645`）。
 *
 * 右侧 `side-inspector` 是 1.0 的「视频记忆」说明块。2.0 没有视频链路（`videos` /
 * `video-scenes` 端点都不存在），所以**这里是空状态而不是照抄那段文案**——照抄会让
 * 人以为场景图片堆是可用的。
 */

import { useSearchParams } from "react-router";

import { useScope } from "../../app/ScopeProvider";
import { PageHeader } from "../../shared/ui/PageHeader";
import { Timeline } from "./Timeline";
import "../../styles/timeline.css";

export function TimelinePage() {
  const { scopeId, spaces } = useScope();
  const scopeName = spaces.find((space) => space.id === scopeId)?.name ?? "全部相册";
  const [searchParams, setSearchParams] = useSearchParams();
  const focusedEventId = searchParams.get("event");

  /**
   * `?event=` 是详情的唯一状态：概览页跳过来、刷新、浏览器后退都能还原。
   *
   * **以 `window.location.search` 为准重建参数**——与 PeoplePage 的 `?person=` 同一条
   * 理由：ScopeProvider 切相册时用 `history.replaceState` 直接写 URL，react-router
   * 收不到那次变更，在 router 的旧参数上增删会把刚切的相册覆盖掉。
   */
  function openEvent(eventId: string | null) {
    const next = new URLSearchParams(window.location.search);
    if (eventId === null) next.delete("event");
    else next.set("event", eventId);
    setSearchParams(next);
  }

  return (
    <>
      <PageHeader
        kicker="记忆组织 / 事件"
        title="家里的时间线，不只是文件列表。"
        description={`当前范围：${scopeName}。照片和视频片段按真实拍摄时间统一整理。`}
      />
      <section className="timeline-layout">
        <div className="timeline-main">
          <Timeline
            scopeId={scopeId}
            focusEventId={focusedEventId}
            onOpenEvent={openEvent}
          />
        </div>
        <aside className="side-inspector">
          <p className="section-kicker">视频记忆</p>
          <h2>一段视频，也能成为清晰的家庭回忆</h2>
          <p>系统会选出有代表性的画面，按场景整理，并继承拍摄时间与地点。</p>
          <div className="inspector-note">
            场景图片堆 <strong>尚未开放</strong>
            <small>视频链路在 2.0 还没做——videos 与 video-scenes 端点都不存在。这里的结构留着，链路接上就能填。</small>
          </div>
        </aside>
      </section>
    </>
  );
}
