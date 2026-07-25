/**
 * ApprovalCenterPage — F145 三源统一审批中心
 *
 * 把三个后台提议源收进一页（spec D1 单页三分组）：
 * - 新记忆（F084 memory 候选）：复用 CandidateCard / BatchRejectButton 既有交互
 * - 记忆合并建议（F127）：ProposalCard + 来源预览 + conflict 终态分流
 * - 规则精简建议（F111）：ProposalCard + unified diff 折叠渲染
 *
 * 每晚后台产的提议，早上打开这一页划一划批完（M10 体验核心）。
 * 三源并行加载、按源降级（Constitution #6）：一源失败只影响该分组，其余可操作。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import "./ApprovalCenterPage.css";
import CandidateCard from "../../components/memory/CandidateCard";
import BatchRejectButton, {
  type BulkDiscardOutcome,
} from "../../components/memory/BatchRejectButton";
import { DiffLineList } from "../../components/diff/DiffBody";
import { fetchMemoryCandidates } from "../../api/memory-candidates";
import type { MemoryCandidate } from "../../api/memory-candidates";
import {
  acceptCompactCandidate,
  acceptConsolidationCandidate,
  bulkRejectConsolidation,
  fetchCompactCandidates,
  fetchConsolidationCandidates,
  rejectCompactCandidate,
  rejectConsolidationCandidate,
} from "../../api/approval-center";
import type {
  CompactCandidate,
  ConsolidationCandidate,
} from "../../api/approval-center";
import {
  compactSummary,
  consolidationSummary,
  mapApprovalFailure,
  parseUnifiedDiff,
} from "./approvalModels";
import ProposalCard from "./ProposalCard";
import { APPROVAL_CENTER_CHANGED_EVENT } from "../../hooks/useApprovalCenterCount";
import {
  resolveResourcePageState,
  type ResourcePageResolution,
} from "../shared/resourcePageState";
import ResourceState from "../../ui/primitives/ResourceState";

interface ToastState {
  message: string;
  isError: boolean;
}

/** 单个提议源的加载状态（三源各一份，互不拖累） */
interface SourceState<T> {
  items: T[];
  loading: boolean;
  error: Error | null;
}

function useApprovalSource<T>(load: () => Promise<T[]>) {
  const [state, setState] = useState<SourceState<T>>({
    items: [],
    loading: true,
    error: null,
  });

  const reload = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const items = await load();
      setState({ items, loading: false, error: null });
    } catch (err) {
      const error = err instanceof Error ? err : new Error("加载失败");
      setState({ items: [], loading: false, error });
    }
  }, [load]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const removeItem = useCallback((predicate: (item: T) => boolean) => {
    setState((prev) => ({
      ...prev,
      items: prev.items.filter((item) => !predicate(item)),
    }));
  }, []);

  return { ...state, reload, removeItem };
}

function notifyApprovalChanged() {
  window.dispatchEvent(new CustomEvent(APPROVAL_CENTER_CHANGED_EVENT));
}

/** 分组骨架：标题 + 说明 + 头部动作位 */
function SourceSection(props: {
  title: string;
  stateTitle: string;
  subtitle: string;
  count: number;
  error: Error | null;
  onRetry: () => void;
  headerAction?: React.ReactNode;
  children?: React.ReactNode;
}) {
  const {
    title,
    stateTitle,
    subtitle,
    count,
    error,
    onRetry,
    headerAction,
    children,
  } = props;
  const headingId = `approval-source-${stateTitle}`;
  const resolution =
    error === null
      ? null
      : resolveResourcePageState({
          loading: false,
          hasContent: false,
          connected: true,
          error,
        });
  return (
    <section className="f149-approval-source" aria-labelledby={headingId}>
      <div className="f149-approval-source-header">
        <div>
          <h2 id={headingId}>
            {title}
            {count > 0 ? `（${count}）` : ""}
          </h2>
          <p>{subtitle}</p>
        </div>
        {headerAction && (
          <div className="f149-approval-source-action">{headerAction}</div>
        )}
      </div>
      {resolution !== null ? (
        <ApprovalState
          resolution={resolution}
          title={`${stateTitle}暂不可用`}
          detail={sourceErrorDetail(resolution)}
          onRetry={onRetry}
        />
      ) : (
        children
      )}
    </section>
  );
}

function sourceErrorDetail(resolution: ResourcePageResolution): string {
  if (resolution.owner === "global-auth" || resolution.state === null) {
    return "";
  }
  switch (resolution.state.kind) {
    case "permission-denied":
      return "资源权限不足，请联系管理员确认访问范围。";
    case "not-found":
      return "这部分内容暂时不存在，可能已经被处理。";
    case "conflict":
      return "内容刚刚发生变化，请刷新后再试。";
    default:
      return "加载没有成功，请稍后重试。";
  }
}

function ApprovalState(props: {
  resolution: ResourcePageResolution;
  title: string;
  detail: string;
  onRetry?: () => void;
}) {
  return (
    <div className="f149-approval-state">
      <ResourceState
        resolution={props.resolution}
        title={props.title}
        detail={props.detail}
        retryLabel="重新加载"
        onRetry={props.onRetry}
      />
    </div>
  );
}

export default function ApprovalCenterPage() {
  const memory = useApprovalSource<MemoryCandidate>(
    useCallback(async () => (await fetchMemoryCandidates()).candidates, []),
  );
  const consolidation = useApprovalSource<ConsolidationCandidate>(
    useCallback(
      async () => (await fetchConsolidationCandidates()).candidates,
      [],
    ),
  );
  const compact = useApprovalSource<CompactCandidate>(
    useCallback(async () => (await fetchCompactCandidates()).candidates, []),
  );

  const [toast, setToast] = useState<ToastState | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = useCallback((message: string, isError: boolean) => {
    if (toastTimerRef.current !== null) {
      clearTimeout(toastTimerRef.current);
    }
    setToast({ message, isError });
    toastTimerRef.current = setTimeout(() => {
      setToast(null);
      toastTimerRef.current = null;
    }, 3000);
  }, []);

  useEffect(() => {
    return () => {
      if (toastTimerRef.current !== null) {
        clearTimeout(toastTimerRef.current);
      }
    };
  }, []);

  // ---- memory 源（CandidateCard 自带 API 调用，成功后回调移除） ----

  const handleMemoryRemove = useCallback(
    (id: string) => {
      memory.removeItem((c) => c.id === id);
      notifyApprovalChanged();
    },
    [memory.removeItem], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const handleMemoryBulk = useCallback(
    (outcome: BulkDiscardOutcome) => {
      const discardedSet = new Set(outcome.discardedIds);
      memory.removeItem((c) => discardedSet.has(c.id));
      notifyApprovalChanged();
    },
    [memory.removeItem], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // ---- F127 / F111 源（页面层统一处理成功/失败呈现，D4 分流） ----

  async function runProposalAction(options: {
    action: () => Promise<void>;
    remove: () => void;
    successMessage: string;
  }) {
    try {
      await options.action();
      options.remove();
      notifyApprovalChanged();
      showToast(options.successMessage, false);
    } catch (err) {
      const presentation = mapApprovalFailure(err);
      if (presentation.removeCard) {
        // 终态（conflict/not_found）：卡片移除 + badge 刷新，不诱导反复重试
        options.remove();
        notifyApprovalChanged();
      }
      showToast(presentation.message, true);
      console.warn("approval action failed", err);
    }
  }

  async function handleConsolidationBulkReject() {
    const ids = consolidation.items.map((c) => c.candidate_id);
    if (ids.length === 0) return;
    try {
      const result = await bulkRejectConsolidation(ids);
      const rejectedSet = new Set(result.rejected);
      consolidation.removeItem((c) => rejectedSet.has(c.candidate_id));
      notifyApprovalChanged();
      if (result.skipped.length > 0) {
        showToast(
          `已拒绝 ${result.rejected.length} 条，${result.skipped.length} 条跳过`,
          false,
        );
      } else {
        showToast(`已拒绝全部 ${result.rejected.length} 条合并建议`, false);
      }
    } catch (err) {
      showToast("批量操作失败，请重试。", true);
      console.warn("bulk reject failed", err);
    }
  }

  const allLoaded =
    !memory.loading && !consolidation.loading && !compact.loading;
  const anyError =
    memory.error !== null ||
    consolidation.error !== null ||
    compact.error !== null;
  const allEmpty =
    memory.items.length === 0 &&
    consolidation.items.length === 0 &&
    compact.items.length === 0;
  const totalPending =
    memory.items.length + consolidation.items.length + compact.items.length;
  const loadingResolution = resolveResourcePageState({
    loading: true,
    hasContent: false,
    connected: true,
    error: null,
  });
  const emptyResolution = resolveResourcePageState({
    loading: false,
    hasContent: false,
    connected: true,
    error: null,
  });

  return (
    <main
      className="f149-approval-page"
      aria-labelledby="approval-center-title"
    >
      <section className="f149-approval-hero">
        <div className="f149-approval-hero-copy">
          <span className="f149-approval-eyebrow">
            {totalPending > 0 ? `${totalPending} 条待处理提议` : "你的知识整理"}
          </span>
          <div>
            <h1 id="approval-center-title">审批中心</h1>
            <p>
              确认新记忆，审阅记忆整合与规则精简。每项操作都会先保留清晰的上下文。
            </p>
          </div>
        </div>

        <ul className="f149-approval-overview" aria-label="候选概览">
          <li className="is-primary">
            <span>新记忆</span>
            <strong>{memory.items.length}</strong>
          </li>
          <li>
            <span>记忆整合</span>
            <strong>{consolidation.items.length}</strong>
          </li>
          <li>
            <span>行为精简</span>
            <strong>{compact.items.length}</strong>
          </li>
        </ul>

        {toast !== null && (
          <div
            className={`f149-approval-toast ${toast.isError ? "is-error" : "is-muted"}`}
            role="status"
            aria-live="polite"
          >
            <span>{toast.message}</span>
          </div>
        )}

        {!allLoaded && (
          <ApprovalState
            resolution={loadingResolution}
            title="正在加载审批提议"
            detail="正在整理三类候选，请稍候。"
          />
        )}

        {allLoaded && !anyError && allEmpty && (
          <ApprovalState
            resolution={emptyResolution}
            title="暂无待处理提议"
            detail="Agent 会持续整理记忆与规则，有新提议时会出现在这里。"
          />
        )}
      </section>

      {/* 新记忆（memory 候选） */}
      {(memory.items.length > 0 || memory.error !== null) && (
        <SourceSection
          title="新记忆"
          stateTitle="新记忆"
          subtitle="Agent 在对话中发现的信息片段，确认后将存入你的长期记忆。"
          count={memory.items.length}
          error={memory.error}
          onRetry={() => void memory.reload()}
          headerAction={
            memory.items.length > 0 && (
              <BatchRejectButton
                candidateIds={memory.items.map((c) => c.id)}
                onBulkDiscarded={handleMemoryBulk}
                onToast={showToast}
              />
            )
          }
        >
          <div className="f149-approval-list">
            {memory.items.map((candidate) => (
              <CandidateCard
                key={candidate.id}
                candidate={candidate}
                onRemove={handleMemoryRemove}
                onToast={showToast}
              />
            ))}
          </div>
        </SourceSection>
      )}

      {/* 记忆合并建议（F127） */}
      {(consolidation.items.length > 0 || consolidation.error !== null) && (
        <SourceSection
          title="记忆合并建议"
          stateTitle="记忆整合"
          subtitle="后台整理发现的相似记忆，接受后合并为一条更准确的记忆。"
          count={consolidation.items.length}
          error={consolidation.error}
          onRetry={() => void consolidation.reload()}
          headerAction={
            consolidation.items.length > 0 && (
              <button
                type="button"
                onClick={() => void handleConsolidationBulkReject()}
              >
                全部拒绝（{consolidation.items.length} 条）
              </button>
            )
          }
        >
          <div className="f149-approval-list">
            {consolidation.items.map((candidate) => (
              <ProposalCard
                key={candidate.candidate_id}
                typeLabel="记忆合并"
                summary={consolidationSummary(candidate)}
                createdAt={candidate.created_at}
                sensitive={candidate.is_sensitive}
                body={<p>{candidate.merged_content}</p>}
                details={
                  <div>
                    {candidate.rationale && <p>理由：{candidate.rationale}</p>}
                    {candidate.source_previews.length > 0 && (
                      <div>
                        <p>将被合并的记忆：</p>
                        <ul>
                          {candidate.source_previews.map((preview, idx) => (
                            <li key={idx}>{preview}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                }
                detailsLabel="高级 · 原因与来源"
                onAccept={() =>
                  runProposalAction({
                    action: () =>
                      acceptConsolidationCandidate(candidate.candidate_id),
                    remove: () =>
                      consolidation.removeItem(
                        (c) => c.candidate_id === candidate.candidate_id,
                      ),
                    successMessage: "已合并为一条记忆。",
                  })
                }
                onReject={() =>
                  runProposalAction({
                    action: () =>
                      rejectConsolidationCandidate(candidate.candidate_id),
                    remove: () =>
                      consolidation.removeItem(
                        (c) => c.candidate_id === candidate.candidate_id,
                      ),
                    successMessage: "已拒绝这条合并建议。",
                  })
                }
              />
            ))}
          </div>
        </SourceSection>
      )}

      {/* 规则精简建议（F111） */}
      {(compact.items.length > 0 || compact.error !== null) && (
        <SourceSection
          title="规则精简建议"
          stateTitle="行为精简"
          subtitle="后台发现行为规则里有重复表述，接受后文件会更新为精简版。"
          count={compact.items.length}
          error={compact.error}
          onRetry={() => void compact.reload()}
        >
          <div className="f149-approval-list">
            {compact.items.map((candidate) => {
              const diffRows = parseUnifiedDiff(candidate.diff);
              return (
                <ProposalCard
                  key={candidate.candidate_id}
                  typeLabel="规则精简"
                  summary={compactSummary(candidate)}
                  createdAt={candidate.created_at}
                  rootTestId="approval-compact-card"
                  acceptTestId="approval-compact-accept"
                  details={
                    <div>
                      {candidate.rationale && (
                        <p>理由：{candidate.rationale}</p>
                      )}
                      {diffRows.length > 0 ? (
                        <DiffLineList rows={diffRows} />
                      ) : (
                        <p>（内容没有行级变化）</p>
                      )}
                    </div>
                  }
                  detailsLabel="高级 · 原因与变更"
                  onAccept={() =>
                    runProposalAction({
                      action: () =>
                        acceptCompactCandidate(candidate.candidate_id),
                      remove: () =>
                        compact.removeItem(
                          (c) => c.candidate_id === candidate.candidate_id,
                        ),
                      successMessage: "已接受，规则文件已更新。",
                    })
                  }
                  onReject={() =>
                    runProposalAction({
                      action: () =>
                        rejectCompactCandidate(candidate.candidate_id),
                      remove: () =>
                        compact.removeItem(
                          (c) => c.candidate_id === candidate.candidate_id,
                        ),
                      successMessage: "已拒绝这条精简建议。",
                    })
                  }
                />
              );
            })}
          </div>
        </SourceSection>
      )}
    </main>
  );
}
