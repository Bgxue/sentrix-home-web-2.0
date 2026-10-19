import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    fetchKnowledge: vi.fn(),
    listEntityGroups: vi.fn(),
    listPeople: vi.fn(),
    listMemorySpaces: vi.fn(),
    getEntityGroup: vi.fn(),
    getEntity: vi.fn(),
    updateEntityProperty: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import type {
  EntityGroupDetailResponse,
  EntityGroupPayload,
  EntitySummaryPayload,
  ObservationPayload,
} from "../../shared/api/client";
import { ScopeProvider } from "../../app/ScopeProvider";
import { KnowledgePage } from "./KnowledgePage";

function member(name: string, id: string): EntitySummaryPayload {
  return {
    id,
    scope_id: "album1",
    entity_type: "object",
    canonical_name: name,
    status: "confirmed",
    family_role: null,
    summary: "",
    confidence: 0.9,
    identity_state: "stable",
    role_state: "stable",
    name_state: "stable",
    created_at: null,
    updated_at: null,
    cluster_count: 0,
    photo_count: 1,
    mention_count: 1,
    evidence_count: 1,
    relationship_count: 0,
    reviewable: false,
    single_sample: null,
    avatar_face_instance_id: null,
    preview_asset_id: null,
    preview_file_name: null,
    preview_media_type: null,
    private: null,
  };
}

function group(
  overrides: Partial<EntityGroupPayload> & { id: string; canonical_name: string },
): EntityGroupPayload {
  return {
    scope_id: "album1",
    entity_type: "object",
    members: [member(overrides.canonical_name, `entity_${overrides.id}`)],
    member_entity_ids: [`entity_${overrides.id}`],
    source_labels: [],
    semantic_details: ["由图片观察维护"],
    evidence_count: 1,
    confidence: 0.9,
    preview_asset_id: null,
    preview_file_name: null,
    preview_media_type: null,
    rationale: { strategy: "label", normalized_key: overrides.canonical_name },
    is_semantic_cluster: false,
    ...overrides,
  };
}

const observation: ObservationPayload = {
  id: "obs1",
  asset_id: "asset1",
  source_type: "image",
  captured_at: "2026-01-02T10:00:00+00:00",
  caption: "蓝色背包放在长椅上",
  asset: { id: "asset1", file_name: "bag.jpg", media_type: "image", path: "/tmp/bag.jpg" },
};

const objectGroup = group({ id: "g1", canonical_name: "背包" });
const atmosphereGroup = group({ id: "g2", canonical_name: "温馨", entity_type: "atmosphere" });

function groupDetail(groupPayload: EntityGroupPayload): EntityGroupDetailResponse {
  return { group: groupPayload, members: [], observations: [observation], events: [] };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("知识页", () => {
  it("渲染物品 / 氛围实体组，点开组能看到合并后的原始证据", async () => {
    vi.mocked(client.listMemorySpaces).mockResolvedValue([
      { id: "album1", name: "第一册", kind: "benchmark", status: "active" },
    ]);
    vi.mocked(client.fetchKnowledge).mockResolvedValue({ profiles: [], claims: [], spaces: [] });
    vi.mocked(client.listPeople).mockResolvedValue([]);
    vi.mocked(client.listEntityGroups).mockResolvedValue([objectGroup, atmosphereGroup]);
    vi.mocked(client.getEntityGroup).mockResolvedValue(groupDetail(objectGroup));

    render(
      <ScopeProvider>
        <KnowledgePage />
      </ScopeProvider>,
    );

    expect(await screen.findByText("背包")).toBeTruthy();
    expect(screen.getByText("温馨")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /背包/ }));

    await waitFor(() =>
      expect(client.getEntityGroup).toHaveBeenCalledWith("g1", ""),
    );
    expect(await screen.findByText("蓝色背包放在长椅上")).toBeTruthy();
  });

  it("地点聚合与行程候选都如实说明缺少后端字段 / 端点", async () => {
    vi.mocked(client.listMemorySpaces).mockResolvedValue([]);
    vi.mocked(client.fetchKnowledge).mockResolvedValue({ profiles: [], claims: [], spaces: [] });
    vi.mocked(client.listPeople).mockResolvedValue([]);
    vi.mocked(client.listEntityGroups).mockResolvedValue([]);

    render(
      <ScopeProvider>
        <KnowledgePage />
      </ScopeProvider>,
    );

    expect(await screen.findByText("地点聚合暂不可用")).toBeTruthy();
    expect(screen.getByText(/没有 trips 端点/)).toBeTruthy();
  });
});
