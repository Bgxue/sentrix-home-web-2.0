/**
 * `/people` 冒烟——钉住计划书 §0 的核心结论。
 *
 * 实测那份真实数据是 **8 个人、0 条关系、membership 全 unknown**。1.0 的图谱只收
 * family/friend，在这个形状下会画出一张空图，还把待归类的人藏进另一个 tab。
 * 所以这里的断言不是「能渲染」，而是**「8 个人必须出现 8 张卡，而不是空状态」**。
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    getFamilyGraph: vi.fn(),
    listMemorySpaces: vi.fn(),
    listFaceClusters: vi.fn(),
    listPeople: vi.fn(),
    confirmFaceCluster: vi.fn(),
    rejectFaceCluster: vi.fn(),
    mergeFaceClusters: vi.fn(),
    splitFaceCluster: vi.fn(),
    renamePerson: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import type { FamilyGraph, FamilyPerson, FamilyRelationship } from "../../shared/api/client";
import { ScopeProvider } from "../../app/ScopeProvider";
import { PeoplePage } from "./PeoplePage";

/** 一份「8 个人、0 条关系、全 unknown」的真实形状。 */
function realShapeGraph(): FamilyGraph {
  const people: FamilyPerson[] = Array.from({ length: 8 }, (_, index) => ({
    id: `person_${index}`,
    scope_id: "album1",
    display_name: `人物${index}`,
    // 实测里 membership 是行对象但 membership 字段为 "unknown"；null 的那条分支由
    // 布局单测覆盖（graphLayout.test.ts）。
    membership: {
      id: `m_${index}`,
      scope_id: "album1",
      entity_id: `person_${index}`,
      membership: "unknown",
      source: "model",
      confidence: 0,
      evidence_refs: [],
      inference_run_id: "run1",
      state: "active",
      locked: false,
      revision: 1,
      supersedes_id: null,
      created_at: "2026-10-02T00:00:00+00:00",
      updated_at: "2026-10-02T00:00:00+00:00",
    },
    portrait: null,
    avatar_face_instance_id: null,
    appearance_count: 1,
    representative_media: [],
  }));
  return { scope_id: "album1", run: null, people, scope_ids: ["album1"], relationships: [] };
}

/** 一条库里的关系行。**注意：一次用户操作会在库里留下两行**（见下面那条用例）。 */
function relationshipLine(
  subject: string,
  predicate: string,
  object: string,
  inverse: string,
): FamilyRelationship {
  return {
    id: `rel_${subject}_${object}`,
    scope_id: "album1",
    family_graph_id: null,
    subject_entity_id: subject,
    predicate,
    object_entity_id: object,
    inverse_predicate: inverse,
    source: "user_override",
    confidence: 1,
    evidence_refs: [],
    state: "effective",
    locked: true,
    revision: 1,
  };
}

function mount(graph: FamilyGraph = realShapeGraph()) {
  vi.mocked(client.listMemorySpaces).mockResolvedValue([
    { id: "album1", name: "第一册", kind: "benchmark", status: "active" },
  ]);
  vi.mocked(client.getFamilyGraph).mockResolvedValue(graph);
  vi.mocked(client.listFaceClusters).mockResolvedValue([]);
  vi.mocked(client.listPeople).mockResolvedValue([]);
  // ScopeProvider 优先读 `?scope=`；设上它，取数才是 album1（而不是「全部相册」）。
  window.history.replaceState(null, "", "/?scope=album1");
  return render(
    <MemoryRouter>
      <ScopeProvider>
        <PeoplePage />
      </ScopeProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/");
});

describe("/people 名人录（F7 §0 真实形状）", () => {
  it("8 个人 0 条关系时渲染 8 张人物卡，不是空状态", async () => {
    const { container } = mount();

    await waitFor(() => {
      expect(container.querySelectorAll("[data-person-id]")).toHaveLength(8);
    });
    // 「有没有内容」由人数决定，关系条数为 0 不算空——这正是 1.0 画空图的那个坑。
    expect(screen.queryByText("还没有可显示的人物")).toBeNull();
    expect(screen.getByText("8 人 · 0 条关系")).toBeTruthy();
    // 默认视图是名录（计划书裁决②），图谱 tab 此时不该被卸载掉门槛
    expect(screen.getByRole("tab", { name: "名录" }).getAttribute("aria-selected")).toBe("true");
  });

  it("点一张卡走 ?person= 打开详情侧栏（不加路由）", async () => {
    const { container } = mount();
    await waitFor(() => {
      expect(container.querySelectorAll("[data-person-id]")).toHaveLength(8);
    });

    fireEvent.click(container.querySelectorAll("[data-person-id]")[0]);

    // 详情是一个 aside，不再多一条路由；组件的取数/渲染都走查询参数
    expect(await screen.findByRole("complementary")).toBeTruthy();
    expect(container.querySelectorAll(".person-detail")).toHaveLength(1);
  });

  it("一次建关系在库里是两行，计数只算一条", async () => {
    // 实测确认：`PUT /family-graph/relationships` 会写**正向 + 反向两行**
    // （父亲→儿子 与 儿子→父亲，state 都是 effective），GET 原样返回。
    // 所以直接数 `relationships.length` 会把用户建的 1 条关系显示成「2 条关系」。
    // 布局与详情面板都走了 `dedupeRelationships`，页头那个计数也得对齐。
    mount({
      ...realShapeGraph(),
      relationships: [
        relationshipLine("person_0", "父亲", "person_1", "儿子"),
        relationshipLine("person_1", "儿子", "person_0", "父亲"),
      ],
    });

    await waitFor(() => {
      // 关键在「1」而不是「2」——两行是同一段关系的两个方向。
      expect(screen.getByText("8 人 · 1 条关系")).toBeTruthy();
    });
  });

});
