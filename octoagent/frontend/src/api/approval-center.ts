/**
 * 审批中心 API — F145 三源统一审批的调用层
 *
 * 覆盖两个此前无前端的提议源 + badge 汇总端点：
 * - F127 记忆合并提议：/api/consolidation/candidates（accept 是破坏性 MERGE，
 *   失败三态 body.status: conflict=终态 / pending=可重试 / not_found）
 * - F111 规则精简提议：/api/behavior/compact/candidates（同款三态；无 bulk 端点）
 * - F145 汇总：/api/approval-center/summary（badge 计数）
 *
 * memory 候选沿用既有 api/memory-candidates.ts，不在本模块重复。
 *
 * 与 apiFetchMemory 的差异：accept/reject 失败响应的 body.status 字段是 UI 呈现
 * 决策依据（HTTP 409 两义：conflict 终态 vs pending 回滚可重试），通用 fetch 会把
 * body 压成 message 丢掉 status——本模块的 postApproval 保留它（ApprovalActionError）。
 */
import { ApiError, getFrontDoorToken } from "./client";

// ---------------------------------------------------------------------------
// 类型（与后端 response schema 对齐）
// ---------------------------------------------------------------------------

/** F127 记忆合并提议（GET /api/consolidation/candidates item） */
export interface ConsolidationCandidate {
  candidate_id: string;
  run_id: string;
  partition: string;
  subject_key: string;
  source_count: number;
  merged_content: string;
  rationale: string;
  confidence: number;
  is_sensitive: boolean;
  status: string;
  created_at: string;
  source_previews: string[];
}

export interface ConsolidationCandidatesResponse {
  candidates: ConsolidationCandidate[];
  pending_count: number;
}

/** F111 规则精简提议（GET /api/behavior/compact/candidates item） */
export interface CompactCandidate {
  candidate_id: string;
  run_id: string;
  file_id: string;
  agent_slug: string;
  project_slug: string;
  rationale: string;
  size_before: number;
  size_after: number;
  status: string;
  created_at: string;
  diff: string;
}

export interface CompactCandidatesResponse {
  candidates: CompactCandidate[];
  pending_count: number;
}

/** GET /api/approval-center/summary 响应 */
export interface ApprovalSummary {
  memory_pending: number;
  consolidation_pending: number;
  behavior_compact_pending: number;
  total_pending: number;
}

/** accept/reject 失败结果状态（后端 body.status；unknown = 无法解析/网络层错误） */
export type ApprovalFailureStatus = "conflict" | "pending" | "not_found" | "unknown";

/** accept/reject 非 2xx 时抛出：保留 body.status 供 UI 按终态/可重试分流呈现 */
export class ApprovalActionError extends Error {
  /** HTTP 状态码（0 = 网络层失败） */
  httpStatus: number;
  /** 后端结果状态（conflict/pending/not_found/unknown） */
  resultStatus: ApprovalFailureStatus;
  /** 后端 detail 技术文案（仅诊断，不直接上 UI） */
  detail: string;

  constructor(options: {
    httpStatus: number;
    resultStatus: ApprovalFailureStatus;
    detail: string;
  }) {
    super(`approval action failed: ${options.resultStatus} (HTTP ${options.httpStatus})`);
    this.name = "ApprovalActionError";
    this.httpStatus = options.httpStatus;
    this.resultStatus = options.resultStatus;
    this.detail = options.detail;
  }
}

// ---------------------------------------------------------------------------
// fetch 工具
// ---------------------------------------------------------------------------

function buildHeaders(): Headers {
  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  const token = getFrontDoorToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return headers;
}

/** GET 类调用：失败抛 ApiError（与 apiFetchMemory 同语义） */
async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(path, { headers: buildHeaders() });
  if (!resp.ok) {
    let message = `HTTP ${resp.status}`;
    try {
      const body = (await resp.json()) as Record<string, unknown>;
      if (typeof body?.detail === "string") message = body.detail;
    } catch {
      // 保留默认 HTTP 状态描述
    }
    throw new ApiError(message, { status: resp.status });
  }
  return resp.json() as Promise<T>;
}

const KNOWN_FAILURE_STATUSES: ReadonlySet<string> = new Set([
  "conflict",
  "pending",
  "not_found",
]);

/** accept/reject 类调用：非 2xx 抛 ApprovalActionError（保留 body.status） */
async function postApproval(path: string): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(path, { method: "POST", headers: buildHeaders() });
  } catch (err) {
    throw new ApprovalActionError({
      httpStatus: 0,
      resultStatus: "unknown",
      detail: err instanceof Error ? err.message : String(err),
    });
  }
  if (resp.ok) {
    return;
  }
  let resultStatus: ApprovalFailureStatus = "unknown";
  let detail = `HTTP ${resp.status}`;
  try {
    const body = (await resp.json()) as Record<string, unknown>;
    if (
      typeof body?.status === "string" &&
      KNOWN_FAILURE_STATUSES.has(body.status)
    ) {
      resultStatus = body.status as ApprovalFailureStatus;
    }
    if (typeof body?.detail === "string") detail = body.detail;
  } catch {
    // body 不可解析：保持 unknown
  }
  // FastAPI 原生 404（如 route 层 HTTPException）没有 status 字段，用 HTTP 码兜底
  if (resultStatus === "unknown" && resp.status === 404) {
    resultStatus = "not_found";
  }
  throw new ApprovalActionError({ httpStatus: resp.status, resultStatus, detail });
}

// ---------------------------------------------------------------------------
// F127 记忆合并提议
// ---------------------------------------------------------------------------

export async function fetchConsolidationCandidates(): Promise<ConsolidationCandidatesResponse> {
  return getJson<ConsolidationCandidatesResponse>("/api/consolidation/candidates");
}

export async function acceptConsolidationCandidate(id: string): Promise<void> {
  await postApproval(
    `/api/consolidation/candidates/${encodeURIComponent(id)}/accept`
  );
}

export async function rejectConsolidationCandidate(id: string): Promise<void> {
  await postApproval(
    `/api/consolidation/candidates/${encodeURIComponent(id)}/reject`
  );
}

/** PUT bulk_reject → {rejected: string[], skipped: string[]} */
export async function bulkRejectConsolidation(
  candidateIds: string[]
): Promise<{ rejected: string[]; skipped: string[] }> {
  const resp = await fetch("/api/consolidation/candidates/bulk_reject", {
    method: "PUT",
    headers: buildHeaders(),
    body: JSON.stringify({ candidate_ids: candidateIds }),
  });
  if (!resp.ok) {
    throw new ApiError(`HTTP ${resp.status}`, { status: resp.status });
  }
  return resp.json() as Promise<{ rejected: string[]; skipped: string[] }>;
}

// ---------------------------------------------------------------------------
// F111 规则精简提议
// ---------------------------------------------------------------------------

export async function fetchCompactCandidates(): Promise<CompactCandidatesResponse> {
  return getJson<CompactCandidatesResponse>("/api/behavior/compact/candidates");
}

export async function acceptCompactCandidate(id: string): Promise<void> {
  await postApproval(
    `/api/behavior/compact/candidates/${encodeURIComponent(id)}/accept`
  );
}

export async function rejectCompactCandidate(id: string): Promise<void> {
  await postApproval(
    `/api/behavior/compact/candidates/${encodeURIComponent(id)}/reject`
  );
}

// ---------------------------------------------------------------------------
// F145 汇总（badge）
// ---------------------------------------------------------------------------

export async function fetchApprovalSummary(): Promise<ApprovalSummary> {
  return getJson<ApprovalSummary>("/api/approval-center/summary");
}
