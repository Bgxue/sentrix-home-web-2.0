/**
 * 布局单测。布局是纯函数，这些断言直接钉住计划书 §5 要的几条：
 * 分层正确性、**待归类必须出现在结果里**、空图不炸、一对人多条边只出一条。
 *
 * 假件按契约的完整字段造，不省字段——省字段的假件比真实契约宽松，前端漏读一个字段
 * 它也能过，那种红灯要留到真后端上才亮（`PeopleView.test.tsx` 的同一条理由）。
 */

import { describe, expect, it } from "vitest";

import type { FamilyPerson, FamilyRelationship } from "../../../shared/api/client";
import { dedupeRelationships, familyLevels, graphLayout, layoutBounds } from "./graphLayout";

const STUB = {
  scope_id: "album1",
  avatar_face_instance_id: null,
  representative_media: [] as FamilyPerson["representative_media"],
};

function person(id: string, overrides: Partial<FamilyPerson> = {}): FamilyPerson {
  return {
    ...STUB,
    id,
    display_name: id,
    membership: null,
    portrait: null,
    appearance_count: 1,
    ...overrides,
  };
}

function member(id: string, membership: "family" | "friend" | "unknown"): FamilyPerson {
  return person(id, {
    membership: {
      id: `m_${id}`,
      scope_id: "album1",
      entity_id: id,
      membership,
      source: "model",
      confidence: 0.5,
      evidence_refs: [],
      inference_run_id: null,
      state: "active",
      locked: false,
      revision: 1,
      supersedes_id: null,
      created_at: "2026-10-02T00:00:00+00:00",
      updated_at: "2026-10-02T00:00:00+00:00",
    },
  });
}

function relation(
  subject: string,
  predicate: string,
  inverse: string,
  object: string,
): FamilyRelationship {
  return {
    id: `r_${subject}_${object}`,
    scope_id: "album1",
    family_graph_id: null,
    subject_entity_id: subject,
    predicate,
    object_entity_id: object,
    inverse_predicate: inverse,
    source: "model",
    confidence: 0.5,
    evidence_refs: [],
    state: "active",
    locked: false,
    revision: 1,
  };
}

describe("dedupeRelationships", () => {
  it("一对人多条边只出一条，优先级小者胜", () => {
    const edges = dedupeRelationships([
      relation("son", "儿子", "父亲", "dad"),
      relation("dad", "朋友", "朋友", "son"),
      relation("dad", "父亲", "儿子", "son"),
    ]);
    expect(edges).toHaveLength(1);
    // 父亲(1) 胜过 朋友(8)；方向保留 subject=长辈
    expect(edges[0].predicate).toBe("父亲");
    expect(edges[0].subject_entity_id).toBe("dad");
    expect(edges[0].object_entity_id).toBe("son");
  });

  it("同优先级按谓词字典序决胜——不靠插入顺序", () => {
    const forward = dedupeRelationships([
      relation("a", "母亲", "儿子", "b"),
      relation("a", "父亲", "儿子", "b"),
    ]);
    const backward = dedupeRelationships([
      relation("a", "父亲", "儿子", "b"),
      relation("a", "母亲", "儿子", "b"),
    ]);
    // 按 UTF-16 码位比较：母(U+6BCD) < 父(U+7236)，所以「母亲」胜出，
    // 与两条边的插入顺序无关。
    expect(forward[0].predicate).toBe("母亲");
    expect(backward[0].predicate).toBe("母亲");
  });
});

describe("familyLevels", () => {
  it("辈分传播：object 比 subject 低一层，方向不翻转", () => {
    const people = [member("dad", "family"), member("son", "family")];
    const levels = familyLevels(people, [relation("dad", "父亲", "儿子", "son")]);
    expect(levels.get("dad")).toBe(0);
    expect(levels.get("son")).toBe(1);
  });

  it("只有 1.0 那六个长辈谓词参与传播，叔伯不在内", () => {
    const people = [member("uncle", "family"), member("nephew", "family")];
    const levels = familyLevels(people, [relation("uncle", "叔叔", "侄子", "nephew")]);
    // 叔叔不在 parentRelations 里，两个人都留在第 0 层——这是 1.0 的原样行为
    expect(levels.get("uncle")).toBe(0);
    expect(levels.get("nephew")).toBe(0);
  });
});

describe("graphLayout", () => {
  it("父亲画在儿子上一层（y 更小）", () => {
    const people = [member("dad", "family"), member("son", "family")];
    const layout = graphLayout(people, [relation("dad", "父亲", "儿子", "son")], 900, 520);
    const dad = layout.get("dad");
    const son = layout.get("son");
    expect(dad).toBeDefined();
    expect(son).toBeDefined();
    expect(dad!.y).toBeLessThan(son!.y);
    // 相邻两层：中间没有别人，差正好是一个行距
    expect(son!.y - dad!.y).toBeGreaterThan(0);
  });

  it("待归类（unknown 与 null）必须出现在布局结果里，且排在家人的下面", () => {
    const people = [
      member("dad", "family"),
      member("son", "family"),
      member("friend", "friend"),
      member("unknown_row", "unknown"),
      person("never_inferred"), // membership === null：与 unknown 同一件事
    ];
    const layout = graphLayout(people, [relation("dad", "父亲", "儿子", "son")], 900, 520);

    expect(layout.size).toBe(5);
    const familyY = layout.get("son")!.y;
    // friend 与两个 unknown 都在家人层之下
    expect(layout.get("friend")!.y).toBeGreaterThan(familyY);
    expect(layout.get("unknown_row")!.y).toBeGreaterThan(layout.get("friend")!.y);
    expect(layout.get("never_inferred")!.y).toBeGreaterThan(layout.get("friend")!.y);
  });

  it("空图不炸：没有人生成空 Map，包围盒为 null", () => {
    const layout = graphLayout([], [], 900, 520);
    expect(layout.size).toBe(0);
    expect(layoutBounds(layout)).toBeNull();
  });

  it("人多了不重叠：同一行的相邻节点中心距不小于最小列距", () => {
    const people = Array.from({ length: 12 }, (_, index) => member(`p${index}`, "family"));
    const layout = graphLayout(people, [], 900, 520);
    const xs = [...layout.values()].map((point) => point.x).sort((a, b) => a - b);
    for (let index = 1; index < xs.length; index += 1) {
      expect(xs[index] - xs[index - 1]).toBeGreaterThanOrEqual(124);
    }
  });
});
