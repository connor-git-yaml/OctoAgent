import type {
  ImportRunDocument,
  ImportSourceDocument,
  ImportWorkbenchDocument,
} from "../../types";

interface ImportDraft {
  sourceType: string;
  inputPath: string;
  mediaRoot: string;
  formatHint: string;
}

interface ImportWorkbenchSectionProps {
  imports: ImportWorkbenchDocument;
  importDraft: ImportDraft;
  importBusy: boolean;
  selectedImportSourceId: string;
  selectedImportRunId: string;
  importSourceDetail: ImportSourceDocument | null;
  importRunDetail: ImportRunDocument | null;
  importMappingDraft: string;
  busyActionId: string | null;
  onUpdateImportDraft: (key: keyof ImportDraft, value: string) => void;
  onDetectSource: () => void;
  onRefreshWorkbench: () => void;
  onSelectSource: (sourceId: string) => void;
  onSelectRun: (runId: string) => void;
  onResumeImport: (resumeId: string) => void;
  onImportMappingDraftChange: (value: string) => void;
  onGenerateDefaultMapping: () => void;
  onSaveMapping: () => void;
  onPreviewImport: () => void;
  onRunImport: () => void;
  formatDateTime: (value?: string | null) => string;
  formatJson: (value: unknown) => string;
  statusTone: (status: string) => string;
}

export default function ImportWorkbenchSection({
  imports,
  importDraft,
  importBusy,
  selectedImportSourceId,
  selectedImportRunId,
  importSourceDetail,
  importRunDetail,
  importMappingDraft,
  busyActionId,
  onUpdateImportDraft,
  onDetectSource,
  onRefreshWorkbench,
  onSelectSource,
  onSelectRun,
  onResumeImport,
  onImportMappingDraftChange,
  onGenerateDefaultMapping,
  onSaveMapping,
  onPreviewImport,
  onRunImport,
  formatDateTime,
  formatJson,
  statusTone,
}: ImportWorkbenchSectionProps) {
  return (
    <section className="stack-section">
      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Import Workbench</p>
            <h3>{imports.summary.source_count}</h3>
          </div>
          <div className="chip-stack">
            <span className="tone-chip neutral">Runs {imports.summary.recent_run_count}</span>
            <span className="tone-chip warning">
              Resume {imports.summary.resume_available_count}
            </span>
          </div>
        </div>
        <div className="form-grid">
          <label>
            Source Type
            <input
              value={importDraft.sourceType}
              onChange={(event) => onUpdateImportDraft("sourceType", event.target.value)}
            />
          </label>
          <label>
            Input Path
            <input
              value={importDraft.inputPath}
              onChange={(event) => onUpdateImportDraft("inputPath", event.target.value)}
              placeholder="/path/to/wechat-export"
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
              placeholder="json / html / sqlite"
            />
          </label>
        </div>
        <div className="action-row">
          <button
            type="button"
            className="secondary-button"
            onClick={onDetectSource}
            disabled={busyActionId === "import.source.detect"}
          >
            Detect Source
          </button>
          <button
            type="button"
            className="ghost-button"
            onClick={onRefreshWorkbench}
            disabled={importBusy}
          >
            刷新 Workbench
          </button>
        </div>
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Detected Sources</p>
            <h3>{imports.sources.length}</h3>
          </div>
        </div>
        <div className="event-list">
          {imports.sources.map((item) => (
            <button
              key={item.source_id}
              type="button"
              className="event-item"
              onClick={() => onSelectSource(item.source_id)}
            >
              <div>
                <strong>{item.source_type}</strong>
                <p>{item.input_ref.input_path}</p>
              </div>
              <small>{item.detected_conversations.length} conversations</small>
            </button>
          ))}
        </div>
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Recent Runs / Resume</p>
            <h3>{imports.recent_runs.length}</h3>
          </div>
        </div>
        <div className="event-list">
          {imports.recent_runs.map((item) => (
            <button
              key={item.resource_id}
              type="button"
              className="event-item"
              onClick={() => onSelectRun(item.resource_id)}
            >
              <div>
                <strong>{item.status}</strong>
                <p>{item.source_id}</p>
              </div>
              <small>{formatDateTime(item.completed_at ?? item.updated_at)}</small>
            </button>
          ))}
          {imports.resume_entries.map((item) => (
            <div key={item.resume_id} className="event-item">
              <div>
                <strong>{item.resume_id}</strong>
                <p>{item.scope_id || item.source_id}</p>
              </div>
              <button
                type="button"
                className="ghost-button"
                onClick={() => onResumeImport(item.resume_id)}
              >
                Resume
              </button>
            </div>
          ))}
        </div>
      </article>

      {importSourceDetail ? (
        <article className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">Source Detail</p>
              <h3>{importSourceDetail.source_id}</h3>
            </div>
            <div className="chip-stack">
              <span className={`tone-chip ${statusTone(importSourceDetail.status)}`}>
                {importSourceDetail.status}
              </span>
              {selectedImportSourceId ? (
                <span className="tone-chip neutral">{selectedImportSourceId}</span>
              ) : null}
            </div>
          </div>
          <div className="session-list compact">
            {importSourceDetail.detected_conversations.map((conversation) => (
              <div key={conversation.conversation_key} className="session-card">
                <div className="session-meta">
                  <strong>{conversation.label || conversation.conversation_key}</strong>
                  <span>{conversation.message_count} messages</span>
                </div>
                <p>conversation_key: {conversation.conversation_key}</p>
                <p>attachments: {conversation.attachment_count}</p>
              </div>
            ))}
          </div>
          <label className="textarea-label">
            Mapping JSON
            <textarea
              rows={10}
              value={importMappingDraft}
              onChange={(event) => onImportMappingDraftChange(event.target.value)}
            />
          </label>
          <div className="action-row">
            <button type="button" className="ghost-button" onClick={onGenerateDefaultMapping}>
              生成默认 Mapping
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={onSaveMapping}
              disabled={busyActionId === "import.mapping.save"}
            >
              保存 Mapping
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={onPreviewImport}
              disabled={busyActionId === "import.preview"}
            >
              Preview
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={onRunImport}
              disabled={busyActionId === "import.run"}
            >
              Run Import
            </button>
          </div>
        </article>
      ) : null}

      {importRunDetail ? (
        <article className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">Run Detail</p>
              <h3>{importRunDetail.resource_id}</h3>
            </div>
            <div className="chip-stack">
              <span className={`tone-chip ${statusTone(importRunDetail.status)}`}>
                {importRunDetail.status}
              </span>
              {selectedImportRunId ? <span className="tone-chip neutral">{selectedImportRunId}</span> : null}
            </div>
          </div>
          <pre className="config-preview">{formatJson(importRunDetail.summary)}</pre>
          {importRunDetail.warnings.length ? (
            <div className="warning-list">
              {importRunDetail.warnings.map((item) => (
                <p key={item}>{item}</p>
              ))}
            </div>
          ) : null}
          {importRunDetail.errors.length ? (
            <div className="warning-list danger">
              {importRunDetail.errors.map((item) => (
                <p key={item}>{item}</p>
              ))}
            </div>
          ) : null}
          <div className="event-list">
            {importRunDetail.dedupe_details.slice(0, 10).map((item, index) => (
              <div
                key={`${index}-${String(item.message_key ?? item.reason ?? "detail")}`}
                className="event-item"
              >
                <div>
                  <strong>{String(item.reason ?? "detail")}</strong>
                  <p>{String(item.preview ?? item.source_cursor ?? "")}</p>
                </div>
              </div>
            ))}
          </div>
        </article>
      ) : null}
    </section>
  );
}
