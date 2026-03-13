import type { DelegationPlaneDocument, WorkProjectionItem } from "../../types";

interface DelegationSectionProps {
  delegationPlane: DelegationPlaneDocument;
  busyActionId: string | null;
  onRefreshDelegation: () => void;
  onCancelWork: (work: WorkProjectionItem) => void;
  onRetryWork: (work: WorkProjectionItem) => void;
  onSplitWork: (work: WorkProjectionItem) => void;
  onMergeWork: (work: WorkProjectionItem) => void;
  onEscalateWork: (work: WorkProjectionItem) => void;
  onExtractProfileFromRuntime: (work: WorkProjectionItem) => void;
  formatJson: (value: unknown) => string;
  formatWorkerType: (value: string) => string;
  describeFreshnessWorkPath: (work: WorkProjectionItem) => string;
  statusTone: (status: string) => string;
}

export default function DelegationSection({
  delegationPlane,
  busyActionId,
  onRefreshDelegation,
  onCancelWork,
  onRetryWork,
  onSplitWork,
  onMergeWork,
  onEscalateWork,
  onExtractProfileFromRuntime,
  formatJson,
  formatWorkerType,
  describeFreshnessWorkPath,
  statusTone,
}: DelegationSectionProps) {
  return (
    <section className="stack-section">
      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Delegation Overview</p>
            <h3>{delegationPlane.works.length}</h3>
          </div>
          <button
            type="button"
            className="secondary-button"
            onClick={onRefreshDelegation}
            disabled={busyActionId === "work.refresh"}
          >
            刷新委派面板
          </button>
        </div>
        <pre className="json-preview">{formatJson(delegationPlane.summary)}</pre>
      </article>

      {delegationPlane.works.map((work) => {
        const freshnessPath = describeFreshnessWorkPath(work);
        return (
          <article key={work.work_id} className="panel">
            <div className="panel-head">
              <div>
                <p className="eyebrow">{work.work_id}</p>
                <h3>{work.title || work.task_id}</h3>
              </div>
              <span className={`tone-chip ${statusTone(work.status)}`}>{work.status}</span>
            </div>
            <div className="meta-grid">
              <span>Worker {formatWorkerType(work.selected_worker_type || "-")}</span>
              <span>Target {work.target_kind || "-"}</span>
              <span>Runtime {work.runtime_id || "-"}</span>
              <span>Pipeline {work.pipeline_run_id || "-"}</span>
              <span>Children {work.child_work_count}</span>
              <span>Merge Ready {work.merge_ready ? "yes" : "no"}</span>
              <span>Agent {work.agent_profile_id || "-"}</span>
              <span>使用的模板 {work.requested_worker_profile_id || "回退到 archetype"}</span>
              <span>
                Revision {work.requested_worker_profile_version || "-"} / Snapshot{" "}
                {work.effective_worker_snapshot_id || "-"}
              </span>
              <span>工具分配 {work.tool_resolution_mode || "legacy"}</span>
            </div>
            <p>{work.route_reason || "无 route reason"}</p>
            <p className="muted">已选工具: {work.selected_tools.join(", ") || "none"}</p>
            {(work.blocked_tools?.length ?? 0) > 0 ? (
              <p className="muted">
                当前不可用工具:{" "}
                {work.blocked_tools
                  ?.map((tool) => `${tool.tool_name}(${tool.reason_code || tool.status})`)
                  .join(", ") || "none"}
              </p>
            ) : null}
            {freshnessPath ? <p className="muted">{freshnessPath}</p> : null}
            {work.runtime_summary && Object.keys(work.runtime_summary).length > 0 ? (
              <pre className="json-preview">{formatJson(work.runtime_summary)}</pre>
            ) : null}
            <div className="action-row">
              <button
                type="button"
                className="secondary-button"
                onClick={() => onCancelWork(work)}
                disabled={busyActionId === "work.cancel"}
              >
                取消 Work
              </button>
              <button
                type="button"
                className="ghost-button"
                onClick={() => onRetryWork(work)}
                disabled={busyActionId === "work.retry"}
              >
                重试
              </button>
              <button
                type="button"
                className="ghost-button"
                onClick={() => onSplitWork(work)}
                disabled={busyActionId === "work.split"}
              >
                拆分
              </button>
              <button
                type="button"
                className="ghost-button"
                onClick={() => onMergeWork(work)}
                disabled={busyActionId === "work.merge" || !work.merge_ready}
              >
                合并
              </button>
              <button
                type="button"
                className="ghost-button"
                onClick={() => onEscalateWork(work)}
                disabled={busyActionId === "work.escalate"}
              >
                升级
              </button>
              <button
                type="button"
                className="ghost-button"
                onClick={() => onExtractProfileFromRuntime(work)}
                disabled={busyActionId === "worker.extract_profile_from_runtime"}
              >
                提炼模板
              </button>
            </div>
          </article>
        );
      })}
    </section>
  );
}
