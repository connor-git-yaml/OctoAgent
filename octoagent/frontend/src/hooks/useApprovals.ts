/**
 * useApprovals Hook -- T043
 *
 * SSE EventSource 订阅 approval:* 事件 + 30s 轮询兜底 + 状态管理 + 自动重连
 * 对齐 FR-022: SSE 实时更新
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { frontDoorRequest } from "../api/client";

/** 审批列表项（对齐后端 ApprovalListItem） */
export interface ApprovalItem {
  approval_id: string;
  task_id: string;
  tool_name: string;
  tool_args_summary: string;
  risk_explanation: string;
  policy_label: string;
  side_effect_level: string;
  remaining_seconds: number;
  created_at: string;
}

/** 审批决策（对齐后端 ApprovalDecision） */
export type ApprovalDecision = "allow-once" | "allow-always" | "deny";

/** Hook 返回值 */
export interface UseApprovalsReturn {
  /** 待审批列表 */
  approvals: ApprovalItem[];
  /** 总数 */
  total: number;
  /** 加载状态 */
  loading: boolean;
  /** 错误信息 */
  error: string | null;
  /** 提交审批决策 */
  resolve: (approvalId: string, decision: ApprovalDecision) => Promise<boolean>;
  /** 手动刷新 */
  refresh: () => Promise<void>;
}

const POLL_INTERVAL = 30_000; // 30s 轮询兜底

export function useApprovals(): UseApprovalsReturn {
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /** 从 REST API 获取审批列表 */
  const fetchApprovals = useCallback(async () => {
    try {
      const resp = await frontDoorRequest("/api/approvals");
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = await resp.json();
      setApprovals(data.approvals || []);
      setTotal(data.total || 0);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "获取审批列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  /** 提交审批决策 */
  const resolve = useCallback(
    async (approvalId: string, decision: ApprovalDecision): Promise<boolean> => {
      try {
        const resp = await frontDoorRequest(`/api/approve/${approvalId}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision }),
        });

        if (!resp.ok) {
          const data = await resp.json().catch(() => null);
          setError(data?.message || `审批失败: HTTP ${resp.status}`);
          return false;
        }

        // 审批成功后刷新列表
        await fetchApprovals();
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : "审批请求失败");
        return false;
      }
    },
    [fetchApprovals]
  );

  /** 手动刷新 */
  const refresh = useCallback(async () => {
    setLoading(true);
    await fetchApprovals();
  }, [fetchApprovals]);

  // 初始加载 + 30s 轮询兜底
  useEffect(() => {
    fetchApprovals();

    pollTimerRef.current = setInterval(fetchApprovals, POLL_INTERVAL);

    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
      }
    };
  }, [fetchApprovals]);

  // SSE 实时更新说明:
  // 审批事件通过 per-task SSE 流推送（approval:requested/resolved/expired）
  // SSE 实时更新在 per-task 视图中通过 useSSE hook 覆盖
  // 全局审批面板使用 30s 轮询保证数据一致性
  // M2 可扩展为独立的全局 SSE 连接

  return {
    approvals,
    total,
    loading,
    error,
    resolve,
    refresh,
  };
}
