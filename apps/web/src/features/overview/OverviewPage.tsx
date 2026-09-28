/**
 * 家庭概览。对应 1.0 `src/app.js:242` 的 `overview()`（pageHeader 在 `:245`）。
 *
 * 版式照 1.0：范围内卡片 → **深色提问区** → 统计卡 → 最近事件。
 *
 * 提问框的行为也是照 1.0 的：那里的 `searchBar()` 是**提交进助手对话**，不是跳到
 * 检索页。2.0 用 `?q=` 交接——概览把问题塞进 URL，助手页取出后自动问一次。
 *
 * **1.0 的四张卡只搬得来三张。** 第四张是「待确认事实」，而 2.0 没有 `facts` /
 * `dashboard` 端点。留一张恒为 0 的卡比不显示更糟：它看起来像条通的链路。
 */

import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";

import { useScope } from "../../app/ScopeProvider";
import { PageHeader } from "../../shared/ui/PageHeader";
import { Section } from "../../shared/ui/Section";
import { StatCard } from "../../shared/ui/StatCard";
import {
  fetchHealth,
  fetchTimeline,
  listAssets,
  listPeople,
  type TimelineDay,
} from "../../shared/api/client";
import "../../styles/overview.css";

export function OverviewPage() {
  const navigate = useNavigate();
  const { scopeId, spaces } = useScope();
  const [assets, setAssets] = useState<number | null>(null);
  const [days, setDays] = useState<readonly TimelineDay[]>([]);
  const [people, setPeople] = useState<number | null>(null);
  const [aiReady, setAiReady] = useState<boolean | null>(null);
  const [question, setQuestion] = useState("");

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([
      listAssets(scopeId),
      fetchTimeline(scopeId, 0, 5),
      fetchHealth(),
      listPeople(scopeId),
    ]).then(([assetResult, timelineResult, healthResult, peopleResult]) => {
      if (cancelled) return;
      setAssets(assetResult.status === "fulfilled" ? assetResult.value.length : null);
      setDays(timelineResult.status === "fulfilled" ? timelineResult.value.days : []);
      setAiReady(healthResult.status === "fulfilled" && healthResult.value.status === "ok");
      setPeople(peopleResult.status === "fulfilled" ? peopleResult.value.length : null);
    });
    return () => {
      cancelled = true;
    };
  }, [scopeId]);

  function ask(event: FormEvent) {
    event.preventDefault();
    const trimmed = question.trim();
    if (trimmed === "") return;
    // 交接到助手页。用 query 而不是全局状态：刷新后问题还在，也能把链接发给别人。
    navigate(`/assistant?q=${encodeURIComponent(trimmed)}`);
  }

  const eventCount = days.reduce((sum, day) => sum + day.events.length, 0);
  const scopeName = spaces.find((space) => space.id === scopeId)?.name ?? "全部相册";
  const show = (value: number | null) => (value === null ? "—" : value);

  return (
    <>
      <PageHeader
        kicker="家庭记忆 / 真实数据"
        title="把家里的记忆，重新放在一起。"
        description="这里展示已经导入并完成处理的本地资料。没有资料时，Sentrix 不会用示例内容填充。"
        action={
          <button type="button" className="button primary" onClick={() => navigate("/imports")}>
            导入资料
          </button>
        }
      />

      <div className="album-context">
        <span className="section-kicker">当前数据范围</span>
        <strong>{scopeName}</strong>
        <small>{scopeId === "" ? "当前正在查看全部相册" : "当前正在查看单个相册"}</small>
      </div>

      <section className="overview-search">
        <div>
          <p className="section-kicker">问 Sentrix</p>
          <h2>你想找回哪一段记忆？</h2>
          <p>从人物、时间、地点、物体或一句描述开始，答案会带回原始证据。</p>
        </div>
        <form className="search-bar" onSubmit={ask}>
          <span className="search-symbol" aria-hidden="true">⌕</span>
          <input
            aria-label="问 Sentrix"
            placeholder="例如：去年在海边拍的照片"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
          />
          <button type="submit" aria-label="提问">→</button>
        </form>
      </section>

      <section className="stats-grid">
        <StatCard label="已整理内容" value={show(assets)} note="本地 Asset" />
        <StatCard label="已形成事件" value={show(eventCount)} note="可回到 Observation" />
        <StatCard label="已确认人物" value={show(people)} note="来自 /people" />
        <StatCard
          label="本地 AI 状态"
          value={aiReady === null ? "—" : aiReady ? "正常" : "未知"}
          note={scopeName}
          accent
        />
      </section>

      <Section
        kicker="最近事件"
        title="家里的时间线"
        action={
          <button type="button" className="text-button" onClick={() => navigate("/timeline")}>
            查看全部 →
          </button>
        }
      >
        {days.length === 0 ? (
          <div className="empty-queue">
            <span aria-hidden="true">↕</span>
            <strong>还没有事件</strong>
            <p>导入图片后，处理完成的 Observation 会在这里形成事件。</p>
          </div>
        ) : (
          <div className="event-list">
            {days.flatMap((day) =>
              day.events.slice(0, 2).map((event) => (
                <button
                  key={event.id}
                  type="button"
                  className="event-row"
                  onClick={() => navigate("/timeline")}
                >
                  <strong>{event.title}</strong>
                  <small>{day.date} · {event.place || "未解析出地点"}</small>
                  <span>{event.photos.length} 张照片 · 可回到原始证据</span>
                </button>
              )),
            )}
          </div>
        )}
      </Section>
    </>
  );
}
