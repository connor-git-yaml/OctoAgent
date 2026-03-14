import { useDeferredValue, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { AgentEditorDraft, AgentEditorReview, CapabilityProviderEntry, ToolOption } from "./agentManagementData";

interface SelectOption {
  value: string;
  label: string;
}

interface EditorToolGroupOption {
  value: string;
  missing: boolean;
}

interface EditorToolOption extends ToolOption {
  missing: boolean;
}

interface AgentEditorSectionProps {
  title: string;
  description: string;
  saveLabel: string;
  draft: AgentEditorDraft;
  review: AgentEditorReview | null;
  busy: boolean;
  projectOptions: SelectOption[];
  modelAliasOptions: string[];
  toolProfileOptions: SelectOption[];
  toolGroupOptions: string[];
  toolOptions: ToolOption[];
  policyOptions: SelectOption[];
  skillEntries: CapabilityProviderEntry[];
  mcpEntries: CapabilityProviderEntry[];
  metadataError: string;
  onChangeDraft: <Key extends keyof AgentEditorDraft>(key: Key, value: AgentEditorDraft[Key]) => void;
  onToggleDefaultToolGroup: (value: string) => void;
  onToggleSelectedTool: (value: string) => void;
  onToggleCapability: (selectionItemId: string, selected: boolean) => void;
  onToggleRuntimeKind: (value: string) => void;
  onTogglePolicyRef: (value: string) => void;
  onSave: () => void;
  onCancel: () => void;
  formatTokenLabel: (value: string) => string;
}

const RUNTIME_KIND_OPTIONS: Array<{ value: string; label: string; description: string }> = [
  { value: "worker", label: "Worker", description: "适合日常任务拆分和持续推进。" },
  { value: "subagent", label: "Subagent", description: "适合短链路的专项协助。" },
  { value: "acp_runtime", label: "ACP Runtime", description: "适合需要工具 runtime 的执行场景。" },
  { value: "graph_agent", label: "Graph Agent", description: "适合有固定步骤的流程处理。" },
];

function renderCapabilityGroup(
  title: string,
  manageTo: string,
  entries: CapabilityProviderEntry[],
  selection: Record<string, boolean>,
  onToggle: (selectionItemId: string, selected: boolean) => void
) {
  if (entries.length === 0) {
    return (
      <div className="wb-note">
        <strong>{title}</strong>
        <span>当前还没有可绑定的项目，先去对应管理页补上，再回来选择。</span>
      </div>
    );
  }

  return (
    <div className="wb-note-stack">
      <div className="wb-panel-head">
        <div>
          <strong>{title}</strong>
          <p className="wb-panel-copy">只保留这个 Agent 真正需要的能力，避免权限越来越乱。</p>
        </div>
        <Link className="wb-button wb-button-tertiary" to={manageTo}>
          去管理
        </Link>
      </div>
      <div className="wb-agent-check-grid">
        {entries.map((entry) => {
          const selected = selection[entry.selectionItemId] ?? entry.defaultSelected;
          return (
            <label key={entry.selectionItemId} className="wb-agent-option-card">
              <div className="wb-agent-option-copy">
                <strong>{entry.label}</strong>
                <p>{entry.description || entry.providerId}</p>
                <small>
                  默认{entry.defaultSelected ? "开启" : "关闭"} · 当前 {entry.availability}
                </small>
              </div>
              <input
                type="checkbox"
                checked={selected}
                disabled={!entry.enabled}
                onChange={(event) => onToggle(entry.selectionItemId, event.target.checked)}
              />
            </label>
          );
        })}
      </div>
    </div>
  );
}

export default function AgentEditorSection({
  title,
  description,
  saveLabel,
  draft,
  review,
  busy,
  projectOptions,
  modelAliasOptions,
  toolProfileOptions,
  toolGroupOptions,
  toolOptions,
  policyOptions,
  skillEntries,
  mcpEntries,
  metadataError,
  onChangeDraft,
  onToggleDefaultToolGroup,
  onToggleSelectedTool,
  onToggleCapability,
  onToggleRuntimeKind,
  onTogglePolicyRef,
  onSave,
  onCancel,
  formatTokenLabel,
}: AgentEditorSectionProps) {
  const [toolSearch, setToolSearch] = useState("");
  const deferredToolSearch = useDeferredValue(toolSearch);
  const visibleToolGroups = useMemo(() => {
    const knownGroups = new Set(toolGroupOptions);
    const staleGroups = draft.defaultToolGroups.filter((group) => !knownGroups.has(group));
    return [
      ...toolGroupOptions.map((group) => ({
        value: group,
        missing: false,
      })),
      ...staleGroups.map((group) => ({
        value: group,
        missing: true,
      })),
    ] satisfies EditorToolGroupOption[];
  }, [draft.defaultToolGroups, toolGroupOptions]);
  const visibleToolOptions = useMemo(() => {
    const knownTools = new Set(toolOptions.map((tool) => tool.toolName));
    const staleTools = draft.selectedTools
      .filter((toolName) => !knownTools.has(toolName))
      .map((toolName) => ({
        toolName,
        label: formatTokenLabel(toolName),
        toolGroup: "legacy",
        availability: "missing",
        missing: true,
      }));
    return [
      ...staleTools,
      ...toolOptions.map((tool) => ({
        ...tool,
        missing: false,
      })),
    ] satisfies EditorToolOption[];
  }, [draft.selectedTools, formatTokenLabel, toolOptions]);

  const filteredTools = useMemo(() => {
    const keyword = deferredToolSearch.trim().toLowerCase();
    if (!keyword) {
      return visibleToolOptions;
    }
    return visibleToolOptions.filter((tool) =>
      [tool.toolName, tool.label, tool.toolGroup, tool.availability]
        .join(" ")
        .toLowerCase()
        .includes(keyword)
    );
  }, [deferredToolSearch, visibleToolOptions]);

  return (
    <section className="wb-panel wb-agent-editor-shell">
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">编辑 Agent</p>
          <h3>{title}</h3>
          <p className="wb-panel-copy">{description}</p>
        </div>
        <div className="wb-inline-actions">
          <button
            type="button"
            className="wb-button wb-button-primary"
            disabled={busy || Boolean(metadataError)}
            onClick={onSave}
          >
            {saveLabel}
          </button>
          <button
            type="button"
            className="wb-button wb-button-tertiary"
            disabled={busy}
            onClick={onCancel}
          >
            先不改了
          </button>
        </div>
      </div>

      {review ? (
        <div
          className={`wb-inline-banner ${
            review.ready && review.canSave
              ? "is-muted"
              : review.blockingReasons.length > 0
                ? "is-error"
                : "is-warning"
          }`}
        >
          <strong>
            {review.ready && review.canSave
              ? "这个 Agent 可以保存。"
              : review.blockingReasons.length > 0
                ? "保存前还有问题要处理。"
                : "当前配置还需要再确认。"}
          </strong>
          <span>
            {review.nextActions[0] || review.blockingReasons[0] || review.warnings[0] || "没有额外提示。"}
          </span>
        </div>
      ) : null}

      <div className="wb-form-grid wb-agent-editor-grid">
        <label className="wb-field">
          <span>名称</span>
          <input
            type="text"
            value={draft.name}
            onChange={(event) => onChangeDraft("name", event.target.value)}
          />
          <small>用户在列表里会直接看到这个名字。</small>
        </label>

        <label className="wb-field">
          <span>所属项目</span>
          <select
            value={draft.projectId}
            disabled={projectOptions.length <= 1}
            onChange={(event) => onChangeDraft("projectId", event.target.value)}
          >
            {projectOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <small>切换顶部 Project 后，就会看到那个项目自己的 Agent 列表。</small>
        </label>

        <label className="wb-field wb-field-span-2">
          <span>Persona / 用途说明</span>
          <textarea
            className="wb-textarea-prose"
            value={draft.persona}
            onChange={(event) => onChangeDraft("persona", event.target.value)}
          />
          <small>直接说明它负责什么、什么时候该用它，会比写技术术语更容易理解。</small>
        </label>
      </div>

      <div className="wb-agent-editor-grid">
        <div className="wb-field">
          <span>使用的模型</span>
          <select
            value={draft.modelAlias}
            onChange={(event) => onChangeDraft("modelAlias", event.target.value)}
          >
            {modelAliasOptions.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>

        <div className="wb-field">
          <span>当前起点</span>
          <div className="wb-note">
            <strong>{formatTokenLabel(draft.baseArchetype)}</strong>
            <span>这个 Agent 现在主要沿用这类工作的默认起点。</span>
          </div>
        </div>
      </div>

      <div className="wb-note-stack">
        <div className="wb-panel-head">
          <div>
            <strong>默认工具组</strong>
            <p className="wb-panel-copy">常用的一组能力放在这里，进入任务时会优先带上。</p>
          </div>
          <span className="wb-chip">{draft.defaultToolGroups.length} 已选择</span>
        </div>
        <div className="wb-agent-check-grid">
          {visibleToolGroups.map((toolGroup) => (
            <label key={toolGroup.value} className="wb-agent-option-card">
              <div className="wb-agent-option-copy">
                <strong>{formatTokenLabel(toolGroup.value)}</strong>
                <p>
                  {toolGroup.missing
                    ? "这个工具组已经不在当前 catalog 里了，取消后不会自动恢复。"
                    : "适合作为默认随身工具组。"}
                </p>
              </div>
              <input
                type="checkbox"
                checked={draft.defaultToolGroups.includes(toolGroup.value)}
                onChange={() => onToggleDefaultToolGroup(toolGroup.value)}
              />
            </label>
          ))}
        </div>
      </div>

      <div className="wb-note-stack">
        <div className="wb-panel-head">
          <div>
            <strong>固定工具</strong>
            <p className="wb-panel-copy">只有你确定长期需要固定带上的工具，才建议放到这里。</p>
          </div>
          <span className="wb-chip">{draft.selectedTools.length} 已固定</span>
        </div>
        <label className="wb-field">
          <span>搜索工具</span>
          <input
            type="text"
            value={toolSearch}
            onChange={(event) => setToolSearch(event.target.value)}
            placeholder="按工具名、标签或工具组搜索"
          />
        </label>
        <div className="wb-agent-tool-browser">
          {filteredTools.map((tool) => (
            <label key={tool.toolName} className="wb-agent-tool-row">
              <div>
                <strong>{tool.label}</strong>
                <p>
                  {tool.missing
                    ? "这个工具已经不在当前 catalog 里了，取消后不会自动恢复。"
                    : `${formatTokenLabel(tool.toolGroup)} · ${tool.availability}`}
                </p>
                <small>{tool.toolName}</small>
              </div>
              <input
                type="checkbox"
                checked={draft.selectedTools.includes(tool.toolName)}
                onChange={() => onToggleSelectedTool(tool.toolName)}
              />
            </label>
          ))}
        </div>
      </div>

      <div className="wb-section-stack">
        {renderCapabilityGroup(
          "Skills 能力绑定",
          "/agents/skills",
          skillEntries,
          draft.capabilitySelection,
          onToggleCapability
        )}
        {renderCapabilityGroup(
          "MCP 能力绑定",
          "/agents/mcp",
          mcpEntries,
          draft.capabilitySelection,
          onToggleCapability
        )}
      </div>

      <details className="wb-agent-details">
        <summary>高级设置</summary>
        <div className="wb-form-grid wb-agent-editor-grid">
          <label className="wb-field">
            <span>工具权限范围</span>
            <select
              value={draft.toolProfile}
              onChange={(event) => onChangeDraft("toolProfile", event.target.value)}
            >
              {toolProfileOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <div className="wb-field">
            <span>运行形态</span>
            <div className="wb-agent-check-grid">
              {RUNTIME_KIND_OPTIONS.map((option) => (
                <label key={option.value} className="wb-agent-option-card">
                  <div className="wb-agent-option-copy">
                    <strong>{option.label}</strong>
                    <p>{option.description}</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={draft.runtimeKinds.includes(option.value)}
                    onChange={() => onToggleRuntimeKind(option.value)}
                  />
                </label>
              ))}
            </div>
          </div>

          <div className="wb-field wb-field-span-2">
            <span>策略参考</span>
            <div className="wb-agent-check-grid">
              {policyOptions.map((option) => (
                <label key={option.value} className="wb-agent-option-card">
                  <div className="wb-agent-option-copy">
                    <strong>{option.label}</strong>
                    <p>只在你需要明确限制策略时再勾选。</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={draft.policyRefs.includes(option.value)}
                    onChange={() => onTogglePolicyRef(option.value)}
                  />
                </label>
              ))}
            </div>
          </div>

          <label className="wb-field wb-field-span-2">
            <span>额外提醒</span>
            <textarea
              className="wb-textarea-prose"
              value={draft.instructionOverlaysText}
              onChange={(event) => onChangeDraft("instructionOverlaysText", event.target.value)}
            />
            <small>只有确实需要长期保留的工作习惯，再放到这里。</small>
          </label>

          <label className="wb-field">
            <span>内部标签</span>
            <textarea
              value={draft.tagsText}
              onChange={(event) => onChangeDraft("tagsText", event.target.value)}
            />
          </label>

          <label className="wb-field">
            <span>附加配置（JSON）</span>
            <textarea
              value={draft.metadataText}
              onChange={(event) => onChangeDraft("metadataText", event.target.value)}
            />
            <small>{metadataError || "留空即可；只有明确需要额外配置时再填写。"}</small>
          </label>
        </div>
      </details>
    </section>
  );
}
