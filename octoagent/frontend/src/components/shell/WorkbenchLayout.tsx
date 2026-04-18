import { createContext, useContext, useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import NewSessionModal from "../ChatUI/NewSessionModal";
import DeleteSessionModal from "../ChatUI/DeleteSessionModal";
import FrontDoorGate from "../FrontDoorGate";
import { useWorkbenchData, type WorkbenchDataState } from "../../platform/queries";
import type { SessionProjectionDocument, SessionProjectionItem } from "../../types";
import {
  formatDateTime,
  formatSessionDisplayTitle,
  getValueAtPath,
} from "../../workbench/utils";

const WorkbenchContext = createContext<WorkbenchDataState | null>(null);
const ACTIVE_WORK_STATUSES = new Set(["created", "assigned", "running", "escalated"]);

export function useOptionalWorkbench() {
  return useContext(WorkbenchContext);
}

export function useWorkbench() {
  const value = useOptionalWorkbench();
  if (!value) {
    throw new Error("useWorkbench 必须在 WorkbenchLayout 中使用");
  }
  return value;
}

function formatActionResult(message: { message: string; code: string }): string {
  const normalized = String(message.message ?? "").trim();
  return normalized || "刚才的操作已经处理完成。";
}

function formatDiagnosticsLabel(status: string): string {
  const normalized = String(status ?? "").trim().toLowerCase();
  if (["ready", "ok", "healthy"].includes(normalized)) {
    return "可直接使用";
  }
  if (["warning", "warn", "degraded", "partial"].includes(normalized)) {
    return "受限运行";
  }
  if (["failed", "error", "offline", "unavailable"].includes(normalized)) {
    return "需要检查";
  }
  return "状态检查中";
}

function buildShellStatus(options: {
  runtimeMode: string;
  pendingCount: number;
  pendingTitle: string;
  diagnosticsStatus: string;
  activeWorkCount: number;
}): { title: string; summary: string } {
  const diagnosticsNormalized = String(options.diagnosticsStatus ?? "").trim().toLowerCase();
  if (options.runtimeMode === "echo") {
    return {
      title: "还在体验模式",
      summary: "先连上真实模型后，联网查询、专门角色协作和长期使用才会稳定。",
    };
  }
  if (options.pendingCount > 0) {
    return {
      title: `有 ${options.pendingCount} 项需要处理`,
      summary: options.pendingTitle || "先看一下待处理事项，再继续会更稳。",
    };
  }
  if (!["ready", "ok", "healthy"].includes(diagnosticsNormalized)) {
    return {
      title: "可以继续用，但外部能力受影响",
      summary: "普通对话还能继续，实时查询、外部连接或后台能力可能会变慢或失败。",
    };
  }
  if (options.activeWorkCount > 0) {
    return {
      title: `有 ${options.activeWorkCount} 项事情还在处理中`,
      summary: "你可以继续聊天，也可以去 Work 看当前进度。",
    };
  }
  return {
    title: "现在可以直接开始",
    summary: "直接进入 Chat 发第一条消息即可，不用先看控制台数字。",
  };
}

function renderNavDescription(path: string): string {
  switch (path) {
    case "/":
      return "直接和主助手对话";
    case "/agents":
      return "管理主助手和分工";
    case "/skills":
      return "查看和管理技能";
    case "/mcp":
      return "外部工具和服务连接";
    case "/memory":
      return "回看系统记住的背景";
    case "/settings":
      return "连接模型与常用入口";
    default:
      return "";
  }
}

function formatSessionTitle(session: SessionProjectionItem): string {
  return formatSessionDisplayTitle({
    alias: session.alias,
    title: session.title,
    latestMessageSummary: session.latest_message_summary,
  });
}

function resolveSessionOwnerProfileId(session: SessionProjectionItem): string {
  return session.session_owner_profile_id?.trim() || session.agent_profile_id?.trim() || "";
}

// 基于 agent_profile_id 的稳定色板——同一 Agent 始终同色
const SESSION_ACCENT_PALETTE = [
  "#1f6a5b", "#3b82f6", "#8b5cf6", "#f59e0b", "#ef4444",
  "#06b6d4", "#ec4899", "#10b981", "#6366f1", "#f97316",
];

function sessionAccentColor(agentProfileId: string): string {
  let hash = 0;
  for (let i = 0; i < agentProfileId.length; i++) {
    hash = agentProfileId.charCodeAt(i) + ((hash << 5) - hash);
  }
  return SESSION_ACCENT_PALETTE[Math.abs(hash) % SESSION_ACCENT_PALETTE.length];
}

function generateSessionName(): string {
  const now = new Date();
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  const hh = String(now.getHours()).padStart(2, "0");
  const min = String(now.getMinutes()).padStart(2, "0");
  return `对话 ${mm}-${dd} ${hh}:${min}`;
}

function ChatNavSection({
  sessions,
  currentPath,
  onNavigate,
  onNewSession,
  onDeleteSession,
  resolveAgentName,
  newSessionBusy,
}: {
  sessions: SessionProjectionDocument;
  currentPath: string;
  onNavigate: () => void;
  onNewSession: () => void;
  onDeleteSession: (session: SessionProjectionItem) => void;
  resolveAgentName: (agentProfileId: string) => string;
  newSessionBusy: boolean;
}) {
  const navigate = useNavigate();
  const sessionItems = Array.isArray(sessions.sessions) ? sessions.sessions : [];
  // 只展示 web 渠道的 session，如果没有 web session 才退化到全部
  const webSessions = sessionItems.filter((item) => item.channel === "web");
  const displaySessions = webSessions.length > 0 ? webSessions : sessionItems;

  return (
    <div className="wb-nav-group">
      <div className="wb-nav-session-list">
        {displaySessions.map((session) => {
          const sessionPath = `/chat/${session.session_id}`;
          const isActive = currentPath === sessionPath
            || (currentPath === "/" && session === displaySessions[0]);
          const statusNormalized = session.status?.toLowerCase() ?? "";
          const isRunning = ["running", "waiting_input", "waiting_approval"].includes(
            statusNormalized
          );
          const ownerProfileId = resolveSessionOwnerProfileId(session);
          const ownerLabel = session.session_owner_name?.trim() || (ownerProfileId ? resolveAgentName(ownerProfileId) : "Agent");
          const accent = sessionAccentColor(ownerProfileId);
          return (
            <button
              type="button"
              key={session.session_id}
              className={`wb-nav-session-item ${isActive ? "is-active" : ""} ${isRunning ? "is-running" : ""}`}
              style={{ borderLeftColor: accent }}
              onClick={() => {
                navigate(sessionPath);
                onNavigate();
              }}
            >
              <span className="wb-nav-session-copy">
                <span className="wb-nav-session-title">
                  {formatSessionTitle(session)}
                </span>
                <span className="wb-nav-session-agent-name">
                  {ownerLabel}
                </span>
              </span>
              <span
                role="button"
                tabIndex={-1}
                aria-hidden="true"
                className="wb-nav-session-delete"
                title="删除对话"
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteSession(session);
                }}
              >
                ×
              </span>
            </button>
          );
        })}
        <button
          type="button"
          className="wb-nav-session-item wb-nav-session-new"
          onClick={onNewSession}
          disabled={newSessionBusy}
        >
          {newSessionBusy ? "创建中…" : "+ 新建对话"}
        </button>
      </div>
    </div>
  );
}

export default function WorkbenchLayout() {
  const workbench = useWorkbenchData();
  const [navOpen, setNavOpen] = useState(false);
  const sidebarRef = useRef<HTMLElement>(null);
  const menuBtnRef = useRef<HTMLButtonElement>(null);

  // 移动端点击 sidebar 外部关闭
  useEffect(() => {
    if (!navOpen) return;
    function handleClick(e: MouseEvent) {
      const target = e.target as Node;
      if (sidebarRef.current?.contains(target)) return;
      if (menuBtnRef.current?.contains(target)) return;
      setNavOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [navOpen]);

  const [showNewSessionModal, setShowNewSessionModal] = useState(false);
  const [newSessionBusy, setNewSessionBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<SessionProjectionItem | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  if (workbench.loading && workbench.snapshot === null) {
    return (
      <div className="wb-boot">
        <div className="wb-boot-card">
          <p className="wb-kicker">OctoAgent Workbench</p>
          <h1>正在整理你的工作台</h1>
          <p>我们先读取当前 project、配置、任务和记忆状态。</p>
        </div>
      </div>
    );
  }

  if (workbench.authError && workbench.snapshot === null) {
    return (
      <FrontDoorGate
        error={workbench.authError}
        title="OctoAgent Workbench"
        onRetry={workbench.refreshSnapshot}
      />
    );
  }

  if (workbench.error && workbench.snapshot === null) {
    return (
      <div className="wb-boot">
        <div className="wb-boot-card wb-boot-card-error">
          <p className="wb-kicker">工作台错误</p>
          <h1>工作台暂时打不开</h1>
          <p>{workbench.error}</p>
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={() => void workbench.refreshSnapshot()}
          >
            重新加载
          </button>
        </div>
      </div>
    );
  }

  // 防御：实例重启期间 snapshot 可能为 null 且不在 loading/error 状态
  if (workbench.snapshot === null) {
    return (
      <div className="wb-boot">
        <div className="wb-boot-card">
          <p className="wb-kicker">OctoAgent Workbench</p>
          <h1>正在重新连接</h1>
          <p>系统正在重启中，请稍候…</p>
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={() => void workbench.refreshSnapshot()}
          >
            重新加载
          </button>
        </div>
      </div>
    );
  }

  const snapshot = workbench.snapshot!;
  const diagnostics = snapshot.resources.diagnostics;
  const sessions = snapshot.resources.sessions;
  const config = snapshot.resources.config;
  const delegation = snapshot.resources.delegation;
  const workerProfiles = snapshot.resources.worker_profiles;
  const workerProfileList = Array.isArray(workerProfiles?.profiles) ? workerProfiles.profiles : [];
  const resolveAgentName = (agentProfileId: string): string => {
    const match = workerProfileList.find((p) => p.profile_id === agentProfileId);
    return match?.name || "Agent";
  };
  const pendingTotal = sessions.operator_summary?.total_pending ?? 0;
  const runtimeMode =
    String(getValueAtPath(config.current_value, "runtime.llm_mode") ?? "echo")
      .trim()
      .toLowerCase() || "echo";
  const activeWorkCount = (delegation?.works ?? []).filter((item) =>
    ACTIVE_WORK_STATUSES.has(String(item.status).toLowerCase())
  ).length;
  const operatorItems = Array.isArray(sessions.operator_items) ? sessions.operator_items : [];
  const pendingTitle = operatorItems[0]?.title?.trim() ?? "";
  const shellStatus = buildShellStatus({
    runtimeMode,
    pendingCount: pendingTotal,
    pendingTitle,
    diagnosticsStatus: diagnostics.overall_status,
    activeWorkCount,
  });
  const agentOptions = workerProfileList
    .filter((p) => p.status === "active" && p.origin_kind !== "builtin")
    .map((p) => ({
      profile_id: p.profile_id,
      name: p.name,
    }));

  // 检测当前聚焦会话的 Agent，用于 Modal 默认选中
  const allSessionItems = Array.isArray(sessions?.sessions) ? sessions.sessions : [];
  const currentSessionId = location.pathname.startsWith("/chat/")
    ? location.pathname.slice("/chat/".length)
    : null;
  const currentSession = currentSessionId
    ? allSessionItems.find((s) => s.session_id === currentSessionId)
    : null;
  const currentAgentProfileId = currentSession
    ? resolveSessionOwnerProfileId(currentSession)
    : "";

  const handleCreateSession = async (agentProfileId: string, projectName: string) => {
    setNewSessionBusy(true);
    try {
      const result = await workbench.submitAction("session.create_with_project", {
        agent_profile_id: agentProfileId,
        project_name: projectName,
      });
      if (result?.data?.session_id) {
        setShowNewSessionModal(false);
        navigate(`/chat/${result.data.session_id}`);
      }
    } finally {
      setNewSessionBusy(false);
    }
  };

  const handleNewSession = () => {
    if (newSessionBusy) return;
    if (agentOptions.length === 1) {
      // 单 Agent：跳过 Modal，直接创建
      void handleCreateSession(agentOptions[0].profile_id, generateSessionName());
    } else {
      setShowNewSessionModal(true);
    }
  };

  const suppressChatSetupReviewBanner =
    (location.pathname === "/" || location.pathname.startsWith("/chat")) &&
    workbench.lastAction?.code === "SETUP_REVIEW_READY";

  return (
    <WorkbenchContext.Provider value={workbench}>
      <div className="wb-shell">
        <aside ref={sidebarRef} className={`wb-sidebar ${navOpen ? "is-open" : ""}`}>
            <div className="wb-sidebar-card wb-sidebar-brand">
              <p className="wb-kicker">你的工作台</p>
              <div className="wb-brand-lockup">
                <img
                  className="wb-brand-mark"
                  src="/octo-mark.svg"
                  alt="OctoAgent logo"
                  width={60}
                  height={60}
                />
                <div className="wb-brand-copy">
                  <h1>OctoAgent</h1>
                  <span className="wb-brand-tagline">Personal AI OS</span>
                </div>
              </div>
            </div>

          <nav className="wb-nav" aria-label="Workbench Navigation">
            <ChatNavSection
              sessions={sessions}
              currentPath={location.pathname}
              onNavigate={() => setNavOpen(false)}
              onNewSession={handleNewSession}
              onDeleteSession={setDeleteTarget}
              resolveAgentName={resolveAgentName}
              newSessionBusy={newSessionBusy}
            />
            {[
              { to: "/agents", label: "智能体" },
              { to: "/skills", label: "技能" },
              { to: "/mcp", label: "MCP" },
              { to: "/memory", label: "记忆" },
              { to: "/settings", label: "设置" },
            ].map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  `wb-nav-item ${isActive ? "is-active" : ""}`
                }
                onClick={() => setNavOpen(false)}
              >
                <strong>{item.label}</strong>
                <span>{renderNavDescription(item.to)}</span>
              </NavLink>
            ))}
          </nav>

          <div className="wb-sidebar-card">
            <p className="wb-card-label">当前状态</p>
            <strong>{shellStatus.title}</strong>
            <p>{shellStatus.summary}</p>
          </div>
        </aside>

        <div className="wb-main">
          <header className="wb-topbar">
            <div className="wb-topbar-leading">
              <button
                ref={menuBtnRef}
                type="button"
                className="wb-topbar-menu"
                onClick={() => setNavOpen((current) => !current)}
                aria-label="切换导航"
              >
                ☰
              </button>
              <p className="wb-topbar-meta">
                {formatDateTime(snapshot.generated_at)}
              </p>
            </div>
            <div className="wb-topbar-actions">
              <span className={`wb-status-pill is-${diagnostics.overall_status}`}>
                {formatDiagnosticsLabel(diagnostics.overall_status)}
              </span>
              <button
                type="button"
                className="wb-topbar-refresh"
                onClick={() => void workbench.refreshSnapshot()}
                aria-label="刷新"
                title="刷新"
              >
                ↻
              </button>
            </div>
          </header>

          {workbench.error ? (
            <div className="wb-inline-banner is-error">
              <strong>刚才的操作没有成功。</strong>
              <span>{workbench.error}</span>
            </div>
          ) : null}

          {workbench.lastAction && !suppressChatSetupReviewBanner ? (
            <div className="wb-inline-banner is-muted">
              <strong>{formatActionResult(workbench.lastAction)}</strong>
              <span>{formatDateTime(workbench.lastAction.handled_at)}</span>
            </div>
          ) : null}

          <Outlet />
        </div>

        {showNewSessionModal && (
          <NewSessionModal
            agents={agentOptions}
            defaultAgentId={currentAgentProfileId}
            busy={newSessionBusy}
            onConfirm={handleCreateSession}
            onClose={() => setShowNewSessionModal(false)}
          />
        )}
        {deleteTarget && (
          <DeleteSessionModal
            sessionTitle={formatSessionTitle(deleteTarget)}
            taskCount={0}
            busy={deleteBusy}
            onConfirm={async () => {
              setDeleteBusy(true);
              try {
                const result = await workbench.submitAction("session.delete", {
                  session_id: deleteTarget.session_id,
                });
                if (result?.status === "completed") {
                  setDeleteTarget(null);
                  if (location.pathname === `/chat/${deleteTarget.session_id}`) {
                    navigate("/");
                  }
                } else if (result?.message) {
                  alert(result.message);
                }
              } finally {
                setDeleteBusy(false);
              }
            }}
            onClose={() => setDeleteTarget(null)}
          />
        )}
      </div>
    </WorkbenchContext.Provider>
  );
}
