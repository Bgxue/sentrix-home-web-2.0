import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    getEntity: vi.fn(),
    updateEntityProperty: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import type {
  EntityDetailFullResponse,
  EntityPropertyPayload,
  ObservationPayload,
} from "../../shared/api/client";
import { EntityDetailView } from "./EntityDetailView";

const observation: ObservationPayload = {
  id: "obs1",
  asset_id: "asset1",
  source_type: "image",
  captured_at: "2026-01-02T10:00:00+00:00",
  caption: "湖边长椅",
  asset: { id: "asset1", file_name: "lake.jpg", media_type: "image", path: "/tmp/lake.jpg" },
};

function property(key: string, value: unknown): EntityPropertyPayload {
  return {
    id: `prop_${key}`,
    entity_id: "e1",
    property_key: key,
    value,
    source: "user",
    confidence: 1,
    status: "active",
    evidence_ids: [],
    supersedes_property_id: null,
    revision: 1,
    created_at: null,
    updated_at: null,
  };
}

const detail: EntityDetailFullResponse = {
  entity: {
    id: "e1",
    scope_id: "album1",
    entity_type: "place",
    canonical_name: "西湖",
    status: "confirmed",
    family_role: null,
    summary: "常去的湖边",
    confidence: 0.9,
    identity_state: "stable",
    role_state: "stable",
    name_state: "stable",
    created_at: null,
    updated_at: null,
    avatar_face_instance_id: null,
    evidence_count: 1,
  },
  properties: [property("alias", "我们的老地方"), property("private_flag", false)],
  property_history: [property("alias", "我们的老地方"), property("private_flag", false)],
  clusters: [],
  facts: [],
  events: [],
  observations: [observation],
  appearance_evidence: [],
  evidence_files: [],
  relationships: [],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("实体详情", () => {
  it("地点实体通过 PUT /entities/{id}/properties/{key} 保存别名与私密标记", async () => {
    vi.mocked(client.getEntity).mockResolvedValue(detail);
    vi.mocked(client.updateEntityProperty).mockResolvedValue(property("alias", "新别名"));

    render(<EntityDetailView detail={detail} onClose={() => {}} />);

    expect(screen.getByText("西湖")).toBeTruthy();
    expect(screen.getByDisplayValue("我们的老地方")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("自定义别名"), { target: { value: "新别名" } });
    fireEvent.click(screen.getByLabelText("在普通页面和问答中隐藏精确地点"));
    fireEvent.click(screen.getByLabelText("原始证据 1"));
    fireEvent.click(screen.getByRole("button", { name: "保存地点属性" }));

    await waitFor(() => expect(client.updateEntityProperty).toHaveBeenCalledTimes(2));
    expect(client.updateEntityProperty).toHaveBeenCalledWith("e1", "alias", "新别名", ["obs1"]);
    expect(client.updateEntityProperty).toHaveBeenCalledWith("e1", "private_flag", true, ["obs1"]);
    await waitFor(() => expect(client.getEntity).toHaveBeenCalledWith("e1"));
  });
});
