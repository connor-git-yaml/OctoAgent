import type { ConfigSchemaDocument } from "../../types";

interface ConfigCenterSectionProps {
  config: ConfigSchemaDocument;
  configDraft: string;
  busyActionId: string | null;
  onSaveConfig: () => void;
  onChangeDraft: (value: string) => void;
}

export default function ConfigCenterSection({
  config,
  configDraft,
  busyActionId,
  onSaveConfig,
  onChangeDraft,
}: ConfigCenterSectionProps) {
  return (
    <section className="stack-section">
      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Config Center</p>
            <h3>Schema + uiHints</h3>
          </div>
          <button
            type="button"
            className="primary-button"
            onClick={onSaveConfig}
            disabled={busyActionId === "config.apply"}
          >
            保存配置
          </button>
        </div>
        <div className="config-layout">
          <textarea
            className="config-editor"
            value={configDraft}
            onChange={(event) => onChangeDraft(event.target.value)}
            spellCheck={false}
          />
          <div className="config-hints">
            {Object.values(config.ui_hints)
              .sort((left, right) => left.order - right.order)
              .map((hint) => (
                <div key={hint.field_path} className="hint-card">
                  <strong>{hint.label || hint.field_path}</strong>
                  <p>{hint.description || hint.field_path}</p>
                  <small>
                    {hint.section} / {hint.widget}
                  </small>
                </div>
              ))}
          </div>
        </div>
        <div className="meta-grid">
          {config.validation_rules.map((rule) => (
            <span key={rule}>{rule}</span>
          ))}
        </div>
      </article>
    </section>
  );
}
