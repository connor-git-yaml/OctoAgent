import { createContext, useContext, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import FrontDoorGate from "../FrontDoorGate";
import { useWorkbenchData, type WorkbenchDataState } from "../../platform/queries";
import { formatDateTime, getValueAtPath } from "../../workbench/utils";

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
  const normalized = message.message.trim();
  return normalized || "刚才的操作已经处理完成。";
}

function formatDiagnosticsLabel(status: string): string {
  const normalized = status.trim().toLowerCase();
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
  const diagnosticsNormalized = options.diagnosticsStatus.trim().toLowerCase();
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
    case "/work":
      return "看进行中的事情";
    case "/memory":
      return "回看系统记住的背景";
    case "/settings":
      return "连接模型与常用入口";
    case "/advanced":
      return "只有排错时再来";
    default:
      return "";
  }
}

export default function WorkbenchLayout() {
  const workbench = useWorkbenchData();
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();

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
          <p className="wb-kicker">Workbench Error</p>
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

  const snapshot = workbench.snapshot!;
  const diagnostics = snapshot.resources.diagnostics;
  const sessions = snapshot.resources.sessions;
  const config = snapshot.resources.config;
  const delegation = snapshot.resources.delegation;
  const pendingTotal = sessions.operator_summary?.total_pending ?? 0;
  const runtimeMode =
    String(getValueAtPath(config.current_value, "runtime.llm_mode") ?? "echo")
      .trim()
      .toLowerCase() || "echo";
  const activeWorkCount = delegation.works.filter((item) =>
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
  const suppressChatSetupReviewBanner =
    (location.pathname === "/" || location.pathname === "/chat") &&
    workbench.lastAction?.code === "SETUP_REVIEW_READY";

  return (
    <WorkbenchContext.Provider value={workbench}>
      <div className="wb-shell">
        <aside className={`wb-sidebar ${navOpen ? "is-open" : ""}`}>
            <div className="wb-sidebar-card wb-sidebar-brand">
              <p className="wb-kicker">你的工作台</p>
              <h1>OctoAgent</h1>
              <p>把对话、设置和运行中的事情放在一个地方，常用入口都在这里。</p>
            </div>

          <nav className="wb-nav" aria-label="Workbench Navigation">
            {[
              { to: "/", label: "Chat" },
              { to: "/agents", label: "Agents" },
              { to: "/skills", label: "Skills" },
              { to: "/mcp", label: "MCP" },
              { to: "/work", label: "Work" },
              { to: "/memory", label: "Memory" },
              { to: "/settings", label: "Settings" },
              { to: "/advanced", label: "Advanced" },
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
                type="button"
                className="wb-topbar-menu"
                onClick={() => setNavOpen((current) => !current)}
                aria-label="切换导航"
              >
                导航
              </button>
              <div className="wb-topbar-copy">
                <p className="wb-topbar-meta">
                  更新于 {formatDateTime(snapshot.generated_at)}
                </p>
              </div>
            </div>
            <div className="wb-topbar-actions">
              <span className={`wb-status-pill is-${diagnostics.overall_status}`}>
                {formatDiagnosticsLabel(diagnostics.overall_status)}
              </span>
              <button
                type="button"
                className="wb-button wb-button-secondary wb-button-inline"
                onClick={() => void workbench.refreshSnapshot()}
              >
                刷新
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
      </div>
    </WorkbenchContext.Provider>
  );
}
