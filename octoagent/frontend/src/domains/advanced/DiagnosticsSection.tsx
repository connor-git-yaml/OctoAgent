import type { ControlPlaneEvent, DiagnosticsSummaryDocument } from "../../types";

interface RestoreDraft {
  bundle: string;
  targetRoot: string;
}

interface ImportDraft {
  sourceType: string;
  inputPath: string;
  mediaRoot: string;
  formatHint: string;
}

interface DiagnosticsSectionProps {
  diagnostics: DiagnosticsSummaryDocument;
  diagnosticTone: string;
  restoreDraft: RestoreDraft;
  importDraft: ImportDraft;
  events: ControlPlaneEvent[];
  busyActionId: string | null;
  onUpdateRestoreDraft: (key: keyof RestoreDraft, value: string) => void;
  onUpdateImportDraft: (key: keyof ImportDraft, value: string) => void;
  onPlanRestore: () => void;
  onDetectImportSource: () => void;
  onOpenImports: () => void;
  onRestartRuntime: () => void;
  formatDateTime: (value?: string | null) => string;
  statusTone: (status: string) => string;
}

export default function DiagnosticsSection({
  diagnostics,
  diagnosticTone,
  restoreDraft,
  importDraft,
  events,
  busyActionId,
  onUpdateRestoreDraft,
  onUpdateImportDraft,
  onPlanRestore,
  onDetectImportSource,
  onOpenImports,
  onRestartRuntime,
  formatDateTime,
  statusTone,
}: DiagnosticsSectionProps) {
  return (
    <section className="stack-section">
      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Runtime Diagnostics Console</p>
            <h3>{diagnostics.overall_status}</h3>
          </div>
          <span className={`tone-chip ${diagnosticTone}`}>
            {diagnostics.recent_failures.length} recent failures
          </span>
        </div>
        <div className="diagnostics-grid">
          {diagnostics.subsystems.map((item) => (
            <div key={item.subsystem_id} className="diagnostic-card">
              <strong>{item.label}</strong>
              <span className={`tone-chip ${statusTone(item.status)}`}>
                {item.status}
              </span>
              <p>{item.summary}</p>
              {item.detail_ref ? (
                <a href={item.detail_ref} target="_blank" rel="noreferrer">
                  深入查看
                </a>
              ) : null}
            </div>
          ))}
        </div>
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Restore / Import / Runtime</p>
            <h3>统一运维入口</h3>
          </div>
        </div>
        <div className="form-grid">
          <label>
            Restore Bundle
            <input
              value={restoreDraft.bundle}
              onChange={(event) => onUpdateRestoreDraft("bundle", event.target.value)}
              placeholder="/path/to/bundle.zip"
            />
          </label>
          <label>
            Restore Target Root
            <input
              value={restoreDraft.targetRoot}
              onChange={(event) => onUpdateRestoreDraft("targetRoot", event.target.value)}
              placeholder="/path/to/restore-root"
            />
          </label>
          <label>
            Import Path
            <input
              value={importDraft.inputPath}
              onChange={(event) => onUpdateImportDraft("inputPath", event.target.value)}
              placeholder="/path/to/chat.jsonl"
            />
          </label>
          <label>
            Source Type
            <input
              value={importDraft.sourceType}
              onChange={(event) => onUpdateImportDraft("sourceType", event.target.value)}
            />
          </label>
          <label>
            Media Root
            <input
              value={importDraft.mediaRoot}
              onChange={(event) => onUpdateImportDraft("mediaRoot", event.target.value)}
              placeholder="/path/to/media"
            />
          </label>
          <label>
            Format Hint
            <input
              value={importDraft.formatHint}
              onChange={(event) => onUpdateImportDraft("formatHint", event.target.value)}
            />
          </label>
        </div>
        <div className="action-row">
          <button
            type="button"
            className="secondary-button"
            onClick={onPlanRestore}
            disabled={busyActionId === "restore.plan"}
          >
            生成 Restore Plan
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={onDetectImportSource}
            disabled={busyActionId === "import.source.detect"}
          >
            识别 Import Source
          </button>
          <button type="button" className="ghost-button" onClick={onOpenImports}>
            打开 Import Workbench
          </button>
          <button
            type="button"
            className="ghost-button"
            onClick={onRestartRuntime}
            disabled={busyActionId === "runtime.restart"}
          >
            Runtime Restart
          </button>
        </div>
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Recent Control Events</p>
            <h3>{events.length}</h3>
          </div>
        </div>
        <div className="event-list">
          {events.map((event) => (
            <div
              key={`${event.event_type}-${event.request_id}-${event.occurred_at}`}
              className="event-item"
            >
              <div>
                <strong>{event.event_type}</strong>
                <p>{event.payload_summary}</p>
              </div>
              <small>{formatDateTime(event.occurred_at)}</small>
            </div>
          ))}
        </div>
      </article>
    </section>
  );
}
