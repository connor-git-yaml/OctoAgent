import type {
  DiagnosticsSummaryDocument,
  OperatorActionKind,
  OperatorInboxItem,
} from "../../types";

interface ChannelManagementSectionProps {
  diagnostics: DiagnosticsSummaryDocument;
  pairingItems: OperatorInboxItem[];
  busyActionId: string | null;
  onTriggerQuickAction: (item: OperatorInboxItem, kind: OperatorActionKind) => void;
}

export default function ChannelManagementSection({
  diagnostics,
  pairingItems,
  busyActionId,
  onTriggerQuickAction,
}: ChannelManagementSectionProps) {
  const telegram =
    (diagnostics.channel_summary.telegram as Record<string, unknown> | undefined) ??
    undefined;

  return (
    <section className="stack-section">
      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Channel / Device Management</p>
            <h3>Telegram</h3>
          </div>
        </div>
        <div className="meta-grid">
          <span>Enabled {String(telegram?.enabled ?? false)}</span>
          <span>Mode {String(telegram?.mode ?? "-")}</span>
          <span>DM Policy {String(telegram?.dm_policy ?? "-")}</span>
          <span>Group Policy {String(telegram?.group_policy ?? "-")}</span>
          <span>Pending Pairings {String(telegram?.pending_pairings ?? 0)}</span>
          <span>Approved Users {String(telegram?.approved_users ?? 0)}</span>
        </div>
      </article>

      {pairingItems.map((item) => (
        <article key={item.item_id} className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">Pairing Request</p>
              <h3>{item.title}</h3>
            </div>
            <span className="tone-chip warning">{item.state}</span>
          </div>
          <p>{item.summary}</p>
          <div className="meta-grid">
            {Object.entries(item.metadata).map(([key, value]) => (
              <span key={key}>
                {key}: {value}
              </span>
            ))}
          </div>
          <div className="action-row">
            {item.quick_actions.map((action) => (
              <button
                key={`${item.item_id}-${action.kind}`}
                type="button"
                className="secondary-button"
                onClick={() => onTriggerQuickAction(item, action.kind)}
                disabled={!action.enabled || busyActionId != null}
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
