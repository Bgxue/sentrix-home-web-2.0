/**
 * 家庭概览。对应 1.0 `src/app.js:242`（`overview()`，pageHeader 在 `:245`）。
 *
 * **只放后端真能给的东西。** 1.0 这里有四张卡（含「待确认事实」）与三张记忆卡
 * （含视频场景记忆），2.0 的 `facts` / `dashboard` / 视频端点都还没有——留个恒为 0
 * 的卡会让人以为那条链路是通的。空状态本身就是 S6–S11 的可见进度表。
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { EmptyState } from "../../shared/ui/EmptyState";
import { PageHeader } from "../../shared/ui/PageHeader";
import { Section } from "../../shared/ui/Section";
import { StatCard } from "../../shared/ui/StatCard";
import { useScope } from "../../app/ScopeProvider";
import { fetchHealth, fetchTimeline, listAssets } from "../../shared/api/client";

export function OverviewPage() {
  const navigate = useNavigate();
  const { scopeId } = useScope();
  const [assets, setAssets] = useState<number | null>(null);
  const [events, setEvents] = useState<number | null>(null);
  const [aiReady, setAiReady] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([listAssets(scopeId), fetchTimeline(scopeId), fetchHealth()]).then(
      ([assetResult, timelineResult, healthResult]) => {
        if (cancelled) return;
        setAssets(assetResult.status === "fulfilled" ? assetResult.value.length : null);
        setEvents(
          timelineResult.status === "fulfilled"
            ? timelineResult.value.days.reduce((sum, day) => sum + day.events.length, 0)
            : null,
        );
        setAiReady(healthResult.status === "fulfilled" && healthResult.value.status === "ok");
      },
    );
    return () => {
      cancelled = true;
    };
  }, [scopeId]);

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

      <section className="stats-grid">
        <StatCard label="已整理内容" value={show(assets)} note="本地 Asset" />
        <StatCard label="已形成事件" value={show(events)} note="可回到 Observation" />
        <StatCard
          label="本地 AI 状态"
          value={aiReady === null ? "—" : aiReady ? "正常" : "未知"}
          note={scopeId === "" ? "全部相册" : scopeId}
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
        <EmptyState
          symbol="↕"
          title="到时间线里看"
          description="事件按拍摄时间分组，每条都能回到原始证据。"
          action={
            <button type="button" className="button small primary" onClick={() => navigate("/timeline")}>
              打开时间线
            </button>
          }
        />
      </Section>
    </>
  );
}
