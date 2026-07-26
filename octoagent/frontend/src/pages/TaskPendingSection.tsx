import { Link } from "react-router-dom";
import { describeOperatorItemForUser } from "../domains/operator/userFacing";
import { useOperatorInbox } from "../hooks/useOperatorInbox";

function pendingSummary(options: {
  retryableFailures: number;
  approvals: number;
  alerts: number;
  pairingRequests: number;
}): string {
  const parts = [
    options.retryableFailures > 0
      ? `${options.retryableFailures} 条需要重试`
      : null,
    options.approvals > 0 ? `${options.approvals} 条需要确认` : null,
    options.alerts > 0 ? `${options.alerts} 条提醒` : null,
    options.pairingRequests > 0
      ? `${options.pairingRequests} 条连接确认`
      : null,
  ].filter((item): item is string => item !== null);
  return parts.join(" · ");
}

export default function TaskPendingSection() {
  const { inbox, loading, error, reload } = useOperatorInbox();
  const summary = inbox?.summary ?? null;
  const items = inbox?.items ?? [];
  const totalPending = summary?.total_pending ?? 0;

  if (loading) {
    return (
      <p
        className="f149-task-pending-quiet is-empty"
        role="status"
        aria-label="正在检查待处理事项"
      >
        正在检查待处理事项…
      </p>
    );
  }

  if (error) {
    return (
      <div className="f149-task-pending-quiet is-empty">
        <span>待处理事项暂时不可用。</span>
        <button type="button" onClick={() => void reload()}>
          重新检查
        </button>
      </div>
    );
  }

  if (totalPending === 0 || items.length === 0 || summary === null) {
    return <p className="f149-task-pending-quiet is-empty">暂无待处理事项</p>;
  }

  return (
    <details className="f149-task-pending">
      <summary>
        <span className="f149-task-pending-mark" aria-hidden="true">
          !
        </span>
        <span className="f149-task-pending-copy">
          <strong>{totalPending} 件待处理事项</strong>
          <span>
            {pendingSummary({
              retryableFailures: summary.retryable_failures,
              approvals: summary.approvals,
              alerts: summary.alerts,
              pairingRequests: summary.pairing_requests,
            })}
          </span>
        </span>
        <span className="f149-task-pending-action">进入处理</span>
      </summary>

      <div className="f149-task-pending-list">
        {items.map((item) => {
          const presentation = describeOperatorItemForUser(item);
          return (
            <article key={item.item_id} className="f149-task-pending-item">
              <span>{presentation.kindLabel}</span>
              <strong>{presentation.title}</strong>
              <p>{presentation.summary}</p>
              <small>{presentation.nextStep}</small>
              {presentation.taskLinkTo ? (
                <Link to={presentation.taskLinkTo}>打开对应任务</Link>
              ) : null}
            </article>
          );
        })}
      </div>
    </details>
  );
}
