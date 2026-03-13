import type { OperatorActionKind, OperatorInboxItem } from "../../types";

interface OperatorSummary {
  total_pending?: number | null;
  approvals?: number | null;
  alerts?: number | null;
  retryable_failures?: number | null;
  pairing_requests?: number | null;
}

interface OperatorInboxSectionProps {
  summary: OperatorSummary | null | undefined;
  operatorItems: OperatorInboxItem[];
  onTriggerQuickAction: (item: OperatorInboxItem, kind: OperatorActionKind) => void;
  isQuickActionBusy: (item: OperatorInboxItem, kind: OperatorActionKind) => boolean;
  formatDateTime: (value?: string | null) => string;
  statusTone: (status: string) => string;
}

export default function OperatorInboxSection({
  summary,
  operatorItems,
  onTriggerQuickAction,
  isQuickActionBusy,
  formatDateTime,
  statusTone,
}: OperatorInboxSectionProps) {
  return (
    <section className="stack-section">
      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Operator Inbox</p>
            <h3>{summary?.total_pending ?? 0}</h3>
          </div>
          <span className="tone-chip neutral">Approvals {summary?.approvals ?? 0}</span>
        </div>
        <div className="meta-grid">
          <span>Alerts {summary?.alerts ?? 0}</span>
          <span>Retryables {summary?.retryable_failures ?? 0}</span>
          <span>Pairings {summary?.pairing_requests ?? 0}</span>
        </div>
      </article>

      {operatorItems.map((item) => (
        <article key={item.item_id} className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">{item.kind}</p>
              <h3>{item.title}</h3>
            </div>
            <span className={`tone-chip ${statusTone(item.state)}`}>{item.state}</span>
          </div>
          <p>{item.summary}</p>
          <div className="meta-grid">
            <span>Item: {item.item_id}</span>
            <span>Task: {item.task_id ?? "-"}</span>
            <span>Thread: {item.thread_id ?? "-"}</span>
            <span>Created: {formatDateTime(item.created_at)}</span>
          </div>
          <div className="action-row">
            {item.quick_actions.map((action) => (
              <button
                key={`${item.item_id}-${action.kind}`}
                type="button"
                className={action.style === "primary" ? "secondary-button" : "ghost-button"}
                onClick={() => onTriggerQuickAction(item, action.kind)}
                disabled={!action.enabled || isQuickActionBusy(item, action.kind)}
              >
                {action.label}
              </button>
            ))}
          </div>
        </article>
      ))}
    </section>
  );
}
