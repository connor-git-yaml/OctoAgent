interface AgentWorkspaceViewItem {
  id: string;
  label: string;
  title: string;
  description: string;
}

interface AgentHeroSectionProps {
  currentProjectName: string;
  currentWorkspaceName: string;
  reviewTone: "success" | "warning" | "danger";
  primaryReady: boolean;
  primaryBlockingCount: number;
  primaryWarningCount: number;
  pendingChanges: number;
  savedPrimaryName: string;
  savedPrimaryToolProfileLabel: string;
  rootAgentProfilesCount: number;
  defaultRootAgentName: string;
  workInstancesCount: number;
  activeWorkAgents: number;
  attentionWorkAgents: number;
  savedPrimaryProjectName: string;
  savedPrimaryWorkspaceName: string;
  savedPrimaryModelAlias: string;
  currentPolicyLabel: string;
  primaryDirty: boolean;
  flashMessage: string;
  activeWorkspaceView: string;
  workspaceViews: AgentWorkspaceViewItem[];
  onOpenWorkspaceView: (viewId: string) => void;
}

export default function AgentHeroSection({
  currentProjectName,
  currentWorkspaceName,
  reviewTone,
  primaryReady,
  primaryBlockingCount,
  primaryWarningCount,
  pendingChanges,
  savedPrimaryName,
  savedPrimaryToolProfileLabel,
  rootAgentProfilesCount,
  defaultRootAgentName,
  workInstancesCount,
  activeWorkAgents,
  attentionWorkAgents,
  savedPrimaryProjectName,
  savedPrimaryWorkspaceName,
  savedPrimaryModelAlias,
  currentPolicyLabel,
  primaryDirty,
  flashMessage,
  activeWorkspaceView,
  workspaceViews,
  onOpenWorkspaceView,
}: AgentHeroSectionProps) {
  const activeView =
    workspaceViews.find((view) => view.id === activeWorkspaceView) ?? workspaceViews[0] ?? null;

  return (
    <>
      <section className="wb-hero wb-hero-agent wb-butler-hero">
        <div className="wb-hero-copy">
          <p className="wb-kicker">Agents</p>
          <h1>Butler 与 Worker</h1>
          <p>在这里配置 Butler、维护 Worker 模板，并查看当前运行中的 Worker。</p>
          <div className="wb-chip-row">
            <span className="wb-chip">当前项目 {currentProjectName}</span>
            <span className="wb-chip">当前工作区 {currentWorkspaceName}</span>
            <span className={`wb-chip ${reviewTone === "danger" ? "is-warning" : "is-success"}`}>
              {reviewTone === "danger"
                ? `阻塞 ${primaryBlockingCount}`
                : primaryReady
                  ? "配置检查通过"
                  : `提醒 ${primaryWarningCount}`}
            </span>
            <span className={`wb-chip ${pendingChanges > 0 ? "is-warning" : "is-success"}`}>
              {pendingChanges > 0 ? `待确认改动 ${pendingChanges}` : "当前已同步"}
            </span>
          </div>
        </div>

        <div className="wb-hero-insights">
          <article className="wb-hero-metric">
            <p className="wb-card-label">Butler 配置</p>
            <strong>{savedPrimaryName}</strong>
            <span>{savedPrimaryToolProfileLabel}</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">Worker 模板</p>
            <strong>{rootAgentProfilesCount}</strong>
            <span>默认模板 {defaultRootAgentName || "未设置"}</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">运行中的 Worker</p>
            <strong>{workInstancesCount}</strong>
            <span>活跃 {activeWorkAgents} / 待处理 {attentionWorkAgents}</span>
          </article>
        </div>
      </section>

      <div className="wb-butler-summary-grid">
        <article className="wb-butler-summary-card is-accent">
          <p className="wb-card-label">Butler 配置</p>
          <strong>名称、默认位置、审批和记忆边界都在这里</strong>
          <span>改这里会影响新会话默认怎么工作，不会直接改已经在运行的任务。</span>
        </article>
        <article className="wb-butler-summary-card">
          <p className="wb-card-label">默认位置</p>
          <strong>{savedPrimaryProjectName}</strong>
          <span>{savedPrimaryWorkspaceName}</span>
        </article>
        <article className="wb-butler-summary-card">
          <p className="wb-card-label">模型与工具</p>
          <strong>
            {savedPrimaryModelAlias} · {savedPrimaryToolProfileLabel}
          </strong>
          <span>{currentPolicyLabel}</span>
        </article>
        <article className={`wb-butler-summary-card ${pendingChanges > 0 ? "is-warning" : ""}`}>
          <p className="wb-card-label">待保存改动</p>
          <strong>{pendingChanges > 0 ? `${pendingChanges} 处` : "已同步"}</strong>
          <span>{primaryDirty ? "Butler 草案未保存" : "Butler 已同步到底层配置"}</span>
        </article>
      </div>

      <div className="wb-inline-banner is-muted">
        <strong>当前提示</strong>
        <span>{flashMessage}</span>
      </div>

      <div className="wb-agent-workflow-nav" role="tablist" aria-label="Agents 工作流">
        {workspaceViews.map((view) => (
          <button
            key={view.id}
            type="button"
            role="tab"
            aria-selected={activeWorkspaceView === view.id}
            className={`wb-agent-workflow-button ${
              activeWorkspaceView === view.id ? "is-active" : ""
            }`}
            onClick={() => onOpenWorkspaceView(view.id)}
          >
            <strong>{view.label}</strong>
            <span>{view.description}</span>
          </button>
        ))}
      </div>

      {activeView ? (
        <div className="wb-inline-banner is-muted">
          <strong>{activeView.title}</strong>
          <span>{activeView.description}</span>
        </div>
      ) : null}
    </>
  );
}
