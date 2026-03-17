import { useMemo } from "react";
import { useOperatorInbox } from "../hooks/useOperatorInbox";
import type { OperatorInboxItem } from "../types";
import { formatDateTimeSafe } from "../utils/formatTime";

function renderMeta(item: OperatorInboxItem): string {
  if (item.kind === "approval") {
    return item.metadata.tool_name || item.source_ref;
  }
  if (item.kind === "pairing_request") {
    return item.metadata.username || item.metadata.user_id || item.source_ref;
  }
  if (item.kind === "retryable_failure") {
    return item.metadata.error_type || item.source_ref;
  }
  return item.metadata.journal_state || item.source_ref;
}

export default function OperatorInboxPanel() {
  const { inbox, loading, error, busyItemId, lastResult, submitAction } = useOperatorInbox();
  const summary = inbox?.summary ?? null;
  const items = inbox?.items ?? [];

  const degradedText = useMemo(() => {
    const degraded = summary?.degraded_sources ?? [];
    if (degraded.length === 0) {
      return null;
    }
    return `部分数据源已降级: ${degraded.join(", ")}`;
  }, [summary?.degraded_sources]);

  return (
    <section className="card recovery-panel">
      <div className="recovery-header">
        <div>
          <h2>Operator Inbox</h2>
          <p className="muted">统一处理 approvals、alerts、retry 和 Telegram pairing</p>
        </div>
        <span className="status-badge RUNNING">
          {summary ? `${summary.total_pending} Pending` : error ? "Unavailable" : "--"}
        </span>
      </div>

      {loading ? <div className="muted">Loading operator inbox...</div> : null}
      {error ? <div className="error-inline">{error}</div> : null}
      {degradedText ? <div className="notice-inline">{degradedText}</div> : null}
      {lastResult ? (
        <div className="notice-inline">
          最近动作: {lastResult.message} ({lastResult.outcome})
        </div>
      ) : null}

      <div className="recovery-grid">
        <div className="recovery-item">
          <div className="muted">Approvals</div>
          <strong>{summary?.approvals ?? "-"}</strong>
        </div>
        <div className="recovery-item">
          <div className="muted">Alerts</div>
          <strong>{summary?.alerts ?? "-"}</strong>
        </div>
        <div className="recovery-item">
          <div className="muted">Retryables</div>
          <strong>{summary?.retryable_failures ?? "-"}</strong>
        </div>
        <div className="recovery-item">
          <div className="muted">Pairings</div>
          <strong>{summary?.pairing_requests ?? "-"}</strong>
        </div>
      </div>

      {summary !== null && !loading && !error && items.length === 0 ? (
        <div className="recovery-item">
          <div className="muted">当前没有待处理 operator 工作项。</div>
        </div>
      ) : null}

      {items.map((item) => (
        <div key={item.item_id} className="recovery-item" style={{ gap: "var(--space-sm)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-md)" }}>
            <div>
              <strong>{item.title}</strong>
              <div className="muted">{item.summary}</div>
            </div>
            <span className="status-badge RUNNING">{item.kind}</span>
          </div>
          <div className="muted">关联: {renderMeta(item)}</div>
          <div className="muted">
            创建于 {formatDateTimeSafe(item.created_at)}
            {item.expires_at ? ` · 过期 ${formatDateTimeSafe(item.expires_at)}` : ""}
          </div>
          {item.recent_action_result ? (
            <div className="notice-inline">
              最近结果: {item.recent_action_result.message} ({item.recent_action_result.source})
            </div>
          ) : null}
          <div className="recovery-actions">
            {item.quick_actions.map((action) => (
              <button
                key={`${item.item_id}-${action.kind}`}
                type="button"
                className={`action-button ${action.style === "primary" ? "" : "secondary"}`.trim()}
                disabled={!action.enabled || busyItemId === item.item_id}
                onClick={() => void submitAction(item, action.kind)}
              >
                {busyItemId === item.item_id ? "处理中..." : action.label}
              </button>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
