import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useWorkbench } from "../../components/shell/WorkbenchLayout";
import { PageIntro } from "../../ui/primitives";
import { formatDateTime, getValueAtPath } from "../../workbench/utils";
import { computeReadinessLabel } from "./readiness";

const ACTIVE_WORK_STATUSES = new Set(["created", "assigned", "running", "escalated"]);

export default function HomePage() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const selector = snapshot!.resources.project_selector;
  const wizard = snapshot!.resources.wizard;
  const diagnostics = snapshot!.resources.diagnostics;
  const sessions = snapshot!.resources.sessions;
  const memory = snapshot!.resources.memory;
  const context = snapshot!.resources.context_continuity;
  const setup = snapshot!.resources.setup_governance;
  const config = snapshot!.resources.config;
  const delegation = snapshot!.resources.delegation;
  const currentProject =
    selector.available_projects.find((item) => item.project_id === selector.current_project_id) ??
    null;
  const [selectedProjectId, setSelectedProjectId] = useState(selector.current_project_id);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState(selector.current_workspace_id);
  const availableWorkspaces = selector.available_workspaces.filter(
    (item) => item.project_id === selectedProjectId
  );
  const activeWorkCount = delegation.works.filter((item) =>
    ACTIVE_WORK_STATUSES.has(item.status)
  ).length;
  const channelSummaryEntries = Object.entries(diagnostics.channel_summary ?? {}).filter(
    ([, value]) => Boolean(value)
  );
  const latestContextSummary =
    context.frames
      .slice()
      .sort((left, right) =>
        (right.created_at ?? "").localeCompare(left.created_at ?? "")
      )[0]
      ?.recent_summary ?? "";
  const runtimeMode =
    String(getValueAtPath(config.current_value, "runtime.llm_mode") ?? "echo")
      .trim()
      .toLowerCase() || "echo";
  const usingEchoMode = runtimeMode === "echo";
  const readiness = computeReadinessLabel(
    setup.review.ready,
    wizard.status,
    diagnostics.overall_status,
    sessions.operator_summary?.total_pending ?? 0
  );

  useEffect(() => {
    setSelectedProjectId(selector.current_project_id);
    setSelectedWorkspaceId(selector.current_workspace_id);
  }, [selector.current_project_id, selector.current_workspace_id]);

  useEffect(() => {
    if (availableWorkspaces.some((item) => item.workspace_id === selectedWorkspaceId)) {
      return;
    }
    setSelectedWorkspaceId(availableWorkspaces[0]?.workspace_id ?? "");
  }, [availableWorkspaces, selectedWorkspaceId]);

  return (
    <div className="wb-page">
      <PageIntro
        kicker="Home"
        title={readiness.label}
        summary={readiness.summary}
        actions={
          <>
            {usingEchoMode || !setup.review.ready ? (
              <Link className="wb-button wb-button-primary" to="/settings">
                连接真实模型
              </Link>
            ) : (
              <Link className="wb-button wb-button-primary" to="/chat">
                进入聊天
              </Link>
            )}
            <Link className="wb-button wb-button-secondary" to="/agents">
              Agent 管理
            </Link>
            <Link className="wb-button wb-button-secondary" to="/settings">
              打开设置
            </Link>
          </>
        }
      />

      <div className="wb-card-grid wb-card-grid-4">
        <article className={`wb-card wb-card-accent is-${readiness.tone}`}>
          <p className="wb-card-label">当前状态</p>
          <strong>{readiness.label}</strong>
          <span>配置检查：{setup.review.ready ? "已通过" : "仍有阻塞"}</span>
          <span>初始化向导：{wizard.status}</span>
          <span>运行诊断：{diagnostics.overall_status}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">待你确认</p>
          <strong>{sessions.operator_summary?.total_pending ?? 0}</strong>
          <span>审批 {sessions.operator_summary?.approvals ?? 0}</span>
          <span>协作请求 {sessions.operator_summary?.pairing_requests ?? 0}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">当前工作</p>
          <strong>{delegation.works.length}</strong>
          <span>进行中 {activeWorkCount}</span>
          <span>最新更新时间 {formatDateTime(delegation.updated_at)}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">记忆摘要</p>
          <strong>{memory.summary.sor_current_count}</strong>
          <span>fragments {memory.summary.fragment_count}</span>
          <span>proposals {memory.summary.proposal_count}</span>
        </article>
      </div>

      <div className="wb-split">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">建议下一步</p>
              <h3>先把常用入口走一遍</h3>
            </div>
          </div>
          {!setup.review.ready || setup.review.next_actions.length > 0 ? (
            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>当前最重要的一步</strong>
                <span>{setup.review.next_actions[0] ?? "当前 setup 已通过。"}</span>
              </div>
              {setup.review.blocking_reasons.slice(0, 3).map((item) => (
                <div key={item} className="wb-note">
                  <strong>阻塞项</strong>
                  <span>{item}</span>
                </div>
              ))}
            </div>
          ) : null}
          <div className="wb-action-list">
            <button
              type="button"
              className="wb-action-card"
              onClick={() => void submitAction("wizard.refresh", {})}
              disabled={busyActionId === "wizard.refresh"}
            >
              <strong>刷新向导状态</strong>
              <span>重新检查当前配置和后续步骤。</span>
            </button>
            <Link className="wb-action-card" to="/settings">
              <strong>{usingEchoMode ? "连接真实模型" : "打开设置"}</strong>
              <span>
                {usingEchoMode
                  ? "当前还是 echo 模式，先去把 Provider、密钥和运行连接一次接好。"
                  : "先把主 Agent、模型连接和工作区确认好。"}
              </span>
            </Link>
            <Link className="wb-action-card" to="/agents">
              <strong>进入 Agent 管理</strong>
              <span>集中查看主 Agent、Work Agent 和 Project 的当前分工。</span>
            </Link>
            <Link className="wb-action-card" to="/chat">
              <strong>发第一条消息</strong>
              <span>直接开始对话，观察任务和工作是否正常流转。</span>
            </Link>
            <Link className="wb-action-card" to="/advanced">
              <strong>查看详细诊断</strong>
              <span>遇到异常时，这里可以看到更完整的运行信息。</span>
            </Link>
          </div>
        </section>

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">当前 Project</p>
              <h3>{currentProject?.name ?? selector.current_project_id}</h3>
            </div>
          </div>
          <p className="wb-panel-copy">
            当前 workspace: <strong>{selector.current_workspace_id}</strong>
          </p>
          {selector.fallback_reason ? (
            <p className="wb-panel-copy wb-copy-warning">{selector.fallback_reason}</p>
          ) : null}
          <div className="wb-inline-form">
            <label className="wb-field">
              <span>切换 Project</span>
              <select
                value={selectedProjectId}
                onChange={(event) => setSelectedProjectId(event.target.value)}
              >
                {selector.available_projects.map((project) => (
                  <option key={project.project_id} value={project.project_id}>
                    {project.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="wb-field">
              <span>切换 Workspace</span>
              <select
                value={selectedWorkspaceId}
                onChange={(event) => setSelectedWorkspaceId(event.target.value)}
                disabled={availableWorkspaces.length === 0}
              >
                {availableWorkspaces.map((workspace) => (
                  <option key={workspace.workspace_id} value={workspace.workspace_id}>
                    {workspace.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="wb-button wb-button-secondary"
              disabled={
                busyActionId === "project.select" ||
                (selectedProjectId === selector.current_project_id &&
                  selectedWorkspaceId === selector.current_workspace_id)
              }
              onClick={() =>
                void submitAction("project.select", {
                  project_id: selectedProjectId,
                  workspace_id: selectedWorkspaceId,
                })
              }
            >
              切换
            </button>
          </div>
        </section>
      </div>

      <div className="wb-split">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">最近会话</p>
              <h3>最近发生了什么</h3>
            </div>
            <Link className="wb-button wb-button-tertiary" to="/work">
              查看 Work
            </Link>
          </div>
          <div className="wb-list">
            {sessions.sessions.slice(0, 4).map((session) => (
              <Link key={session.session_id} to={`/tasks/${session.task_id}`} className="wb-list-row">
                <div>
                  <strong>{session.title}</strong>
                  <p>{session.latest_message_summary}</p>
                </div>
                <div className="wb-list-meta">
                  <span className={`wb-status-pill is-${session.status.toLowerCase()}`}>
                    {session.status}
                  </span>
                  <small>{formatDateTime(session.latest_event_at)}</small>
                </div>
              </Link>
            ))}
          </div>
        </section>

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">当前提醒</p>
              <h3>先看这三个状态</h3>
            </div>
          </div>
          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>渠道连接</strong>
              <span>
                {channelSummaryEntries.length > 0
                  ? channelSummaryEntries
                      .map(([key, value]) => `${key}: ${String(value)}`)
                      .join("；")
                  : "当前还没有启用外部渠道，先用 Web 即可。"}
              </span>
            </div>
            <div className="wb-note">
              <strong>上下文连续性</strong>
              <span>
                {context.degraded.is_degraded
                  ? "上下文摘要目前不完整，但不影响基础使用。"
                  : "当前工作区的上下文摘要可以正常使用。"}
              </span>
            </div>
            <div className="wb-note">
              <strong>最近摘要</strong>
              <span>{latestContextSummary || "还没有可用的对话摘要。发送一条消息后会逐步出现。"}</span>
            </div>
          </div>
        </section>

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">上下文</p>
              <h3>当前工作区的记忆状态</h3>
            </div>
          </div>
          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>摘要片段</strong>
              <span>{context.frames.length}</span>
            </div>
            <div className="wb-note">
              <strong>会话数</strong>
              <span>{context.sessions.length}</span>
            </div>
            <div className="wb-note">
              <strong>状态</strong>
              <span>
                {context.degraded.is_degraded
                  ? "当前只显示基础摘要，稍后会继续补齐。"
                  : "上下文摘要已连接到当前工作区。"}
              </span>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
