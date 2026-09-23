import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  type FaceClusterSummary,
  type FaceCropRef,
  type PersonPayload,
} from "../../shared/api/client";
import { PeopleView } from "./PeopleView";
import { describeReviewError } from "./reviewErrors";

// ---- 假件 --------------------------------------------------------------------
//
// 假件按**契约的完整字段**造：``FaceCropRef`` / ``FaceClusterSummary`` / ``PersonPayload``
// 一个字段都不省。省字段的假件比真实契约宽松，前端漏读一个字段它也能过——那种红灯
// 要留到真后端上才亮。

function crop(faceInstanceId: string, assetId: string, fileName: string, quality: number): FaceCropRef {
  return {
    face_instance_id: faceInstanceId,
    asset_id: assetId,
    observation_id: `obs_${assetId}`,
    file_name: fileName,
    media_type: "image",
    bbox: [10, 20, 110, 140],
    detection_confidence: 0.87,
    quality,
    crop_url: `/api/v1/face-instances/${faceInstanceId}/crop`,
  };
}

function cluster(overrides: Partial<FaceClusterSummary> & { id: string }): FaceClusterSummary {
  return {
    scope_id: "album1",
    status: "pending",
    entity_id: null,
    canonical_name: null,
    family_role: null,
    entity_status: "pending",
    member_count: 1,
    confidence: 0.78,
    revision: 1,
    created_at: "2026-09-23T12:00:00+00:00",
    updated_at: "2026-09-23T12:00:00+00:00",
    reviewable: true,
    single_sample: false,
    samples: [],
    ...overrides,
  };
}

function person(overrides: Partial<PersonPayload> & { id: string }): PersonPayload {
  return {
    scope_id: "album1",
    canonical_name: "妈妈",
    status: "confirmed",
    family_role: "母亲",
    summary: "",
    confidence: 0.9,
    confirmed: true,
    aliases: [],
    ...overrides,
  };
}

interface Call {
  url: string;
  method: string;
  body: Record<string, string> | null;
}

function respond(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

/**
 * 内存里的假后端。它**会真的改状态**（确认掉的簇从列表里消失、合并后的 target
 * member_count 变大），这样「点完确认后页面确实变了」才是断言而不是摆设。
 */
class FakeBackend {
  clusters: FaceClusterSummary[];
  people: PersonPayload[];
  calls: Call[] = [];
  /** 下一次请求强制失败，用来测错误码 → 中文文案。 */
  failNext: { status: number; body: unknown } | null = null;

  constructor(clusters: FaceClusterSummary[], people: PersonPayload[]) {
    this.clusters = clusters;
    this.people = people;
  }

  handle = async (input: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const url = input;
    const body =
      typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, string>) : null;
    this.calls.push({ url, method, body });

    if (this.failNext !== null) {
      const failure = this.failNext;
      this.failNext = null;
      return respond(failure.status, failure.body);
    }

    if (method === "GET" && url.startsWith("/api/v1/face-clusters")) {
      return respond(200, { clusters: this.clusters });
    }
    if (method === "GET" && url.startsWith("/api/v1/people")) {
      return respond(200, { people: this.people });
    }

    const confirm = /^\/api\/v1\/face-clusters\/([^/]+)\/confirm$/.exec(url);
    if (method === "POST" && confirm !== null) {
      const id = confirm[1];
      const entity = person({
        id: `entity_for_${id}`,
        canonical_name: body?.name ?? "",
        family_role: body?.family_role ?? null,
      });
      this.clusters = this.clusters.filter((item) => item.id !== id);
      this.people = [...this.people, entity];
      return respond(200, { entity, merged_into: null });
    }

    const reject = /^\/api\/v1\/face-clusters\/([^/]+)\/reject$/.exec(url);
    if (method === "POST" && reject !== null) {
      const id = reject[1];
      this.clusters = this.clusters.filter((item) => item.id !== id);
      return respond(200, { cluster_id: id, rejected_entity_id: null });
    }

    if (method === "POST" && url === "/api/v1/face-clusters/merge") {
      const targetId = body?.target_cluster_id ?? "";
      const sourceId = body?.source_cluster_id ?? "";
      const target = this.clusters.find((item) => item.id === targetId);
      const source = this.clusters.find((item) => item.id === sourceId);
      if (target === undefined || source === undefined) {
        return respond(404, { code: "not_found", message: "cluster not found", detail: "" });
      }
      const merged: FaceClusterSummary = {
        ...target,
        member_count: target.member_count + source.member_count,
        samples: [...target.samples, ...source.samples],
        revision: target.revision + 1,
      };
      this.clusters = this.clusters
        .filter((item) => item.id !== sourceId)
        .map((item) => (item.id === targetId ? merged : item));
      return respond(200, merged);
    }

    const split = /^\/api\/v1\/face-clusters\/([^/]+)\/split$/.exec(url);
    if (method === "POST" && split !== null) {
      const id = split[1];
      const source = this.clusters.find((item) => item.id === id);
      const faceId = body?.face_instance_id ?? "";
      const face = source?.samples.find((item) => item.face_instance_id === faceId);
      if (source === undefined || face === undefined) {
        return respond(422, {
          code: "unprocessable",
          message: "face instance is not in this cluster",
          detail: "",
        });
      }
      const created = cluster({
        id: `cluster_split_${face.face_instance_id}`,
        canonical_name: source.canonical_name,
        member_count: 1,
        single_sample: true,
        samples: [face],
      });
      this.clusters = this.clusters
        .map((item) =>
          item.id === id
            ? {
                ...item,
                member_count: item.member_count - 1,
                samples: item.samples.filter((s) => s.face_instance_id !== faceId),
              }
            : item,
        )
        .concat(created);
      return respond(200, created);
    }

    const rename = /^\/api\/v1\/people\/([^/]+)\/name$/.exec(url);
    if (method === "PATCH" && rename !== null) {
      const id = rename[1];
      const found = this.people.find((item) => item.id === id);
      if (found === undefined) {
        return respond(404, { code: "not_found", message: "person not found", detail: "" });
      }
      const updated: PersonPayload = {
        ...found,
        canonical_name: body?.name ?? "",
        aliases: [...found.aliases, found.canonical_name],
      };
      this.people = this.people.map((item) => (item.id === id ? updated : item));
      return respond(200, { entity: updated, aliases: updated.aliases });
    }

    throw new Error(`假后端没有这条路由：${method} ${url}`);
  };
}

function mount(clusters: FaceClusterSummary[], people: PersonPayload[] = []) {
  const backend = new FakeBackend(clusters, people);
  vi.stubGlobal("fetch", backend.handle);
  return backend;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function twoSampleCluster(id: string, name: string | null) {
  return cluster({
    id,
    canonical_name: name,
    member_count: 2,
    samples: [
      crop("face_1", "asset_1", "a.jpg", 0.8),
      crop("face_2", "asset_2", "b.jpg", 0.7),
    ],
  });
}

// ---- 用例 --------------------------------------------------------------------

describe("人脸簇审阅页", () => {
  it("列出待确认簇，并用服务端给的 crop_url 渲染成员裁剪图", async () => {
    const backend = mount([
      cluster({
        id: "cluster_a",
        canonical_name: "待命名成员",
        member_count: 2,
        samples: [
          crop("face_1", "asset_1", "a.jpg", 0.8),
          crop("face_2", "asset_2", "b.jpg", 0.7),
        ],
      }),
      cluster({
        id: "cluster_single",
        canonical_name: null,
        member_count: 1,
        single_sample: true,
        samples: [crop("face_3", "asset_3", "c.jpg", 0.6)],
      }),
    ]);

    render(<PeopleView scopeId="album1" />);

    const sources = (await screen.findAllByRole("img")).map((img) => img.getAttribute("src"));
    // src 就是服务端拼好的整串，前端没有二次拼路径
    expect(sources).toContain("/api/v1/face-instances/face_1/crop");
    expect(sources).toContain("/api/v1/face-instances/face_2/crop");
    // 孤证簇折在 <details> 里，裁剪图照样要渲染出来
    expect(screen.getByAltText("未命名 的人脸样本 1").getAttribute("src")).toBe(
      "/api/v1/face-instances/face_3/crop",
    );

    expect(screen.getByText("2 个待确认簇")).toBeTruthy();
    // 只问 pending：默认列表不含 rejected，本页也不该把已确认的再拉一遍
    expect(backend.calls[0].url).toBe("/api/v1/face-clusters?scope_id=album1&status=pending");
    expect(backend.calls[1].url).toBe("/api/v1/people?scope_id=album1&status=confirmed");
  });

  it("确认一个簇会带着名字与家庭角色打到 confirm 端点，并让它从待确认里消失", async () => {
    const backend = mount([twoSampleCluster("cluster_a", "待命名成员")]);

    render(<PeopleView scopeId="album1" />);
    fireEvent.click(await screen.findByRole("button", { name: "确认身份" }));

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "妈妈" } });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "母亲" } });
    fireEvent.click(screen.getByRole("button", { name: "确认并写入" }));

    await waitFor(() => {
      expect(
        backend.calls.some((call) => call.url === "/api/v1/face-clusters/cluster_a/confirm"),
      ).toBe(true);
    });
    const call = backend.calls.find(
      (item) => item.url === "/api/v1/face-clusters/cluster_a/confirm",
    );
    expect(call?.method).toBe("POST");
    expect(call?.body).toEqual({ name: "妈妈", family_role: "母亲" });

    await waitFor(() => expect(screen.getByText("0 个待确认簇")).toBeTruthy());
    expect(await screen.findByText("妈妈")).toBeTruthy();
  });

  it("拒绝一个簇会打到 reject 端点，并把它从列表里去掉", async () => {
    const backend = mount([twoSampleCluster("cluster_a", "待命名成员")]);

    render(<PeopleView scopeId="album1" />);
    fireEvent.click(await screen.findByRole("button", { name: "不是人物" }));

    await waitFor(() => {
      expect(
        backend.calls.some((call) => call.url === "/api/v1/face-clusters/cluster_a/reject"),
      ).toBe(true);
    });
    const call = backend.calls.find(
      (item) => item.url === "/api/v1/face-clusters/cluster_a/reject",
    );
    expect(call?.method).toBe("POST");
    expect(call?.body).toBeNull();

    await waitFor(() => expect(screen.getByText("0 个待确认簇")).toBeTruthy());
  });

  it("选中两个簇后能在合并面板里指定目标，再合并", async () => {
    const backend = mount([
      twoSampleCluster("cluster_a", "待命名成员"),
      cluster({
        id: "cluster_b",
        canonical_name: null,
        member_count: 3,
        samples: [
          crop("face_4", "asset_4", "d.jpg", 0.9),
          crop("face_5", "asset_5", "e.jpg", 0.85),
          crop("face_6", "asset_6", "f.jpg", 0.8),
        ],
      }),
    ]);

    render(<PeopleView scopeId="album1" />);
    fireEvent.click(await screen.findByLabelText("选中 待命名成员 用于合并"));
    fireEvent.click(screen.getByLabelText("选中 未命名 用于合并"));

    const bar = await screen.findByRole("region", { name: "合并人物簇" });
    // 没显式选过目标时，默认是先点的那个
    expect((within(bar).getByRole("radio", { name: /待命名成员/ }) as HTMLInputElement).checked).toBe(
      true,
    );

    fireEvent.click(within(bar).getByRole("radio", { name: /未命名/ }));
    fireEvent.click(within(bar).getByRole("button", { name: "合并人物簇" }));

    await waitFor(() => {
      expect(backend.calls.some((call) => call.url === "/api/v1/face-clusters/merge")).toBe(true);
    });
    const call = backend.calls.find((item) => item.url === "/api/v1/face-clusters/merge");
    expect(call?.method).toBe("POST");
    // 保留的是显式点中的那个，另一个作为来源并进去
    expect(call?.body).toEqual({
      target_cluster_id: "cluster_b",
      source_cluster_id: "cluster_a",
    });

    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "合并人物簇" })).toBeNull(),
    );
    expect(await screen.findByText(/5 张样本/)).toBeTruthy();
  });

  it("在一簇里点中某一张脸就能把它拆成新簇", async () => {
    const backend = mount([twoSampleCluster("cluster_a", "待命名成员")]);

    render(<PeopleView scopeId="album1" />);
    fireEvent.click(await screen.findByRole("button", { name: "待命名成员 的第 2 张人脸" }));
    fireEvent.click(screen.getByRole("button", { name: "拆分为新簇" }));

    await waitFor(() => {
      expect(
        backend.calls.some((call) => call.url === "/api/v1/face-clusters/cluster_a/split"),
      ).toBe(true);
    });
    const call = backend.calls.find(
      (item) => item.url === "/api/v1/face-clusters/cluster_a/split",
    );
    expect(call?.method).toBe("POST");
    // 拆的是被点中的那张，不是「前端自己挑一张」
    expect(call?.body).toEqual({ face_instance_id: "face_2" });

    // 刷新后多出一个孤证簇：拆出来的那张脸真的成了独立的一簇
    await waitFor(() => expect(screen.getByText(/孤证/)).toBeTruthy());
  });

  it("给已确认的人物改名会打到 PATCH /people/{id}/name", async () => {
    const backend = mount([], [person({ id: "entity_1", canonical_name: "妈妈" })]);

    render(<PeopleView scopeId="album1" />);
    fireEvent.click(await screen.findByRole("button", { name: "改名" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "母亲大人" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(backend.calls.some((call) => call.url === "/api/v1/people/entity_1/name")).toBe(true);
    });
    const call = backend.calls.find((item) => item.url === "/api/v1/people/entity_1/name");
    expect(call?.method).toBe("PATCH");
    expect(call?.body).toEqual({ name: "母亲大人" });
    expect(await screen.findByText("母亲大人")).toBeTruthy();
  });

  it("后端报 invalid_request 时把中文文案显示在页面上", async () => {
    const backend = mount([twoSampleCluster("cluster_a", "待命名成员")]);

    render(<PeopleView scopeId="album1" />);
    fireEvent.click(await screen.findByRole("button", { name: "确认身份" }));

    backend.failNext = {
      status: 400,
      body: { code: "invalid_request", message: "name is required", detail: "name is required" },
    };
    fireEvent.click(screen.getByRole("button", { name: "确认并写入" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("请求不成立");
    expect(alert.textContent).toContain("name is required");
  });
});

describe("describeReviewError", () => {
  it("四个 code 各自给一句不同的中文，未归类的带上状态码与 detail", () => {
    const cases: [string, number, string][] = [
      ["invalid_request", 400, "请求不成立"],
      ["not_found", 404, "找不到"],
      ["conflict", 409, "当前状态不允许"],
      ["unprocessable", 422, "处理不了"],
    ];
    for (const [code, status, expected] of cases) {
      const text = describeReviewError(
        new ApiError(status, { code, message: "boom", detail: "detail" }, "raw"),
      );
      expect(text).toContain(expected);
      expect(text).toContain("boom");
    }

    expect(describeReviewError(new ApiError(500, null, "gateway down"))).toContain("HTTP 500");
    expect(describeReviewError(new Error("网络断了"))).toContain("网络断了");
    expect(describeReviewError("不是 Error")).toContain("未知错误");
  });
});
