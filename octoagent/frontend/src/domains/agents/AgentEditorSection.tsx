import type { AgentEditorDraft, AgentEditorReview, ApprovalOverrideDisplay, BehaviorFileInfo } from "./agentManagementData";

/** 行为文件描述（固定 3 个 Agent 私有文件） */
const BEHAVIOR_FILE_META: Record<string, { title: string; description: string }> = {
  "IDENTITY.md": { title: "身份", description: "名称和角色定位。" },
  "SOUL.md": { title: "风格", description: "语气和协作方式。" },
  "HEARTBEAT.md": { title: "节奏", description: "运行策略和自检。" },
};

interface AgentEditorSectionProps {
  title: string;
  description: string;
  saveLabel: string;
  draft: AgentEditorDraft;
  review: AgentEditorReview | null;
  busy: boolean;
  isCreate: boolean;
  modelAliasOptions: string[];
  behaviorFiles: BehaviorFileInfo[];
  approvalOverrides: ApprovalOverrideDisplay[];
  approvalOverridesLoading: boolean;
  onChangeDraft: <Key extends keyof AgentEditorDraft>(key: Key, value: AgentEditorDraft[Key]) => void;
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
  saveLabel,
  draft,
  review,
  busy,
  isCreate,
  modelAliasOptions,
  behaviorFiles,
  approvalOverrides,
  approvalOverridesLoading,
  onChangeDraft,
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
      {/* 顶部操作栏：标题 + 保存/取消 */}
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">编辑 Agent</p>
          <h3>{title}</h3>
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
            取消
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
              ? "可以保存。"
              : review.blockingReasons.length > 0
                ? "保存前需处理以下问题。"
                : "请确认当前配置。"}
          </strong>
          <span>
            {review.nextActions[0] || review.blockingReasons[0] || review.warnings[0] || ""}
          </span>
        </div>
      ) : null}

      {/* 名称 + 模型：同一行 */}
      <div className="wb-agent-editor-grid wb-agent-name-model-row">
        <label className="wb-field" style={{ flex: 1 }}>
          <span>名称</span>
          <input
            type="text"
            value={draft.name}
            onChange={(event) => onChangeDraft("name", event.target.value)}
          />
        </label>
        <div className="wb-field" style={{ flex: 0, minWidth: 160 }}>
          <span>模型</span>
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
        </div>
      </div>

      {/* 权限模式 + 行为文件：同一行 */}
      <div className="wb-agent-editor-grid">
        <label className="wb-field">
          <span>权限模式</span>
          <select
            value={draft.permissionPreset}
            onChange={(event) => onChangeDraft("permissionPreset", event.target.value)}
          >
            <option value="minimal">保守 — 只读直接执行，其余需确认</option>
            <option value="normal">标准 — 读写直接执行，不可逆需确认</option>
            <option value="full">完全信任 — 所有操作直接执行</option>
          </select>
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
                    <p>{meta?.description ?? "Agent 私有配置。"}</p>
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
        </div>
      </div>

      {/* 已授权工具 —— 仅已保存的 Agent 才显示 */}
      {!isCreate && (
        <div className="wb-note-stack">
          <div className="wb-panel-head">
            <div>
              <strong>已授权工具</strong>
              <p className="wb-panel-copy">
                运行中选择"始终允许"的工具会出现在这里，可随时撤销。
              </p>
            </div>
            <span className="wb-chip">{approvalOverrides.length} 条</span>
          </div>
          {approvalOverridesLoading ? (
            <div className="wb-note"><span>加载中…</span></div>
          ) : approvalOverrides.length === 0 ? (
            <div className="wb-note"><span>暂无授权记录。</span></div>
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
      )}

    </section>
  );
}
