/**
 * MemoryCandidatesPage — 记忆候选确认页
 * 通过 GET /api/memory/candidates 加载候选列表，
 * 支持单条 accept/edit+accept/reject 以及批量 reject
 * Feature 084 FR-8.1~8.4 / T053
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import CandidateCard from "../../components/memory/CandidateCard";
import BatchRejectButton from "../../components/memory/BatchRejectButton";
import { fetchMemoryCandidates } from "../../api/memory-candidates";
import type { MemoryCandidate } from "../../api/memory-candidates";

interface ToastState {
  message: string;
  isError: boolean;
}

export default function MemoryCandidatesPage() {
  const [candidates, setCandidates] = useState<MemoryCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** 加载候选列表 */
  const loadCandidates = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetchMemoryCandidates();
      setCandidates(resp.candidates);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "加载失败，请重试";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCandidates();
  }, [loadCandidates]);

  /** 乐观更新：从列表移除指定候选（promote / discard 单条都调用） */
  const handleRemove = useCallback((id: string) => {
    setCandidates((prev) => prev.filter((c) => c.id !== id));
    // F32 修复：单条移除后通知 layout 刷新 badge
    window.dispatchEvent(new CustomEvent("memory-candidates-changed"));
  }, []);

  /**
   * F31 修复：批量操作完成后只移除 discardedIds 对应的候选，skippedIds 保留可见。
   * 服务端 bulk_discard 已 ACID 保证只对真实 pending 状态的候选改 status；
   * skipped 的候选可能是已 promoted / 已 rejected / 不存在，前端不应让它们消失。
   */
  const handleBulkDiscarded = useCallback(
    (outcome: { discardedIds: string[]; skippedIds: string[] }) => {
      const discardedSet = new Set(outcome.discardedIds);
      setCandidates((prev) => prev.filter((c) => !discardedSet.has(c.id)));
      // F32 修复：操作完成后通知 layout 刷新 badge（dispatch 模块级事件）
      window.dispatchEvent(new CustomEvent("memory-candidates-changed"));
    },
    [],
  );

  /** toast 通知（3s 后自动消失，清理 timer 避免内存泄漏） */
  const handleToast = useCallback((message: string, isError: boolean) => {
    if (toastTimerRef.current !== null) {
      clearTimeout(toastTimerRef.current);
    }
    setToast({ message, isError });
    toastTimerRef.current = setTimeout(() => {
      setToast(null);
      toastTimerRef.current = null;
    }, 3000);
  }, []);

  // 卸载时清理 timer
  useEffect(() => {
    return () => {
      if (toastTimerRef.current !== null) {
        clearTimeout(toastTimerRef.current);
      }
    };
  }, []);

  const candidateIds = candidates.map((c) => c.id);

  return (
    <div className="wb-page">
      <section className="wb-panel">
        <div className="wb-panel-header">
          <div>
            <h2>待确认记忆</h2>
            <p className="wb-panel-subtitle">
              Agent 在对话中发现的信息片段，确认后将存入你的长期记忆。
            </p>
          </div>
          <div className="wb-panel-header-actions">
            <Link className="wb-button wb-button-secondary" to="/memory">
              回到记忆中心
            </Link>
            {candidates.length > 0 && (
              <BatchRejectButton
                candidateIds={candidateIds}
                onBulkDiscarded={handleBulkDiscarded}
                onToast={handleToast}
              />
            )}
          </div>
        </div>

        {/* Toast 通知 */}
        {toast !== null && (
          <div
            className={`wb-inline-banner ${toast.isError ? "is-error" : "is-muted"}`}
            role="status"
            aria-live="polite"
          >
            <span>{toast.message}</span>
          </div>
        )}

        {/* Loading 状态 */}
        {loading && (
          <div className="wb-empty-state" aria-label="加载中">
            <span>正在加载候选记忆…</span>
          </div>
        )}

        {/* Error 状态 */}
        {!loading && error !== null && (
          <div className="wb-empty-state">
            <strong>加载失败</strong>
            <span>{error}</span>
            <button
              type="button"
              className="wb-button wb-button-primary"
              onClick={() => void loadCandidates()}
            >
              重新加载
            </button>
          </div>
        )}

        {/* Empty 状态 */}
        {!loading && error === null && candidates.length === 0 && (
          <div className="wb-empty-state">
            <strong>暂无待确认的记忆</strong>
            <span>Agent 会在对话过程中自动提炼，下次有新候选时这里会出现内容。</span>
            <Link className="wb-button wb-button-secondary" to="/memory">
              回到记忆中心
            </Link>
          </div>
        )}

        {/* 候选列表 */}
        {!loading && error === null && candidates.length > 0 && (
          <div className="wb-candidate-list">
            {candidates.map((candidate) => (
              <CandidateCard
                key={candidate.id}
                candidate={candidate}
                onRemove={handleRemove}
                onToast={handleToast}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
