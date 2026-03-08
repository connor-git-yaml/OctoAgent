import {
  startTransition,
  useDeferredValue,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  executeControlAction,
  fetchControlEvents,
  fetchControlResource,
  fetchMemoryConsole,
  fetchControlSnapshot,
  fetchImportRun,
  fetchImportSource,
  fetchImportWorkbench,
  fetchMemoryProposals,
  fetchMemorySubjectHistory,
  fetchVaultAuthorization,
  isFrontDoorApiError,
} from "../api/client";
import FrontDoorGate from "../components/FrontDoorGate";
import type {
  ActionResultEnvelope,
  ActionRequestEnvelope,
  AutomationJobItem,
  CapabilityPackDocument,
  ControlPlaneEvent,
  ControlPlaneResourceRef,
  ControlPlaneSnapshot,
  DelegationPlaneDocument,
  ImportRunDocument,
  ImportSourceDocument,
  MemoryProposalAuditDocument,
  MemoryRecordProjection,
  MemorySubjectHistoryDocument,
  OperatorActionKind,
  OperatorInboxItem,
  SessionProjectionItem,
  SkillPipelineDocument,
  VaultAuthorizationDocument,
} from "../types";

const EMPTY_CAPABILITY_PACK: CapabilityPackDocument = {
  contract_version: "1.0.0",
  resource_type: "capability_pack",
  resource_id: "capability:bundled",
  schema_version: 1,
  generated_at: "",
  updated_at: "",
  status: "degraded",
  degraded: {
    is_degraded: true,
    reasons: ["capability_pack_missing"],
    unavailable_sections: ["capability_pack"],
  },
  warnings: ["capability pack resource missing from snapshot"],
  capabilities: [],
  refs: {},
  pack: {
    pack_id: "bundled:missing",
    version: "0.0.0",
    skills: [],
    tools: [],
    worker_profiles: [],
    bootstrap_files: [],
    fallback_toolset: [],
    degraded_reason: "resource_missing",
    generated_at: "",
  },
  selected_project_id: "",
  selected_workspace_id: "",
};

const EMPTY_DELEGATION: DelegationPlaneDocument = {
  contract_version: "1.0.0",
  resource_type: "delegation_plane",
  resource_id: "delegation:overview",
  schema_version: 1,
  generated_at: "",
  updated_at: "",
  status: "degraded",
  degraded: {
    is_degraded: true,
    reasons: ["delegation_plane_missing"],
    unavailable_sections: ["delegation"],
  },
  warnings: ["delegation resource missing from snapshot"],
  capabilities: [],
  refs: {},
  works: [],
  summary: {},
};

const EMPTY_PIPELINES: SkillPipelineDocument = {
  contract_version: "1.0.0",
  resource_type: "skill_pipeline",
  resource_id: "pipeline:overview",
  schema_version: 1,
  generated_at: "",
  updated_at: "",
  status: "degraded",
  degraded: {
    is_degraded: true,
    reasons: ["skill_pipeline_missing"],
    unavailable_sections: ["pipelines"],
  },
  warnings: ["pipeline resource missing from snapshot"],
  capabilities: [],
  refs: {},
  runs: [],
  summary: {},
};

type SectionId =
  | "dashboard"
  | "projects"
  | "capability"
  | "delegation"
  | "pipelines"
  | "sessions"
  | "operator"
  | "automation"
  | "diagnostics"
  | "memory"
  | "imports"
  | "config"
  | "channels";

const SECTION_LABELS: Array<{ id: SectionId; label: string; accent: string }> = [
  { id: "dashboard", label: "Dashboard", accent: "总览" },
  { id: "projects", label: "Projects", accent: "Project / Workspace" },
  { id: "capability", label: "Capability", accent: "Pack / ToolIndex" },
  { id: "delegation", label: "Delegation", accent: "Work / Routing" },
  { id: "pipelines", label: "Pipelines", accent: "Checkpoint / Replay" },
  { id: "sessions", label: "Sessions", accent: "Session Center" },
  { id: "operator", label: "Operator", accent: "Approvals / Retry / Cancel" },
  { id: "automation", label: "Automation", accent: "Scheduler" },
  { id: "diagnostics", label: "Diagnostics", accent: "Runtime Console" },
  { id: "imports", label: "Imports", accent: "WeChat / Multi-source" },
  { id: "memory", label: "Memory", accent: "SoR / Vault / Proposal" },
  { id: "config", label: "Config", accent: "Schema + uiHints" },
  { id: "channels", label: "Channels", accent: "Telegram / Devices" },
];

type ControlResourceRoute =
  | "wizard"
  | "config"
  | "project-selector"
  | "sessions"
  | "capability-pack"
  | "delegation"
  | "pipelines"
  | "automation"
  | "diagnostics"
  | "memory"
  | "import-workbench";

type SnapshotResourceKey = keyof ControlPlaneSnapshot["resources"];

const RESOURCE_ROUTE_BY_TYPE: Record<string, ControlResourceRoute> = {
  wizard_session: "wizard",
  config_schema: "config",
  project_selector: "project-selector",
  session_projection: "sessions",
  capability_pack: "capability-pack",
  delegation_plane: "delegation",
  skill_pipeline: "pipelines",
  automation_job: "automation",
  diagnostics_summary: "diagnostics",
  memory_console: "memory",
  import_workbench: "import-workbench",
  import_source: "import-workbench",
  import_run: "import-workbench",
};

const SNAPSHOT_RESOURCE_KEY_BY_ROUTE: Record<
  ControlResourceRoute,
  SnapshotResourceKey
> = {
  wizard: "wizard",
  config: "config",
  "project-selector": "project_selector",
  sessions: "sessions",
  "capability-pack": "capability_pack",
  delegation: "delegation",
  pipelines: "pipelines",
  automation: "automation",
  diagnostics: "diagnostics",
  memory: "memory",
  "import-workbench": "imports",
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
  route: ControlResourceRoute,
  options?: {
    memoryQuery?: {
      projectId?: string;
      workspaceId?: string;
      scopeId?: string;
      partition?: string;
      layer?: string;
      query?: string;
      includeHistory?: boolean;
      includeVaultRefs?: boolean;
      limit?: number;
    };
    importQuery?: {
      projectId?: string;
      workspaceId?: string;
    };
  }
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
    case "capability-pack":
      return fetchControlResource("capability-pack");
    case "delegation":
      return fetchControlResource("delegation");
    case "pipelines":
      return fetchControlResource("pipelines");
    case "automation":
      return fetchControlResource("automation");
    case "diagnostics":
      return fetchControlResource("diagnostics");
    case "memory":
      return fetchMemoryConsole(options?.memoryQuery ?? {});
    case "import-workbench":
      return fetchImportWorkbench(options?.importQuery ?? {});
  }
}

function parseCsvList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function firstMemorySubject(records: MemoryRecordProjection[]): string {
  return records.find((record) => Boolean(record.subject_key))?.subject_key ?? "";
}

function memoryActionResult(
  result: ActionResultEnvelope | null
): ActionResultEnvelope | null {
  if (!result) {
    return null;
  }
  return result.action_id.startsWith("memory.") || result.action_id.startsWith("vault.")
    ? result
    : null;
}

function buildMemoryQueryFromSnapshot(
  projectId: string,
  workspaceId: string,
  draft: {
    scopeId: string;
    partition: string;
    layer: string;
    query: string;
    includeHistory: boolean;
    includeVaultRefs: boolean;
    limit: number;
  }
) {
  return {
    projectId,
    workspaceId,
    scopeId: draft.scopeId || undefined,
    partition: draft.partition || undefined,
    layer: draft.layer || undefined,
    query: draft.query || undefined,
    includeHistory: draft.includeHistory,
    includeVaultRefs: draft.includeVaultRefs,
    limit: draft.limit,
  };
}

function slugifyImportScope(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "import-thread";
}

function buildDefaultImportMappings(source: ImportSourceDocument): Array<Record<string, unknown>> {
  return source.detected_conversations.map((conversation) => ({
    conversation_key: conversation.conversation_key,
    conversation_label: conversation.label,
    scope_id:
      source.source_type === "wechat"
        ? `chat:wechat_import:${slugifyImportScope(conversation.conversation_key)}`
        : `chat:import:${slugifyImportScope(conversation.conversation_key)}`,
    partition: "chat",
  }));
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
    sourceType: "wechat",
    inputPath: "",
    mediaRoot: "",
    formatHint: "json",
  });
  const [importMappingDraft, setImportMappingDraft] = useState("[]");
  const [selectedImportSourceId, setSelectedImportSourceId] = useState("");
  const [selectedImportRunId, setSelectedImportRunId] = useState("");
  const [importSourceDetail, setImportSourceDetail] = useState<ImportSourceDocument | null>(
    null
  );
  const [importRunDetail, setImportRunDetail] = useState<ImportRunDocument | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [automationDraft, setAutomationDraft] = useState({
    name: "",
    actionId: "diagnostics.refresh",
    scheduleKind: "interval",
    scheduleExpr: "3600",
    enabled: true,
  });
  const [memoryQueryDraft, setMemoryQueryDraft] = useState({
    scopeId: "",
    partition: "",
    layer: "",
    query: "",
    includeHistory: false,
    includeVaultRefs: true,
    limit: 50,
  });
  const [memoryAccessDraft, setMemoryAccessDraft] = useState({
    scopeId: "",
    partition: "",
    subjectKey: "",
    reason: "",
  });
  const [memoryRetrieveDraft, setMemoryRetrieveDraft] = useState({
    scopeId: "",
    partition: "",
    subjectKey: "",
    query: "",
    grantId: "",
  });
  const [memoryExportDraft, setMemoryExportDraft] = useState({
    scopeIds: "",
    includeHistory: false,
    includeVaultRefs: true,
  });
  const [memoryRestoreDraft, setMemoryRestoreDraft] = useState({
    snapshotRef: "",
    targetScopeMode: "current_project",
    scopeIds: "",
  });
  const [selectedMemorySubjectKey, setSelectedMemorySubjectKey] = useState("");
  const [memorySubject, setMemorySubject] = useState<MemorySubjectHistoryDocument | null>(
    null
  );
  const [memoryProposals, setMemoryProposals] =
    useState<MemoryProposalAuditDocument | null>(null);
  const [vaultAuthorization, setVaultAuthorization] =
    useState<VaultAuthorizationDocument | null>(null);
  const [memoryBusy, setMemoryBusy] = useState(false);
  const [authError, setAuthError] = useState<ApiError | null>(null);

  function applyPageError(err: unknown, fallback: string): string {
    const message = err instanceof Error ? err.message : fallback;
    setError(message);
    setAuthError(isFrontDoorApiError(err) ? err : null);
    return message;
  }

  function clearPageError() {
    setError(null);
    setAuthError(null);
  }

  async function refreshEvents() {
    const eventPayload = await fetchControlEvents(undefined, 50);
    startTransition(() => {
      setEvents(dedupeEvents(eventPayload.events));
    });
  }

  async function refreshMemoryDetails(subjectKey?: string) {
    const memory = snapshot?.resources.memory;
    if (!memory) {
      return;
    }
    const resolvedSubjectKey =
      subjectKey || selectedMemorySubjectKey || firstMemorySubject(memory.records);
    setMemoryBusy(true);
    try {
      const [nextProposals, nextVaultAuthorization, nextSubjectHistory] =
        await Promise.all([
          fetchMemoryProposals({
            projectId: memory.active_project_id,
            workspaceId: memory.active_workspace_id,
            scopeId: memory.filters.scope_id || undefined,
            limit: memoryQueryDraft.limit,
          }),
          fetchVaultAuthorization({
            projectId: memory.active_project_id,
            workspaceId: memory.active_workspace_id,
            scopeId: memory.filters.scope_id || undefined,
            subjectKey: resolvedSubjectKey || undefined,
          }),
          resolvedSubjectKey
            ? fetchMemorySubjectHistory(resolvedSubjectKey, {
                projectId: memory.active_project_id,
                workspaceId: memory.active_workspace_id,
                scopeId: memory.filters.scope_id || undefined,
              })
            : Promise.resolve(null),
        ]);
      startTransition(() => {
        setMemoryProposals(nextProposals);
        setVaultAuthorization(nextVaultAuthorization);
        setMemorySubject(nextSubjectHistory);
        if (resolvedSubjectKey) {
          setSelectedMemorySubjectKey(resolvedSubjectKey);
        }
      });
    } catch (err) {
      applyPageError(err, "Memory 细节资源加载失败");
    } finally {
      setMemoryBusy(false);
    }
  }

  async function refreshImportDetails(sourceId?: string, runId?: string) {
    const imports = snapshot?.resources.imports;
    if (!imports) {
      return;
    }
    const resolvedSourceId = sourceId || selectedImportSourceId || imports.sources[0]?.source_id;
    const resolvedRunId = runId || selectedImportRunId || imports.recent_runs[0]?.resource_id;
    setImportBusy(true);
    try {
      const [nextSource, nextRun] = await Promise.all([
        resolvedSourceId ? fetchImportSource(resolvedSourceId) : Promise.resolve(null),
        resolvedRunId ? fetchImportRun(resolvedRunId) : Promise.resolve(null),
      ]);
      startTransition(() => {
        setImportSourceDetail(nextSource);
        setImportRunDetail(nextRun);
        if (resolvedSourceId) {
          setSelectedImportSourceId(resolvedSourceId);
        }
        if (resolvedRunId) {
          setSelectedImportRunId(resolvedRunId);
        }
        if (nextSource) {
          setImportMappingDraft(
            formatJson(buildDefaultImportMappings(nextSource))
          );
        }
      });
    } catch (err) {
      applyPageError(err, "Import 详情资源加载失败");
    } finally {
      setImportBusy(false);
    }
  }

  async function reloadData(options?: { preserveConfigDraft?: boolean }) {
    const preserveConfigDraft = options?.preserveConfigDraft ?? true;
    const [nextSnapshot, eventPayload] = await Promise.all([
      fetchControlSnapshot(),
      fetchControlEvents(undefined, 50),
    ]);
    clearPageError();
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
    const memoryQuery =
      snapshot?.resources.memory != null
        ? buildMemoryQueryFromSnapshot(
            snapshot.resources.memory.active_project_id,
            snapshot.resources.memory.active_workspace_id,
            memoryQueryDraft
          )
        : undefined;
    const importQuery =
      snapshot?.resources.imports != null
        ? {
            projectId: snapshot.resources.imports.active_project_id,
            workspaceId: snapshot.resources.imports.active_workspace_id,
          }
        : undefined;

    if (routes.length === 0) {
      await reloadData({ preserveConfigDraft });
      return;
    }

    try {
      const updates = await Promise.all(
        routes.map((route) =>
          loadControlResource(route, {
            memoryQuery: route === "memory" ? memoryQuery : undefined,
            importQuery: route === "import-workbench" ? importQuery : undefined,
          })
        )
      );
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
    } catch (err) {
      try {
        await reloadData({ preserveConfigDraft });
      } catch (reloadErr) {
        applyPageError(reloadErr, "控制台资源刷新失败");
        throw reloadErr;
      }
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
        clearPageError();
        startTransition(() => {
          setSnapshot(nextSnapshot);
          setEvents(eventPayload.events);
          setConfigDraft(formatJson(nextSnapshot.resources.config.current_value));
        });
      } catch (err) {
        if (cancelled) {
          return;
        }
        applyPageError(err, "控制台加载失败");
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void boot();
    const interval = window.setInterval(() => {
      void reloadData().catch((err) => {
        applyPageError(err, "控制台刷新失败");
      });
    }, 15000);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    configDirtyRef.current = configDirty;
  }, [configDirty]);

  useEffect(() => {
    const memory = snapshot?.resources.memory;
    if (!memory) {
      return;
    }
    setMemoryQueryDraft({
      scopeId: memory.filters.scope_id,
      partition: memory.filters.partition,
      layer: memory.filters.layer,
      query: memory.filters.query,
      includeHistory: memory.filters.include_history,
      includeVaultRefs: memory.filters.include_vault_refs,
      limit: memory.filters.limit,
    });
    setMemoryAccessDraft((current) => ({
      ...current,
      scopeId: current.scopeId || memory.filters.scope_id,
      partition: current.partition || memory.filters.partition,
    }));
    setMemoryRetrieveDraft((current) => ({
      ...current,
      scopeId: current.scopeId || memory.filters.scope_id,
      partition: current.partition || memory.filters.partition,
    }));
    setMemoryExportDraft((current) => ({
      ...current,
      scopeIds:
        current.scopeIds || (memory.filters.scope_id ? memory.filters.scope_id : ""),
    }));
  }, [snapshot?.resources.memory.generated_at]);

  useEffect(() => {
    if (activeSection !== "memory" || !snapshot?.resources.memory) {
      return;
    }
    const fallbackSubjectKey =
      selectedMemorySubjectKey || firstMemorySubject(snapshot.resources.memory.records);
    if (fallbackSubjectKey && fallbackSubjectKey !== selectedMemorySubjectKey) {
      setSelectedMemorySubjectKey(fallbackSubjectKey);
    }
    void refreshMemoryDetails(fallbackSubjectKey);
  }, [activeSection, snapshot?.resources.memory.generated_at]);

  useEffect(() => {
    if (activeSection !== "imports" || !snapshot?.resources.imports) {
      return;
    }
    const fallbackSourceId =
      selectedImportSourceId || snapshot.resources.imports.sources[0]?.source_id;
    const fallbackRunId =
      selectedImportRunId || snapshot.resources.imports.recent_runs[0]?.resource_id;
    void refreshImportDetails(fallbackSourceId, fallbackRunId);
  }, [activeSection, snapshot?.resources.imports.generated_at]);

  const filteredSessions = (snapshot?.resources.sessions.sessions ?? []).filter((item) =>
    sessionMatches(item, deferredSessionFilter)
  );

  async function bootControlPlane() {
    clearPageError();
    setLoading(true);
    try {
      const [nextSnapshot, eventPayload] = await Promise.all([
        fetchControlSnapshot(),
        fetchControlEvents(undefined, 50),
      ]);
      startTransition(() => {
        setSnapshot(nextSnapshot);
        setEvents(eventPayload.events);
        setConfigDraft(formatJson(nextSnapshot.resources.config.current_value));
      });
    } catch (err) {
      applyPageError(err, "控制台加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function submitAction(
    actionId: string,
    params: Record<string, unknown>,
    options?: {
      refreshConfigDraft?: boolean;
      subjectKey?: string;
      sourceId?: string;
      runId?: string;
    }
  ) {
    setBusyActionId(actionId);
    clearPageError();
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
      if (
        activeSection === "memory" &&
        (actionId.startsWith("memory.") || actionId.startsWith("vault."))
      ) {
        await refreshMemoryDetails(options?.subjectKey);
      }
      if (activeSection === "imports" && actionId.startsWith("import.")) {
        await refreshImportDetails(options?.sourceId, options?.runId);
      }
      return result;
    } catch (err) {
      applyPageError(err, `动作执行失败: ${actionId}`);
      return null;
    } finally {
      setBusyActionId(null);
    }
  }

  if (loading) {
    return <div className="control-loading">正在装载 Control Plane...</div>;
  }

  if (authError && snapshot === null) {
    return (
      <FrontDoorGate
        error={authError}
        title="Control Plane"
        onRetry={bootControlPlane}
      />
    );
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

  const {
    wizard,
    config,
    project_selector,
    sessions,
    capability_pack,
    delegation,
    pipelines,
    automation,
    diagnostics,
    memory,
    imports,
  } = snapshot.resources;
  const capabilityPack: CapabilityPackDocument =
    capability_pack ?? EMPTY_CAPABILITY_PACK;
  const delegationPlane: DelegationPlaneDocument =
    delegation ?? EMPTY_DELEGATION;
  const skillPipelines: SkillPipelineDocument = pipelines ?? EMPTY_PIPELINES;
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
  const lastMemoryAction = memoryActionResult(lastAction);
  const selectedSubjectHistory =
    memorySubject?.subject_key === selectedMemorySubjectKey ? memorySubject : null;

  return (
    <div className="control-shell">
      <aside className="control-sidebar">
        <div className="control-brand">
          <p className="eyebrow">Feature 026 / 027 / 030</p>
          <h1>OctoAgent Control Plane</h1>
          <p>
            统一消费 wizard / project / capability / delegation / pipeline /
            session / automation / diagnostics / memory / imports / config
            contract。
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
                  <p className="eyebrow">Capability Pack</p>
                  <h3>{capabilityPack.pack.tools.length}</h3>
                </div>
                <span className="tone-chip neutral">
                  Skills {capabilityPack.pack.skills.length}
                </span>
              </div>
              <p>
                Worker Profiles {capabilityPack.pack.worker_profiles.length} / Bootstrap{" "}
                {capabilityPack.pack.bootstrap_files.length}
              </p>
              <p className="muted">
                ToolIndex {capabilityPack.pack.degraded_reason || "active"}
              </p>
            </article>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Delegation</p>
                  <h3>{delegationPlane.works.length}</h3>
                </div>
                <span className="tone-chip neutral">
                  Pipelines {skillPipelines.runs.length}
                </span>
              </div>
              <div className="event-list">
                {delegationPlane.works.slice(0, 2).map((item) => (
                  <div key={item.work_id} className="event-item">
                    <div>
                      <strong>{item.title || item.work_id}</strong>
                      <p>{item.route_reason || item.selected_worker_type}</p>
                    </div>
                    <small>{item.status}</small>
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

        {activeSection === "capability" ? (
          <section className="stack-section">
            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Bundled Capability Pack</p>
                  <h3>{capabilityPack.pack.pack_id}</h3>
                </div>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => void submitAction("capability.refresh", {})}
                  disabled={busyActionId === "capability.refresh"}
                >
                  刷新能力包
                </button>
              </div>
              <div className="meta-grid">
                <span>Version {capabilityPack.pack.version}</span>
                <span>Tools {capabilityPack.pack.tools.length}</span>
                <span>Skills {capabilityPack.pack.skills.length}</span>
                <span>
                  Fallback {capabilityPack.pack.fallback_toolset.join(", ") || "-"}
                </span>
              </div>
            </article>
            {capabilityPack.pack.worker_profiles.map((profile) => (
              <article key={profile.worker_type} className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">Worker Profile</p>
                    <h3>{profile.worker_type}</h3>
                  </div>
                  <span className="tone-chip neutral">
                    Runtime {profile.runtime_kinds.join(", ")}
                  </span>
                </div>
                <div className="meta-grid">
                  <span>Capabilities {profile.capabilities.join(", ")}</span>
                  <span>Model {profile.default_model_alias}</span>
                  <span>Profile {profile.default_tool_profile}</span>
                  <span>Groups {profile.default_tool_groups.join(", ")}</span>
                </div>
              </article>
            ))}
            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Bundled Tools</p>
                  <h3>{capabilityPack.pack.tools.length}</h3>
                </div>
              </div>
              <div className="event-list">
                {capabilityPack.pack.tools.map((tool) => (
                  <div key={tool.tool_name} className="event-item">
                    <div>
                      <strong>{tool.tool_name}</strong>
                      <p>{tool.description || tool.tool_group}</p>
                    </div>
                    <small>{tool.tags.join(", ") || tool.tool_profile}</small>
                  </div>
                ))}
              </div>
            </article>
          </section>
        ) : null}

        {activeSection === "delegation" ? (
          <section className="stack-section">
            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Delegation Overview</p>
                  <h3>{delegationPlane.works.length}</h3>
                </div>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => void submitAction("work.refresh", {})}
                  disabled={busyActionId === "work.refresh"}
                >
                  刷新委派面板
                </button>
              </div>
              <pre className="json-preview">{formatJson(delegationPlane.summary)}</pre>
            </article>
            {delegationPlane.works.map((work) => (
              <article key={work.work_id} className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">{work.work_id}</p>
                    <h3>{work.title || work.task_id}</h3>
                  </div>
                  <span className={`tone-chip ${statusTone(work.status)}`}>
                    {work.status}
                  </span>
                </div>
                <div className="meta-grid">
                  <span>Worker {work.selected_worker_type || "-"}</span>
                  <span>Target {work.target_kind || "-"}</span>
                  <span>Runtime {work.runtime_id || "-"}</span>
                  <span>Pipeline {work.pipeline_run_id || "-"}</span>
                </div>
                <p>{work.route_reason || "无 route reason"}</p>
                <p className="muted">
                  Selected Tools: {work.selected_tools.join(", ") || "none"}
                </p>
                <div className="action-row">
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => void submitAction("work.cancel", { work_id: work.work_id })}
                    disabled={busyActionId === "work.cancel"}
                  >
                    取消 Work
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() => void submitAction("work.retry", { work_id: work.work_id })}
                    disabled={busyActionId === "work.retry"}
                  >
                    重试
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() =>
                      void submitAction("work.escalate", { work_id: work.work_id })
                    }
                    disabled={busyActionId === "work.escalate"}
                  >
                    升级
                  </button>
                </div>
              </article>
            ))}
          </section>
        ) : null}

        {activeSection === "pipelines" ? (
          <section className="stack-section">
            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Skill Pipeline</p>
                  <h3>{skillPipelines.runs.length}</h3>
                </div>
              </div>
              <pre className="json-preview">{formatJson(skillPipelines.summary)}</pre>
            </article>
            {skillPipelines.runs.map((run) => (
              <article key={run.run_id} className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">{run.pipeline_id}</p>
                    <h3>{run.run_id}</h3>
                  </div>
                  <span className={`tone-chip ${statusTone(run.status)}`}>
                    {run.status}
                  </span>
                </div>
                <div className="meta-grid">
                  <span>Work {run.work_id}</span>
                  <span>Task {run.task_id}</span>
                  <span>Node {run.current_node_id || "-"}</span>
                  <span>Pause {run.pause_reason || "-"}</span>
                </div>
                <div className="action-row">
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      void submitAction("pipeline.resume", { work_id: run.work_id })
                    }
                    disabled={busyActionId === "pipeline.resume"}
                  >
                    恢复
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() =>
                      void submitAction("pipeline.retry_node", { work_id: run.work_id })
                    }
                    disabled={busyActionId === "pipeline.retry_node"}
                  >
                    重试节点
                  </button>
                </div>
                <div className="event-list">
                  {run.replay_frames.map((frame) => (
                    <div key={frame.frame_id} className="event-item">
                      <div>
                        <strong>{frame.node_id}</strong>
                        <p>{frame.summary || frame.status}</p>
                      </div>
                      <small>{formatDateTime(frame.ts)}</small>
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
                      project_id: project_selector.current_project_id,
                      workspace_id: project_selector.current_workspace_id,
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
                  Source Type
                  <input
                    value={importDraft.sourceType}
                    onChange={(event) =>
                      setImportDraft((current) => ({
                        ...current,
                        sourceType: event.target.value,
                      }))
                    }
                  />
                </label>
                <label>
                  Media Root
                  <input
                    value={importDraft.mediaRoot}
                    onChange={(event) =>
                      setImportDraft((current) => ({
                        ...current,
                        mediaRoot: event.target.value,
                      }))
                    }
                    placeholder="/path/to/media"
                  />
                </label>
                <label>
                  Format Hint
                  <input
                    value={importDraft.formatHint}
                    onChange={(event) =>
                      setImportDraft((current) => ({
                        ...current,
                        formatHint: event.target.value,
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
                    void submitAction("import.source.detect", {
                      source_type: importDraft.sourceType,
                      input_path: importDraft.inputPath,
                      media_root: importDraft.mediaRoot,
                      format_hint: importDraft.formatHint,
                    })
                  }
                  disabled={busyActionId === "import.source.detect"}
                >
                  识别 Import Source
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => setActiveSection("imports")}
                >
                  打开 Import Workbench
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

        {activeSection === "imports" ? (
          <section className="stack-section">
            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Import Workbench</p>
                  <h3>{imports.summary.source_count}</h3>
                </div>
                <div className="chip-stack">
                  <span className="tone-chip neutral">
                    Runs {imports.summary.recent_run_count}
                  </span>
                  <span className="tone-chip warning">
                    Resume {imports.summary.resume_available_count}
                  </span>
                </div>
              </div>
              <div className="form-grid">
                <label>
                  Source Type
                  <input
                    value={importDraft.sourceType}
                    onChange={(event) =>
                      setImportDraft((current) => ({
                        ...current,
                        sourceType: event.target.value,
                      }))
                    }
                  />
                </label>
                <label>
                  Input Path
                  <input
                    value={importDraft.inputPath}
                    onChange={(event) =>
                      setImportDraft((current) => ({
                        ...current,
                        inputPath: event.target.value,
                      }))
                    }
                    placeholder="/path/to/wechat-export"
                  />
                </label>
                <label>
                  Media Root
                  <input
                    value={importDraft.mediaRoot}
                    onChange={(event) =>
                      setImportDraft((current) => ({
                        ...current,
                        mediaRoot: event.target.value,
                      }))
                    }
                    placeholder="/path/to/media"
                  />
                </label>
                <label>
                  Format Hint
                  <input
                    value={importDraft.formatHint}
                    onChange={(event) =>
                      setImportDraft((current) => ({
                        ...current,
                        formatHint: event.target.value,
                      }))
                    }
                    placeholder="json / html / sqlite"
                  />
                </label>
              </div>
              <div className="action-row">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() =>
                    void submitAction(
                      "import.source.detect",
                      {
                        source_type: importDraft.sourceType,
                        input_path: importDraft.inputPath,
                        media_root: importDraft.mediaRoot,
                        format_hint: importDraft.formatHint,
                      },
                      {}
                    )
                  }
                  disabled={busyActionId === "import.source.detect"}
                >
                  Detect Source
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() =>
                    void refreshImportDetails(selectedImportSourceId, selectedImportRunId)
                  }
                  disabled={importBusy}
                >
                  刷新 Workbench
                </button>
              </div>
            </article>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Detected Sources</p>
                  <h3>{imports.sources.length}</h3>
                </div>
              </div>
              <div className="event-list">
                {imports.sources.map((item) => (
                  <button
                    key={item.source_id}
                    type="button"
                    className="event-item"
                    onClick={() => void refreshImportDetails(item.source_id, selectedImportRunId)}
                  >
                    <div>
                      <strong>{item.source_type}</strong>
                      <p>{item.input_ref.input_path}</p>
                    </div>
                    <small>{item.detected_conversations.length} conversations</small>
                  </button>
                ))}
              </div>
            </article>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Recent Runs / Resume</p>
                  <h3>{imports.recent_runs.length}</h3>
                </div>
              </div>
              <div className="event-list">
                {imports.recent_runs.map((item) => (
                  <button
                    key={item.resource_id}
                    type="button"
                    className="event-item"
                    onClick={() => void refreshImportDetails(selectedImportSourceId, item.resource_id)}
                  >
                    <div>
                      <strong>{item.status}</strong>
                      <p>{item.source_id}</p>
                    </div>
                    <small>{formatDateTime(item.completed_at ?? item.updated_at)}</small>
                  </button>
                ))}
                {imports.resume_entries.map((item) => (
                  <div key={item.resume_id} className="event-item">
                    <div>
                      <strong>{item.resume_id}</strong>
                      <p>{item.scope_id || item.source_id}</p>
                    </div>
                    <button
                      type="button"
                      className="ghost-button"
                      onClick={() =>
                        void submitAction(
                          "import.resume",
                          { resume_id: item.resume_id },
                          { runId: selectedImportRunId }
                        )
                      }
                    >
                      Resume
                    </button>
                  </div>
                ))}
              </div>
            </article>

            {importSourceDetail ? (
              <article className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">Source Detail</p>
                    <h3>{importSourceDetail.source_id}</h3>
                  </div>
                  <div className="chip-stack">
                    <span className={`tone-chip ${statusTone(importSourceDetail.status)}`}>
                      {importSourceDetail.status}
                    </span>
                  </div>
                </div>
                <div className="session-list compact">
                  {importSourceDetail.detected_conversations.map((conversation) => (
                    <div key={conversation.conversation_key} className="session-card">
                      <div className="session-meta">
                        <strong>{conversation.label || conversation.conversation_key}</strong>
                        <span>{conversation.message_count} messages</span>
                      </div>
                      <p>conversation_key: {conversation.conversation_key}</p>
                      <p>attachments: {conversation.attachment_count}</p>
                    </div>
                  ))}
                </div>
                <label className="textarea-label">
                  Mapping JSON
                  <textarea
                    rows={10}
                    value={importMappingDraft}
                    onChange={(event) => setImportMappingDraft(event.target.value)}
                  />
                </label>
                <div className="action-row">
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() =>
                      setImportMappingDraft(
                        formatJson(buildDefaultImportMappings(importSourceDetail))
                      )
                    }
                  >
                    生成默认 Mapping
                  </button>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => {
                      try {
                        const mappings = JSON.parse(importMappingDraft) as Array<Record<string, unknown>>;
                        void submitAction(
                          "import.mapping.save",
                          {
                            source_id: importSourceDetail.source_id,
                            conversation_mappings: mappings,
                          },
                          { sourceId: importSourceDetail.source_id }
                        );
                      } catch (err) {
                        setError(err instanceof Error ? err.message : "Mapping JSON 解析失败");
                      }
                    }}
                    disabled={busyActionId === "import.mapping.save"}
                  >
                    保存 Mapping
                  </button>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      void submitAction(
                        "import.preview",
                        { source_id: importSourceDetail.source_id },
                        { sourceId: importSourceDetail.source_id }
                      )
                    }
                    disabled={busyActionId === "import.preview"}
                  >
                    Preview
                  </button>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      void submitAction(
                        "import.run",
                        { source_id: importSourceDetail.source_id },
                        { sourceId: importSourceDetail.source_id }
                      )
                    }
                    disabled={busyActionId === "import.run"}
                  >
                    Run Import
                  </button>
                </div>
              </article>
            ) : null}

            {importRunDetail ? (
              <article className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">Run Detail</p>
                    <h3>{importRunDetail.resource_id}</h3>
                  </div>
                  <div className="chip-stack">
                    <span className={`tone-chip ${statusTone(importRunDetail.status)}`}>
                      {importRunDetail.status}
                    </span>
                  </div>
                </div>
                <pre className="config-preview">{formatJson(importRunDetail.summary)}</pre>
                {importRunDetail.warnings.length ? (
                  <div className="warning-list">
                    {importRunDetail.warnings.map((item) => (
                      <p key={item}>{item}</p>
                    ))}
                  </div>
                ) : null}
                {importRunDetail.errors.length ? (
                  <div className="warning-list danger">
                    {importRunDetail.errors.map((item) => (
                      <p key={item}>{item}</p>
                    ))}
                  </div>
                ) : null}
                <div className="event-list">
                  {importRunDetail.dedupe_details.slice(0, 10).map((item, index) => (
                    <div key={`${index}-${String(item.message_key ?? item.reason ?? "detail")}`} className="event-item">
                      <div>
                        <strong>{String(item.reason ?? "detail")}</strong>
                        <p>{String(item.preview ?? item.source_cursor ?? "")}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </article>
            ) : null}
          </section>
        ) : null}

        {activeSection === "memory" ? (
          <section className="stack-section">
            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Memory Console</p>
                  <h3>{memory.active_project_id || "未绑定 Project"}</h3>
                </div>
                <span className={`tone-chip ${statusTone(memory.status)}`}>
                  {formatRelativeStatus(memory.status)}
                </span>
              </div>
              <div className="meta-grid">
                <span>Workspace {memory.active_workspace_id || "-"}</span>
                <span>Scopes {memory.summary.scope_count}</span>
                <span>Fragments {memory.summary.fragment_count}</span>
                <span>SoR Current {memory.summary.sor_current_count}</span>
                <span>SoR History {memory.summary.sor_history_count}</span>
                <span>Vault Refs {memory.summary.vault_ref_count}</span>
                <span>Proposals {memory.summary.proposal_count}</span>
              </div>
              <div className="form-grid">
                <label>
                  Scope ID
                  <input
                    value={memoryQueryDraft.scopeId}
                    onChange={(event) =>
                      setMemoryQueryDraft((current) => ({
                        ...current,
                        scopeId: event.target.value,
                      }))
                    }
                    placeholder="scope-..."
                  />
                </label>
                <label>
                  Partition
                  <input
                    value={memoryQueryDraft.partition}
                    onChange={(event) =>
                      setMemoryQueryDraft((current) => ({
                        ...current,
                        partition: event.target.value,
                      }))
                    }
                    placeholder="profile / credential"
                  />
                </label>
                <label>
                  Layer
                  <input
                    value={memoryQueryDraft.layer}
                    onChange={(event) =>
                      setMemoryQueryDraft((current) => ({
                        ...current,
                        layer: event.target.value,
                      }))
                    }
                    placeholder="fragment / sor / vault"
                  />
                </label>
                <label>
                  Query
                  <input
                    value={memoryQueryDraft.query}
                    onChange={(event) =>
                      setMemoryQueryDraft((current) => ({
                        ...current,
                        query: event.target.value,
                      }))
                    }
                    placeholder="subject / summary / evidence"
                  />
                </label>
                <label>
                  Limit
                  <input
                    type="number"
                    min={1}
                    max={200}
                    value={memoryQueryDraft.limit}
                    onChange={(event) =>
                      setMemoryQueryDraft((current) => ({
                        ...current,
                        limit: Number(event.target.value) || 50,
                      }))
                    }
                  />
                </label>
                <label className="checkbox-line">
                  <input
                    type="checkbox"
                    checked={memoryQueryDraft.includeHistory}
                    onChange={(event) =>
                      setMemoryQueryDraft((current) => ({
                        ...current,
                        includeHistory: event.target.checked,
                      }))
                    }
                  />
                  包含 superseded 历史
                </label>
                <label className="checkbox-line">
                  <input
                    type="checkbox"
                    checked={memoryQueryDraft.includeVaultRefs}
                    onChange={(event) =>
                      setMemoryQueryDraft((current) => ({
                        ...current,
                        includeVaultRefs: event.target.checked,
                      }))
                    }
                  />
                  包含 Vault 引用
                </label>
              </div>
              <div className="action-row">
                <button
                  type="button"
                  className="primary-button"
                  onClick={() =>
                    void submitAction("memory.query", {
                      project_id: memory.active_project_id,
                      workspace_id: memory.active_workspace_id,
                      scope_id: memoryQueryDraft.scopeId,
                      partition: memoryQueryDraft.partition,
                      layer: memoryQueryDraft.layer,
                      query: memoryQueryDraft.query,
                      include_history: memoryQueryDraft.includeHistory,
                      include_vault_refs: memoryQueryDraft.includeVaultRefs,
                      limit: memoryQueryDraft.limit,
                    })
                  }
                  disabled={busyActionId === "memory.query"}
                >
                  刷新 Memory 视图
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => void refreshMemoryDetails(selectedMemorySubjectKey)}
                  disabled={memoryBusy}
                >
                  刷新 Proposal / Vault
                </button>
              </div>
              {memory.available_scopes.length > 0 ? (
                <p className="muted">
                  Available Scopes: {memory.available_scopes.join(", ")}
                </p>
              ) : null}
            </article>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Memory Records</p>
                  <h3>{memory.records.length}</h3>
                </div>
                <span className="tone-chip neutral">
                  {memory.available_layers.join(" / ") || "all layers"}
                </span>
              </div>
              <div className="event-list">
                {memory.records.map((record) => (
                  <div key={record.record_id} className="event-item">
                    <div>
                      <strong>{record.summary || record.subject_key || record.record_id}</strong>
                      <p>
                        {record.layer} / {record.partition} / {record.scope_id}
                      </p>
                      <small>
                        subject={record.subject_key || "-"} | status={record.status} | version=
                        {record.version ?? "-"}
                      </small>
                    </div>
                    <div className="action-row">
                      <span
                        className={`tone-chip ${
                          record.requires_vault_authorization ? "warning" : "neutral"
                        }`}
                      >
                        {record.requires_vault_authorization ? "Vault" : "Open"}
                      </span>
                      {record.subject_key ? (
                        <button
                          type="button"
                          className="ghost-button"
                          onClick={() => {
                            setSelectedMemorySubjectKey(record.subject_key);
                            void refreshMemoryDetails(record.subject_key);
                          }}
                          disabled={memoryBusy}
                        >
                          查看历史
                        </button>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            </article>

            <div className="section-grid">
              <article className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">Subject History</p>
                    <h3>{selectedSubjectHistory?.subject_key || "未选择 Subject"}</h3>
                  </div>
                  <span className="tone-chip neutral">
                    {selectedSubjectHistory?.history.length ?? 0} history
                  </span>
                </div>
                {selectedSubjectHistory ? (
                  <>
                    <p>
                      Current:{" "}
                      <strong>
                        {selectedSubjectHistory.current_record?.summary ||
                          selectedSubjectHistory.current_record?.record_id ||
                          "无 current"}
                      </strong>
                    </p>
                    <div className="event-list">
                      {selectedSubjectHistory.history.map((record) => (
                        <div key={record.record_id} className="event-item">
                          <div>
                            <strong>{record.summary || record.record_id}</strong>
                            <p>
                              {record.status} / v{record.version ?? "-"} /{" "}
                              {formatDateTime(record.updated_at || record.created_at)}
                            </p>
                          </div>
                          <small>{record.partition}</small>
                        </div>
                      ))}
                    </div>
                  </>
                ) : (
                  <p className="muted">从上方记录列表选择一个带 subject_key 的条目。</p>
                )}
              </article>

              <article className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">WriteProposal Audit</p>
                    <h3>{memoryProposals?.items.length ?? 0}</h3>
                  </div>
                  <span className="tone-chip neutral">
                    Pending {memoryProposals?.summary.pending ?? 0}
                  </span>
                </div>
                <div className="meta-grid">
                  <span>Validated {memoryProposals?.summary.validated ?? 0}</span>
                  <span>Rejected {memoryProposals?.summary.rejected ?? 0}</span>
                  <span>Committed {memoryProposals?.summary.committed ?? 0}</span>
                </div>
                <div className="event-list">
                  {(memoryProposals?.items ?? []).map((item) => (
                    <div key={item.proposal_id} className="event-item">
                      <div>
                        <strong>{item.subject_key || item.proposal_id}</strong>
                        <p>
                          {item.action} / {item.partition} / {item.scope_id}
                        </p>
                        <small>{item.rationale || "无额外 rationale"}</small>
                      </div>
                      <small>{item.status}</small>
                    </div>
                  ))}
                </div>
              </article>

              <article className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">Vault Authorization</p>
                    <h3>{vaultAuthorization?.active_requests.length ?? 0}</h3>
                  </div>
                  <span className="tone-chip neutral">
                    Grants {vaultAuthorization?.active_grants.length ?? 0}
                  </span>
                </div>
                <div className="event-list">
                  {(vaultAuthorization?.active_requests ?? []).map((item) => (
                    <div key={item.request_id} className="event-item">
                      <div>
                        <strong>{item.subject_key || item.request_id}</strong>
                        <p>
                          {item.scope_id} / {item.partition || "-"} / {item.status}
                        </p>
                        <small>{item.reason || "未填写理由"}</small>
                      </div>
                      <div className="action-row">
                        <button
                          type="button"
                          className="secondary-button"
                          onClick={() =>
                            void submitAction("vault.access.resolve", {
                              request_id: item.request_id,
                              decision: "approve",
                              expires_in_seconds: 3600,
                            })
                          }
                          disabled={busyActionId === "vault.access.resolve"}
                        >
                          批准授权
                        </button>
                        <button
                          type="button"
                          className="ghost-button"
                          onClick={() =>
                            void submitAction("vault.access.resolve", {
                              request_id: item.request_id,
                              decision: "reject",
                            })
                          }
                          disabled={busyActionId === "vault.access.resolve"}
                        >
                          拒绝
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="meta-grid">
                  {(vaultAuthorization?.active_grants ?? []).slice(0, 4).map((grant) => (
                    <span key={grant.grant_id}>
                      {grant.subject_key || grant.grant_id}: {grant.status}
                    </span>
                  ))}
                </div>
              </article>
            </div>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">Vault Actions</p>
                  <h3>授权申请 / 检索 / 校验</h3>
                </div>
                <span className="tone-chip neutral">
                  Retrievals {vaultAuthorization?.recent_retrievals.length ?? 0}
                </span>
              </div>
              <div className="form-grid">
                <label>
                  Access Scope
                  <input
                    value={memoryAccessDraft.scopeId}
                    onChange={(event) =>
                      setMemoryAccessDraft((current) => ({
                        ...current,
                        scopeId: event.target.value,
                      }))
                    }
                    placeholder="scope-..."
                  />
                </label>
                <label>
                  Access Partition
                  <input
                    value={memoryAccessDraft.partition}
                    onChange={(event) =>
                      setMemoryAccessDraft((current) => ({
                        ...current,
                        partition: event.target.value,
                      }))
                    }
                    placeholder="credential"
                  />
                </label>
                <label>
                  Access Subject
                  <input
                    value={memoryAccessDraft.subjectKey}
                    onChange={(event) =>
                      setMemoryAccessDraft((current) => ({
                        ...current,
                        subjectKey: event.target.value,
                      }))
                    }
                    placeholder="subject_key"
                  />
                </label>
                <label>
                  Access Reason
                  <input
                    value={memoryAccessDraft.reason}
                    onChange={(event) =>
                      setMemoryAccessDraft((current) => ({
                        ...current,
                        reason: event.target.value,
                      }))
                    }
                    placeholder="说明检索目的"
                  />
                </label>
                <label>
                  Export Scope IDs
                  <input
                    value={memoryExportDraft.scopeIds}
                    onChange={(event) =>
                      setMemoryExportDraft((current) => ({
                        ...current,
                        scopeIds: event.target.value,
                      }))
                    }
                    placeholder="scope-a,scope-b"
                  />
                </label>
                <label className="checkbox-line">
                  <input
                    type="checkbox"
                    checked={memoryExportDraft.includeHistory}
                    onChange={(event) =>
                      setMemoryExportDraft((current) => ({
                        ...current,
                        includeHistory: event.target.checked,
                      }))
                    }
                  />
                  Export 包含历史
                </label>
                <label className="checkbox-line">
                  <input
                    type="checkbox"
                    checked={memoryExportDraft.includeVaultRefs}
                    onChange={(event) =>
                      setMemoryExportDraft((current) => ({
                        ...current,
                        includeVaultRefs: event.target.checked,
                      }))
                    }
                  />
                  Export 包含 Vault 引用
                </label>
                <label>
                  Retrieve Query
                  <input
                    value={memoryRetrieveDraft.query}
                    onChange={(event) =>
                      setMemoryRetrieveDraft((current) => ({
                        ...current,
                        query: event.target.value,
                      }))
                    }
                    placeholder="summary contains..."
                  />
                </label>
                <label>
                  Retrieve Scope
                  <input
                    value={memoryRetrieveDraft.scopeId}
                    onChange={(event) =>
                      setMemoryRetrieveDraft((current) => ({
                        ...current,
                        scopeId: event.target.value,
                      }))
                    }
                    placeholder="scope-..."
                  />
                </label>
                <label>
                  Retrieve Partition
                  <input
                    value={memoryRetrieveDraft.partition}
                    onChange={(event) =>
                      setMemoryRetrieveDraft((current) => ({
                        ...current,
                        partition: event.target.value,
                      }))
                    }
                    placeholder="credential"
                  />
                </label>
                <label>
                  Retrieve Subject
                  <input
                    value={memoryRetrieveDraft.subjectKey}
                    onChange={(event) =>
                      setMemoryRetrieveDraft((current) => ({
                        ...current,
                        subjectKey: event.target.value,
                      }))
                    }
                    placeholder="subject_key"
                  />
                </label>
                <label>
                  Grant ID
                  <input
                    value={memoryRetrieveDraft.grantId}
                    onChange={(event) =>
                      setMemoryRetrieveDraft((current) => ({
                        ...current,
                        grantId: event.target.value,
                      }))
                    }
                    placeholder="可留空，自动匹配"
                  />
                </label>
                <label>
                  Snapshot Ref
                  <input
                    value={memoryRestoreDraft.snapshotRef}
                    onChange={(event) =>
                      setMemoryRestoreDraft((current) => ({
                        ...current,
                        snapshotRef: event.target.value,
                      }))
                    }
                    placeholder="/path/to/memory-export.zip"
                  />
                </label>
                <label>
                  Restore Scope Mode
                  <input
                    value={memoryRestoreDraft.targetScopeMode}
                    onChange={(event) =>
                      setMemoryRestoreDraft((current) => ({
                        ...current,
                        targetScopeMode: event.target.value,
                      }))
                    }
                    placeholder="current_project"
                  />
                </label>
                <label>
                  Restore Scope IDs
                  <input
                    value={memoryRestoreDraft.scopeIds}
                    onChange={(event) =>
                      setMemoryRestoreDraft((current) => ({
                        ...current,
                        scopeIds: event.target.value,
                      }))
                    }
                    placeholder="scope-a,scope-b"
                  />
                </label>
              </div>
              <div className="action-row">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() =>
                    void submitAction("vault.access.request", {
                      project_id: memory.active_project_id,
                      workspace_id: memory.active_workspace_id,
                      scope_id: memoryAccessDraft.scopeId,
                      partition: memoryAccessDraft.partition,
                      subject_key: memoryAccessDraft.subjectKey,
                      reason: memoryAccessDraft.reason,
                    })
                  }
                  disabled={busyActionId === "vault.access.request"}
                >
                  发起授权申请
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() =>
                    void submitAction("vault.retrieve", {
                      project_id: memory.active_project_id,
                      workspace_id: memory.active_workspace_id,
                      scope_id: memoryRetrieveDraft.scopeId || memoryAccessDraft.scopeId,
                      partition:
                        memoryRetrieveDraft.partition || memoryAccessDraft.partition,
                      subject_key:
                        memoryRetrieveDraft.subjectKey || memoryAccessDraft.subjectKey,
                      query: memoryRetrieveDraft.query,
                      grant_id: memoryRetrieveDraft.grantId,
                    })
                  }
                  disabled={busyActionId === "vault.retrieve"}
                >
                  执行 Vault 检索
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() =>
                    void submitAction("memory.export.inspect", {
                      project_id: memory.active_project_id,
                      workspace_id: memory.active_workspace_id,
                      scope_ids: parseCsvList(memoryExportDraft.scopeIds),
                      include_history: memoryExportDraft.includeHistory,
                      include_vault_refs: memoryExportDraft.includeVaultRefs,
                    })
                  }
                  disabled={busyActionId === "memory.export.inspect"}
                >
                  Export Inspect
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() =>
                    void submitAction("memory.restore.verify", {
                      project_id: memory.active_project_id,
                      workspace_id: memory.active_workspace_id,
                      snapshot_ref: memoryRestoreDraft.snapshotRef,
                      target_scope_mode: memoryRestoreDraft.targetScopeMode,
                      scope_ids: parseCsvList(memoryRestoreDraft.scopeIds),
                    })
                  }
                  disabled={busyActionId === "memory.restore.verify"}
                >
                  Restore Verify
                </button>
              </div>
              <div className="meta-grid">
                {(vaultAuthorization?.recent_retrievals ?? []).slice(0, 4).map((item) => (
                  <span key={item.retrieval_id}>
                    {item.subject_key || item.retrieval_id}: {item.reason_code} /{" "}
                    {item.result_count}
                  </span>
                ))}
              </div>
            </article>

            {lastMemoryAction ? (
              <article className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">Latest Memory Action</p>
                    <h3>{lastMemoryAction.action_id}</h3>
                  </div>
                  <span className={`tone-chip ${statusTone(lastMemoryAction.status)}`}>
                    {lastMemoryAction.code}
                  </span>
                </div>
                <pre className="config-editor">{formatJson(lastMemoryAction.data)}</pre>
              </article>
            ) : null}
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
