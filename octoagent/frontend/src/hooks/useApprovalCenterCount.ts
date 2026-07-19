/**
 * useApprovalCenterCount — 「审批」nav 红点 badge 的三源合计 hook（F145）
 *
 * 拉 GET /api/approval-center/summary（三源 pending 汇总只读端点），mount 时拉取 +
 * 监听全局事件 "approval-center-changed"（审批中心页任何操作成功后 dispatch），
 * 让 badge 与服务端状态保持同步（前身 useMemoryCandidateCount 的 F32 模式推广）。
 */
import { useEffect, useState } from "react";
import { fetchApprovalSummary } from "../api/approval-center";

export const APPROVAL_CENTER_CHANGED_EVENT = "approval-center-changed";

export function useApprovalCenterCount(): number {
  const [count, setCount] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      try {
        const summary = await fetchApprovalSummary();
        if (!cancelled) {
          setCount(summary.total_pending);
        }
      } catch {
        // 拉取失败静默处理，badge 不更新（badge 是辅助信号，不该打断使用）
      }
    }

    void refresh();

    function handleChanged() {
      void refresh();
    }
    window.addEventListener(APPROVAL_CENTER_CHANGED_EVENT, handleChanged);

    return () => {
      cancelled = true;
      window.removeEventListener(APPROVAL_CENTER_CHANGED_EVENT, handleChanged);
    };
  }, []);

  return count;
}
