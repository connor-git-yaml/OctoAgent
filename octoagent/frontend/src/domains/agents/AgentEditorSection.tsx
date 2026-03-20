import type { AgentEditorDraft, AgentEditorReview, ApprovalOverrideDisplay, BehaviorFileInfo } from "./agentManagementData";

interface SelectOption {
  value: string;
  label: string;
}

/** 行为文件描述（固定 3 个 Agent 私有文件） */
const BEHAVIOR_FILE_META: Record<string, { title: string; description: string }> = {
  "IDENTITY.md": { title: "身份补充", description: "Agent 的名称和角色定位。" },
  "SOUL.md": { title: "表达风格", description: "个性、语气和协作方式。" },
  "HEARTBEAT.md": { title: "运行节奏", description: "内部运行节奏和自检策略。" },
};

const RUNTIME_KIND_OPTIONS: Array<{ value: string; label: string; description: string }> = [
  { value: "worker", label: "Worker", description: "适合日常任务拆分和持续推进。" },
  { value: "subagent", label: "Subagent", description: "适合短链路的专项协助。" },
  { value: "acp_runtime", label: "ACP Runtime", description: "适合需要工具 runtime 的执行场景。" },
  { value: "graph_agent", label: "Graph Agent", description: "适合有固定步骤的流程处理。" },
];

interface AgentEditorSectionProps {
  title: string;
  description: string;
  saveLabel: string;
  draft: AgentEditorDraft;
  review: AgentEditorReview | null;
  busy: boolean;
  projectOptions: SelectOption[];
  modelAliasOptions: string[];
  behaviorFiles: BehaviorFileInfo[];
  approvalOverrides: ApprovalOverrideDisplay[];
  approvalOverridesLoading: boolean;
  onChangeDraft: <Key extends keyof AgentEditorDraft>(key: Key, value: AgentEditorDraft[Key]) => void;
  onToggleRuntimeKind: (value: string) => void;
  onOpenBehaviorFile: (path: string, fileId: string) => void;
  onRevokeOverride: (agentRuntimeId: string, toolName: string) => void;
  onSave: () => void;
  onCancel: () => void;
  formatTokenLabel: (value: string) => string;
}

function formatOverrideTime(iso: string): string {
  try {
    const date = new Date(iso);
    return date.toLocaleDateString("zh-CN", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
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
  behaviorFiles,
  approvalOverrides,
  approvalOverridesLoading,
  onChangeDraft,
  onToggleRuntimeKind,
  onOpenBehaviorFile,
  onRevokeOverride,
  onSave,
  onCancel,
  formatTokenLabel,
}: AgentEditorSectionProps) {
  const modelAliasMissingFromOptions =
    draft.modelAlias.trim().length > 0 && !modelAliasOptions.includes(draft.modelAlias);
  const effectiveModelAliasOptions = modelAliasMissingFromOptions
    ? [draft.modelAlias, ...modelAliasOptions]
    : modelAliasOptions;

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
            disabled={busy}
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
          <small>这里决定的是这个 Agent 属于哪个项目的默认定义；已存在会话不会因此改绑。</small>
        </label>

      </div>

      <div className="wb-agent-editor-grid">
        <div className="wb-field">
          <span>使用的模型</span>
          <select
            value={draft.modelAlias}
            onChange={(event) => onChangeDraft("modelAlias", event.target.value)}
          >
            {effectiveModelAliasOptions.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
          {modelAliasMissingFromOptions ? (
            <small>当前值不在可用 alias 列表中。保存前需要切换到现有别名。</small>
          ) : (
            <small>这里列出的都是当前配置里真实可用的模型别名。</small>
          )}
        </div>
      </div>

      <div className="wb-agent-editor-grid">
        <label className="wb-field">
          <span>权限模式</span>
          <select
            value={draft.permissionPreset}
            onChange={(event) => onChangeDraft("permissionPreset", event.target.value)}
          >
            <option value="minimal">保守模式 — 只读操作直接执行，其余需要你确认</option>
            <option value="normal">标准模式 — 读写操作直接执行，不可逆操作需要确认</option>
            <option value="full">完全信任 — 所有操作直接执行，不需要确认</option>
          </select>
          <small>决定这个 Agent 执行工具时需不需要你先确认。大多数情况选标准模式就好。</small>
        </label>

        <div className="wb-field">
          <span>行为文件</span>
          <div className="wb-agent-check-grid">
            {(["IDENTITY.md", "SOUL.md", "HEARTBEAT.md"] as const).map((fileId) => {
              const meta = BEHAVIOR_FILE_META[fileId];
              const fileInfo = behaviorFiles.find((f) => f.file_id === fileId);
              return (
                <button
                  key={fileId}
                  type="button"
                  className="wb-agent-option-card"
                  style={{ cursor: "pointer", textAlign: "left", background: "none", border: "1px solid var(--wb-border, #ddd)" }}
                  onClick={() => {
                    if (fileInfo?.path) {
                      onOpenBehaviorFile(fileInfo.path, fileId);
                    }
                  }}
                  disabled={!fileInfo?.path}
                >
                  <div className="wb-agent-option-copy">
                    <strong>{fileId}</strong>
                    <p>{meta?.description ?? "Agent 私有行为配置。"}</p>
                    <small>
                      {meta?.title ?? fileId}
                      {" · "}
                      {fileInfo?.exists_on_disk ? "已创建" : "待创建"}
                    </small>
                  </div>
                </button>
              );
            })}
          </div>
          <small>点击文件名可以查看和编辑。这些文件定义了 Agent 的身份、风格和运行节奏。</small>
        </div>
      </div>

      {/* ── 已授权工具（审批覆盖） ── */}
      <div className="wb-note-stack">
        <div className="wb-panel-head">
          <div>
            <strong>已授权工具</strong>
            <p className="wb-panel-copy">
              Agent 运行过程中你选择"始终允许"的工具会出现在这里。如果想收回授权，点击撤销即可。
            </p>
          </div>
          <span className="wb-chip">{approvalOverrides.length} 条授权</span>
        </div>
        {approvalOverridesLoading ? (
          <div className="wb-note">
            <span>加载中…</span>
          </div>
        ) : approvalOverrides.length === 0 ? (
          <div className="wb-note">
            <span>还没有额外授权记录。Agent 工作时如果遇到需要确认的工具，你可以选择"始终允许"来跳过后续确认。</span>
          </div>
        ) : (
          <div className="wb-agent-tool-browser">
            {approvalOverrides.map((override) => (
              <div
                key={`${override.agentRuntimeId}:${override.toolName}`}
                className="wb-agent-tool-row"
              >
                <div>
                  <strong>{formatTokenLabel(override.toolName)}</strong>
                  <p>
                    始终允许 · 授权于 {formatOverrideTime(override.createdAt)}
                  </p>
                  <small>{override.toolName}</small>
                </div>
                <button
                  type="button"
                  className="wb-button wb-button-tertiary"
                  onClick={() => onRevokeOverride(override.agentRuntimeId, override.toolName)}
                >
                  撤销
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <details className="wb-agent-details">
        <summary>高级设置</summary>
        <div className="wb-form-grid wb-agent-editor-grid">

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

        </div>
      </details>
    </section>
  );
}
