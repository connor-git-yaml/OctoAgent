import type { ProjectOption, WorkspaceOption } from "../../types";

type WorkerCatalogView = "instances" | "templates";
type WorkUnitKind = "instance" | "template";
type WorkEditorMode = "edit" | "create";

interface CatalogCopy {
  label: string;
  title: string;
  description: string;
}

interface WorkAgentItem {
  id: string;
  kind: WorkUnitKind;
  name: string;
  workerType: string;
  projectId: string;
  workspaceId: string;
  status: "active" | "syncing" | "attention" | "paused" | "draft";
  source: "runtime" | "capability" | "manual";
  toolProfile: string;
  modelAlias: string;
  autonomy: string;
  summary: string;
  tags: string[];
  selectedTools: string[];
  taskCount: number;
  waitingCount: number;
  mergeReadyCount: number;
  lastUpdated: string | null;
}

interface WorkAgentDraft {
  id: string;
  kind: WorkUnitKind;
  name: string;
  workerType: string;
  projectId: string;
  workspaceId: string;
  status: "active" | "syncing" | "attention" | "paused" | "draft";
  source: "runtime" | "capability" | "manual";
  toolProfile: string;
  modelAlias: string;
  autonomy: string;
  summary: string;
  tags: string[];
  selectedTools: string[];
  taskCount: string;
  waitingCount: string;
  mergeReadyCount: string;
}

interface SelectOption {
  value: string;
  label: string;
}

interface ChoiceOption {
  value: string;
  label: string;
  description: string;
}

interface WorkerWorkspaceSectionProps {
  activeCatalog: WorkerCatalogView;
  catalogCopy: Record<WorkerCatalogView, CatalogCopy>;
  searchQuery: string;
  selectedWorkAgentId: string;
  selectedWorkAgentIds: string[];
  visibleCatalogItems: WorkAgentItem[];
  editorMode: WorkEditorMode;
  editingKind: WorkUnitKind;
  selectedWorkAgent: WorkAgentItem | null;
  workDraft: WorkAgentDraft;
  workInstances: WorkAgentItem[];
  selectedTemplateUsageCount: number;
  recommendedTags: string[];
  recommendedTools: string[];
  toolLabelByName: Record<string, string>;
  modelAliasOptions: string[];
  toolProfileOptions: string[];
  workProjectOptions: SelectOption[];
  workWorkspaceOptions: SelectOption[];
  availableProjects: ProjectOption[];
  availableWorkspaces: WorkspaceOption[];
  autonomyOptions: readonly ChoiceOption[];
  modelAliasHints: Record<string, string>;
  workAgentStatusLabels: Record<string, string>;
  workAgentSourceLabels: Record<string, string>;
  onOpenCatalog: (catalog: WorkerCatalogView) => void;
  onSearchQueryChange: (value: string) => void;
  onMergeWorkAgents: () => void;
  onSplitWorkAgent: () => void;
  onCreateDraft: (kind: WorkUnitKind) => void;
  onCreateInstanceFromCurrentTemplate: () => void;
  onToggleWorkAgentSelection: (agentId: string) => void;
  onSelectWorkAgent: (agent: WorkAgentItem) => void;
  onCreateInstanceFromTemplate: (agent: WorkAgentItem) => void;
  onSaveTemplateFromInstance: (agent: WorkAgentItem) => void;
  onUpdateWorkField: (key: keyof WorkAgentDraft, value: string) => void;
  onUpdateWorkProject: (projectId: string) => void;
  onUpdateWorkerType: (workerType: string) => void;
  onToggleDraftToken: (key: "tags" | "selectedTools", value: string) => void;
  onResetWorkAgent: () => void;
  onForkTemplateFromInstance: () => void;
  onSaveWorkAgent: () => void;
  formatDateTime: (value: string) => string;
  formatWorkerType: (workerType: string) => string;
  formatAutonomy: (value: string) => string;
  formatToolProfile: (value: string) => string;
  formatProjectWorkspace: (
    projects: ProjectOption[],
    workspaces: WorkspaceOption[],
    projectId: string,
    workspaceId: string
  ) => string;
  formatTokenLabel: (token: string) => string;
  formatToolToken: (toolName: string, labels: Record<string, string>) => string;
  workAgentBadge: (agent: WorkAgentItem) => { label: string; tone: string };
}

export default function WorkerWorkspaceSection({
  activeCatalog,
  catalogCopy,
  searchQuery,
  selectedWorkAgentId,
  selectedWorkAgentIds,
  visibleCatalogItems,
  editorMode,
  editingKind,
  selectedWorkAgent,
  workDraft,
  workInstances,
  selectedTemplateUsageCount,
  recommendedTags,
  recommendedTools,
  toolLabelByName,
  modelAliasOptions,
  toolProfileOptions,
  workProjectOptions,
  workWorkspaceOptions,
  availableProjects,
  availableWorkspaces,
  autonomyOptions,
  modelAliasHints,
  workAgentStatusLabels,
  workAgentSourceLabels,
  onOpenCatalog,
  onSearchQueryChange,
  onMergeWorkAgents,
  onSplitWorkAgent,
  onCreateDraft,
  onCreateInstanceFromCurrentTemplate,
  onToggleWorkAgentSelection,
  onSelectWorkAgent,
  onCreateInstanceFromTemplate,
  onSaveTemplateFromInstance,
  onUpdateWorkField,
  onUpdateWorkProject,
  onUpdateWorkerType,
  onToggleDraftToken,
  onResetWorkAgent,
  onForkTemplateFromInstance,
  onSaveWorkAgent,
  formatDateTime,
  formatWorkerType,
  formatAutonomy,
  formatToolProfile,
  formatProjectWorkspace,
  formatTokenLabel,
  formatToolToken,
  workAgentBadge,
}: WorkerWorkspaceSectionProps) {
  return (
    <section className="wb-panel wb-worker-hub">
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">Worker 管理</p>
          <h3>先看实例，再决定是否沉淀成草案</h3>
          <p className="wb-panel-copy">
            这里优先处理当前正在运行的 Worker。只有当某个实例值得长期复用时，再把它沉淀成草案。
          </p>
        </div>
      </div>

      <div className="wb-agent-tablist" role="tablist" aria-label="Worker 管理视图">
        <button
          type="button"
          role="tab"
          aria-selected={activeCatalog === "instances"}
          className={`wb-agent-tab ${activeCatalog === "instances" ? "is-active" : ""}`}
          onClick={() => onOpenCatalog("instances")}
        >
          <strong>{catalogCopy.instances.label}</strong>
          <span>{catalogCopy.instances.description}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeCatalog === "templates"}
          className={`wb-agent-tab ${activeCatalog === "templates" ? "is-active" : ""}`}
          onClick={() => onOpenCatalog("templates")}
        >
          <strong>{catalogCopy.templates.label}</strong>
          <span>{catalogCopy.templates.description}</span>
        </button>
      </div>

      <div className="wb-inline-banner is-muted">
        <strong>{catalogCopy[activeCatalog].title}</strong>
        <span>
          {activeCatalog === "instances"
            ? "实例适合看当前负载、归属和合并拆分；需要沉淀经验时，再切到实例草案。"
            : "这里的草案来自当前实例。要发布长期默认模板，请回到上面的 Worker 模板工作台。"}
        </span>
      </div>

      <div className="wb-worker-toolbar">
        <label className="wb-field">
          <span>{activeCatalog === "instances" ? "搜索实例" : "搜索草案"}</span>
          <input
            type="text"
            value={searchQuery}
            placeholder={
              activeCatalog === "instances"
                ? "例如：开发、待处理、Primary Workspace"
                : "例如：巡检、默认工具、handoff"
            }
            onChange={(event) => onSearchQueryChange(event.target.value)}
          />
        </label>

        <div className="wb-inline-actions wb-inline-actions-wrap">
          {activeCatalog === "instances" ? (
            <>
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={onMergeWorkAgents}
                disabled={selectedWorkAgentIds.length < 2}
              >
                合并选中实例
              </button>
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={onSplitWorkAgent}
                disabled={!selectedWorkAgent || selectedWorkAgent.kind !== "instance"}
              >
                拆分当前实例
              </button>
              <button
                type="button"
                className="wb-button wb-button-primary"
                onClick={() => onCreateDraft("instance")}
              >
                新建 Worker 实例
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={onCreateInstanceFromCurrentTemplate}
                disabled={!selectedWorkAgent || selectedWorkAgent.kind !== "template"}
              >
                按当前草案新建实例
              </button>
              <button
                type="button"
                className="wb-button wb-button-primary"
                onClick={() => onCreateDraft("template")}
              >
                新建实例草案
              </button>
            </>
          )}
        </div>
      </div>

      <div className="wb-worker-hub-layout">
        <div className="wb-worker-browser">
          {visibleCatalogItems.length === 0 ? (
            <div className="wb-empty-state">
              <strong>当前没有匹配内容</strong>
              <span>
                试着切换 Project 过滤，或者直接创建一个新的
                {activeCatalog === "instances" ? "实例" : "草案"}。
              </span>
            </div>
          ) : (
            <div className="wb-agent-list">
              {visibleCatalogItems.map((agent) => {
                const badge = workAgentBadge(agent);
                const isActive = selectedWorkAgentId === agent.id && editorMode === "edit";
                return (
                  <article
                    key={agent.id}
                    className={`wb-agent-runtime-card ${isActive ? "is-active" : ""}`}
                  >
                    <div className="wb-worker-card-head">
                      <div className="wb-worker-card-leading">
                        {agent.kind === "instance" ? (
                          <label className="wb-agent-check">
                            <input
                              type="checkbox"
                              checked={selectedWorkAgentIds.includes(agent.id)}
                              onChange={() => onToggleWorkAgentSelection(agent.id)}
                            />
                            <span>批量选择</span>
                          </label>
                        ) : (
                          <span className="wb-card-label">草案</span>
                        )}
                        <span className={`wb-status-pill is-${badge.tone}`}>{badge.label}</span>
                      </div>
                      <span className="wb-card-label">{formatWorkerType(agent.workerType)}</span>
                    </div>

                    <div className="wb-worker-card-body">
                      <strong>{agent.name}</strong>
                      <p>{agent.summary}</p>
                      <div className="wb-chip-row">
                        <span className="wb-chip">
                          {formatProjectWorkspace(
                            availableProjects,
                            availableWorkspaces,
                            agent.projectId,
                            agent.workspaceId
                          )}
                        </span>
                        <span className="wb-chip">{formatAutonomy(agent.autonomy)}</span>
                        <span className="wb-chip">{formatToolProfile(agent.toolProfile)}</span>
                      </div>
                      <div className="wb-chip-row">
                        {agent.tags.map((tag) => (
                          <span key={tag} className="wb-chip is-warning">
                            {formatTokenLabel(tag)}
                          </span>
                        ))}
                      </div>
                      <div className="wb-agent-runtime-meta">
                        {agent.kind === "instance" ? (
                          <>
                            <span>当前任务 {agent.taskCount}</span>
                            <span>等待 {agent.waitingCount}</span>
                            <span>适合合并 {agent.mergeReadyCount}</span>
                          </>
                        ) : (
                          <>
                            <span>默认工具 {agent.selectedTools.length}</span>
                            <span>
                              同类型实例{" "}
                              {workInstances.filter((item) => item.workerType === agent.workerType).length}
                            </span>
                            <span>{workAgentSourceLabels[agent.source]}</span>
                          </>
                        )}
                        <span>
                          {agent.lastUpdated ? `更新于 ${formatDateTime(agent.lastUpdated)}` : "尚未保存"}
                        </span>
                      </div>
                    </div>

                    <div className="wb-worker-card-actions">
                      <button
                        type="button"
                        className="wb-button wb-button-secondary wb-button-inline"
                        onClick={() => onSelectWorkAgent(agent)}
                      >
                        {agent.kind === "instance" ? "查看与修改实例" : "查看与修改模板"}
                      </button>
                      {agent.kind === "template" ? (
                        <button
                          type="button"
                          className="wb-button wb-button-tertiary wb-button-inline"
                          onClick={() => onCreateInstanceFromTemplate(agent)}
                        >
                          用它新建实例
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="wb-button wb-button-tertiary wb-button-inline"
                          onClick={() => onSaveTemplateFromInstance(agent)}
                        >
                          另存为草案
                        </button>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </div>

        <aside className="wb-worker-editor">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">{editingKind === "instance" ? "实例编辑器" : "草案编辑器"}</p>
              <h3>
                {editingKind === "instance"
                  ? editorMode === "create"
                    ? "创建新的 Worker 实例"
                    : "调整当前 Worker 实例"
                  : editorMode === "create"
                    ? "创建新的实例草案"
                    : "调整当前实例草案"}
              </h3>
            </div>
            <span className="wb-chip">
              {editorMode === "create"
                ? editingKind === "instance"
                  ? "新实例草案"
                  : "新草案"
                : selectedWorkAgent?.name ?? "未选择"}
            </span>
          </div>

          <div className="wb-inline-banner is-muted">
            <strong>{editingKind === "instance" ? "你正在编辑实例" : "你正在编辑草案"}</strong>
            <span>
              {editingKind === "instance"
                ? "实例对应当前分工。这里改的是归属、角色和默认做法，运行指标仅作参考。"
                : "草案来自当前实例，用来沉淀新的默认做法。要发布长期默认模板，请回到上面的 Worker 模板。"}
            </span>
          </div>

          <div className="wb-stat-grid">
            <div className="wb-detail-block">
              <span className="wb-card-label">归属位置</span>
              <strong>
                {formatProjectWorkspace(
                  availableProjects,
                  availableWorkspaces,
                  workDraft.projectId,
                  workDraft.workspaceId
                )}
              </strong>
              <p>{formatWorkerType(workDraft.workerType)}</p>
            </div>
            <div className="wb-detail-block">
              <span className="wb-card-label">
                {editingKind === "instance" ? "当前状态" : "草案用途"}
              </span>
              <strong>
                {editingKind === "instance"
                  ? workAgentStatusLabels[workDraft.status]
                  : workAgentSourceLabels[workDraft.source]}
              </strong>
              <p>{formatAutonomy(workDraft.autonomy)}</p>
            </div>
            {editingKind === "instance" ? (
              <>
                <div className="wb-detail-block">
                  <span className="wb-card-label">当前任务量</span>
                  <strong>{workDraft.taskCount}</strong>
                  <p>等待中 {workDraft.waitingCount}</p>
                </div>
                <div className="wb-detail-block">
                  <span className="wb-card-label">适合合并</span>
                  <strong>{workDraft.mergeReadyCount}</strong>
                  <p>用于判断是否需要收口同类实例</p>
                </div>
              </>
            ) : (
              <>
                <div className="wb-detail-block">
                  <span className="wb-card-label">同类型实例</span>
                  <strong>{selectedTemplateUsageCount}</strong>
                  <p>当前正在使用相近角色的实例数量</p>
                </div>
                <div className="wb-detail-block">
                  <span className="wb-card-label">默认工具</span>
                  <strong>{workDraft.selectedTools.length}</strong>
                  <p>保存后会作为新建时的默认勾选</p>
                </div>
              </>
            )}
          </div>

          <div className="wb-agent-form-grid">
            <label className="wb-field">
              <span>{editingKind === "instance" ? "实例名称" : "草案名称"}</span>
              <input
                type="text"
                value={workDraft.name}
                onChange={(event) => onUpdateWorkField("name", event.target.value)}
              />
            </label>
            <label className="wb-field">
              <span>负责角色</span>
              <select
                value={workDraft.workerType}
                onChange={(event) => onUpdateWorkerType(event.target.value)}
              >
                {["general", "research", "dev", "ops"].map((workerType) => (
                  <option key={workerType} value={workerType}>
                    {formatWorkerType(workerType)}
                  </option>
                ))}
              </select>
            </label>
            <label className="wb-field">
              <span>归属 Project</span>
              <select
                value={workDraft.projectId}
                onChange={(event) => onUpdateWorkProject(event.target.value)}
              >
                {workProjectOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="wb-field">
              <span>归属 Workspace</span>
              <select
                value={workDraft.workspaceId}
                onChange={(event) => onUpdateWorkField("workspaceId", event.target.value)}
              >
                {workWorkspaceOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="wb-field wb-field-span-2">
              <span>{editingKind === "instance" ? "当前职责说明" : "这个草案适合什么场景"}</span>
              <textarea
                rows={4}
                className="wb-textarea-prose"
                value={workDraft.summary}
                onChange={(event) => onUpdateWorkField("summary", event.target.value)}
              />
            </label>
          </div>

          <div className="wb-agent-option-stack">
            <div>
              <p className="wb-card-label">处理方式</p>
              <div className="wb-agent-choice-grid">
                {autonomyOptions.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    className={`wb-agent-choice-card ${workDraft.autonomy === option.value ? "is-active" : ""}`}
                    onClick={() => onUpdateWorkField("autonomy", option.value)}
                  >
                    <strong>{option.label}</strong>
                    <span>{option.description}</span>
                  </button>
                ))}
              </div>
            </div>

            <div>
              <p className="wb-card-label">职责标签</p>
              <div className="wb-chip-row">
                {recommendedTags.map((tag) => (
                  <button
                    key={tag}
                    type="button"
                    className={`wb-chip-button ${workDraft.tags.includes(tag) ? "is-active" : ""}`}
                    onClick={() => onToggleDraftToken("tags", tag)}
                  >
                    {formatTokenLabel(tag)}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <p className="wb-card-label">默认工具</p>
              <div className="wb-chip-row">
                {recommendedTools.map((tool) => (
                  <button
                    key={tool}
                    type="button"
                    className={`wb-chip-button ${workDraft.selectedTools.includes(tool) ? "is-active" : ""}`}
                    onClick={() => onToggleDraftToken("selectedTools", tool)}
                  >
                    {formatToolToken(tool, toolLabelByName)}
                  </button>
                ))}
              </div>
            </div>

            <details className="wb-agent-details">
              <summary>展开高级配置</summary>
              <div className="wb-agent-option-stack">
                <div>
                  <p className="wb-card-label">思考档位</p>
                  <div className="wb-chip-row">
                    {modelAliasOptions.map((alias) => (
                      <button
                        key={alias}
                        type="button"
                        className={`wb-chip-button ${workDraft.modelAlias === alias ? "is-active" : ""}`}
                        onClick={() => onUpdateWorkField("modelAlias", alias)}
                      >
                        {alias}
                      </button>
                    ))}
                  </div>
                  <p className="wb-inline-note">
                    {modelAliasHints[workDraft.modelAlias] ?? "用于决定默认模型档位。"}
                  </p>
                </div>

                <div>
                  <p className="wb-card-label">工具范围</p>
                  <div className="wb-chip-row">
                    {toolProfileOptions.map((profile) => (
                      <button
                        key={profile}
                        type="button"
                        className={`wb-chip-button ${workDraft.toolProfile === profile ? "is-active" : ""}`}
                        onClick={() => onUpdateWorkField("toolProfile", profile)}
                      >
                        {formatToolProfile(profile)}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </details>
          </div>

          <div className="wb-inline-actions wb-inline-actions-wrap">
            <button type="button" className="wb-button wb-button-secondary" onClick={onResetWorkAgent}>
              撤回编辑
            </button>
            {editingKind === "instance" ? (
              <button
                type="button"
                className="wb-button wb-button-tertiary"
                onClick={onForkTemplateFromInstance}
                disabled={!selectedWorkAgent || selectedWorkAgent.kind !== "instance"}
              >
                另存为草案
              </button>
            ) : (
              <button
                type="button"
                className="wb-button wb-button-tertiary"
                onClick={onCreateInstanceFromCurrentTemplate}
                disabled={!selectedWorkAgent || selectedWorkAgent.kind !== "template"}
              >
                按草案新建实例
              </button>
            )}
            <button type="button" className="wb-button wb-button-primary" onClick={onSaveWorkAgent}>
              {editingKind === "instance"
                ? editorMode === "create"
                  ? "保存实例草案"
                  : "保存实例调整"
                : editorMode === "create"
                  ? "保存草案"
                  : "保存草案调整"}
            </button>
          </div>
        </aside>
      </div>
    </section>
  );
}
