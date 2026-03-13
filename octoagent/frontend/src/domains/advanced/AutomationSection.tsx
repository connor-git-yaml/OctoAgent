import type { AutomationJobDocument } from "../../types";

interface AutomationDraft {
  name: string;
  actionId: string;
  scheduleKind: string;
  scheduleExpr: string;
  enabled: boolean;
}

interface AutomationSectionProps {
  automation: AutomationJobDocument;
  automationDraft: AutomationDraft;
  busyActionId: string | null;
  onUpdateAutomationDraft: (
    key: keyof AutomationDraft,
    value: string | boolean
  ) => void;
  onCreateAutomation: () => void;
  onRunAutomation: (jobId: string) => void;
  onPauseAutomation: (jobId: string) => void;
  onResumeAutomation: (jobId: string) => void;
  onDeleteAutomation: (jobId: string) => void;
  formatDateTime: (value?: string | null) => string;
  statusTone: (status: string) => string;
}

export default function AutomationSection({
  automation,
  automationDraft,
  busyActionId,
  onUpdateAutomationDraft,
  onCreateAutomation,
  onRunAutomation,
  onPauseAutomation,
  onResumeAutomation,
  onDeleteAutomation,
  formatDateTime,
  statusTone,
}: AutomationSectionProps) {
  return (
    <section className="stack-section">
      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Automation Create</p>
            <h3>调度新作业</h3>
          </div>
        </div>
        <div className="form-grid">
          <label>
            名称
            <input
              value={automationDraft.name}
              onChange={(event) => onUpdateAutomationDraft("name", event.target.value)}
            />
          </label>
          <label>
            Action ID
            <input
              value={automationDraft.actionId}
              onChange={(event) => onUpdateAutomationDraft("actionId", event.target.value)}
            />
          </label>
          <label>
            Schedule Kind
            <select
              value={automationDraft.scheduleKind}
              onChange={(event) =>
                onUpdateAutomationDraft("scheduleKind", event.target.value)
              }
            >
              <option value="interval">interval</option>
              <option value="cron">cron</option>
              <option value="once">once</option>
            </select>
          </label>
          <label>
            Schedule Expr
            <input
              value={automationDraft.scheduleExpr}
              onChange={(event) => onUpdateAutomationDraft("scheduleExpr", event.target.value)}
            />
          </label>
          <label className="checkbox-line">
            <input
              type="checkbox"
              checked={automationDraft.enabled}
              onChange={(event) => onUpdateAutomationDraft("enabled", event.target.checked)}
            />
            创建后立即启用
          </label>
        </div>
        <div className="action-row">
          <button
            type="button"
            className="primary-button"
            onClick={onCreateAutomation}
            disabled={busyActionId === "automation.create"}
          >
            创建作业
          </button>
        </div>
      </article>

      {automation.jobs.map((item) => (
        <article key={item.job.job_id} className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">{item.job.job_id}</p>
              <h3>{item.job.name}</h3>
            </div>
            <span className={`tone-chip ${statusTone(item.status)}`}>{item.status}</span>
          </div>
          <div className="meta-grid">
            <span>Action: {item.job.action_id}</span>
            <span>Schedule: {item.job.schedule_kind}</span>
            <span>Expr: {item.job.schedule_expr}</span>
            <span>Next: {formatDateTime(item.next_run_at)}</span>
          </div>
          {item.last_run ? (
            <p className="muted">
              Last Run: {item.last_run.status} / {formatDateTime(item.last_run.completed_at)}
            </p>
          ) : null}
          {item.degraded_reason ? <p className="warning-text">{item.degraded_reason}</p> : null}
          <div className="action-row">
            <button
              type="button"
              className="secondary-button"
              onClick={() => onRunAutomation(item.job.job_id)}
              disabled={busyActionId === "automation.run"}
            >
              Run Now
            </button>
            <button
              type="button"
              className="ghost-button"
              onClick={() => onPauseAutomation(item.job.job_id)}
              disabled={busyActionId === "automation.pause"}
            >
              Pause
            </button>
            <button
              type="button"
              className="ghost-button"
              onClick={() => onResumeAutomation(item.job.job_id)}
              disabled={busyActionId === "automation.resume"}
            >
              Resume
            </button>
            <button
              type="button"
              className="ghost-button"
              onClick={() => onDeleteAutomation(item.job.job_id)}
              disabled={busyActionId === "automation.delete"}
            >
              Delete
            </button>
          </div>
        </article>
      ))}
    </section>
  );
}
