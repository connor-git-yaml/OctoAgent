import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import { formatDateTime } from "../workbench/utils";

const ACTIVE_WORK_STATUSES = new Set(["created", "assigned", "running", "escalated"]);

function computeReadinessLabel(
  setupReady: boolean,
  wizardStatus: string,
  diagnosticsStatus: string,
  pendingCount: number
): { label: string; tone: string; summary: string } {
  if (!setupReady) {
    return {
      label: "先完成 Setup",
      tone: "danger",
      summary: "Provider、权限或技能依赖仍有阻塞项，先到 Settings 完成 review/apply。",
    };
  }
  if (wizardStatus !== "ready") {
    return {
      label: "继续完成设置",
      tone: "warning",
      summary: "向导还没有结束，建议先把配置与诊断补完。",
    };
  }
  if (diagnosticsStatus !== "ready" && diagnosticsStatus !== "ok") {
    return {
      label: "系统需要检查",
      tone: "danger",
      summary: "运行状态存在降级项，先处理诊断和渠道就绪度。",
    };
  }
  if (pendingCount > 0) {
    return {
      label: "有待你确认的事项",
      tone: "warning",
      summary: "系统已可用，但还有审批或 pairing 待处理。",
    };
  }
  return {
    label: "已经可以开始",
    tone: "success",
    summary: "可以直接进入聊天、查看工作和继续配置细节。",
  };
}

export default function Home() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const selector = snapshot!.resources.project_selector;
  const wizard = snapshot!.resources.wizard;
  const diagnostics = snapshot!.resources.diagnostics;
  const sessions = snapshot!.resources.sessions;
  const memory = snapshot!.resources.memory;
  const context = snapshot!.resources.context_continuity;
  const setup = snapshot!.resources.setup_governance;
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
      <section className="wb-hero">
        <div>
          <p className="wb-kicker">Home</p>
          <h1>{readiness.label}</h1>
          <p>{readiness.summary}</p>
        </div>
        <div className="wb-hero-actions">
          <Link className="wb-button wb-button-primary" to="/chat">
            进入聊天
          </Link>
          <Link className="wb-button wb-button-secondary" to="/settings">
            打开设置
          </Link>
        </div>
      </section>

      <div className="wb-card-grid wb-card-grid-4">
        <article className={`wb-card wb-card-accent is-${readiness.tone}`}>
          <p className="wb-card-label">当前状态</p>
          <strong>{readiness.label}</strong>
          <span>Setup: {setup.review.ready ? "ready" : "blocked"}</span>
          <span>Wizard: {wizard.status}</span>
          <span>Diagnostics: {diagnostics.overall_status}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">待你确认</p>
          <strong>{sessions.operator_summary?.total_pending ?? 0}</strong>
          <span>Approvals {sessions.operator_summary?.approvals ?? 0}</span>
          <span>Pairing {sessions.operator_summary?.pairing_requests ?? 0}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">当前工作</p>
          <strong>{delegation.works.length}</strong>
          <span>活跃 works {activeWorkCount}</span>
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
              <p className="wb-card-label">下一步</p>
              <h3>先把常见入口走顺</h3>
            </div>
          </div>
          {!setup.review.ready || setup.review.next_actions.length > 0 ? (
            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>Setup Next Actions</strong>
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
              <strong>图形化改配置</strong>
              <span>先从主 Agent、Channels 和 Project 开始。</span>
            </Link>
            <Link className="wb-action-card" to="/chat">
              <strong>发第一条消息</strong>
              <span>直接进入聊天工作台，观察任务和记忆摘要。</span>
            </Link>
            <Link className="wb-action-card" to="/advanced">
              <strong>需要诊断时进 Advanced</strong>
              <span>保留完整 control plane，不把高级能力删掉。</span>
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
              <h3>先从这些地方理解系统状态</h3>
            </div>
          </div>
          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>Channels</strong>
              <span>
                当前 channel summary:{" "}
                {JSON.stringify(diagnostics.channel_summary ?? {}, null, 0)}
              </span>
            </div>
            <div className="wb-note">
              <strong>连续上下文</strong>
              <span>Feature 033 完成后会接入 profile/bootstrap/context provenance。</span>
            </div>
            <div className="wb-note">
              <strong>上下文压缩</strong>
              <span>Feature 034 已在运行链里，但这里后续会改成用户化可读提示。</span>
            </div>
          </div>
        </section>

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">Context</p>
              <h3>当前上下文连续性状态</h3>
            </div>
          </div>
          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>Frames</strong>
              <span>{context.frames.length}</span>
            </div>
            <div className="wb-note">
              <strong>Sessions</strong>
              <span>{context.sessions.length}</span>
            </div>
            <div className="wb-note">
              <strong>状态</strong>
              <span>
                {context.degraded.is_degraded
                  ? "033 仍有降级项，当前只展示基础 context frame"
                  : "context continuity 已接入当前作用域"}
              </span>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
