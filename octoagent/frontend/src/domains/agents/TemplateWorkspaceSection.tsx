import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { ProjectOption, WorkerCapabilityProfile, WorkerProfileItem } from "../../types";

interface SelectOption {
  value: string;
  label: string;
}

interface RootAgentDraft {
  profileId: string;
  scope: string;
  projectId: string;
  name: string;
  summary: string;
  baseArchetype: string;
  modelAlias: string;
  toolProfile: string;
  defaultToolGroupsText: string;
  selectedToolsText: string;
  runtimeKindsText: string;
  policyRefsText: string;
  instructionOverlaysText: string;
  tagsText: string;
  capabilitySelection: Record<string, boolean>;
}

interface RootAgentReviewResult {
  ready: boolean;
  save_errors: string[];
  blocking_reasons: string[];
  warnings: string[];
  next_actions: string[];
  diff: {
    changed_fields: Array<{
      field: string;
    }>;
  };
}

interface TemplateWorkspaceSectionProps {
  rootAgentProfilesCount: number;
  builtinRootAgentProfiles: WorkerProfileItem[];
  customRootAgentProfiles: WorkerProfileItem[];
  rootAgentActiveWorkCount: number;
  rootAgentRunningWorkCount: number;
  rootAgentAttentionWorkCount: number;
  defaultRootAgentName: string;
  defaultRootAgentId: string;
  selectedRootAgentDisplayName: string;
  selectedRootAgentSummaryLabel: string;
  latestRootAgentUpdateLabel: string;
  latestRootAgentContextLabel: string;
  selectedRootAgentId: string;
  selectedRootAgentProfile: WorkerProfileItem | null;
  selectedRootAgentDisplayStatus: string;
  selectedRootAgentDisplayStatusLabel: string;
  selectedRootAgentIsDefault: boolean;
  rootAgentDraftDirty: boolean;
  selectedRootAgentOriginLabel: string;
  selectedRootAgentScopeLabel: string;
  selectedRootAgentIsBuiltin: boolean;
  selectedRootAgentEditable: boolean;
  selectedRootAgentCapabilityCount: number;
  rootAgentDraft: RootAgentDraft;
  capabilityWorkerProfiles: WorkerCapabilityProfile[];
  rootAgentProjectOptions: SelectOption[];
  modelAliasOptions: string[];
  toolProfileOptions: string[];
  rootAgentSuggestedToolGroups: string[];
  rootAgentSuggestedTools: string[];
  rootAgentSuggestedRuntimeKinds: string[];
  rootAgentSuggestedTags: string[];
  rootAgentReview: RootAgentReviewResult | null;
  rootAgentReviewDiff: Array<{ field: string }>;
  busyActionId: string | null;
  skillCapabilitySection: ReactNode;
  mcpCapabilitySection: ReactNode;
  runtimeRail: ReactNode;
  onCreateFreshRootAgent: () => void;
  onSelectRootAgentProfile: (profile: WorkerProfileItem | null) => void;
  onUpdateRootAgentDraft: (
    key:
      | "name"
      | "scope"
      | "baseArchetype"
      | "modelAlias"
      | "toolProfile"
      | "summary"
      | "defaultToolGroupsText"
      | "selectedToolsText"
      | "runtimeKindsText"
      | "policyRefsText"
      | "instructionOverlaysText"
      | "tagsText",
    value: string
  ) => void;
  onUpdateRootAgentProject: (projectId: string) => void;
  onApplyRootAgentArchetypeDefaults: () => void;
  onAppendRootAgentDraftValue: (
    field:
      | "defaultToolGroupsText"
      | "selectedToolsText"
      | "runtimeKindsText"
      | "tagsText",
    value: string
  ) => void;
  onReviewRootAgentDraft: () => void;
  onSaveRootAgentDraft: (publish: boolean) => void;
  onCreateRootAgentDraftFromTemplate: (profile: WorkerProfileItem) => void;
  onBindRootAgentDefault: () => void;
  onSwitchToRootAgentContext: (profile: WorkerProfileItem) => void;
  onArchiveRootAgent: () => void;
  formatWorkerTemplateName: (name: string, baseArchetype?: string | null) => string;
  formatWorkerProfileStatus: (status: string) => string;
  formatWorkerProfileOrigin: (originKind: string) => string;
  formatWorkerType: (workerType: string) => string;
  formatToolProfile: (toolProfile: string) => string;
  formatScope: (scope: string) => string;
  formatTokenLabel: (token: string) => string;
  formatToolToken: (toolName: string, toolLabels: Record<string, string>) => string;
  findProjectName: (projects: ProjectOption[], projectId: string) => string;
  availableProjects: ProjectOption[];
  toolLabelByName: Record<string, string>;
}

export default function TemplateWorkspaceSection({
  rootAgentProfilesCount,
  builtinRootAgentProfiles,
  customRootAgentProfiles,
  rootAgentActiveWorkCount,
  rootAgentRunningWorkCount,
  rootAgentAttentionWorkCount,
  defaultRootAgentName,
  defaultRootAgentId,
  selectedRootAgentDisplayName,
  selectedRootAgentSummaryLabel,
  latestRootAgentUpdateLabel,
  latestRootAgentContextLabel,
  selectedRootAgentId,
  selectedRootAgentProfile,
  selectedRootAgentDisplayStatus,
  selectedRootAgentDisplayStatusLabel,
  selectedRootAgentIsDefault,
  rootAgentDraftDirty,
  selectedRootAgentOriginLabel,
  selectedRootAgentScopeLabel,
  selectedRootAgentIsBuiltin,
  selectedRootAgentEditable,
  selectedRootAgentCapabilityCount,
  rootAgentDraft,
  capabilityWorkerProfiles,
  rootAgentProjectOptions,
  modelAliasOptions,
  toolProfileOptions,
  rootAgentSuggestedToolGroups,
  rootAgentSuggestedTools,
  rootAgentSuggestedRuntimeKinds,
  rootAgentSuggestedTags,
  rootAgentReview,
  rootAgentReviewDiff,
  busyActionId,
  skillCapabilitySection,
  mcpCapabilitySection,
  runtimeRail,
  onCreateFreshRootAgent,
  onSelectRootAgentProfile,
  onUpdateRootAgentDraft,
  onUpdateRootAgentProject,
  onApplyRootAgentArchetypeDefaults,
  onAppendRootAgentDraftValue,
  onReviewRootAgentDraft,
  onSaveRootAgentDraft,
  onCreateRootAgentDraftFromTemplate,
  onBindRootAgentDefault,
  onSwitchToRootAgentContext,
  onArchiveRootAgent,
  formatWorkerTemplateName,
  formatWorkerProfileStatus,
  formatWorkerProfileOrigin,
  formatWorkerType,
  formatToolProfile,
  formatScope,
  formatTokenLabel,
  formatToolToken,
  findProjectName,
  availableProjects,
  toolLabelByName,
}: TemplateWorkspaceSectionProps) {
  return (
    <section className="wb-panel wb-root-agent-hub">
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">Worker 模板</p>
          <h3>在这里维护 Butler 会调用的 Worker 模板，并查看它们最近做了什么</h3>
          <p className="wb-panel-copy">
            左侧选模板，中间改默认配置，右侧看当前运行状态和最近任务。需要追内部链路时，再去
            Advanced。
          </p>
        </div>
        <div className="wb-inline-actions wb-inline-actions-wrap">
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={onCreateFreshRootAgent}
          >
            新建 Worker 模板
          </button>
          <Link className="wb-button wb-button-secondary" to="/advanced">
            去 Control Plane
          </Link>
          <Link className="wb-button wb-button-tertiary" to="/work">
            去看 Work
          </Link>
        </div>
      </div>

      <div className="wb-inline-banner is-muted">
        <strong>当前按单模板模式工作</strong>
        <span>
          一个 Worker 模板通常对应你现在看到的默认工作方式。这里会同时展示静态配置、当前
          Project / Workspace、运行负载和版本记录，方便你决定何时保存、发布或提炼新模板。
        </span>
      </div>

      <div className="wb-root-agent-summary-grid">
        <div className="wb-detail-block">
          <span className="wb-card-label">模板总数</span>
          <strong>{rootAgentProfilesCount}</strong>
          <p>内置 {builtinRootAgentProfiles.length} / 自定义 {customRootAgentProfiles.length}</p>
        </div>
        <div className="wb-detail-block">
          <span className="wb-card-label">激活中的 Work</span>
          <strong>{rootAgentActiveWorkCount}</strong>
          <p>运行中 {rootAgentRunningWorkCount} / 需关注 {rootAgentAttentionWorkCount}</p>
        </div>
        <div className="wb-detail-block">
          <span className="wb-card-label">默认 Worker 模板</span>
          <strong>{defaultRootAgentName || "还没有默认值"}</strong>
          <p>{defaultRootAgentId || "发布并绑定后会显示在这里"}</p>
        </div>
        <div className="wb-detail-block">
          <span className="wb-card-label">当前选中</span>
          <strong>{selectedRootAgentDisplayName || "新建草稿"}</strong>
          <p>{selectedRootAgentSummaryLabel}</p>
        </div>
        <div className="wb-detail-block">
          <span className="wb-card-label">最近更新时间</span>
          <strong>{latestRootAgentUpdateLabel}</strong>
          <p>{latestRootAgentContextLabel}</p>
        </div>
      </div>

      <div className="wb-root-agent-layout">
        <aside className="wb-root-agent-browser">
          <article className="wb-root-agent-browser-panel">
            <div className="wb-root-agent-browser-head">
              <div>
                <p className="wb-card-label">内置模板</p>
                <strong>先选一个起点，再决定是否另存为自己的 Worker 模板</strong>
              </div>
              <span className="wb-status-pill is-ready">{builtinRootAgentProfiles.length}</span>
            </div>
            <div className="wb-root-agent-library-section">
              {builtinRootAgentProfiles.map((profile) => (
                <button
                  key={profile.profile_id}
                  type="button"
                  className={`wb-root-agent-library-item ${
                    selectedRootAgentId === profile.profile_id ? "is-active" : ""
                  }`}
                  onClick={() => onSelectRootAgentProfile(profile)}
                >
                  <div className="wb-root-agent-library-head">
                    <div>
                      <strong>
                        {formatWorkerTemplateName(
                          profile.name,
                          profile.static_config.base_archetype
                        )}
                      </strong>
                      <span>{profile.summary || "系统 archetype 默认配置。"}</span>
                    </div>
                    <span className={`wb-status-pill is-${profile.status}`}>
                      {formatWorkerProfileStatus(profile.status)}
                    </span>
                  </div>
                  <div className="wb-chip-row">
                    <span className="wb-chip">{formatWorkerProfileOrigin(profile.origin_kind)}</span>
                    <span className="wb-chip">
                      {formatWorkerType(profile.static_config.base_archetype)}
                    </span>
                    <span className="wb-chip">
                      {formatToolProfile(profile.static_config.tool_profile)}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          </article>

          <article className="wb-root-agent-browser-panel">
            <div className="wb-root-agent-browser-head">
              <div>
                <p className="wb-card-label">已保存模板</p>
                <strong>你已经保存过的 Worker 模板</strong>
              </div>
              <span className="wb-status-pill is-active">{customRootAgentProfiles.length}</span>
            </div>
            {customRootAgentProfiles.length === 0 ? (
              <div className="wb-empty-state">
                <strong>还没有自定义 Worker 模板</strong>
                <span>从左侧选一个内置模板，或直接点“新建 Worker 模板”。</span>
              </div>
            ) : (
              <div className="wb-root-agent-library-section">
                {customRootAgentProfiles.map((profile) => {
                  const isSelected = selectedRootAgentId === profile.profile_id;
                  const hasAttention = profile.dynamic_context.attention_work_count > 0;
                  return (
                    <button
                      key={profile.profile_id}
                      type="button"
                      className={`wb-root-agent-library-item ${isSelected ? "is-active" : ""}`}
                      onClick={() => onSelectRootAgentProfile(profile)}
                    >
                      <div className="wb-root-agent-library-head">
                        <div>
                          <strong>
                            {formatWorkerTemplateName(
                              profile.name,
                              profile.static_config.base_archetype
                            )}
                          </strong>
                          <span>{profile.summary || "当前 profile 没有额外摘要。"}</span>
                        </div>
                        <span
                          className={`wb-status-pill is-${hasAttention ? "warning" : profile.status}`}
                        >
                          {hasAttention
                            ? `提醒 ${profile.dynamic_context.attention_work_count}`
                            : formatWorkerProfileStatus(profile.status)}
                        </span>
                      </div>
                      <div className="wb-root-agent-library-meta">
                        <span>
                          {findProjectName(
                            availableProjects,
                            profile.project_id || ""
                          )}
                        </span>
                        <span>
                          版本 {profile.active_revision || 0}
                          {profile.draft_revision > profile.active_revision
                            ? ` / 草稿 ${profile.draft_revision}`
                            : ""}
                        </span>
                      </div>
                      <div className="wb-chip-row">
                        <span className="wb-chip">{formatWorkerProfileOrigin(profile.origin_kind)}</span>
                        <span className="wb-chip">{formatScope(profile.scope)}</span>
                        <span className="wb-chip">
                          {formatWorkerType(profile.static_config.base_archetype)}
                        </span>
                        {profile.profile_id === defaultRootAgentId ? (
                          <span className="wb-chip is-success">聊天默认</span>
                        ) : null}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </article>
        </aside>

        <section className="wb-root-agent-studio">
          <article className="wb-root-agent-studio-panel">
            <div className="wb-root-agent-card-head">
              <div>
                <p className="wb-card-label">模板编辑</p>
                <h3>{selectedRootAgentDisplayName || "新的 Worker 模板草稿"}</h3>
                <p className="wb-inline-note">
                  这里改的是默认配置。右侧会同步显示当前运行状态、版本记录和最近任务。
                </p>
              </div>
              <div className="wb-chip-row">
                <span className={`wb-status-pill is-${selectedRootAgentDisplayStatus}`}>
                  {selectedRootAgentDisplayStatusLabel}
                </span>
                {selectedRootAgentIsDefault ? (
                  <span className="wb-chip is-success">当前聊天默认</span>
                ) : null}
                {rootAgentDraftDirty ? <span className="wb-chip is-warning">未保存变更</span> : null}
                <span className="wb-chip">{selectedRootAgentOriginLabel}</span>
                <span className="wb-chip">{selectedRootAgentScopeLabel}</span>
              </div>
            </div>

            {selectedRootAgentIsBuiltin ? (
              <div className="wb-inline-banner is-muted">
                <strong>当前选中的是内置模板</strong>
                <span>
                  你可以直接修改并保存，系统会自动生成新的 Worker 模板；也可以先点击“复制成新模板”保留原模板不动。
                </span>
              </div>
            ) : null}

            <div className="wb-root-agent-studio-form">
              <label className="wb-field">
                <span>名称</span>
                <input
                  type="text"
                  value={rootAgentDraft.name}
                  onChange={(event) => onUpdateRootAgentDraft("name", event.target.value)}
                  placeholder="例如：家庭 NAS 管家"
                />
              </label>
              <label className="wb-field">
                <span>作用范围</span>
                <select
                  value={rootAgentDraft.scope}
                  onChange={(event) => onUpdateRootAgentDraft("scope", event.target.value)}
                >
                  <option value="project">项目级默认</option>
                  <option value="system">系统级默认</option>
                </select>
              </label>
              <label className="wb-field">
                <span>所属项目</span>
                <select
                  value={rootAgentDraft.projectId}
                  disabled={rootAgentDraft.scope !== "project"}
                  onChange={(event) => onUpdateRootAgentProject(event.target.value)}
                >
                  {rootAgentProjectOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="wb-field">
                <span>模板起点</span>
                <select
                  value={rootAgentDraft.baseArchetype}
                  onChange={(event) => onUpdateRootAgentDraft("baseArchetype", event.target.value)}
                >
                  {capabilityWorkerProfiles.map((profile) => (
                    <option key={profile.worker_type} value={profile.worker_type}>
                      {formatWorkerType(profile.worker_type)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="wb-field">
                <span>模型别名</span>
                <select
                  value={rootAgentDraft.modelAlias}
                  onChange={(event) => onUpdateRootAgentDraft("modelAlias", event.target.value)}
                >
                  {modelAliasOptions.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </label>
              <label className="wb-field">
                <span>工具边界</span>
                <select
                  value={rootAgentDraft.toolProfile}
                  onChange={(event) => onUpdateRootAgentDraft("toolProfile", event.target.value)}
                >
                  {toolProfileOptions.map((option) => (
                    <option key={option} value={option}>
                      {formatToolProfile(option)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="wb-field wb-field-span-2">
                <span>摘要</span>
                <textarea
                  className="wb-textarea-prose"
                  value={rootAgentDraft.summary}
                  onChange={(event) => onUpdateRootAgentDraft("summary", event.target.value)}
                  placeholder="说明它长期负责什么、边界在哪里、什么时候应该叫它出场。"
                />
              </label>
              <label className="wb-field">
                <span>默认工具组</span>
                <textarea
                  value={rootAgentDraft.defaultToolGroupsText}
                  onChange={(event) =>
                    onUpdateRootAgentDraft("defaultToolGroupsText", event.target.value)
                  }
                  placeholder="每行一个 tool_group，例如 web、memory、project"
                />
                <small>这里写 tool group，不是具体 tool name。</small>
              </label>
              <label className="wb-field">
                <span>固定工具</span>
                <textarea
                  value={rootAgentDraft.selectedToolsText}
                  onChange={(event) =>
                    onUpdateRootAgentDraft("selectedToolsText", event.target.value)
                  }
                  placeholder="每行一个 tool，例如 web.search"
                />
                <small>pin 住 1-3 个关键工具，运行行为会稳定很多。</small>
              </label>
              <label className="wb-field">
                <span>运行形态</span>
                <textarea
                  value={rootAgentDraft.runtimeKindsText}
                  onChange={(event) =>
                    onUpdateRootAgentDraft("runtimeKindsText", event.target.value)
                  }
                  placeholder="例如 worker、subagent"
                />
              </label>
              <label className="wb-field">
                <span>策略引用</span>
                <textarea
                  value={rootAgentDraft.policyRefsText}
                  onChange={(event) =>
                    onUpdateRootAgentDraft("policyRefsText", event.target.value)
                  }
                  placeholder="例如 default"
                />
              </label>
              <label className="wb-field">
                <span>补充指令</span>
                <textarea
                  value={rootAgentDraft.instructionOverlaysText}
                  onChange={(event) =>
                    onUpdateRootAgentDraft("instructionOverlaysText", event.target.value)
                  }
                  placeholder="每行一句补充指令，例如：优先解释风险，不直接执行高危操作。"
                />
              </label>
              <label className="wb-field">
                <span>Tags</span>
                <textarea
                  value={rootAgentDraft.tagsText}
                  onChange={(event) => onUpdateRootAgentDraft("tagsText", event.target.value)}
                  placeholder="每行一个标签，例如 nas、router、finance"
                />
              </label>
            </div>

            <div className="wb-root-agent-token-grid">
              <div className="wb-root-agent-token-card">
                <div className="wb-root-agent-column-head">
                  <strong>推荐工具组</strong>
                  <button
                    type="button"
                    className="wb-button wb-button-tertiary"
                    onClick={onApplyRootAgentArchetypeDefaults}
                  >
                    套用 archetype 默认值
                  </button>
                </div>
                <div className="wb-chip-row">
                  {rootAgentSuggestedToolGroups.map((toolGroup) => (
                    <button
                      key={toolGroup}
                      type="button"
                      className="wb-chip-button"
                      onClick={() => onAppendRootAgentDraftValue("defaultToolGroupsText", toolGroup)}
                    >
                      {formatTokenLabel(toolGroup)}
                    </button>
                  ))}
                </div>
              </div>
              <div className="wb-root-agent-token-card">
                <div className="wb-root-agent-column-head">
                  <strong>推荐固定工具</strong>
                  <span>优先 pin 关键能力</span>
                </div>
                <div className="wb-chip-row">
                  {rootAgentSuggestedTools.map((tool) => (
                    <button
                      key={tool}
                      type="button"
                      className="wb-chip-button"
                      onClick={() => onAppendRootAgentDraftValue("selectedToolsText", tool)}
                    >
                      {formatToolToken(tool, toolLabelByName)}
                    </button>
                  ))}
                </div>
              </div>
              <div className="wb-root-agent-token-card">
                <div className="wb-root-agent-column-head">
                  <strong>运行形态</strong>
                  <span>和 Agent Zero / OpenClaw 一样，先把运行边界说清楚</span>
                </div>
                <div className="wb-chip-row">
                  {rootAgentSuggestedRuntimeKinds.map((kind) => (
                    <button
                      key={kind}
                      type="button"
                      className="wb-chip-button"
                      onClick={() => onAppendRootAgentDraftValue("runtimeKindsText", kind)}
                    >
                      {formatTokenLabel(kind)}
                    </button>
                  ))}
                </div>
              </div>
              <div className="wb-root-agent-token-card">
                <div className="wb-root-agent-column-head">
                  <strong>标签建议</strong>
                  <span>标签帮助 Butler 更快找到合适的 Worker 模板</span>
                </div>
                <div className="wb-chip-row">
                  {rootAgentSuggestedTags.map((tag) => (
                    <button
                      key={tag}
                      type="button"
                      className="wb-chip-button"
                      onClick={() => onAppendRootAgentDraftValue("tagsText", tag)}
                    >
                      {formatTokenLabel(tag)}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="wb-root-agent-review-panel">
              <div className="wb-root-agent-card-head">
                <div>
                  <p className="wb-card-label">Provider 白名单</p>
                  <strong>给这个 Worker 模板圈定允许调用的能力 Provider</strong>
                </div>
                <span className="wb-chip">当前勾选 {selectedRootAgentCapabilityCount}</span>
              </div>
              {skillCapabilitySection}
              {mcpCapabilitySection}
            </div>

            {rootAgentReview ? (
              <div className="wb-root-agent-review-panel">
                <div className="wb-root-agent-card-head">
                  <div>
                    <p className="wb-card-label">检查结果</p>
                    <strong>
                      {rootAgentReview.ready ? "当前草稿可以保存或发布" : "先处理阻塞项，再继续发布"}
                    </strong>
                  </div>
                  <span
                    className={`wb-status-pill is-${
                      rootAgentReview.ready
                        ? "success"
                        : rootAgentReview.save_errors.length > 0
                          ? "danger"
                          : "warning"
                    }`}
                  >
                    {rootAgentReview.ready ? "通过" : "待处理"}
                  </span>
                </div>
                <div className="wb-root-agent-review-grid">
                  <div className="wb-note-stack">
                    {rootAgentReview.save_errors.map((item) => (
                      <div key={item} className="wb-note">
                        <strong>保存失败</strong>
                        <span>{item}</span>
                      </div>
                    ))}
                    {rootAgentReview.blocking_reasons.map((item) => (
                      <div key={item} className="wb-note">
                        <strong>阻塞项</strong>
                        <span>{item}</span>
                      </div>
                    ))}
                    {rootAgentReview.warnings.map((item) => (
                      <div key={item} className="wb-note">
                        <strong>提醒</strong>
                        <span>{item}</span>
                      </div>
                    ))}
                  </div>
                  <div className="wb-note-stack">
                    {rootAgentReview.next_actions.map((item) => (
                      <div key={item} className="wb-note">
                        <strong>下一步</strong>
                        <span>{item}</span>
                      </div>
                    ))}
                    {rootAgentReviewDiff.length > 0 ? (
                      <div className="wb-note">
                        <strong>变更字段</strong>
                        <span>{rootAgentReviewDiff.map((item) => formatTokenLabel(item.field)).join("、")}</span>
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>
            ) : null}

            <div className="wb-inline-actions wb-inline-actions-wrap">
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={onReviewRootAgentDraft}
                disabled={busyActionId === "worker_profile.review"}
              >
                检查草稿
              </button>
              <button
                type="button"
                className="wb-button wb-button-primary"
                onClick={() => onSaveRootAgentDraft(false)}
                disabled={busyActionId === "worker_profile.apply"}
              >
                {selectedRootAgentIsBuiltin ? "另存草稿" : "保存草稿"}
              </button>
              <button
                type="button"
                className="wb-button wb-button-primary"
                onClick={() => onSaveRootAgentDraft(true)}
                disabled={busyActionId === "worker_profile.apply"}
              >
                {selectedRootAgentIsBuiltin ? "另存并发布" : "发布版本"}
              </button>
              <button
                type="button"
                className="wb-button wb-button-tertiary"
                onClick={() =>
                  selectedRootAgentProfile ? onCreateRootAgentDraftFromTemplate(selectedRootAgentProfile) : undefined
                }
                disabled={!selectedRootAgentProfile || busyActionId === "worker_profile.clone"}
              >
                复制成新模板
              </button>
              <button
                type="button"
                className="wb-button wb-button-tertiary"
                onClick={onBindRootAgentDefault}
                disabled={
                  !selectedRootAgentProfile ||
                  selectedRootAgentIsBuiltin ||
                  selectedRootAgentDisplayStatus !== "active" ||
                  busyActionId === "worker_profile.bind_default"
                }
              >
                {selectedRootAgentIsDefault ? "已是聊天默认" : "设为聊天默认"}
              </button>
              <button
                type="button"
                className="wb-button wb-button-tertiary"
                onClick={() =>
                  selectedRootAgentProfile ? onSwitchToRootAgentContext(selectedRootAgentProfile) : undefined
                }
                disabled={!selectedRootAgentProfile || busyActionId === "project.select"}
              >
                切到这个模板的上下文
              </button>
              <button
                type="button"
                className="wb-button wb-button-tertiary"
                onClick={onArchiveRootAgent}
                disabled={
                  !rootAgentDraft.profileId ||
                  !selectedRootAgentEditable ||
                  busyActionId === "worker_profile.archive"
                }
              >
                归档当前模板
              </button>
            </div>
          </article>
        </section>

        {runtimeRail}
      </div>
    </section>
  );
}
