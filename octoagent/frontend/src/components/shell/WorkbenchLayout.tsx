import { createContext, useContext, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import FrontDoorGate from "../FrontDoorGate";
import { useWorkbenchSnapshot, type WorkbenchSnapshotState } from "../../hooks/useWorkbenchSnapshot";
import { formatDateTime } from "../../workbench/utils";

const WorkbenchContext = createContext<WorkbenchSnapshotState | null>(null);

export function useWorkbench() {
  const value = useContext(WorkbenchContext);
  if (!value) {
    throw new Error("useWorkbench 必须在 WorkbenchLayout 中使用");
  }
  return value;
}

function formatActionResult(message: { message: string; code: string }): string {
  return `${message.message} [${message.code}]`;
}

function renderNavDescription(path: string): string {
  switch (path) {
    case "/":
      return "系统状态与下一步";
    case "/chat":
      return "对话、任务与回复";
    case "/work":
      return "运行中的工作与子任务";
    case "/memory":
      return "系统记住了什么";
    case "/settings":
      return "基础配置与连接";
    case "/advanced":
      return "完整控制面与诊断";
    default:
      return "";
  }
}

export default function WorkbenchLayout() {
  const workbench = useWorkbenchSnapshot();
  const [navOpen, setNavOpen] = useState(false);

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
  const selector = snapshot.resources.project_selector;
  const diagnostics = snapshot.resources.diagnostics;
  const sessions = snapshot.resources.sessions;
  const memory = snapshot.resources.memory;
  const currentProject =
    selector.available_projects.find((item) => item.project_id === selector.current_project_id) ??
    null;
  const currentWorkspace =
    selector.available_workspaces.find(
      (item) => item.workspace_id === selector.current_workspace_id
    ) ?? null;
  const pendingTotal = sessions.operator_summary?.total_pending ?? 0;
  const activeMemoryCount =
    memory.summary.sor_current_count +
    memory.summary.fragment_count +
    memory.summary.vault_ref_count;
  const workCount = snapshot.resources.delegation.works.length;

  return (
    <WorkbenchContext.Provider value={workbench}>
      <div className="wb-shell">
        <aside className={`wb-sidebar ${navOpen ? "is-open" : ""}`}>
          <div className="wb-sidebar-card wb-sidebar-brand">
            <p className="wb-kicker">你的工作台</p>
            <h1>OctoAgent</h1>
            <p>先从状态、设置、对话和工作开始，常用入口都在这里。</p>
          </div>

          <nav className="wb-nav" aria-label="Workbench Navigation">
            {[
              { to: "/", label: "Home" },
              { to: "/chat", label: "Chat" },
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
            <p className="wb-card-label">当前 Project</p>
            <strong>{currentProject?.name ?? selector.current_project_id}</strong>
            <p>{currentWorkspace?.name ?? selector.current_workspace_id}</p>
          </div>

          <div className="wb-sidebar-grid">
            <div className="wb-sidebar-card">
              <p className="wb-card-label">待你确认</p>
              <strong>{sessions.operator_summary?.total_pending ?? 0}</strong>
              <p>
                approvals {sessions.operator_summary?.approvals ?? 0} / pairing{" "}
                {sessions.operator_summary?.pairing_requests ?? 0}
              </p>
            </div>
            <div className="wb-sidebar-card">
              <p className="wb-card-label">记忆摘要</p>
              <strong>{memory.summary.sor_current_count}</strong>
              <p>current records</p>
            </div>
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
                  {currentProject?.slug ?? selector.current_project_id} /{" "}
                  {currentWorkspace?.slug ?? selector.current_workspace_id}
                </p>
                <h2>{currentProject?.name ?? "OctoAgent Workbench"}</h2>
                <div className="wb-chip-row">
                  <span className="wb-chip">待确认 {pendingTotal}</span>
                  <span className="wb-chip">可见 work {workCount}</span>
                  <span className="wb-chip">记忆记录 {activeMemoryCount}</span>
                  <span className="wb-chip">更新于 {formatDateTime(snapshot.generated_at)}</span>
                </div>
              </div>
            </div>
            <div className="wb-topbar-actions">
              <span className={`wb-status-pill is-${diagnostics.overall_status}`}>
                {diagnostics.overall_status}
              </span>
              <button
                type="button"
                className="wb-button wb-button-secondary"
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

          {workbench.lastAction ? (
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
