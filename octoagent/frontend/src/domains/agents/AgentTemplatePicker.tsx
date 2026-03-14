import type { BuiltinAgentTemplateViewModel } from "./agentManagementData";

interface AgentTemplatePickerProps {
  currentProjectName: string;
  templates: BuiltinAgentTemplateViewModel[];
  onPickTemplate: (templateId: string) => void;
  onPickBlank: () => void;
  onCancel: () => void;
}

export default function AgentTemplatePicker({
  currentProjectName,
  templates,
  onPickTemplate,
  onPickBlank,
  onCancel,
}: AgentTemplatePickerProps) {
  return (
    <section className="wb-panel wb-agent-editor-shell">
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">新建 Agent</p>
          <h3>先选一个起点，再补最少必要信息</h3>
          <p className="wb-panel-copy">
            这些模板只在创建时出现，不会混进你已经创建好的 Agent 列表。
          </p>
        </div>
        <div className="wb-inline-actions">
          <button type="button" className="wb-button wb-button-secondary" onClick={onPickBlank}>
            从空白开始
          </button>
          <button type="button" className="wb-button wb-button-tertiary" onClick={onCancel}>
            先不创建
          </button>
        </div>
      </div>

      <div className="wb-inline-banner is-muted">
        <strong>{currentProjectName}</strong>
        <span>新建后的 Agent 会先归到当前项目，后面聊天和工作分派都从这里挑选。</span>
      </div>

      <div className="wb-agent-template-grid">
        {templates.map((template) => (
          <button
            key={template.templateId}
            type="button"
            className="wb-agent-template-card"
            onClick={() => onPickTemplate(template.templateId)}
          >
            <div className="wb-agent-card-topline">
              <span className="wb-status-pill is-ready">模板</span>
              <span className="wb-chip">{template.modelAlias}</span>
            </div>
            <strong>{template.name}</strong>
            <p>{template.summary}</p>
            <div className="wb-chip-row">
              {template.defaultToolGroups.slice(0, 3).map((group) => (
                <span key={`${template.templateId}:${group}`} className="wb-chip">
                  {group}
                </span>
              ))}
            </div>
            <span className="wb-text-link">用这个模板开始</span>
          </button>
        ))}

        <button type="button" className="wb-agent-template-card" onClick={onPickBlank}>
          <div className="wb-agent-card-topline">
            <span className="wb-status-pill">空白</span>
          </div>
          <strong>空白 Agent</strong>
          <p>适合你已经很清楚要做什么，只想从最基础的默认值开始慢慢补。</p>
          <span className="wb-text-link">从空白开始</span>
        </button>
      </div>
    </section>
  );
}
