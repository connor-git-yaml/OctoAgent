import { Link } from "react-router-dom";
import type { SkillGovernanceItem } from "../../types";

interface ProviderWorkspaceSectionProps {
  skillProviderCount: number;
  customSkillProviderCount: number;
  builtinSkillProviderCount: number;
  mcpProviderCount: number;
  enabledMcpProviderCount: number;
  healthyMcpProviderCount: number;
  selectedCapabilityCount: number;
  blockedCapabilityCount: number;
  unavailableCapabilityCount: number;
  capabilitySelection: Record<string, boolean>;
  capabilityItems: SkillGovernanceItem[];
  capabilityDirty: boolean;
  capabilitySaveBusy: boolean;
  onCapabilitySelectionChange: (itemId: string, selected: boolean) => void;
  onSaveCapabilitySelection: () => void;
  onResetCapabilitySelection: () => void;
  onOpenButler: () => void;
  onOpenTemplates: () => void;
}

function capabilityGroupLabel(item: SkillGovernanceItem): "Skills" | "MCP" {
  if (item.item_id.startsWith("mcp:") || item.source_kind.toLowerCase().includes("mcp")) {
    return "MCP";
  }
  return "Skills";
}

export default function ProviderWorkspaceSection({
  skillProviderCount,
  customSkillProviderCount,
  builtinSkillProviderCount,
  mcpProviderCount,
  enabledMcpProviderCount,
  healthyMcpProviderCount,
  selectedCapabilityCount,
  blockedCapabilityCount,
  unavailableCapabilityCount,
  capabilitySelection,
  capabilityItems,
  capabilityDirty,
  capabilitySaveBusy,
  onCapabilitySelectionChange,
  onSaveCapabilitySelection,
  onResetCapabilitySelection,
  onOpenButler,
  onOpenTemplates,
}: ProviderWorkspaceSectionProps) {
  const groupedItems = capabilityItems.reduce<Record<"Skills" | "MCP", SkillGovernanceItem[]>>(
    (groups, item) => {
      const group = capabilityGroupLabel(item);
      groups[group] = [...groups[group], item];
      return groups;
    },
    { Skills: [], MCP: [] }
  );

  return (
    <div className="wb-page">
      <section className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">Providers</p>
            <h3>先安装能力 Provider，再把它们绑定给 Butler 或 Worker</h3>
            <p className="wb-panel-copy">
              这里统一管理 Skill / MCP Provider 目录，以及当前项目的默认启用范围。更细的
              Agent 白名单，请回 Butler 或 Worker 模板页勾选。
            </p>
          </div>
          <div className="wb-inline-actions wb-inline-actions-wrap">
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={onOpenButler}
            >
              去 Butler 白名单
            </button>
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={onOpenTemplates}
            >
              去 Worker 模板白名单
            </button>
          </div>
        </div>

        <div className="wb-card-grid wb-card-grid-4">
          <article className="wb-card">
            <p className="wb-card-label">Skill Providers</p>
            <strong>{skillProviderCount}</strong>
            <span>
              自定义 {customSkillProviderCount} / 内置 {builtinSkillProviderCount}
            </span>
            <Link className="wb-button wb-button-tertiary wb-button-inline" to="/agents/skills">
              管理 Skill Providers
            </Link>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">MCP Providers</p>
            <strong>{mcpProviderCount}</strong>
            <span>
              已启用 {enabledMcpProviderCount} / 健康 {healthyMcpProviderCount}
            </span>
            <Link className="wb-button wb-button-tertiary wb-button-inline" to="/agents/mcp">
              管理 MCP Providers
            </Link>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">当前项目默认范围</p>
            <strong>{selectedCapabilityCount}</strong>
            <span>
              阻塞 {blockedCapabilityCount} / 不可用 {unavailableCapabilityCount}
            </span>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">绑定规则</p>
            <strong>先安装，再按 Agent 收窄</strong>
            <span>这里决定当前项目默认可见范围；Butler / Worker 模板还能继续收窄。</span>
          </article>
        </div>
      </section>

      <section className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">当前项目默认启用范围</p>
            <h3>不再放在 Settings 里，这里专门处理 Agent 相关能力边界</h3>
          </div>
          <div className="wb-inline-actions wb-inline-actions-wrap">
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={onSaveCapabilitySelection}
              disabled={capabilitySaveBusy || !capabilityDirty}
            >
              保存默认范围
            </button>
            <button
              type="button"
              className="wb-button wb-button-tertiary"
              onClick={onResetCapabilitySelection}
              disabled={capabilitySaveBusy || !capabilityDirty}
            >
              恢复已保存
            </button>
          </div>
        </div>

        <div className="wb-inline-banner is-muted">
          <strong>先看默认，再看单个 Agent</strong>
          <span>
            如果这里只保留最常用 Provider，Butler 和 Worker 模板里的白名单会更容易理解，也更不容易误绑。
          </span>
        </div>

        {capabilityItems.length === 0 ? (
          <div className="wb-empty-state">
            <strong>当前还没有可管理的 capability provider</strong>
            <span>先安装 Skill Provider 或 MCP Provider，再回这里决定当前项目默认启用哪些能力。</span>
          </div>
        ) : (
          <div className="wb-card-grid wb-card-grid-2">
            {(["Skills", "MCP"] as const).map((group) =>
              groupedItems[group].length === 0 ? null : (
                <div key={group} className="wb-note-stack">
                  <div className="wb-root-agent-column-head">
                    <strong>{group}</strong>
                    <span>{groupedItems[group].length} 个可选 Provider</span>
                  </div>
                  {groupedItems[group].map((item) => {
                    const selected = capabilitySelection[item.item_id] ?? item.selected;
                    return (
                      <label key={item.item_id} className="wb-note wb-capability-toggle">
                        <div>
                          <strong>{item.label}</strong>
                          <span>
                            {item.missing_requirements.length > 0
                              ? item.missing_requirements.join("；")
                              : item.install_hint || `${group} Provider 当前可用`}
                          </span>
                          <small>
                            默认 {item.enabled_by_default ? "开启" : "关闭"} · 当前 {item.availability}
                          </small>
                        </div>
                        <input
                          type="checkbox"
                          aria-label={`启用 ${item.label}`}
                          checked={selected}
                          onChange={(event) =>
                            onCapabilitySelectionChange(item.item_id, event.target.checked)
                          }
                        />
                      </label>
                    );
                  })}
                </div>
              )
            )}
          </div>
        )}
      </section>
    </div>
  );
}
