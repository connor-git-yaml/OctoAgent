/**
 * Memory Candidates API 共享类型 + fetch 工具
 * Feature 084 FR-8.1
 */
import { ApiError, getFrontDoorToken } from "./client";

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

/** 轻量版 apiFetch，直接复用 client 的 token 逻辑，避免循环依赖 */
export async function apiFetchMemory<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getFrontDoorToken();
  const headers = new Headers(init?.headers ?? undefined);
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const resp = await fetch(path, { ...init, headers });
  if (!resp.ok) {
    let message = `HTTP ${resp.status}`;
    try {
      const body = (await resp.json()) as Record<string, unknown>;
      const err = (body?.error ?? body?.detail) as Record<string, unknown> | undefined;
      if (typeof err?.message === "string") message = err.message;
      else if (typeof body?.message === "string") message = body.message as string;
    } catch {
      // 解析失败时使用默认 HTTP 状态描述
    }
    throw new ApiError(message, { status: resp.status });
  }
  return resp.json() as Promise<T>;
}
