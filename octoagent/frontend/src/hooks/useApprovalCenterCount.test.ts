/**
 * useApprovalCenterCount 单测 — F145 AC-5（badge 合计 + 事件刷新）
 */
import { renderHook, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../api/approval-center", () => ({
  fetchApprovalSummary: vi.fn(),
}));

import { fetchApprovalSummary } from "../api/approval-center";
import {
  APPROVAL_CENTER_CHANGED_EVENT,
  useApprovalCenterCount,
} from "./useApprovalCenterCount";

function summary(total: number) {
  return {
    memory_pending: total,
    consolidation_pending: 0,
    behavior_compact_pending: 0,
    total_pending: total,
  };
}

describe("useApprovalCenterCount", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("mount 时拉取 summary 并返回 total_pending", async () => {
    vi.mocked(fetchApprovalSummary).mockResolvedValue(summary(3));
    const { result } = renderHook(() => useApprovalCenterCount());
    await waitFor(() => {
      expect(result.current).toBe(3);
    });
  });

  it("approval-center-changed 事件触发重新拉取", async () => {
    vi.mocked(fetchApprovalSummary).mockResolvedValue(summary(2));
    const { result } = renderHook(() => useApprovalCenterCount());
    await waitFor(() => {
      expect(result.current).toBe(2);
    });

    vi.mocked(fetchApprovalSummary).mockResolvedValue(summary(0));
    act(() => {
      window.dispatchEvent(new CustomEvent(APPROVAL_CENTER_CHANGED_EVENT));
    });
    await waitFor(() => {
      expect(result.current).toBe(0);
    });
  });

  it("拉取失败静默：count 维持 0 不抛错", async () => {
    vi.mocked(fetchApprovalSummary).mockRejectedValue(new Error("网络断了"));
    const { result } = renderHook(() => useApprovalCenterCount());
    // 静默失败：无异常且值保持 0
    await waitFor(() => {
      expect(fetchApprovalSummary).toHaveBeenCalled();
    });
    expect(result.current).toBe(0);
  });
});
