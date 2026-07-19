/**
 * approval-center API 层单测 — Opus 评审 MED-1 闭环
 *
 * D4 分流的承重前半段：postApproval 把后端真实 HTTP body 形态解析成
 * ApprovalActionError.resultStatus（后半段 mapApprovalFailure 已在
 * approvalModels.test.ts 钉住）。此前 Page 测试 mock 整个本模块直接构造
 * 错误对象——若解析误读字段（如 body.status 拼错），全部失败会静默降级
 * unknown（用户对已终态候选反复点接受）而套件仍绿。本文件用 stub fetch
 * 回放后端真实 body 形态，钉住 HTTP→resultStatus 这一跳。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApprovalActionError,
  acceptCompactCandidate,
  acceptConsolidationCandidate,
  bulkRejectConsolidation,
  fetchApprovalSummary,
  fetchConsolidationCandidates,
} from "./approval-center";
import { ApiError } from "./client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function captureApprovalError(
  action: () => Promise<void>
): Promise<ApprovalActionError> {
  try {
    await action();
  } catch (err) {
    expect(err).toBeInstanceOf(ApprovalActionError);
    return err as ApprovalActionError;
  }
  throw new Error("预期抛 ApprovalActionError 但成功了");
}

describe("postApproval：后端真实 body 形态 → resultStatus（MED-1）", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("2xx → 正常返回", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(200, { ok: true, status: "applied", candidate_id: "c1" })
    );
    await expect(acceptConsolidationCandidate("c1")).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/consolidation/candidates/c1/accept",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("409 + body.status=conflict（approval 服务终态）→ conflict", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(409, {
        ok: false,
        status: "conflict",
        candidate_id: "c1",
        detail: "源 SOR 已变更，请重审",
      })
    );
    const err = await captureApprovalError(() => acceptConsolidationCandidate("c1"));
    expect(err.resultStatus).toBe("conflict");
    expect(err.httpStatus).toBe(409);
    expect(err.detail).toContain("已变更");
  });

  it("409 + body.status=pending（回滚可重试）→ pending", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(409, { ok: false, status: "pending", detail: "内部回滚" })
    );
    const err = await captureApprovalError(() => acceptCompactCandidate("b1"));
    expect(err.resultStatus).toBe("pending");
  });

  it("404 + body.status=not_found（approval 服务）→ not_found", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(404, { ok: false, status: "not_found", detail: "候选不存在" })
    );
    const err = await captureApprovalError(() => acceptCompactCandidate("ghost"));
    expect(err.resultStatus).toBe("not_found");
  });

  it("原生 404（detail-only，无 status 字段）→ not_found 兜底", async () => {
    fetchMock.mockResolvedValue(jsonResponse(404, { detail: "Not Found" }));
    const err = await captureApprovalError(() => acceptConsolidationCandidate("x"));
    expect(err.resultStatus).toBe("not_found");
  });

  it("原生 500（root-task ensure 失败等 detail-only）→ unknown（保守保留卡片）", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(500, { detail: "consolidation root task ensure 失败" })
    );
    const err = await captureApprovalError(() => acceptConsolidationCandidate("c1"));
    expect(err.resultStatus).toBe("unknown");
    expect(err.httpStatus).toBe(500);
  });

  it("body.status 为未知值（未来演化）→ unknown 不误分类", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(400, { ok: false, status: "applying", detail: "..." })
    );
    const err = await captureApprovalError(() => acceptCompactCandidate("b1"));
    expect(err.resultStatus).toBe("unknown");
  });

  it("网络层失败 → unknown + httpStatus 0", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const err = await captureApprovalError(() => acceptConsolidationCandidate("c1"));
    expect(err.resultStatus).toBe("unknown");
    expect(err.httpStatus).toBe(0);
  });
});

describe("getJson / bulk 调用形态", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("list 端点 500 → ApiError 带 detail 文案", async () => {
    fetchMock.mockResolvedValue(jsonResponse(500, { detail: "查询失败" }));
    await expect(fetchConsolidationCandidates()).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
      message: "查询失败",
    });
  });

  it("summary 正常返回四字段", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        memory_pending: 1,
        consolidation_pending: 2,
        behavior_compact_pending: 3,
        total_pending: 6,
      })
    );
    const summary = await fetchApprovalSummary();
    expect(summary.total_pending).toBe(6);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/approval-center/summary",
      expect.anything()
    );
  });

  it("bulk_reject PUT 携带 candidate_ids 且回传 rejected/skipped", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(200, { rejected: ["a"], skipped: ["b"] })
    );
    const result = await bulkRejectConsolidation(["a", "b"]);
    expect(result).toEqual({ rejected: ["a"], skipped: ["b"] });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/consolidation/candidates/bulk_reject");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({
      candidate_ids: ["a", "b"],
    });
  });

  it("bulk_reject 非 2xx → ApiError", async () => {
    fetchMock.mockResolvedValue(jsonResponse(500, { detail: "boom" }));
    await expect(bulkRejectConsolidation(["a"])).rejects.toBeInstanceOf(ApiError);
  });
});
