/**
 * BatchRejectButton — 全选并批量拒绝候选
 * Feature 084 FR-8.3 / T055
 */
import { useState } from "react";
import { bulkDiscardCandidates } from "../../api/memory-candidates";

export interface BulkDiscardOutcome {
  /** 服务端真实拒绝的候选 ID（仅这些应从 UI 列表移除） */
  discardedIds: string[];
  /** 服务端报告 skipped 的 ID（已 promoted / 已 rejected / 不存在）；UI 应保留这些可见 */
  skippedIds: string[];
}

export interface BatchRejectButtonProps {
  /** 当前候选 id 列表 */
  candidateIds: string[];
  /**
   * 批量操作完成后通知父层（F31 修复：传服务端真实结果而非"全部已移除"）。
   *
   * 父组件应只移除 discardedIds 对应的候选，skippedIds 对应的候选应保留可见
   * （让用户看到这些 ID 实际未被处理，避免与服务端状态不一致）。
   */
  onBulkDiscarded: (outcome: BulkDiscardOutcome) => void;
  /** toast 通知回调 */
  onToast: (message: string, isError: boolean) => void;
  disabled?: boolean;
}

export default function BatchRejectButton({
  candidateIds,
  onBulkDiscarded,
  onToast,
  disabled,
}: BatchRejectButtonProps) {
  const [busy, setBusy] = useState(false);

  async function handleBatchReject() {
    if (candidateIds.length === 0) return;
    setBusy(true);
    try {
      const result = await bulkDiscardCandidates(candidateIds);
      const skippedIds = result.skipped_ids ?? [];
      const skippedSet = new Set(skippedIds);
      const discardedIds = candidateIds.filter((id) => !skippedSet.has(id));

      if (skippedIds.length > 0) {
        onToast(
          `已忽略 ${result.discarded_count} 条，${skippedIds.length} 条跳过（保留在列表中）`,
          false
        );
      } else {
        onToast(`已忽略全部 ${result.discarded_count} 条候选`, false);
      }
      onBulkDiscarded({ discardedIds, skippedIds });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "批量操作失败，请重试";
      onToast(msg, true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      className="wb-button wb-button-secondary"
      onClick={() => void handleBatchReject()}
      disabled={disabled || busy || candidateIds.length === 0}
    >
      {busy ? "处理中…" : `全部忽略（${candidateIds.length} 条）`}
    </button>
  );
}
