/**
 * 侧边栏导航。
 *
 * **逐字段移植 1.0 `src/app.js:78-87` 的 `navItems`**——含 `icon` 那一个字符：
 * 1.0 用的就是文本字符（`⌂ ⌕ ↕ ◎ ◇ ▦ ▶ ↓`），不是图标字体，照搬即可。
 *
 * 与 1.0 的一处差异：1.0 的 id 就是视图名（`overview` / `search` / `timeline` …），
 * 2.0 走 URL 路由，所以多一个 `path` 字段，id 保留原样用于「当前项」判定与测试断言。
 */

export interface NavItem {
  /** 1.0 的视图名，原样保留。 */
  readonly id: string;
  /** URL 路径。 */
  readonly path: string;
  /** 1.0 原样的字符图标。 */
  readonly icon: string;
  readonly label: string;
}

/** 「家庭记忆」一组。1.0 `src/app.js:79-86` 八项，另加 2.0 的 `/search`（见下）。 */
export const NAV_ITEMS: readonly NavItem[] = [
  { id: "overview", path: "/", icon: "⌂", label: "家庭概览" },
  { id: "search", path: "/assistant", icon: "⌕", label: "家庭记忆助手" },
  { id: "timeline", path: "/timeline", icon: "↕", label: "事件时间线" },
  { id: "people", path: "/people", icon: "◎", label: "人物与关系" },
  { id: "knowledge", path: "/knowledge", icon: "◇", label: "实体与知识" },
  { id: "library", path: "/library", icon: "▦", label: "资料库" },
  { id: "stories", path: "/stories", icon: "▶", label: "故事工作室" },
  { id: "imports", path: "/imports", icon: "↓", label: "导入队列" },
  // 2.0 新增：1.0 没有独立的检索页（提问框直接提交进助手对话），而 2.0 的
  // `GET /api/v1/search` 是直检索（视觉 + 词法 + 元数据），与助手的 agent 链路
  // 是两种能力。放在这一组的末位，与 1.0 的八项区分开。
  { id: "retrieval", path: "/search", icon: "⌕", label: "资料检索" },
];

/** 「空间与系统」一组。1.0 `src/app.js:238` 有三项，其中两项是动作不是页面。 */
export const SYSTEM_ITEMS: readonly NavItem[] = [
  { id: "settings", path: "/settings", icon: "◌", label: "设备与隐私" },
];

/** 当前路径对应的导航项；找不到时返回 undefined（例如详情页）。 */
export function activeNavItem(pathname: string): NavItem | undefined {
  const all = [...NAV_ITEMS, ...SYSTEM_ITEMS];
  // 精确匹配优先：`/` 是 `/` 的前缀，先比精确才不会让概览抢走每一页。
  return all.find((item) => item.path === pathname) ?? all.find((item) => item.path !== "/" && pathname.startsWith(`${item.path}/`));
}
