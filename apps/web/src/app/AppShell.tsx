/**
 * 壳层：240px 侧边栏 + 72px 顶栏 + 内容区。
 *
 * **逐块移植 1.0 `src/app.js:237-238` 的 `shell()` 模板串。** 三处行为直接来自那两行，
 * 不是我自己定的：
 *
 * 1. 面包屑里的「返回」**只在非概览页出现**（`:238` 的三元 `state.view !== "overview"`）；
 * 2. 当前项高亮走 `.nav-item.active`（1.0 `styles.css:44`：浅色字 + lime 底）；
 * 3. 后端不可用时底部脉冲文案是「本地服务不可用」（`:238` 的 `backendError` 三元）。
 */

import { useEffect, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router";

import { NAV_ITEMS, SYSTEM_ITEMS, activeNavItem } from "./nav";
import { useScope } from "./ScopeProvider";
import { CommandPalette } from "./CommandPalette";
import { HelpModal } from "./HelpModal";
import { qaDashboardUrl } from "./qa";
import { fetchHealth } from "../shared/api/client";
import { useToast } from "../shared/ui/Toast";

function NavButton({ icon, label, active, onClick }: { icon: string; label: string; active: boolean; onClick: () => void }) {
  return (
    <button type="button" className={active ? "nav-item active" : "nav-item"} onClick={onClick}>
      <span className="icon" aria-hidden="true">{icon}</span>
      <span>{label}</span>
    </button>
  );
}

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const { scopeId, spaces, setScopeId } = useScope();
  const { notify } = useToast();
  const [backendDown, setBackendDown] = useState(false);
  // 1.0 的模态是全局 `state.modal`；2.0 这里只有两个壳层动作，不需要通用模态栈。
  const [overlay, setOverlay] = useState<"command" | "help" | null>(null);

  /**
   * 切换相册。1.0 的 `select-space` 直接写 state——但写 `localStorage` 会抛
   * （Safari 隐私模式、存储配额满），而抛出来时范围已经变了、侧边栏却停在旧值。
   * 这里把失败接成 Toast，并且**不吞掉异常后继续**：切换失败就保持原范围。
   */
  function switchScope(next: string) {
    if (next !== "" && !spaces.some((space) => space.id === next)) {
      notify("相册切换失败：该相册已不在列表中", "error");
      return;
    }
    try {
      setScopeId(next);
    } catch (cause) {
      notify(`相册切换失败：${(cause as Error).message}`, "error");
    }
  }

  function openQaDashboard() {
    // 不探测服务是否就绪：2.0 API 没有 1.0 的 `/api/photobench/ensure`，
    // 先 fetch 一个不存在的端点只会把「打不开」伪装成「启动中」。
    window.open(qaDashboardUrl(window.location), "_blank", "noopener,noreferrer");
  }

  useEffect(() => {
    let cancelled = false;
    fetchHealth()
      .then(() => !cancelled && setBackendDown(false))
      .catch(() => !cancelled && setBackendDown(true));
    return () => {
      cancelled = true;
    };
  }, [location.pathname]);

  const current = activeNavItem(location.pathname);
  const activeSpace = spaces.find((space) => space.id === scopeId);
  const spaceLabel = activeSpace?.name ?? "全部相册";

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-mark">S</span>
          <div>
            <strong>Sentrix</strong>
            <small>Home Memory</small>
          </div>
        </div>

        <label className="space-switcher">
          <span className="avatar tiny">S</span>
          <span>
            <b>当前相册</b>
            <select
              aria-label="切换全部相册或独立相册"
              value={scopeId}
              onChange={(event) => switchScope(event.target.value)}
            >
              <option value="">全部相册</option>
              {spaces.map((space) => (
                <option key={space.id} value={space.id}>{space.name}</option>
              ))}
            </select>
            <small>{scopeId === "" ? "正在查看全部相册" : `正在查看 ${spaceLabel}`}</small>
          </span>
        </label>

        <div className="side-label">家庭记忆</div>
        <nav className="main-nav">
          {NAV_ITEMS.map((item) => (
            <NavButton
              key={item.id}
              icon={item.icon}
              label={item.label}
              active={current?.id === item.id}
              onClick={() => navigate(item.path)}
            />
          ))}
        </nav>

        <div className="side-label lower">空间与系统</div>
        <nav className="main-nav">
          {SYSTEM_ITEMS.map((item) => (
            <NavButton
              key={item.id}
              icon={item.icon}
              label={item.label}
              active={current?.id === item.id}
              onClick={() => navigate(item.path)}
            />
          ))}
          {/* 两个动作项照 1.0 `:238`：不是路由，所以不进 SYSTEM_ITEMS（那张表
              与 ROUTES 一一对应，路由门禁会拒绝没有页面的导航项）。 */}
          <NavButton
            icon="?"
            label="使用帮助"
            active={false}
            onClick={() => setOverlay("help")}
          />
          <NavButton
            icon="▤"
            label="QA 测评"
            active={false}
            onClick={openQaDashboard}
          />
        </nav>

        <div className="sidebar-footer">
          <div className="local-pulse">
            <i />
            <span>{backendDown ? "本地服务不可用" : "本地 AI 正常运行"}</span>
          </div>
          <small>Sentrix Home · 0.2.0</small>
        </div>
      </aside>

      <div className="main-content">
        <header className="topbar">
          <div className="breadcrumbs">
            {current !== undefined && current.id !== "overview" ? (
              <>
                <button type="button" className="crumb-back" onClick={() => navigate(-1)}>←返回</button>
                <b>/</b>
              </>
            ) : null}
            <Link to="/" style={{ color: "inherit" }}>Sentrix Home</Link>
            {current ? (
              <>
                <b>/</b>
                <strong>{current.label}</strong>
              </>
            ) : null}
          </div>
          <div className="top-actions">
            <button
              type="button"
              className="icon-button"
              aria-label="打开命令搜索"
              onClick={() => setOverlay("command")}
            >
              ⌘
            </button>
            <span className="top-user">
              <span className="avatar tiny">S</span>
              <span>{spaceLabel}</span>
            </span>
          </div>
        </header>
        <div className="view-root">
          <Outlet />
        </div>
      </div>

      {overlay === "command" ? (
        <CommandPalette onClose={() => setOverlay(null)} />
      ) : null}
      {overlay === "help" ? <HelpModal onClose={() => setOverlay(null)} /> : null}
    </main>
  );
}
