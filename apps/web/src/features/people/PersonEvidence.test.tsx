import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    getPerson: vi.fn(),
    getEntityDetail: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import type { FaceCropRef, FaceClusterSummary } from "../../shared/api/client";
import { PersonEvidence } from "./PersonEvidence";

function crop(faceInstanceId: string, assetId: string): FaceCropRef {
  return {
    face_instance_id: faceInstanceId,
    asset_id: assetId,
    observation_id: `obs_${assetId}`,
    file_name: `${assetId}.jpg`,
    media_type: "image",
    bbox: [0, 0, 10, 10],
    detection_confidence: 0.9,
    quality: 0.8,
    crop_url: `/api/v1/face-instances/${faceInstanceId}/crop`,
  };
}

function cluster(id: string, samples: FaceCropRef[]): FaceClusterSummary {
  return {
    id,
    scope_id: "album1",
    status: "confirmed",
    entity_id: "person_1",
    canonical_name: "妈妈",
    family_role: "母亲",
    entity_status: "confirmed",
    member_count: samples.length,
    confidence: 0.9,
    revision: 1,
    created_at: null,
    updated_at: null,
    reviewable: true,
    single_sample: false,
    samples,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("人物证据页", () => {
  it("渲染人脸裁剪、原图链接与实体详情里的关联事件", async () => {
    vi.mocked(client.getPerson).mockResolvedValue({
      entity: {
        id: "person_1",
        scope_id: "album1",
        canonical_name: "妈妈",
        status: "confirmed",
        family_role: "母亲",
        summary: "",
        confidence: 1,
        confirmed: true,
        aliases: [],
      },
      avatar_face_instance_id: "face_1",
      clusters: [
        cluster("cluster_a", [crop("face_1", "asset_1"), crop("face_2", "asset_2")]),
        // 同一张脸可能在多个簇里出现；证据页按 face_instance_id 去重。
        cluster("cluster_b", [crop("face_1", "asset_1")]),
      ],
    });
    vi.mocked(client.getEntityDetail).mockResolvedValue({
      entity: {
        id: "person_1",
        scope_id: "album1",
        canonical_name: "妈妈",
        status: "confirmed",
        family_role: "母亲",
        summary: "",
        identity_state: "confirmed",
        role_state: "confirmed",
        name_state: "confirmed",
        avatar_face_instance_id: "face_1",
        evidence_count: 2,
      },
      clusters: [],
      observations: [],
      events: [
        {
          id: "event_1",
          scope_id: "album1",
          title: "周末郊游",
          event_type: "旅行",
          time_start: "2026-10-01T09:30:00+00:00",
          time_end: null,
          place: "深圳湾",
          admin_district: "南山区",
          activity: "散步",
          summary: "一家人去海边",
          confidence: 0.8,
          aggregation_score: 0.7,
          status: "active",
          revision: 1,
        },
      ],
    });

    render(<PersonEvidence personId="person_1" personName="妈妈" onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("周末郊游")).toBeTruthy();
    });
    expect(screen.getAllByAltText("妈妈 的人脸样本")).toHaveLength(2);
    expect(screen.getAllByAltText("妈妈 的人脸样本")[0].getAttribute("src")).toBe(
      "/api/v1/face-instances/face_1/crop",
    );
    expect(screen.getAllByRole("link", { name: "查看原图" })[0].getAttribute("href")).toBe(
      "/api/v1/assets/asset_1/file",
    );
    expect(screen.getByText("2 张人脸样本 · 1 个关联事件")).toBeTruthy();
  });

  it("关闭按钮把控制权交回详情侧栏", async () => {
    vi.mocked(client.getPerson).mockResolvedValue({
      entity: {
        id: "person_1",
        scope_id: "album1",
        canonical_name: "妈妈",
        status: "confirmed",
        family_role: null,
        summary: "",
        confidence: 1,
        confirmed: true,
        aliases: [],
      },
      avatar_face_instance_id: null,
      clusters: [],
    });
    vi.mocked(client.getEntityDetail).mockResolvedValue({
      entity: {
        id: "person_1",
        scope_id: "album1",
        canonical_name: "妈妈",
        status: "confirmed",
        family_role: null,
        summary: "",
        identity_state: "confirmed",
        role_state: "pending",
        name_state: "confirmed",
        avatar_face_instance_id: null,
        evidence_count: 0,
      },
      clusters: [],
      observations: [],
      events: [],
    });
    const onClose = vi.fn();

    render(<PersonEvidence personId="person_1" personName="妈妈" onClose={onClose} />);
    fireEvent.click(await screen.findByRole("button", { name: "关闭证据页" }));
    expect(onClose).toHaveBeenCalled();
  });
});
