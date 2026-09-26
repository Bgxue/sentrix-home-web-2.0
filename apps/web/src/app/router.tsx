/**
 * 路由表。
 *
 * **十条路由与 `nav.ts` 的导航项一一对应**——这个对应关系由 `router.test.tsx` 双向钉住：
 * 每个导航项都要有路由，每条路由都要指向真实组件，且每个 `*Page.tsx` 都要被引用。
 * 缺了第三条，「页面存在」与「路由挂载」可以各自为真而互不相干。
 */

import type { ComponentType } from "react";

import { AssistantPage } from "../features/assistant/AssistantPage";
import { ImportsPage } from "../features/imports/ImportsPage";
import { KnowledgePage } from "../features/knowledge/KnowledgePage";
import { LibraryPage } from "../features/library/LibraryPage";
import { OverviewPage } from "../features/overview/OverviewPage";
import { PeoplePage } from "../features/people/PeoplePage";
import { SearchPage } from "../features/search/SearchPage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { StoriesPage } from "../features/stories/StoriesPage";
import { TimelinePage } from "../features/timeline/TimelinePage";
import { AppShell } from "./AppShell";

/** 路径 → 页面组件。`nav.ts` 里的每一项都必须在这里有对应的一条。 */
export const ROUTES: Readonly<Record<string, ComponentType>> = {
  "/": OverviewPage,
  "/assistant": AssistantPage,
  "/timeline": TimelinePage,
  "/people": PeoplePage,
  "/knowledge": KnowledgePage,
  "/library": LibraryPage,
  "/stories": StoriesPage,
  "/imports": ImportsPage,
  "/settings": SettingsPage,
  "/search": SearchPage,
};

export const SHELL = AppShell;
