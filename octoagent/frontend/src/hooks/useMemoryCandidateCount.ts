/**
 * useMemoryCandidateCount — 轻量 hook，获取待确认记忆候选数
 * 用于导航红点 badge（FR-8.4）
 *
 * F32 修复：除 mount 时拉取，还监听全局事件 "memory-candidates-changed"
 * （由 MemoryCandidatesPage 在 promote / discard / bulk_discard 成功后 dispatch）
 * 让 badge 在候选页操作完成后自动刷新，避免与服务端状态冲突。
 */
import { useEffect, useState } from "react";
import { fetchMemoryCandidates } from "../api/memory-candidates";

export const MEMORY_CANDIDATES_CHANGED_EVENT = "memory-candidates-changed";

export function useMemoryCandidateCount(): number {
  const [count, setCount] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      try {
        const resp = await fetchMemoryCandidates();
        if (!cancelled) {
          setCount(resp.pending_count);
        }
      } catch {
        // 拉取失败静默处理，badge 不更新
      }
    }

    // 初次 mount 拉取
    void refresh();

    // 监听全局事件，候选页操作后立即刷新（F32）
    function handleChanged() {
      void refresh();
    }
    window.addEventListener(MEMORY_CANDIDATES_CHANGED_EVENT, handleChanged);

    return () => {
      cancelled = true;
      window.removeEventListener(MEMORY_CANDIDATES_CHANGED_EVENT, handleChanged);
    };
  }, []);

  return count;
}
