import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useWorkbench } from "../../components/shell/WorkbenchLayout";
import { PageIntro } from "../../ui/primitives";
import { formatDateTime, getValueAtPath } from "../../workbench/utils";
import { computeReadinessLabel } from "./readiness";

const ACTIVE_WORK_STATUSES = new Set(["created", "assigned", "running", "escalated"]);
const READY_DIAGNOSTIC_STATUSES = new Set(["ready", "ok"]);
const READY_CHANNEL_STATUSES = new Set(["ready", "ok", "healthy", "enabled", "connected"]);
const CHANNEL_LABELS: Record<string, string> = {
  telegram: "Telegram",
  web: "Web",
  wechat: "微信",
  wechat_import: "微信导入",
};

function firstStringValue(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

function firstBooleanValue(record: Record<string, unknown>, keys: string[]): boolean | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "boolean") {
      return value;
    }
  }
  return null;
}

function formatChannelStatusText(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (!normalized) {
    return "状态未记录";
  }
  if (["ready", "ok", "healthy", "enabled", "connected"].includes(normalized)) {
    return "已连接";
  }
  if (["warning", "warn", "degraded", "partial"].includes(normalized)) {
    return "需要检查";
  }
  if (["error", "failed", "unreachable", "offline"].includes(normalized)) {
    return "当前不可用";
  }
  if (["disabled", "off", "none"].includes(normalized)) {
    return "未启用";
  }
  return status;
}

function summarizeChannelEntry(key: string, value: unknown): string {
  const label = CHANNEL_LABELS[key] ?? key;
  if (typeof value === "boolean") {
    return `${label}${value ? "已连接" : "未启用"}`;
  }
  if (typeof value === "string") {
    return `${label}${formatChannelStatusText(value)}`;
  }
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    const status = firstStringValue(record, ["summary", "status", "state", "detail"]);
    if (status) {
      return `${label}${formatChannelStatusText(status)}`;
    }
    const connected = firstBooleanValue(record, [
      "connected",
      "ready",
      "enabled",
      "configured",
    ]);
    if (connected != null) {
      return `${label}${connected ? "已连接" : "待配置"}`;
    }
    return `${label}已配置`;
  }
  return `${label}状态未记录`;
}

function truncateText(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }
  return `${value.slice(0, maxLength - 1)}…`;
}

function buildImpactSummary(
  diagnosticsStatus: string,
  contextDegraded: boolean,
  channelSummaryEntries: Array<[string, unknown]>
): string {
  if (!READY_DIAGNOSTIC_STATUSES.has(diagnosticsStatus.trim().toLowerCase())) {
    return "当前运行环境有降级，实时查询、外部连接或后台能力可能受影响。";
  }
  if (contextDegraded) {
    return "当前背景摘要还在补齐，但不影响继续聊天和追问。";
  }
  const hasUsableChannel = channelSummaryEntries.some(([, value]) => {
    if (typeof value === "boolean") {
      return value;
    }
    if (typeof value === "string") {
      return READY_CHANNEL_STATUSES.has(value.trim().toLowerCase());
    }
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const record = value as Record<string, unknown>;
      const connected = firstBooleanValue(record, ["connected", "ready"]);
      if (connected != null) {
        return connected;
      }
      const enabled = firstBooleanValue(record, ["enabled"]);
      if (enabled != null) {
        return enabled;
      }
      const status = firstStringValue(record, ["status", "state"]);
      if (status) {
        return READY_CHANNEL_STATUSES.has(status.trim().toLowerCase());
      }
    }
    return false;
  });
  if (hasUsableChannel) {
    return "常用入口已经准备好，需要时也可以从外部渠道进入。";
  }
  if (channelSummaryEntries.length > 0) {
    return "已经看到了外部渠道配置，但连接或授权可能还没走完；先用 Web 也不受影响。";
  }
  return "当前先用 Web 即可；外部渠道可以后面再慢慢补。";
}

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
  const pendingCount = sessions.operator_summary?.total_pending ?? 0;
  const channelSummaryEntries = Object.entries(diagnostics.channel_summary ?? {}).filter(
    ([, value]) => Boolean(value)
  );
  const latestSession = useMemo(
    () =>
      [...sessions.sessions].sort((left, right) =>
        String(right.latest_event_at ?? "").localeCompare(String(left.latest_event_at ?? ""))
      )[0] ?? null,
    [sessions.sessions]
  );
  const runtimeMode =
    String(getValueAtPath(config.current_value, "runtime.llm_mode") ?? "echo")
      .trim()
      .toLowerCase() || "echo";
  const usingEchoMode = runtimeMode === "echo";
  const readiness = computeReadinessLabel({
    usingEchoMode,
    setupReady: setup.review.ready,
    wizardStatus: wizard.status,
    diagnosticsStatus: diagnostics.overall_status,
    pendingCount,
    activeWorkCount,
  });
  const channelSummaryText =
    channelSummaryEntries.length > 0
      ? channelSummaryEntries.map(([key, value]) => summarizeChannelEntry(key, value)).join("；")
      : "当前还没有启用外部渠道，先用 Web 即可。";
  const impactSummary = buildImpactSummary(
    diagnostics.overall_status,
    context.degraded.is_degraded,
    channelSummaryEntries
  );
  const topNextAction =
    setup.review.next_actions[0] ??
    (setup.review.ready
      ? "现在可以直接回聊天发第一条消息。"
      : "先去设置页处理阻塞项。");
  const pendingSummary =
    pendingCount > 0
      ? `审批 ${sessions.operator_summary?.approvals ?? 0} / 协作请求 ${
          sessions.operator_summary?.pairing_requests ?? 0
        }`
      : "现在没有需要你确认的事项。";
  const activeWorkSummary =
    activeWorkCount > 0
      ? `历史累计 ${delegation.works.length} / 最近更新 ${formatDateTime(delegation.updated_at)}`
      : delegation.works.length > 0
        ? `历史累计 ${delegation.works.length} / 当前没有进行中的任务`
        : "还没有运行中的任务。";
  const latestSessionTitle = latestSession?.title?.trim() || "还没有最近记录";
  const latestSessionSummary = latestSession?.latest_message_summary?.trim()
    ? truncateText(latestSession.latest_message_summary.trim(), 80)
    : "发一条消息后，这里会显示最近一次结果。";
  const heroSecondaryAction =
    readiness.primaryActionTo === "/chat" ? "/settings" : "/chat";
  const heroSecondaryLabel =
    readiness.primaryActionTo === "/chat" ? "打开设置" : "进入聊天";

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
            <Link className="wb-button wb-button-primary" to={readiness.primaryActionTo}>
              {readiness.primaryActionLabel}
            </Link>
            <Link className="wb-button wb-button-secondary" to={heroSecondaryAction}>
              {heroSecondaryLabel}
            </Link>
            <Link className="wb-button wb-button-secondary" to="/advanced">
              查看诊断
            </Link>
          </>
        }
      />

      <div className="wb-card-grid wb-card-grid-4">
        <article className={`wb-card wb-card-accent is-${readiness.tone}`}>
          <p className="wb-card-label">最重要的一步</p>
          <strong>{readiness.label}</strong>
          <span>{topNextAction}</span>
          <Link className="wb-button wb-button-tertiary" to={readiness.primaryActionTo}>
            {readiness.primaryActionLabel}
          </Link>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">待处理事项</p>
          <strong>{pendingCount}</strong>
          <span>{pendingSummary}</span>
          <Link className="wb-button wb-button-tertiary" to="/work">
            去看待处理工作
          </Link>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">正在进行</p>
          <strong>{activeWorkCount}</strong>
          <span>{activeWorkSummary}</span>
          <Link className="wb-button wb-button-tertiary" to="/work">
            查看当前工作
          </Link>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">最近一次对话</p>
          <strong>{latestSessionTitle}</strong>
          <span>{latestSessionSummary}</span>
          {latestSession?.task_id ? (
            <Link className="wb-button wb-button-tertiary" to={`/tasks/${latestSession.task_id}`}>
              打开这条记录
            </Link>
          ) : (
            <Link className="wb-button wb-button-tertiary" to="/chat">
              进入聊天
            </Link>
          )}
        </article>
      </div>

      <div className="wb-split">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">建议下一步</p>
              <h3>按这条顺序先走通一次</h3>
            </div>
          </div>
          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>现在先做什么</strong>
              <span>{topNextAction}</span>
            </div>
            {setup.review.blocking_reasons.slice(0, 2).map((item) => (
              <div key={item} className="wb-note">
                <strong>为什么现在先做这一步</strong>
                <span>{item}</span>
              </div>
            ))}
          </div>
          <div className="wb-action-list">
            <button
              type="button"
              className="wb-action-card"
              onClick={() => void submitAction("wizard.refresh", {})}
              disabled={busyActionId === "wizard.refresh"}
            >
              <strong>重新检查当前状态</strong>
              <span>把配置、诊断和后续步骤重新确认一遍。</span>
            </button>
            <Link className="wb-action-card" to={readiness.primaryActionTo}>
              <strong>{readiness.primaryActionLabel}</strong>
              <span>{readiness.summary}</span>
            </Link>
            <Link className="wb-action-card" to="/chat">
              <strong>发第一条消息</strong>
              <span>如果已经准备好，直接开始对话，看 Butler 和 Worker 是否正常协作。</span>
            </Link>
            <Link className="wb-action-card" to="/work">
              <strong>查看任务和待处理事项</strong>
              <span>这里集中看进行中的工作、待确认事项和最近完成的记录。</span>
            </Link>
            <Link className="wb-action-card" to="/advanced">
              <strong>查看详细诊断</strong>
              <span>只有遇到异常或想看更深层信息时，再来这里。</span>
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
              <h3>先看这三件事</h3>
            </div>
          </div>
          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>渠道入口</strong>
              <span>{channelSummaryText}</span>
            </div>
            <div className="wb-note">
              <strong>当前影响</strong>
              <span>{impactSummary}</span>
            </div>
            <div className="wb-note">
              <strong>最近一次结果</strong>
              <span>
                {latestSession
                  ? `最近一次对话是“${latestSessionTitle}”。${latestSessionSummary}`
                  : "还没有最近结果。发一条消息后，这里会开始显示。"}
              </span>
            </div>
          </div>
        </section>

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">背景记忆</p>
              <h3>系统已经替你记住了多少</h3>
            </div>
          </div>
          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>已保存的长期结论</strong>
              <span>{memory.summary.sor_current_count}</span>
            </div>
            <div className="wb-note">
              <strong>可继续沿用的背景片段</strong>
              <span>{context.frames.length}</span>
            </div>
            <div className="wb-note">
              <strong>当前状态</strong>
              <span>
                {context.degraded.is_degraded
                  ? "当前只保留了基础背景摘要，继续聊天没问题，稍后会再慢慢补齐。"
                  : "这轮对话的背景已经连上了，你继续追问时不用重复交代。"}
              </span>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
