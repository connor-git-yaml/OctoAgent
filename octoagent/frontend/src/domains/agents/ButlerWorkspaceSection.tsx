import type { ReactNode } from "react";
import { Link } from "react-router-dom";

interface SelectOption {
  value: string;
  label: string;
}

interface ProjectOption {
  project_id: string;
  name: string;
}

interface WorkspaceOption {
  workspace_id: string;
  name: string;
}

interface ScopeOption {
  value: string;
  label: string;
}

interface PrimaryAgentDraft {
  name: string;
  scope: string;
  projectId: string;
  workspaceId: string;
  personaSummary: string;
  modelAlias: string;
  toolProfile: string;
  llmMode: string;
  proxyUrl: string;
  primaryProvider: string;
  policyProfileId: string;
  memoryAccessPolicy: {
    allowVault: boolean;
    includeHistory: boolean;
  };
  memoryRecall: {
    postFilterMode: string;
    rerankMode: string;
    minKeywordOverlap: string;
    scopeLimit: string;
    perScopeLimit: string;
    maxHits: string;
  };
}

interface ReviewState {
  tone: "success" | "warning" | "danger";
  headline: string;
  summary: string;
  nextActions: string[];
}

interface SummaryState {
  primaryProjectName: string;
  primaryWorkspaceName: string;
  currentPolicyLabel: string;
  primaryToolProfileLabel: string;
  primaryModelAliasHint: string;
  recallPresetLabel: string;
  recallPresetDescription: string;
  selectedPrimaryCapabilityCount: number;
}

interface ContextState {
  contextProjectId: string;
  contextWorkspaceId: string;
  availableProjects: ProjectOption[];
  availableContextWorkspaces: WorkspaceOption[];
  canSwitchContext: boolean;
}

interface ProjectFilterStat {
  projectId: string;
  name: string;
  instanceCount: number;
  templateCount: number;
}

interface ButlerWorkspaceSectionProps {
  primaryDirty: boolean;
  butlerBusy: boolean;
  draft: PrimaryAgentDraft;
  scopeOptions: readonly ScopeOption[];
  primaryProjectOptions: SelectOption[];
  primaryWorkspaceOptions: SelectOption[];
  review: ReviewState;
  summary: SummaryState;
  context: ContextState;
  projectFilter: string;
  projectFilterStats: ProjectFilterStat[];
  totalWorkInstances: number;
  totalWorkTemplates: number;
  policyCards: ReactNode;
  modelAliasButtons: ReactNode;
  toolProfileButtons: ReactNode;
  recallPresetButtons: ReactNode;
  skillCapabilitySection: ReactNode;
  mcpCapabilitySection: ReactNode;
  onResetPrimary: () => void;
  onReviewPrimary: () => void;
  onApplyPrimary: () => void;
  onUpdatePrimaryField: (
    key:
      | "name"
      | "scope"
      | "workspaceId"
      | "personaSummary"
      | "modelAlias"
      | "toolProfile",
    value: string
  ) => void;
  onUpdatePrimaryProject: (projectId: string) => void;
  onUpdatePrimaryMemoryAccess: (key: "allowVault" | "includeHistory", value: boolean) => void;
  onUpdatePrimaryMemoryRecallField: (
    key:
      | "postFilterMode"
      | "rerankMode"
      | "minKeywordOverlap"
      | "scopeLimit"
      | "perScopeLimit"
      | "maxHits",
    value: string
  ) => void;
  onContextProjectChange: (projectId: string) => void;
  onContextWorkspaceChange: (workspaceId: string) => void;
  onSwitchProjectContext: () => void;
  onSetProjectFilter: (projectId: string) => void;
}

export default function ButlerWorkspaceSection({
  primaryDirty,
  butlerBusy,
  draft,
  scopeOptions,
  primaryProjectOptions,
  primaryWorkspaceOptions,
  review,
  summary,
  context,
  projectFilter,
  projectFilterStats,
  totalWorkInstances,
  totalWorkTemplates,
  policyCards,
  modelAliasButtons,
  toolProfileButtons,
  recallPresetButtons,
  skillCapabilitySection,
  mcpCapabilitySection,
  onResetPrimary,
  onReviewPrimary,
  onApplyPrimary,
  onUpdatePrimaryField,
  onUpdatePrimaryProject,
  onUpdatePrimaryMemoryAccess,
  onUpdatePrimaryMemoryRecallField,
  onContextProjectChange,
  onContextWorkspaceChange,
  onSwitchProjectContext,
  onSetProjectFilter,
}: ButlerWorkspaceSectionProps) {
  return (
    <div className="wb-agent-layout">
      <section className="wb-panel wb-butler-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">Butler</p>
            <h3>Butler 设置</h3>
            <p className="wb-panel-copy">这里决定 Butler 的名称、默认项目、审批方式和记忆边界。</p>
          </div>
          <div className="wb-inline-actions wb-inline-actions-wrap">
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={onResetPrimary}
              disabled={!primaryDirty || butlerBusy}
            >
              撤回改动
            </button>
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={onReviewPrimary}
              disabled={butlerBusy}
            >
              检查 Butler 变更
            </button>
            <button
              type="button"
              className="wb-button wb-button-primary"
              onClick={onApplyPrimary}
              disabled={!primaryDirty || butlerBusy}
            >
              保存 Butler 配置
            </button>
            <Link className="wb-button wb-button-tertiary" to="/settings">
              去 Settings 调连接
            </Link>
          </div>
        </div>

        <div className={`wb-inline-banner ${review.tone === "danger" ? "is-error" : "is-muted"}`}>
          <strong>{review.headline}</strong>
          <span>{review.summary}</span>
        </div>

        <div className="wb-stat-grid">
          <div className="wb-detail-block">
            <span className="wb-card-label">当前默认 Project</span>
            <strong>{summary.primaryProjectName}</strong>
            <p>{summary.primaryWorkspaceName}</p>
          </div>
          <div className="wb-detail-block">
            <span className="wb-card-label">审批强度</span>
            <strong>{summary.currentPolicyLabel}</strong>
            <p>{summary.primaryToolProfileLabel}</p>
          </div>
          <div className="wb-detail-block">
            <span className="wb-card-label">默认模型</span>
            <strong>{draft.modelAlias}</strong>
            <p>{summary.primaryModelAliasHint}</p>
          </div>
          <div className="wb-detail-block">
            <span className="wb-card-label">记忆策略</span>
            <strong>{summary.recallPresetLabel}</strong>
            <p>
              Vault {draft.memoryAccessPolicy.allowVault ? "可引用" : "关闭"} / 历史
              {draft.memoryAccessPolicy.includeHistory ? "已纳入" : "未纳入"}
            </p>
          </div>
        </div>

        <div className="wb-agent-form-grid">
          <label className="wb-field">
            <span>Butler 名称</span>
            <input
              type="text"
              value={draft.name}
              onChange={(event) => onUpdatePrimaryField("name", event.target.value)}
            />
          </label>
          <label className="wb-field">
            <span>默认生效范围</span>
            <select
              value={draft.scope}
              onChange={(event) => onUpdatePrimaryField("scope", event.target.value)}
            >
              {scopeOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="wb-field">
            <span>默认 Project</span>
            <select
              value={draft.projectId}
              onChange={(event) => onUpdatePrimaryProject(event.target.value)}
            >
              {primaryProjectOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="wb-field">
            <span>默认 Workspace</span>
            <select
              value={draft.workspaceId}
              onChange={(event) => onUpdatePrimaryField("workspaceId", event.target.value)}
            >
              {primaryWorkspaceOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="wb-field wb-field-span-2">
            <span>Persona（角色说明）</span>
            <small>这会影响 Butler 的语气、处理顺序和默认工作方式。</small>
            <textarea
              rows={4}
              className="wb-textarea-prose"
              value={draft.personaSummary}
              placeholder="例如：你负责先整理现状、提醒风险，再把任务交给合适的 Worker。"
              onChange={(event) => onUpdatePrimaryField("personaSummary", event.target.value)}
            />
          </label>
        </div>

        <div className="wb-butler-stack">
          <div>
            <p className="wb-card-label">审批与治理</p>
            <div className="wb-agent-choice-grid">{policyCards}</div>
          </div>

          <div>
            <p className="wb-card-label">记忆边界</p>
            <div className="wb-butler-memory-grid">
              <label className="wb-butler-toggle-card">
                <div>
                  <strong>允许带回 Vault 引用</strong>
                  <span>Butler 在 recall 时可以把受控 Vault 引用纳入候选，但仍受权限与审批约束。</span>
                </div>
                <input
                  type="checkbox"
                  checked={draft.memoryAccessPolicy.allowVault}
                  onChange={(event) =>
                    onUpdatePrimaryMemoryAccess("allowVault", event.target.checked)
                  }
                />
              </label>

              <label className="wb-butler-toggle-card">
                <div>
                  <strong>默认包含历史版本</strong>
                  <span>适合需要看演变过程的项目；如果你更看重简洁上下文，可以先关闭。</span>
                </div>
                <input
                  type="checkbox"
                  checked={draft.memoryAccessPolicy.includeHistory}
                  onChange={(event) =>
                    onUpdatePrimaryMemoryAccess("includeHistory", event.target.checked)
                  }
                />
              </label>
            </div>
          </div>

          <details className="wb-agent-details">
            <summary>展开高级配置</summary>
            <div className="wb-agent-option-stack">
              <div>
                <p className="wb-card-label">默认模型档位</p>
                <div className="wb-chip-row">{modelAliasButtons}</div>
                <p className="wb-inline-note">{summary.primaryModelAliasHint}</p>
              </div>

              <div>
                <p className="wb-card-label">工具范围</p>
                <div className="wb-chip-row">{toolProfileButtons}</div>
                <p className="wb-inline-note">
                  `standard` 适合大多数场景；只有明确需要更宽的工具面时再切 `privileged`。
                </p>
              </div>

              <div className="wb-root-agent-review-panel">
                <div className="wb-root-agent-card-head">
                  <div>
                    <p className="wb-card-label">Provider 白名单</p>
                    <strong>给 Butler 圈定默认可用的 Skills / MCP Providers</strong>
                  </div>
                  <span className="wb-chip">当前勾选 {summary.selectedPrimaryCapabilityCount}</span>
                </div>
                {skillCapabilitySection}
                {mcpCapabilitySection}
              </div>

              <div>
                <p className="wb-card-label">记忆召回预设</p>
                <div className="wb-chip-row">{recallPresetButtons}</div>
                <p className="wb-inline-note">
                  当前 {summary.recallPresetLabel}：{summary.recallPresetDescription}
                </p>
              </div>

              <div className="wb-agent-form-grid">
                <label className="wb-field">
                  <span>后过滤策略</span>
                  <select
                    value={draft.memoryRecall.postFilterMode}
                    onChange={(event) =>
                      onUpdatePrimaryMemoryRecallField("postFilterMode", event.target.value)
                    }
                  >
                    <option value="keyword_overlap">keyword_overlap · 保守过滤</option>
                    <option value="none">none · 不额外过滤</option>
                  </select>
                </label>
                <label className="wb-field">
                  <span>重排策略</span>
                  <select
                    value={draft.memoryRecall.rerankMode}
                    onChange={(event) =>
                      onUpdatePrimaryMemoryRecallField("rerankMode", event.target.value)
                    }
                  >
                    <option value="heuristic">heuristic · 主题优先</option>
                    <option value="none">none · 保留原始顺序</option>
                  </select>
                </label>
                <label className="wb-field">
                  <span>最低关键词重叠</span>
                  <input
                    type="text"
                    value={draft.memoryRecall.minKeywordOverlap}
                    onChange={(event) =>
                      onUpdatePrimaryMemoryRecallField("minKeywordOverlap", event.target.value)
                    }
                  />
                </label>
                <label className="wb-field">
                  <span>最多查几个 Scope</span>
                  <input
                    type="text"
                    value={draft.memoryRecall.scopeLimit}
                    onChange={(event) =>
                      onUpdatePrimaryMemoryRecallField("scopeLimit", event.target.value)
                    }
                  />
                </label>
                <label className="wb-field">
                  <span>每个 Scope 最多带回几条</span>
                  <input
                    type="text"
                    value={draft.memoryRecall.perScopeLimit}
                    onChange={(event) =>
                      onUpdatePrimaryMemoryRecallField("perScopeLimit", event.target.value)
                    }
                  />
                </label>
                <label className="wb-field">
                  <span>总命中上限</span>
                  <input
                    type="text"
                    value={draft.memoryRecall.maxHits}
                    onChange={(event) =>
                      onUpdatePrimaryMemoryRecallField("maxHits", event.target.value)
                    }
                  />
                </label>
              </div>

              <div className="wb-agent-advanced-grid">
                <div className="wb-detail-block">
                  <span className="wb-card-label">模型运行方式</span>
                  <strong>{draft.llmMode}</strong>
                  <p>{draft.primaryProvider}</p>
                </div>
                <div className="wb-detail-block">
                  <span className="wb-card-label">接入地址</span>
                  <strong>{draft.proxyUrl}</strong>
                  <p>只在排查连接问题时需要关注</p>
                </div>
              </div>
            </div>
          </details>
        </div>
      </section>

      <section className="wb-panel wb-butler-side">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">当前状态</p>
            <h3>切换查看范围并处理常见提醒</h3>
            <p className="wb-panel-copy">这里只影响你现在看到的 Project 和 Workspace，不会改默认配置。</p>
          </div>
          <div className="wb-inline-actions wb-inline-actions-wrap">
            <Link className="wb-button wb-button-secondary" to="/settings">
              去 Settings
            </Link>
            <Link className="wb-button wb-button-tertiary" to="/work">
              去看 Work
            </Link>
          </div>
        </div>

        <div className="wb-butler-side-stack">
          <article className="wb-butler-brief-card">
            <div className="wb-butler-brief-head">
              <div>
                <p className="wb-card-label">当前视角</p>
                <strong>切换你正在观察的 Project / Workspace</strong>
              </div>
              <span className="wb-chip">不会改 Butler 默认配置</span>
            </div>
            <div className="wb-inline-form">
              <label className="wb-field">
                <span>查看哪个 Project</span>
                <select
                  value={context.contextProjectId}
                  onChange={(event) => onContextProjectChange(event.target.value)}
                >
                  {context.availableProjects.map((project) => (
                    <option key={project.project_id} value={project.project_id}>
                      {project.name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="wb-field">
                <span>查看哪个 Workspace</span>
                <select
                  value={context.contextWorkspaceId}
                  onChange={(event) => onContextWorkspaceChange(event.target.value)}
                >
                  {context.availableContextWorkspaces.map((workspace) => (
                    <option key={workspace.workspace_id} value={workspace.workspace_id}>
                      {workspace.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="wb-inline-actions wb-inline-actions-wrap">
              <button
                type="button"
                className="wb-button wb-button-secondary"
                disabled={!context.canSwitchContext}
                onClick={onSwitchProjectContext}
              >
                切到这个视角
              </button>
            </div>
          </article>

          <article className="wb-butler-brief-card">
            <div className="wb-butler-brief-head">
              <div>
                <p className="wb-card-label">使用提示</p>
                <strong>先说目标，再看执行细节</strong>
              </div>
            </div>
            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>先说明结果</strong>
                <span>直接告诉 Butler 你要什么结果，比解释内部流程更有效。</span>
              </div>
              <div className="wb-note">
                <strong>高风险会先确认</strong>
                <span>涉及高风险动作时，界面会先停下来让你确认。</span>
              </div>
              <div className="wb-note">
                <strong>执行细节去 Work 看</strong>
                <span>想看谁在执行、卡在哪一步，直接去 Work 页面最清楚。</span>
              </div>
            </div>
          </article>

          <article className="wb-butler-brief-card">
            <div className="wb-butler-brief-head">
              <div>
                <p className="wb-card-label">保存前提醒</p>
                <strong>{review.headline}</strong>
              </div>
              <span className={`wb-status-pill is-${review.tone}`}>{review.tone}</span>
            </div>
            <div className="wb-note-stack">
              {review.nextActions.slice(0, 3).map((item) => (
                <div key={item} className="wb-note">
                  <strong>下一步</strong>
                  <span>{item}</span>
                </div>
              ))}
              {review.nextActions.length === 0 ? (
                <div className="wb-note">
                  <strong>当前状态</strong>
                  <span>没有额外提示，可以继续维护 Worker，或去 Settings 调整平台连接。</span>
                </div>
              ) : null}
            </div>
          </article>

          <div className="wb-agent-project-grid">
            <button
              type="button"
              className={`wb-agent-project-card ${projectFilter === "all" ? "is-active" : ""}`}
              onClick={() => onSetProjectFilter("all")}
            >
              <strong>全部 Project</strong>
              <span>
                实例 {totalWorkInstances} / 模板 {totalWorkTemplates}
              </span>
            </button>
            {projectFilterStats.map((project) => (
              <button
                key={project.projectId}
                type="button"
                className={`wb-agent-project-card ${projectFilter === project.projectId ? "is-active" : ""}`}
                onClick={() => onSetProjectFilter(project.projectId)}
              >
                <strong>{project.name}</strong>
                <span>
                  实例 {project.instanceCount} / 模板 {project.templateCount}
                </span>
              </button>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
