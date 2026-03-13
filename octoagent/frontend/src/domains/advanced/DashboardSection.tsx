import type {
  A2AConversationItem,
  A2AMessageItem,
  AgentSessionContinuityItem,
  AgentRuntimeItem,
  ContextFrameItem,
  DiagnosticsSubsystemStatus,
  RecallFrameItem,
  SessionProjectionItem,
  WizardSessionDocument,
  WorkProjectionItem,
} from "../../types";
import type { FreshnessReadiness } from "../../workbench/freshness";

interface DashboardSectionProps {
  wizard: WizardSessionDocument;
  currentProjectName: string;
  currentProjectId: string;
  currentWorkspaceName: string;
  currentWorkspaceId: string;
  workspaceCount: number;
  fallbackReason: string;
  recentSessions: SessionProjectionItem[];
  operatorPendingCount: number;
  latestContextFrame: ContextFrameItem | null;
  latestMemoryRecall: Record<string, unknown>;
  latestMemoryCitations: Array<Record<string, unknown>>;
  latestA2AConversation: A2AConversationItem | null;
  latestA2AMessage: A2AMessageItem | null;
  latestWorkerRecall: RecallFrameItem | null;
  contextAgentRuntimes: AgentRuntimeItem[];
  contextAgentSessions: AgentSessionContinuityItem[];
  freshnessReadiness: FreshnessReadiness;
  rootAgentLabel: string;
  rootAgentSummary: string;
  capabilityToolCount: number;
  capabilitySkillCount: number;
  capabilityWorkerProfileCount: number;
  capabilityBootstrapFileCount: number;
  capabilityDegradedReason: string;
  delegationItems: WorkProjectionItem[];
  pipelineCount: number;
  diagnosticsSubsystems: DiagnosticsSubsystemStatus[];
  diagnosticsOverallStatus: string;
  diagnosticTone: string;
  automationJobCount: number;
  automationRunHistoryCursor: string;
  busyActionId: string | null;
  onRefreshWizard: () => void;
  onRestartWizard: () => void;
  onRefreshContext: () => void;
  onCreateBackup: () => void;
  onDryRunUpdate: () => void;
  onApplyUpdate: () => void;
  onVerifyRuntime: () => void;
  formatA2ADirection: (direction: string) => string;
  formatA2AMessageType: (value: string) => string;
  formatWorkerType: (value: string) => string;
  formatFreshnessLimitations: (limitations: string[]) => string;
  statusTone: (status: string) => string;
}

function readHitLabel(hit: Record<string, unknown>): string {
  const citation =
    (hit.citation as Record<string, unknown> | undefined) ?? undefined;
  return String(
    citation?.label ??
      hit.record_id ??
      "memory-hit"
  );
}

function readHitSummary(hit: Record<string, unknown>): string {
  return String(hit.content_preview ?? hit.summary ?? "暂无 preview");
}

export default function DashboardSection({
  wizard,
  currentProjectName,
  currentProjectId,
  currentWorkspaceName,
  currentWorkspaceId,
  workspaceCount,
  fallbackReason,
  recentSessions,
  operatorPendingCount,
  latestContextFrame,
  latestMemoryRecall,
  latestMemoryCitations,
  latestA2AConversation,
  latestA2AMessage,
  latestWorkerRecall,
  contextAgentRuntimes,
  contextAgentSessions,
  freshnessReadiness,
  rootAgentLabel,
  rootAgentSummary,
  capabilityToolCount,
  capabilitySkillCount,
  capabilityWorkerProfileCount,
  capabilityBootstrapFileCount,
  capabilityDegradedReason,
  delegationItems,
  pipelineCount,
  diagnosticsSubsystems,
  diagnosticsOverallStatus,
  diagnosticTone,
  automationJobCount,
  automationRunHistoryCursor,
  busyActionId,
  onRefreshWizard,
  onRestartWizard,
  onRefreshContext,
  onCreateBackup,
  onDryRunUpdate,
  onApplyUpdate,
  onVerifyRuntime,
  formatA2ADirection,
  formatA2AMessageType,
  formatWorkerType,
  formatFreshnessLimitations,
  statusTone,
}: DashboardSectionProps) {
  return (
    <section className="section-grid">
      <article className="panel hero-panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">设置进度</p>
            <h3>{wizard.current_step || "未开始"}</h3>
          </div>
          <span className={`tone-chip ${statusTone(wizard.status)}`}>
            {wizard.status}
          </span>
        </div>
        <p>{wizard.blocking_reason || "基础设置已经具备继续使用条件。"}</p>
        <div className="action-row">
          <button
            type="button"
            className="secondary-button"
            onClick={onRefreshWizard}
            disabled={busyActionId === "wizard.refresh"}
          >
            重新检查设置
          </button>
          <button
            type="button"
            className="ghost-button"
            onClick={onRestartWizard}
            disabled={busyActionId === "wizard.restart"}
          >
            从头再配一遍
          </button>
        </div>
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">当前工作对象</p>
            <h3>{currentProjectName}</h3>
          </div>
          <span className="tone-chip neutral">Workspace {workspaceCount}</span>
        </div>
        <p>
          当前 workspace: <strong>{currentWorkspaceName}</strong>
        </p>
        <p className="muted">
          {currentProjectId} / {currentWorkspaceId}
        </p>
        {fallbackReason ? <p className="muted">{fallbackReason}</p> : null}
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">最近对话与任务</p>
            <h3>{recentSessions.length}</h3>
          </div>
          <span className="tone-chip neutral">Operator {operatorPendingCount}</span>
        </div>
        <p>这里会汇总最近发生的对话、任务和执行状态，方便快速回到现场。</p>
        <div className="event-list">
          {recentSessions.length > 0 ? (
            recentSessions.map((session) => (
              <div key={session.session_id} className="event-item">
                <div>
                  <strong>{session.title || session.task_id}</strong>
                  <p>{session.latest_message_summary || "暂无消息摘要"}</p>
                </div>
                <small>{session.status}</small>
              </div>
            ))
          ) : (
            <div className="event-item">
              <div>
                <strong>暂无最近会话</strong>
                <p>当前 project 里还没有新的 session 投影。</p>
              </div>
            </div>
          )}
        </div>
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Context Recall</p>
            <h3>{latestContextFrame?.memory_hit_count ?? 0}</h3>
          </div>
          <span className="tone-chip neutral">
            {String(latestMemoryRecall.backend_id ?? "pending")}
          </span>
        </div>
        <p>{latestContextFrame?.recent_summary || "当前作用域还没有 recent summary。"}</p>
        <p className="muted">
          Query {String(latestMemoryRecall.search_query ?? "未记录")} / Scope{" "}
          {Array.isArray(latestMemoryRecall.scope_ids)
            ? latestMemoryRecall.scope_ids.join(", ") || "-"
            : "-"}
        </p>
        <div className="event-list">
          {latestMemoryCitations.length > 0 ? (
            latestMemoryCitations.map((hit, index) => (
              <div
                key={`${latestContextFrame?.context_frame_id}-${index}`}
                className="event-item"
              >
                <div>
                  <strong>{readHitLabel(hit)}</strong>
                  <p>{readHitSummary(hit)}</p>
                </div>
              </div>
            ))
          ) : (
            <div className="event-item">
              <div>
                <strong>Recall provenance</strong>
                <p>当前还没有可展示的 recall hit。</p>
              </div>
            </div>
          )}
        </div>
        <div className="action-row">
          <button
            type="button"
            className="secondary-button"
            onClick={onRefreshContext}
          >
            刷新 Context
          </button>
        </div>
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">多 Agent 主链</p>
            <h3>{latestA2AConversation?.message_count ?? 0}</h3>
          </div>
          <span className="tone-chip neutral">
            Runtime {contextAgentRuntimes.length} / Session {contextAgentSessions.length}
          </span>
        </div>
        {latestA2AConversation ? (
          <>
            <p>
              最近一条内部委派来自 {latestA2AConversation.source_agent || "Butler"}，
              目标是 {latestA2AConversation.target_agent || "Worker"}。
            </p>
            <div className="meta-grid">
              <span>状态 {latestA2AConversation.status}</span>
              <span>消息数 {latestA2AConversation.message_count}</span>
              <span>
                最新消息 {formatA2AMessageType(latestA2AConversation.latest_message_type)}
              </span>
              <span>Recall hits {latestWorkerRecall?.memory_hit_count ?? 0}</span>
            </div>
            <div className="event-list">
              <div className="event-item">
                <div>
                  <strong>Butler Session</strong>
                  <p>{latestA2AConversation.source_agent_session_id || "未记录"}</p>
                </div>
              </div>
              <div className="event-item">
                <div>
                  <strong>Worker Session</strong>
                  <p>{latestA2AConversation.target_agent_session_id || "未记录"}</p>
                </div>
              </div>
              <div className="event-item">
                <div>
                  <strong>最近一条 A2A 消息</strong>
                  <p>
                    {latestA2AMessage
                      ? `${formatA2ADirection(latestA2AMessage.direction)} / ${formatA2AMessageType(latestA2AMessage.message_type)}`
                      : "当前还没有消息明细。"}
                  </p>
                </div>
              </div>
            </div>
          </>
        ) : (
          <p>当前 project 里还没有可展示的 Butler{" -> "}Worker 内部委派记录。</p>
        )}
      </article>

      <article className="panel wide">
        <div className="panel-head">
          <div>
            <p className="eyebrow">实时问题能力</p>
            <h3>{freshnessReadiness.label}</h3>
          </div>
          <span className={`tone-chip ${freshnessReadiness.tone}`}>
            {freshnessReadiness.badge}
          </span>
        </div>
        <p>{freshnessReadiness.summary}</p>
        <div className="wb-stat-grid">
          {freshnessReadiness.tools.map((tool) => (
            <article key={tool.label} className="wb-note">
              <strong>{tool.label}</strong>
              <span>{tool.summary}</span>
              <span className={`tone-chip ${tool.tone}`}>{tool.statusLabel}</span>
            </article>
          ))}
          <article className="wb-note">
            <strong>可委派角色</strong>
            <span>{freshnessReadiness.workerSummary}</span>
          </article>
          <article className="wb-note">
            <strong>最近一次相关 Work</strong>
            <span>{freshnessReadiness.relevantWorkSummary}</span>
          </article>
        </div>
        {freshnessReadiness.limitations.length > 0 ? (
          <p className="warning-text">
            当前限制：
            {formatFreshnessLimitations(freshnessReadiness.limitations)}
          </p>
        ) : (
          <p className="muted">当前没有 freshness 相关降级原因。</p>
        )}
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">系统能力</p>
            <h3>{capabilityToolCount}</h3>
          </div>
          <span className="tone-chip neutral">Skills {capabilitySkillCount}</span>
        </div>
        <p>
          Worker 配置 {capabilityWorkerProfileCount} / Bootstrap 文件{" "}
          {capabilityBootstrapFileCount}
        </p>
        <p className="muted">ToolIndex {capabilityDegradedReason || "active"}</p>
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">后台执行</p>
            <h3>{delegationItems.length}</h3>
          </div>
          <span className="tone-chip neutral">Pipelines {pipelineCount}</span>
        </div>
        <div className="event-list">
          {delegationItems.length > 0 ? (
            delegationItems.map((item) => (
              <div key={item.work_id} className="event-item">
                <div>
                  <strong>{item.title || item.work_id}</strong>
                  <p>{item.route_reason || formatWorkerType(item.selected_worker_type)}</p>
                </div>
                <small>{item.status}</small>
              </div>
            ))
          ) : (
            <div className="event-item">
              <div>
                <strong>暂无后台执行</strong>
                <p>当前还没有可展示的 work 投影。</p>
              </div>
            </div>
          )}
        </div>
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">系统检查</p>
            <h3>{diagnosticsSubsystems.length}</h3>
          </div>
          <span className={`tone-chip ${diagnosticTone}`}>{diagnosticsOverallStatus}</span>
        </div>
        <div className="diagnostics-grid">
          {diagnosticsSubsystems.slice(0, 2).map((item) => (
            <div key={item.subsystem_id} className="diagnostic-card">
              <strong>{item.label}</strong>
              <p>{item.summary}</p>
            </div>
          ))}
        </div>
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">自动任务</p>
            <h3>{automationJobCount}</h3>
          </div>
          <span className="tone-chip neutral">
            Runs {automationRunHistoryCursor || "none"}
          </span>
        </div>
        <p>可以创建定时动作，也可以对已有自动任务进行立即运行、暂停、恢复和删除。</p>
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">默认 Worker 模板</p>
            <h3>{rootAgentLabel}</h3>
          </div>
        </div>
        <p>{rootAgentSummary}</p>
      </article>

      <article className="panel wide">
        <div className="panel-head">
          <div>
            <p className="eyebrow">常用系统动作</p>
            <h3>排障时常用的几个按钮</h3>
          </div>
        </div>
        <div className="ops-grid">
          <button
            type="button"
            className="secondary-button"
            onClick={onCreateBackup}
            disabled={busyActionId === "backup.create"}
          >
            创建备份
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={onDryRunUpdate}
            disabled={busyActionId === "update.dry_run"}
          >
            预演更新
          </button>
          <button
            type="button"
            className="ghost-button"
            onClick={onApplyUpdate}
            disabled={busyActionId === "update.apply"}
          >
            应用更新
          </button>
          <button
            type="button"
            className="ghost-button"
            onClick={onVerifyRuntime}
            disabled={busyActionId === "runtime.verify"}
          >
            运行自检
          </button>
        </div>
      </article>
    </section>
  );
}
