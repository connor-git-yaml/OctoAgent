import {
  startTransition,
  useDeferredValue,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link } from "react-router-dom";
import {
  executeControlAction,
  fetchControlEvents,
  fetchControlResource,
  fetchControlSnapshot,
} from "../api/client";
import type {
  ActionResultEnvelope,
  ActionRequestEnvelope,
  AutomationJobItem,
  ControlPlaneEvent,
  ControlPlaneResourceRef,
  ControlPlaneSnapshot,
  OperatorActionKind,
  OperatorInboxItem,
  SessionProjectionItem,
} from "../types";

type SectionId =
  | "dashboard"
  | "projects"
  | "sessions"
  | "operator"
  | "automation"
  | "diagnostics"
  | "config"
  | "channels";

const SECTION_LABELS: Array<{ id: SectionId; label: string; accent: string }> = [
  { id: "dashboard", label: "Dashboard", accent: "总览" },
  { id: "projects", label: "Projects", accent: "Project / Workspace" },
  { id: "sessions", label: "Sessions", accent: "Session Center" },
  { id: "operator", label: "Operator", accent: "Approvals / Retry / Cancel" },
  { id: "automation", label: "Automation", accent: "Scheduler" },
  { id: "diagnostics", label: "Diagnostics", accent: "Runtime Console" },
  { id: "config", label: "Config", accent: "Schema + uiHints" },
  { id: "channels", label: "Channels", accent: "Telegram / Devices" },
];

type ControlResourceRoute =
  | "wizard"
  | "config"
  | "project-selector"
  | "sessions"
  | "automation"
  | "diagnostics";

type SnapshotResourceKey = keyof ControlPlaneSnapshot["resources"];

const RESOURCE_ROUTE_BY_TYPE: Record<string, ControlResourceRoute> = {
  wizard_session: "wizard",
  config_schema: "config",
  project_selector: "project-selector",
  session_projection: "sessions",
  automation_job: "automation",
  diagnostics_summary: "diagnostics",
};

const SNAPSHOT_RESOURCE_KEY_BY_ROUTE: Record<
  ControlResourceRoute,
  SnapshotResourceKey
> = {
  wizard: "wizard",
  config: "config",
  "project-selector": "project_selector",
  sessions: "sessions",
  automation: "automation",
  diagnostics: "diagnostics",
};

function makeRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formatDateTime(value?: string | null): string {
  if (!value) {
    return "未记录";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatRelativeStatus(value: string): string {
  return value.replace(/_/g, " ").replace(/-/g, " ");
}

function formatJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

function formatActionResult(result: ActionResultEnvelope): string {
  return `${result.message} [${result.code}]`;
}

function dedupeEvents(events: ControlPlaneEvent[]): ControlPlaneEvent[] {
  const seen = new Set<string>();
  return events.filter((event) => {
    if (seen.has(event.event_id)) {
      return false;
    }
    seen.add(event.event_id);
    return true;
  });
}

function resolveResourceRoutes(
  refs: ControlPlaneResourceRef[]
): ControlResourceRoute[] {
  return Array.from(
    new Set(
      refs
        .map((ref) => RESOURCE_ROUTE_BY_TYPE[ref.resource_type])
        .filter((value): value is ControlResourceRoute => Boolean(value))
    )
  );
}

function isControlResourceDocument(
  value: unknown
): value is { resource_type: string; resource_id: string } {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.resource_type === "string" &&
    typeof candidate.resource_id === "string"
  );
}

async function loadControlResource(
  route: ControlResourceRoute
): Promise<ControlPlaneSnapshot["resources"][SnapshotResourceKey]> {
  switch (route) {
    case "wizard":
      return fetchControlResource("wizard");
    case "config":
      return fetchControlResource("config");
    case "project-selector":
      return fetchControlResource("project-selector");
    case "sessions":
      return fetchControlResource("sessions");
    case "automation":
      return fetchControlResource("automation");
    case "diagnostics":
      return fetchControlResource("diagnostics");
  }
}

function mapQuickAction(
  item: OperatorInboxItem,
  kind: OperatorActionKind
): { actionId: string; params: Record<string, unknown> } | null {
  if (kind === "approve_once") {
    return {
      actionId: "operator.approval.resolve",
      params: {
        approval_id: item.item_id.split(":")[1] ?? "",
        mode: "once",
      },
    };
  }
  if (kind === "approve_always") {
    return {
      actionId: "operator.approval.resolve",
      params: {
        approval_id: item.item_id.split(":")[1] ?? "",
        mode: "always",
      },
    };
  }
  if (kind === "deny") {
    return {
      actionId: "operator.approval.resolve",
      params: {
        approval_id: item.item_id.split(":")[1] ?? "",
        mode: "deny",
      },
    };
  }
  if (kind === "cancel_task") {
    return { actionId: "operator.task.cancel", params: { item_id: item.item_id } };
  }
  if (kind === "retry_task") {
    return { actionId: "operator.task.retry", params: { item_id: item.item_id } };
  }
  if (kind === "ack_alert") {
    return { actionId: "operator.alert.ack", params: { item_id: item.item_id } };
  }
  if (kind === "approve_pairing") {
    return { actionId: "channel.pairing.approve", params: { item_id: item.item_id } };
  }
  if (kind === "reject_pairing") {
    return { actionId: "channel.pairing.reject", params: { item_id: item.item_id } };
  }
  return null;
}

function sessionMatches(item: SessionProjectionItem, keyword: string): boolean {
  if (!keyword) {
    return true;
  }
  const haystack = [
    item.title,
    item.task_id,
    item.thread_id,
    item.latest_message_summary,
    item.requester_id,
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(keyword.toLowerCase());
}

function statusTone(status: string): string {
  const normalized = status.toLowerCase();
  if (normalized.includes("fail") || normalized.includes("reject")) {
    return "danger";
  }
  if (normalized.includes("running") || normalized.includes("deferred")) {
    return "info";
  }
  if (normalized.includes("pause") || normalized.includes("wait")) {
    return "warning";
  }
  return "success";
}

export default function ControlPlane() {
  const [snapshot, setSnapshot] = useState<ControlPlaneSnapshot | null>(null);
  const [events, setEvents] = useState<ControlPlaneEvent[]>([]);
  const [activeSection, setActiveSection] = useState<SectionId>("dashboard");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyActionId, setBusyActionId] = useState<string | null>(null);
  const [lastAction, setLastAction] = useState<ActionResultEnvelope | null>(null);
  const [sessionFilter, setSessionFilter] = useState("");
  const deferredSessionFilter = useDeferredValue(sessionFilter);
  const [configDraft, setConfigDraft] = useState("{}");
  const [configDirty, setConfigDirty] = useState(false);
  const configDirtyRef = useRef(false);
  const [restoreDraft, setRestoreDraft] = useState({
    bundle: "",
    targetRoot: "",
  });
  const [importDraft, setImportDraft] = useState({
    inputPath: "",
    sourceFormat: "normalized_jsonl",
  });
  const [automationDraft, setAutomationDraft] = useState({
    name: "",
    actionId: "diagnostics.refresh",
    scheduleKind: "interval",
    scheduleExpr: "3600",
    enabled: true,
  });

  async function refreshEvents() {
    const eventPayload = await fetchControlEvents(undefined, 50);
    startTransition(() => {
      setEvents(dedupeEvents(eventPayload.events));
    });
  }

  async function reloadData(options?: { preserveConfigDraft?: boolean }) {
    const preserveConfigDraft = options?.preserveConfigDraft ?? true;
    const [nextSnapshot, eventPayload] = await Promise.all([
      fetchControlSnapshot(),
      fetchControlEvents(undefined, 50),
    ]);
    startTransition(() => {
      setSnapshot(nextSnapshot);
      setEvents(dedupeEvents(eventPayload.events));
      if (!preserveConfigDraft || !configDirtyRef.current) {
        setConfigDraft(formatJson(nextSnapshot.resources.config.current_value));
        setConfigDirty(false);
        configDirtyRef.current = false;
      }
    });
  }

  async function refreshResources(
    refs: ControlPlaneResourceRef[],
    options?: { preserveConfigDraft?: boolean }
  ) {
    const preserveConfigDraft = options?.preserveConfigDraft ?? true;
    const routes = resolveResourceRoutes(refs);

    if (routes.length === 0) {
      await reloadData({ preserveConfigDraft });
      return;
    }

    try {
      const updates = await Promise.all(routes.map((route) => loadControlResource(route)));
      if (!updates.every((item) => isControlResourceDocument(item))) {
        throw new Error("control resource refresh returned malformed payload");
      }
      startTransition(() => {
        setSnapshot((current) => {
          if (!current) {
            return current;
          }

          const nextResources = { ...current.resources };
          routes.forEach((route, index) => {
            const key = SNAPSHOT_RESOURCE_KEY_BY_ROUTE[route];
            (nextResources as Record<SnapshotResourceKey, unknown>)[key] =
              updates[index];
          });

          const nextSnapshot: ControlPlaneSnapshot = {
            ...current,
            resources: nextResources,
            generated_at: new Date().toISOString(),
          };

          if (
            !preserveConfigDraft ||
            !configDirtyRef.current ||
            routes.includes("config")
          ) {
            setConfigDraft(formatJson(nextSnapshot.resources.config.current_value));
            setConfigDirty(false);
            configDirtyRef.current = false;
          }

          return nextSnapshot;
        });
      });
      await refreshEvents();
    } catch {
      await reloadData({ preserveConfigDraft });
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function boot() {
      try {
        const [nextSnapshot, eventPayload] = await Promise.all([
          fetchControlSnapshot(),
          fetchControlEvents(undefined, 50),
        ]);
        if (cancelled) {
          return;
        }
        startTransition(() => {
          setSnapshot(nextSnapshot);
          setEvents(eventPayload.events);
          setConfigDraft(formatJson(nextSnapshot.resources.config.current_value));
        });
      } catch (err) {
        if (cancelled) {
          return;
        }
        setError(err instanceof Error ? err.message : "控制台加载失败");
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void boot();
    const interval = window.setInterval(() => {
      void reloadData();
    }, 15000);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    configDirtyRef.current = configDirty;
  }, [configDirty]);

  const filteredSessions = (snapshot?.resources.sessions.sessions ?? []).filter((item) =>
    sessionMatches(item, deferredSessionFilter)
  );

  async function submitAction(
    actionId: string,
    params: Record<string, unknown>,
    options?: { refreshConfigDraft?: boolean }
  ) {
    setBusyActionId(actionId);
    setError(null);
    try {
      const payload: ActionRequestEnvelope = {
        contract_version: snapshot?.contract_version,
        request_id: makeRequestId(),
        action_id: actionId,
        surface: "web",
        actor: {
          actor_id: "user:web",
          actor_label: "Owner",
        },
        params,
      };
      const result = await executeControlAction(payload);
      setLastAction(result);
      await refreshResources(result.resource_refs, {
        preserveConfigDraft: !(options?.refreshConfigDraft ?? false),
      });
      return result;
    } catch (err) {
      const message =
        err instanceof Error ? err.message : `动作执行失败: ${actionId}`;
      setError(message);
      return null;
    } finally {
      setBusyActionId(null);
    }
  }

  if (loading) {
    return <div className="control-loading">正在装载 Control Plane...</div>;
  }

  if (error && snapshot === null) {
    return (
      <div className="control-empty-state">
        <h1>Control Plane</h1>
        <p>{error}</p>
      </div>
    );
  }

  if (!snapshot) {
    return (
      <div className="control-empty-state">
        <h1>Control Plane</h1>
        <p>当前没有可用快照。</p>
      </div>
    );
  }

  const { wizard, config, project_selector, sessions, automation, diagnostics } =
    snapshot.resources;
  const availableProjects = project_selector.available_projects ?? [];
  const availableWorkspaces = project_selector.available_workspaces ?? [];
  const currentProject =
    availableProjects.find(
      (item) => item.project_id === project_selector.current_project_id
    ) ?? null;
  const currentWorkspace =
    availableWorkspaces.find(
      (item) => item.workspace_id === project_selector.current_workspace_id
    ) ?? null;
  const operatorItems = sessions.operator_items ?? [];
  const pairingItems = operatorItems.filter((item) => item.kind === "pairing_request");
  const diagnosticTone = statusTone(diagnostics.overall_status);

  return (
    <div className="control-shell">
      <aside className="control-sidebar">
        <div className="control-brand">
          <p className="eyebrow">Feature 026</p>
          <h1>OctoAgent Control Plane</h1>
          <p>
            统一消费 wizard / project / session / automation / diagnostics /
            config contract。
          </p>
        </div>
        <nav className="control-nav" aria-label="Control sections">
          {SECTION_LABELS.map((section) => (
            <button
              key={section.id}
              type="button"
              className={
                section.id === activeSection
                  ? "control-nav-item active"
                  : "control-nav-item"
              }
              onClick={() => setActiveSection(section.id)}
            >
              <span>{section.label}</span>
              <small>{section.accent}</small>
            </button>
          ))}
        </nav>
        <div className="control-sidebar-foot">
          <div className="chip-stack">
            <span className={`tone-chip ${diagnosticTone}`}>
              Diagnostics {diagnostics.overall_status}
            </span>
            <span className="tone-chip neutral">
              Events {events.length}
            </span>
          </div>
        </div>
      </aside>

      <main className="control-main">
        <header className="control-hero">
          <div>
            <p className="eyebrow">Current Selection</p>
            <h2>{currentProject?.name ?? "Default Project"}</h2>
            <div className="hero-meta">
              <span>{currentProject?.project_id ?? project_selector.current_project_id}</span>
              <span>{currentWorkspace?.name ?? "Primary Workspace"}</span>
              <span>{formatDateTime(snapshot.generated_at)}</span>
            </div>
          </div>
          <div className="hero-actions">
            <button
              type="button"
              className="primary-button"
              onClick={() =>
                void reloadData({ preserveConfigDraft: configDirtyRef.current })
              }
            >
              刷新快照
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={() => void submitAction("diagnostics.refresh", {})}
              disabled={busyActionId === "diagnostics.refresh"}
            >
              诊断刷新
            </button>
          </div>
        </header>

        {lastAction ? (
          <section
            className={`action-banner ${statusTone(lastAction.status)}`}
            role="status"
          >
            <strong>{lastAction.action_id}</strong>
            <span>{formatActionResult(lastAction)}</span>
            <small>{formatDateTime(lastAction.handled_at)}</small>
          </section>
        ) : null}
        {error ? <section className="action-banner danger">{error}</section> : null}

        {activeSection === "dashboard" ? (
          <section className="section-grid">
            <article className="panel hero-panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Wizard</p>
                  <h3>{wizard.current_step || "未开始"}</h3>
                </div>
                <span className={`tone-chip ${statusTone(wizard.status)}`}>
                  {formatRelativeStatus(wizard.status)}
                </span>
              </div>
              <p>{wizard.blocking_reason || "Onboarding 已具备继续推进条件。"}</p>
              <div className="action-row">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => void submitAction("wizard.refresh", {})}
                  disabled={busyActionId === "wizard.refresh"}
                >
                  刷新 Wizard
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => void submitAction("wizard.restart", {})}
                  disabled={busyActionId === "wizard.restart"}
                >
                  重新开始
                </button>
              </div>
            </article>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Projects</p>
                  <h3>{availableProjects.length}</h3>
                </div>
                <span className="tone-chip neutral">
                  Workspace {availableWorkspaces.length}
                </span>
              </div>
              <p>
                当前 Project:{" "}
                <strong>{currentProject?.name ?? project_selector.current_project_id}</strong>
              </p>
              {project_selector.fallback_reason ? (
                <p className="muted">{project_selector.fallback_reason}</p>
              ) : null}
            </article>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Session Center</p>
                  <h3>{sessions.sessions.length}</h3>
                </div>
                <span className="tone-chip neutral">
                  Operator {sessions.operator_summary?.total_pending ?? 0}
                </span>
              </div>
              <p>聚合 thread/task/execution/operator 状态，支持 focus/export/cancel/resume。</p>
              <div className="event-list">
                {sessions.sessions.slice(0, 2).map((session) => (
                  <div key={session.session_id} className="event-item">
                    <div>
                      <strong>{session.title || session.task_id}</strong>
                      <p>{session.latest_message_summary || "暂无消息摘要"}</p>
                    </div>
                    <small>{session.status}</small>
                  </div>
                ))}
              </div>
            </article>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Diagnostics</p>
                  <h3>{diagnostics.subsystems.length}</h3>
                </div>
                <span className={`tone-chip ${diagnosticTone}`}>
                  {diagnostics.overall_status}
                </span>
              </div>
              <div className="diagnostics-grid">
                {diagnostics.subsystems.slice(0, 2).map((item) => (
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
                  <p className="eyebrow">Automation</p>
                  <h3>{automation.jobs.length}</h3>
                </div>
                <span className="tone-chip neutral">
                  Runs {automation.run_history_cursor || "none"}
                </span>
              </div>
              <p>统一 scheduler/job 控制面，run-now / pause / resume / delete 全部走 action registry。</p>
            </article>

            <article className="panel wide">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Ops Workbench</p>
                  <h3>统一入口</h3>
                </div>
              </div>
              <div className="ops-grid">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => void submitAction("backup.create", {})}
                  disabled={busyActionId === "backup.create"}
                >
                  创建 Backup
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => void submitAction("update.dry_run", {})}
                  disabled={busyActionId === "update.dry_run"}
                >
                  Update Dry Run
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => void submitAction("update.apply", {})}
                  disabled={busyActionId === "update.apply"}
                >
                  执行 Update
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => void submitAction("runtime.verify", {})}
                  disabled={busyActionId === "runtime.verify"}
                >
                  Runtime Verify
                </button>
              </div>
            </article>
          </section>
        ) : null}

        {activeSection === "projects" ? (
          <section className="stack-section">
            {availableProjects.map((project) => (
              <article key={project.project_id} className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">{project.project_id}</p>
                    <h3>{project.name}</h3>
                  </div>
                  <span
                    className={`tone-chip ${
                      project.project_id === project_selector.current_project_id
                        ? "success"
                        : "neutral"
                    }`}
                  >
                    {project.project_id === project_selector.current_project_id
                      ? "当前"
                      : formatRelativeStatus(project.status)}
                  </span>
                </div>
                <p className="muted">Slug: {project.slug}</p>
                <div className="workspace-list">
                  {availableWorkspaces
                    .filter((workspace) => workspace.project_id === project.project_id)
                    .map((workspace) => (
                      <div key={workspace.workspace_id} className="workspace-card">
                        <div>
                          <strong>{workspace.name}</strong>
                          <p>{workspace.root_path || workspace.slug}</p>
                        </div>
                        <button
                          type="button"
                          className="ghost-button"
                          onClick={() =>
                            void submitAction("project.select", {
                              project_id: project.project_id,
                              workspace_id: workspace.workspace_id,
                            })
                          }
                          disabled={busyActionId === "project.select"}
                        >
                          切换到 {workspace.name}
                        </button>
                      </div>
                    ))}
                </div>
              </article>
            ))}
          </section>
        ) : null}

        {activeSection === "sessions" ? (
          <section className="stack-section">
            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Session Center</p>
                  <h3>会话与执行投影</h3>
                </div>
                <input
                  className="search-input"
                  value={sessionFilter}
                  onChange={(event) => setSessionFilter(event.target.value)}
                  placeholder="搜索 task / thread / requester"
                />
              </div>
            </article>
            {filteredSessions.map((session) => (
              <article key={session.session_id} className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">{session.thread_id}</p>
                    <h3>{session.title || session.task_id}</h3>
                  </div>
                  <span className={`tone-chip ${statusTone(session.status)}`}>
                    {session.status}
                  </span>
                </div>
                <p>{session.latest_message_summary || "暂无消息摘要"}</p>
                <div className="meta-grid">
                  <span>Task: {session.task_id}</span>
                  <span>Channel: {session.channel}</span>
                  <span>Requester: {session.requester_id}</span>
                  <span>Updated: {formatDateTime(session.latest_event_at)}</span>
                </div>
                <div className="action-row">
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      void submitAction("session.focus", {
                        thread_id: session.thread_id,
                      })
                    }
                    disabled={busyActionId === "session.focus"}
                  >
                    聚焦
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() =>
                      void submitAction("session.export", {
                        thread_id: session.thread_id,
                        task_id: session.task_id,
                      })
                    }
                    disabled={busyActionId === "session.export"}
                  >
                    导出
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() =>
                      void submitAction("session.interrupt", {
                        task_id: session.task_id,
                      })
                    }
                    disabled={busyActionId === "session.interrupt"}
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() =>
                      void submitAction("session.resume", {
                        task_id: session.task_id,
                      })
                    }
                    disabled={busyActionId === "session.resume"}
                  >
                    恢复
                  </button>
                  <Link className="inline-link" to={`/tasks/${session.task_id}`}>
                    打开详情
                  </Link>
                  {session.detail_refs.execution_api ? (
                    <a
                      className="inline-link"
                      href={session.detail_refs.execution_api}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Execution API
                    </a>
                  ) : null}
                </div>
              </article>
            ))}
          </section>
        ) : null}

        {activeSection === "operator" ? (
          <section className="stack-section">
            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Operator Inbox</p>
                  <h3>{sessions.operator_summary?.total_pending ?? 0}</h3>
                </div>
                <span className="tone-chip neutral">
                  Approvals {sessions.operator_summary?.approvals ?? 0}
                </span>
              </div>
              <div className="meta-grid">
                <span>Alerts {sessions.operator_summary?.alerts ?? 0}</span>
                <span>Retryables {sessions.operator_summary?.retryable_failures ?? 0}</span>
                <span>Pairings {sessions.operator_summary?.pairing_requests ?? 0}</span>
              </div>
            </article>
            {operatorItems.map((item) => (
              <article key={item.item_id} className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">{item.kind}</p>
                    <h3>{item.title}</h3>
                  </div>
                  <span className={`tone-chip ${statusTone(item.state)}`}>
                    {item.state}
                  </span>
                </div>
                <p>{item.summary}</p>
                <div className="meta-grid">
                  <span>Item: {item.item_id}</span>
                  <span>Task: {item.task_id ?? "-"}</span>
                  <span>Thread: {item.thread_id ?? "-"}</span>
                  <span>Created: {formatDateTime(item.created_at)}</span>
                </div>
                <div className="action-row">
                  {item.quick_actions.map((action) => {
                    const mapped = mapQuickAction(item, action.kind);
                    if (!mapped) {
                      return null;
                    }
                    return (
                      <button
                        key={`${item.item_id}-${action.kind}`}
                        type="button"
                        className={
                          action.style === "primary"
                            ? "secondary-button"
                            : "ghost-button"
                        }
                        onClick={() =>
                          void submitAction(mapped.actionId, mapped.params)
                        }
                        disabled={!action.enabled || busyActionId === mapped.actionId}
                      >
                        {action.label}
                      </button>
                    );
                  })}
                </div>
              </article>
            ))}
          </section>
        ) : null}

        {activeSection === "automation" ? (
          <section className="stack-section">
            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Automation Create</p>
                  <h3>调度新作业</h3>
                </div>
              </div>
              <div className="form-grid">
                <label>
                  名称
                  <input
                    value={automationDraft.name}
                    onChange={(event) =>
                      setAutomationDraft((current) => ({
                        ...current,
                        name: event.target.value,
                      }))
                    }
                  />
                </label>
                <label>
                  Action ID
                  <input
                    value={automationDraft.actionId}
                    onChange={(event) =>
                      setAutomationDraft((current) => ({
                        ...current,
                        actionId: event.target.value,
                      }))
                    }
                  />
                </label>
                <label>
                  Schedule Kind
                  <select
                    value={automationDraft.scheduleKind}
                    onChange={(event) =>
                      setAutomationDraft((current) => ({
                        ...current,
                        scheduleKind: event.target.value,
                      }))
                    }
                  >
                    <option value="interval">interval</option>
                    <option value="cron">cron</option>
                    <option value="once">once</option>
                  </select>
                </label>
                <label>
                  Schedule Expr
                  <input
                    value={automationDraft.scheduleExpr}
                    onChange={(event) =>
                      setAutomationDraft((current) => ({
                        ...current,
                        scheduleExpr: event.target.value,
                      }))
                    }
                  />
                </label>
                <label className="checkbox-line">
                  <input
                    type="checkbox"
                    checked={automationDraft.enabled}
                    onChange={(event) =>
                      setAutomationDraft((current) => ({
                        ...current,
                        enabled: event.target.checked,
                      }))
                    }
                  />
                  创建后立即启用
                </label>
              </div>
              <div className="action-row">
                <button
                  type="button"
                  className="primary-button"
                  onClick={() =>
                    void submitAction("automation.create", {
                      name: automationDraft.name,
                      action_id: automationDraft.actionId,
                      schedule_kind: automationDraft.scheduleKind,
                      schedule_expr: automationDraft.scheduleExpr,
                      enabled: automationDraft.enabled,
                    })
                  }
                  disabled={busyActionId === "automation.create"}
                >
                  创建作业
                </button>
              </div>
            </article>
            {automation.jobs.map((item: AutomationJobItem) => (
              <article key={item.job.job_id} className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">{item.job.job_id}</p>
                    <h3>{item.job.name}</h3>
                  </div>
                  <span className={`tone-chip ${statusTone(item.status)}`}>
                    {item.status}
                  </span>
                </div>
                <div className="meta-grid">
                  <span>Action: {item.job.action_id}</span>
                  <span>Schedule: {item.job.schedule_kind}</span>
                  <span>Expr: {item.job.schedule_expr}</span>
                  <span>Next: {formatDateTime(item.next_run_at)}</span>
                </div>
                {item.last_run ? (
                  <p className="muted">
                    Last Run: {item.last_run.status} / {formatDateTime(item.last_run.completed_at)}
                  </p>
                ) : null}
                {item.degraded_reason ? (
                  <p className="warning-text">{item.degraded_reason}</p>
                ) : null}
                <div className="action-row">
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      void submitAction("automation.run", { job_id: item.job.job_id })
                    }
                    disabled={busyActionId === "automation.run"}
                  >
                    Run Now
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() =>
                      void submitAction("automation.pause", { job_id: item.job.job_id })
                    }
                    disabled={busyActionId === "automation.pause"}
                  >
                    Pause
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() =>
                      void submitAction("automation.resume", { job_id: item.job.job_id })
                    }
                    disabled={busyActionId === "automation.resume"}
                  >
                    Resume
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() =>
                      void submitAction("automation.delete", { job_id: item.job.job_id })
                    }
                    disabled={busyActionId === "automation.delete"}
                  >
                    Delete
                  </button>
                </div>
              </article>
            ))}
          </section>
        ) : null}

        {activeSection === "diagnostics" ? (
          <section className="stack-section">
            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Runtime Diagnostics Console</p>
                  <h3>{diagnostics.overall_status}</h3>
                </div>
                <span className={`tone-chip ${diagnosticTone}`}>
                  {diagnostics.recent_failures.length} recent failures
                </span>
              </div>
              <div className="diagnostics-grid">
                {diagnostics.subsystems.map((item) => (
                  <div key={item.subsystem_id} className="diagnostic-card">
                    <strong>{item.label}</strong>
                    <span className={`tone-chip ${statusTone(item.status)}`}>
                      {item.status}
                    </span>
                    <p>{item.summary}</p>
                    {item.detail_ref ? (
                      <a href={item.detail_ref} target="_blank" rel="noreferrer">
                        深入查看
                      </a>
                    ) : null}
                  </div>
                ))}
              </div>
            </article>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Restore / Import / Runtime</p>
                  <h3>统一运维入口</h3>
                </div>
              </div>
              <div className="form-grid">
                <label>
                  Restore Bundle
                  <input
                    value={restoreDraft.bundle}
                    onChange={(event) =>
                      setRestoreDraft((current) => ({
                        ...current,
                        bundle: event.target.value,
                      }))
                    }
                    placeholder="/path/to/bundle.zip"
                  />
                </label>
                <label>
                  Restore Target Root
                  <input
                    value={restoreDraft.targetRoot}
                    onChange={(event) =>
                      setRestoreDraft((current) => ({
                        ...current,
                        targetRoot: event.target.value,
                      }))
                    }
                    placeholder="/path/to/restore-root"
                  />
                </label>
                <label>
                  Import Path
                  <input
                    value={importDraft.inputPath}
                    onChange={(event) =>
                      setImportDraft((current) => ({
                        ...current,
                        inputPath: event.target.value,
                      }))
                    }
                    placeholder="/path/to/chat.jsonl"
                  />
                </label>
                <label>
                  Source Format
                  <input
                    value={importDraft.sourceFormat}
                    onChange={(event) =>
                      setImportDraft((current) => ({
                        ...current,
                        sourceFormat: event.target.value,
                      }))
                    }
                  />
                </label>
              </div>
              <div className="action-row">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() =>
                    void submitAction("restore.plan", {
                      bundle: restoreDraft.bundle,
                      target_root: restoreDraft.targetRoot,
                    })
                  }
                  disabled={busyActionId === "restore.plan"}
                >
                  生成 Restore Plan
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() =>
                    void submitAction("import.run", {
                      input_path: importDraft.inputPath,
                      source_format: importDraft.sourceFormat,
                    })
                  }
                  disabled={busyActionId === "import.run"}
                >
                  执行 Import
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => void submitAction("runtime.restart", {})}
                  disabled={busyActionId === "runtime.restart"}
                >
                  Runtime Restart
                </button>
              </div>
            </article>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Recent Control Events</p>
                  <h3>{events.length}</h3>
                </div>
              </div>
              <div className="event-list">
                {events.map((event) => (
                  <div key={`${event.event_type}-${event.request_id}-${event.occurred_at}`} className="event-item">
                    <div>
                      <strong>{event.event_type}</strong>
                      <p>{event.payload_summary}</p>
                    </div>
                    <small>{formatDateTime(event.occurred_at)}</small>
                  </div>
                ))}
              </div>
            </article>
          </section>
        ) : null}

        {activeSection === "config" ? (
          <section className="stack-section">
            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Config Center</p>
                  <h3>Schema + uiHints</h3>
                </div>
                <button
                  type="button"
                  className="primary-button"
                  onClick={() => {
                    try {
                      const parsed = JSON.parse(configDraft) as Record<string, unknown>;
                      void submitAction(
                        "config.apply",
                        { config: parsed },
                        { refreshConfigDraft: true }
                      );
                    } catch {
                      setError("配置 JSON 解析失败");
                    }
                  }}
                  disabled={busyActionId === "config.apply"}
                >
                  保存配置
                </button>
              </div>
              <div className="config-layout">
                <textarea
                  className="config-editor"
                  value={configDraft}
                  onChange={(event) => {
                    setConfigDraft(event.target.value);
                    configDirtyRef.current = true;
                    setConfigDirty(true);
                  }}
                  spellCheck={false}
                />
                <div className="config-hints">
                  {Object.values(config.ui_hints)
                    .sort((left, right) => left.order - right.order)
                    .map((hint) => (
                      <div key={hint.field_path} className="hint-card">
                        <strong>{hint.label || hint.field_path}</strong>
                        <p>{hint.description || hint.field_path}</p>
                        <small>
                          {hint.section} / {hint.widget}
                        </small>
                      </div>
                    ))}
                </div>
              </div>
              <div className="meta-grid">
                {config.validation_rules.map((rule) => (
                  <span key={rule}>{rule}</span>
                ))}
              </div>
            </article>
          </section>
        ) : null}

        {activeSection === "channels" ? (
          <section className="stack-section">
            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Channel / Device Management</p>
                  <h3>Telegram</h3>
                </div>
              </div>
              <div className="meta-grid">
                <span>
                  Enabled {String((diagnostics.channel_summary.telegram as Record<string, unknown> | undefined)?.enabled ?? false)}
                </span>
                <span>
                  Mode {String((diagnostics.channel_summary.telegram as Record<string, unknown> | undefined)?.mode ?? "-")}
                </span>
                <span>
                  DM Policy {String((diagnostics.channel_summary.telegram as Record<string, unknown> | undefined)?.dm_policy ?? "-")}
                </span>
                <span>
                  Group Policy {String((diagnostics.channel_summary.telegram as Record<string, unknown> | undefined)?.group_policy ?? "-")}
                </span>
                <span>
                  Pending Pairings {String((diagnostics.channel_summary.telegram as Record<string, unknown> | undefined)?.pending_pairings ?? 0)}
                </span>
                <span>
                  Approved Users {String((diagnostics.channel_summary.telegram as Record<string, unknown> | undefined)?.approved_users ?? 0)}
                </span>
              </div>
            </article>
            {pairingItems.map((item) => (
              <article key={item.item_id} className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">Pairing Request</p>
                    <h3>{item.title}</h3>
                  </div>
                  <span className="tone-chip warning">{item.state}</span>
                </div>
                <p>{item.summary}</p>
                <div className="meta-grid">
                  {Object.entries(item.metadata).map(([key, value]) => (
                    <span key={key}>
                      {key}: {value}
                    </span>
                  ))}
                </div>
                <div className="action-row">
                  {item.quick_actions.map((action) => {
                    const mapped = mapQuickAction(item, action.kind);
                    if (!mapped) {
                      return null;
                    }
                    return (
                      <button
                        key={`${item.item_id}-${action.kind}`}
                        type="button"
                        className="secondary-button"
                        onClick={() =>
                          void submitAction(mapped.actionId, mapped.params)
                        }
                        disabled={!action.enabled || busyActionId === mapped.actionId}
                      >
                        {action.label}
                      </button>
                    );
                  })}
                </div>
              </article>
            ))}
          </section>
        ) : null}
      </main>
    </div>
  );
}
