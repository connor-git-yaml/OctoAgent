/**
 * Memory Candidates API 共享类型 + 统一client transport
 * Feature 084 FR-8.1
 */
import {
  apiErrorFromResponse,
  frontDoorRequest,
} from "./client";

export interface MemoryCandidate {
  id: string;
  fact_content: string;
  category: string;
  /** 0~1 的浮点置信度 */
  confidence: number;
  created_at: string;
  expires_at: string | null;
  source_turn_id: string | null;
}

export interface MemoryCandidatesResponse {
  candidates: MemoryCandidate[];
  total: number;
  pending_count: number;
}

/** Memory窄JSON wrapper；认证/header/error解析只归api/client。 */
export async function apiFetchMemory<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await frontDoorRequest(path, init);
  if (!resp.ok) {
    throw await apiErrorFromResponse(resp);
  }
  return resp.json() as Promise<T>;
}
