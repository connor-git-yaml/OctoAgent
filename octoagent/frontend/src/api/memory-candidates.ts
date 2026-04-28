/**
 * Memory Candidates API — Feature 084 FR-8.1~8.4 前端调用层
 * 封装 /api/memory/candidates 相关端点
 */
import { apiFetchMemory } from "./memory-candidates-types";
import type { MemoryCandidatesResponse } from "./memory-candidates-types";

export type { MemoryCandidate, MemoryCandidatesResponse } from "./memory-candidates-types";

/** GET /api/memory/candidates — 获取 pending 候选列表 */
export async function fetchMemoryCandidates(): Promise<MemoryCandidatesResponse> {
  return apiFetchMemory<MemoryCandidatesResponse>("/api/memory/candidates");
}

/** POST /api/memory/candidates/{id}/promote — 接受候选（可选传入编辑后内容） */
export async function promoteCandidate(
  id: string,
  factContent?: string
): Promise<void> {
  const body: Record<string, string> = {};
  if (factContent !== undefined) {
    body.fact_content = factContent;
  }
  await apiFetchMemory<unknown>(`/api/memory/candidates/${encodeURIComponent(id)}/promote`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** POST /api/memory/candidates/{id}/discard — 拒绝候选 */
export async function discardCandidate(id: string): Promise<void> {
  await apiFetchMemory<unknown>(
    `/api/memory/candidates/${encodeURIComponent(id)}/discard`,
    { method: "POST", body: JSON.stringify({}) }
  );
}

/** PUT /api/memory/candidates/bulk_discard — 批量拒绝候选 */
export async function bulkDiscardCandidates(candidateIds: string[]): Promise<{
  discarded_count: number;
  skipped_ids: string[];
}> {
  return apiFetchMemory<{ discarded_count: number; skipped_ids: string[] }>(
    "/api/memory/candidates/bulk_discard",
    {
      method: "PUT",
      body: JSON.stringify({ candidate_ids: candidateIds }),
    }
  );
}
