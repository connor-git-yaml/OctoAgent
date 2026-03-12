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
  WorkerProfilesDocument,
} from "../types";
import {
  buildFreshnessReadiness,
  describeFreshnessWorkPath,
  formatFreshnessLimitations,
} from "../workbench/freshness";

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

const EMPTY_ROOT_AGENT_PROFILES: WorkerProfilesDocument = {
  contract_version: "1.0.0",
  resource_type: "worker_profiles",
  resource_id: "worker-profiles:overview",
  schema_version: 1,
  generated_at: "",
  updated_at: "",
  status: "degraded",
  degraded: {
    is_degraded: true,
    reasons: ["worker_profiles_missing"],
    unavailable_sections: ["worker_profiles"],
  },
  warnings: ["worker profiles resource missing from snapshot"],
  capabilities: [],
  refs: {},
  active_project_id: "",
  active_workspace_id: "",
  profiles: [],
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

const WORKER_TYPE_LABELS: Record<string, string> = {
  general: "Butler",
  ops: "Ops Worker",
  research: "Research Worker",
  dev: "Dev Worker",
};

function formatWorkerType(workerType: string): string {
  return WORKER_TYPE_LABELS[workerType] ?? workerType;
}

function formatScope(scope: string): string {
  if (!scope) {
    return "-";
  }
  return scope.replace(/[._-]+/g, " ").trim();
}

function formatProfileMode(mode: string): string {
  if (!mode) {
    return "-";
  }
  return mode.replace(/[._-]+/g, " ").trim();
}

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

const SECTION_LABELS: Array<{
  id: SectionId;
  label: string;
  accent: string;
  description: string;
}> = [
  {
    id: "dashboard",
    label: "先看状态",
    accent: "Dashboard",
    description: "先确认设置是否完成、系统是否健康，以及当前有哪些任务正在运行。",
  },
  {
    id: "operator",
    label: "待你处理",
    accent: "Operator / Approvals / Retry / Cancel",
    description: "审批、告警、失败重试和设备 pairing 都集中在这里。",
  },
  {
    id: "projects",
    label: "项目与空间",
    accent: "Projects / Workspace",
    description: "切换当前 project 和 workspace，避免在错误环境里操作。",
  },
  {
    id: "sessions",
    label: "对话与任务",
    accent: "Session Center",
    description: "查看最近的对话、任务和执行摘要，快速回到上下文。",
  },
  {
    id: "memory",
    label: "记忆与敏感信息",
    accent: "Memory / Vault",
    description: "先看普通记忆，再按需申请查看敏感内容；高级过滤放在折叠区里。",
  },
  {
    id: "channels",
    label: "渠道连接",
    accent: "Telegram / Devices",
    description: "检查 Telegram 与设备配对状态，处理新的连接请求。",
  },
  {
    id: "automation",
    label: "自动任务",
    accent: "Automation / Scheduler",
    description: "创建定时动作、立即运行、暂停或恢复已有的自动任务。",
  },
  {
    id: "imports",
    label: "导入外部记录",
    accent: "Imports / Resume",
    description: "识别导入源、映射会话、预览结果并继续中断的导入。",
  },
  {
    id: "diagnostics",
    label: "诊断与恢复",
    accent: "Diagnostics / Runtime",
    description: "排查运行问题、验证恢复包、触发重启和查看最近控制事件。",
  },
  {
    id: "capability",
    label: "能力清单",
    accent: "Capability / Pack / ToolIndex",
    description: "查看当前 worker、skills 和 tools 的真实可用状态。",
  },
  {
    id: "delegation",
    label: "委派与执行",
    accent: "Delegation / Routing",
    description: "观察 work 的路由、拆分、升级与合并，适合排查执行链路。",
  },
  {
    id: "pipelines",
    label: "流程回放",
    accent: "Pipelines / Checkpoint / Replay",
    description: "查看 skill pipeline 的节点、checkpoint 和恢复信息。",
  },
  {
    id: "config",
    label: "原始配置",
    accent: "Schema + uiHints",
    description: "这里保留最底层 JSON 配置入口，适合熟悉 schema 的用户。",
  },
];

const MEMORY_PARTITION_LABELS: Record<string, string> = {
  profile: "人物资料",
  credential: "凭证与密钥",
  work: "工作状态",
  health: "健康状态",
  chat: "聊天记录",
};

const MEMORY_LAYER_LABELS: Record<string, string> = {
  fragment: "原始片段",
  sor: "当前结论",
  vault: "敏感原文",
  derived: "推导结果",
};

type ControlResourceRoute =
  | "wizard"
  | "config"
  | "project-selector"
  | "sessions"
  | "worker-profiles"
  | "context-frames"
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
  worker_profiles: "worker-profiles",
  context_continuity: "context-frames",
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
  "worker-profiles": "worker_profiles",
  "context-frames": "context_continuity",
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
    case "worker-profiles":
      return fetchControlResource("worker-profiles");
    case "context-frames":
      return fetchControlResource("context-frames");
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
  if (normalized.includes("unavailable")) {
    return "danger";
  }
  if (normalized.includes("degraded") || normalized.includes("install")) {
    return "warning";
  }
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

function collectUniqueValues(
  values: Array<string | null | undefined>
): string[] {
  return Array.from(
    new Set(
      values
        .map((value) => value?.trim() ?? "")
        .filter((value) => value.length > 0)
    )
  );
}

function formatMemoryPartition(value: string): string {
  if (!value) {
    return "全部内容";
  }
  return MEMORY_PARTITION_LABELS[value] ?? value;
}

function formatMemoryLayer(value: string): string {
  if (!value) {
    return "全部来源";
  }
  return MEMORY_LAYER_LABELS[value] ?? value;
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
    context_continuity,
    worker_profiles,
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
  const rootAgentProfilesDocument: WorkerProfilesDocument =
    worker_profiles ?? EMPTY_ROOT_AGENT_PROFILES;
  const rootAgentProfiles = rootAgentProfilesDocument.profiles ?? [];
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
  const primaryRootAgentProfile = rootAgentProfiles[0] ?? null;
  const rootAgentRunningCount = rootAgentProfiles.reduce(
    (sum, profile) => sum + Math.max(profile.dynamic_context.running_work_count || 0, 0),
    0
  );
  const rootAgentAttentionCount = rootAgentProfiles.reduce(
    (sum, profile) => sum + Math.max(profile.dynamic_context.attention_work_count || 0, 0),
    0
  );
  const selectedSubjectHistory =
    memorySubject?.subject_key === selectedMemorySubjectKey ? memorySubject : null;
  const latestContextFrame = context_continuity?.frames?.[0] ?? null;
  const latestMemoryRecall = latestContextFrame?.memory_recall ?? {};
  const latestMemoryCitations = latestContextFrame?.memory_hits.slice(0, 2) ?? [];
  const activeSectionMeta =
    SECTION_LABELS.find((section) => section.id === activeSection) ?? SECTION_LABELS[0];
  const operatorPendingCount = sessions.operator_summary?.total_pending ?? 0;
  const memoryScopeOptions = collectUniqueValues([
    memory.filters.scope_id,
    ...memory.available_scopes,
    memoryAccessDraft.scopeId,
    memoryRetrieveDraft.scopeId,
  ]);
  const memoryPartitionOptions = collectUniqueValues([
    ...memory.available_partitions,
    memoryQueryDraft.partition,
    memoryAccessDraft.partition,
    memoryRetrieveDraft.partition,
  ]);
  const memoryLayerOptions = collectUniqueValues([
    ...memory.available_layers,
    memoryQueryDraft.layer,
  ]);
  const selectedMemoryRecord =
    memory.records.find((record) => record.subject_key === selectedMemorySubjectKey) ?? null;
  const freshnessReadiness = buildFreshnessReadiness({
    context: context_continuity,
    capabilityPack,
    works: delegationPlane.works,
  });

  function focusMemoryRecord(record: MemoryRecordProjection) {
    if (record.subject_key) {
      setSelectedMemorySubjectKey(record.subject_key);
    }
    setMemoryAccessDraft((current) => ({
      ...current,
      scopeId: record.scope_id || current.scopeId,
      partition: record.partition || current.partition,
      subjectKey: record.subject_key || current.subjectKey,
    }));
    setMemoryRetrieveDraft((current) => ({
      ...current,
      scopeId: record.scope_id || current.scopeId,
      partition: record.partition || current.partition,
      subjectKey: record.subject_key || current.subjectKey,
    }));
    if (record.subject_key) {
      void refreshMemoryDetails(record.subject_key);
    }
  }

  return (
    <div className="control-shell">
      <main className="control-main">
        <section className="panel hero-panel control-overview">
          <header className="control-hero">
            <div>
              <p className="eyebrow">Advanced / Control Plane</p>
              <h1>OctoAgent Control Plane</h1>
              <p className="control-lead">
                这里保留完整控制能力，但默认先用“任务目标”组织内容，而不是直接暴露底层模块名。
                先看状态、待你处理和记忆，再按需进入能力、委派、配置等更底层区域。
              </p>
              <div className="hero-meta">
                <span>
                  Project {currentProject?.name ?? "Default Project"} (
                  {currentProject?.project_id ?? project_selector.current_project_id})
                </span>
                <span>
                  Workspace {currentWorkspace?.name ?? "Primary Workspace"} (
                  {currentWorkspace?.workspace_id ?? project_selector.current_workspace_id})
                </span>
                <span>更新于 {formatDateTime(snapshot.generated_at)}</span>
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
                刷新状态
              </button>
              <button
                type="button"
                className="secondary-button"
                onClick={() => void submitAction("diagnostics.refresh", {})}
                disabled={busyActionId === "diagnostics.refresh"}
              >
                重新诊断
              </button>
            </div>
          </header>

          <div className="control-summary-grid">
            <article className="control-summary-card">
              <p className="eyebrow">当前工作对象</p>
              <strong>{currentProject?.name ?? project_selector.current_project_id}</strong>
              <span>
                {currentProject?.project_id ?? project_selector.current_project_id} /{" "}
                {currentWorkspace?.workspace_id ?? project_selector.current_workspace_id}
              </span>
            </article>
            <article className="control-summary-card">
              <p className="eyebrow">系统状态</p>
              <strong>{diagnostics.overall_status}</strong>
              <span>{diagnostics.subsystems.length} 个子系统已汇总</span>
            </article>
            <article className="control-summary-card">
              <p className="eyebrow">待你处理</p>
              <strong>{operatorPendingCount}</strong>
              <span>
                审批 {sessions.operator_summary?.approvals ?? 0} / 配对{" "}
                {sessions.operator_summary?.pairing_requests ?? 0}
              </span>
            </article>
            <article className="control-summary-card">
              <p className="eyebrow">记忆摘要</p>
              <strong>{memory.summary.sor_current_count}</strong>
              <span>
                当前结论 {memory.summary.sor_current_count} / 提议{" "}
                {memory.summary.proposal_count}
              </span>
            </article>
            <article className="control-summary-card">
              <p className="eyebrow">Root Agent</p>
              <strong>{primaryRootAgentProfile?.name ?? "未接入"}</strong>
              <span>
                {primaryRootAgentProfile
                  ? `运行中 ${rootAgentRunningCount} / 需关注 ${rootAgentAttentionCount}`
                  : "等待 worker_profiles canonical resource"}
              </span>
            </article>
          </div>

          <nav className="control-section-nav" aria-label="Control sections">
            {SECTION_LABELS.map((section) => (
              <button
                key={section.id}
                type="button"
                className={
                  section.id === activeSection
                    ? "control-section-button active"
                    : "control-section-button"
                }
                onClick={() => setActiveSection(section.id)}
              >
                <span>{section.label}</span>
                <small>{section.accent}</small>
              </button>
            ))}
          </nav>
        </section>

        <section className="panel control-section-guide">
          <div className="panel-head">
            <div>
              <p className="eyebrow">{activeSectionMeta.accent}</p>
              <h3>{activeSectionMeta.label}</h3>
            </div>
            <div className="chip-stack">
              <span className={`tone-chip ${diagnosticTone}`}>
                Diagnostics {diagnostics.overall_status}
              </span>
              <span className="tone-chip neutral">Events {events.length}</span>
            </div>
          </div>
          <p>{activeSectionMeta.description}</p>
        </section>

        {lastAction ? (
          <section
            className={`action-banner ${statusTone(lastAction.status)}`}
            role="status"
          >
            <strong>{lastAction.message}</strong>
            <span>
              {lastAction.action_id} · {lastAction.code}
            </span>
            <small>{formatDateTime(lastAction.handled_at)}</small>
          </section>
        ) : null}
        {error ? <section className="action-banner danger">{error}</section> : null}

        {activeSection === "dashboard" ? (
          <section className="section-grid">
            <article className="panel hero-panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">设置进度</p>
                  <h3>{wizard.current_step || "未开始"}</h3>
                </div>
                <span className={`tone-chip ${statusTone(wizard.status)}`}>
                  {formatRelativeStatus(wizard.status)}
                </span>
              </div>
              <p>{wizard.blocking_reason || "基础设置已经具备继续使用条件。"}</p>
              <div className="action-row">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => void submitAction("wizard.refresh", {})}
                  disabled={busyActionId === "wizard.refresh"}
                >
                  重新检查设置
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => void submitAction("wizard.restart", {})}
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
                  <h3>{currentProject?.name ?? project_selector.current_project_id}</h3>
                </div>
                <span className="tone-chip neutral">
                  Workspace {availableWorkspaces.length}
                </span>
              </div>
              <p>
                当前 workspace:{" "}
                <strong>{currentWorkspace?.name ?? project_selector.current_workspace_id}</strong>
              </p>
              {project_selector.fallback_reason ? (
                <p className="muted">{project_selector.fallback_reason}</p>
              ) : null}
            </article>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">最近对话与任务</p>
                  <h3>{sessions.sessions.length}</h3>
                </div>
                <span className="tone-chip neutral">
                  Operator {sessions.operator_summary?.total_pending ?? 0}
                </span>
              </div>
              <p>这里会汇总最近发生的对话、任务和执行状态，方便快速回到现场。</p>
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
                    <div key={`${latestContextFrame?.context_frame_id}-${index}`} className="event-item">
                      <div>
                        <strong>
                          {String(
                            ((hit.citation as Record<string, unknown> | undefined)?.label as string | undefined) ||
                              hit.record_id ||
                              "memory-hit"
                          )}
                        </strong>
                        <p>{String(hit.content_preview ?? hit.summary ?? "暂无 preview")}</p>
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
                  onClick={() =>
                    void refreshResources([
                      {
                        resource_type: "context_continuity",
                        resource_id: "context:overview",
                        schema_version: 1,
                      },
                    ])
                  }
                >
                  刷新 Context
                </button>
              </div>
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
                  当前限制：{formatFreshnessLimitations(freshnessReadiness.limitations)}
                </p>
              ) : (
                <p className="muted">当前没有 freshness 相关降级原因。</p>
              )}
            </article>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">系统能力</p>
                  <h3>{capabilityPack.pack.tools.length}</h3>
                </div>
                <span className="tone-chip neutral">
                  Skills {capabilityPack.pack.skills.length}
                </span>
              </div>
              <p>
                Worker 配置 {capabilityPack.pack.worker_profiles.length} / Bootstrap 文件{" "}
                {capabilityPack.pack.bootstrap_files.length}
              </p>
              <p className="muted">
                ToolIndex {capabilityPack.pack.degraded_reason || "active"}
              </p>
            </article>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">后台执行</p>
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
                      <p>{item.route_reason || formatWorkerType(item.selected_worker_type)}</p>
                    </div>
                    <small>{item.status}</small>
                  </div>
                ))}
              </div>
            </article>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">系统检查</p>
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
                  <p className="eyebrow">自动任务</p>
                  <h3>{automation.jobs.length}</h3>
                </div>
                <span className="tone-chip neutral">
                  Runs {automation.run_history_cursor || "none"}
                </span>
              </div>
              <p>可以创建定时动作，也可以对已有自动任务进行立即运行、暂停、恢复和删除。</p>
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
                  onClick={() => void submitAction("backup.create", {})}
                  disabled={busyActionId === "backup.create"}
                >
                  创建备份
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => void submitAction("update.dry_run", {})}
                  disabled={busyActionId === "update.dry_run"}
                >
                  预演更新
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => void submitAction("update.apply", {})}
                  disabled={busyActionId === "update.apply"}
                >
                  应用更新
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => void submitAction("runtime.verify", {})}
                  disabled={busyActionId === "runtime.verify"}
                >
                  运行自检
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
                  <p className="eyebrow">Canonical Root Agent Profiles</p>
                  <h3>{rootAgentProfiles.length}</h3>
                </div>
                <span className={`tone-chip ${statusTone(rootAgentProfilesDocument.status)}`}>
                  {rootAgentProfilesDocument.status}
                </span>
              </div>
              <p className="muted">
                这组数据来自 `worker_profiles` canonical resource，用于看 Root Agent 的真实静态配置与动态上下文；
                它不等同于下方 bundled capability pack 里的 Worker archetypes。
              </p>
              {rootAgentProfiles.length === 0 ? (
                <div className="event-item">
                  <div>
                    <strong>worker_profiles 还没有数据</strong>
                    <p>等后端把 canonical resource 投进 snapshot 后，这里会直接显示 Root Agent lens。</p>
                  </div>
                </div>
              ) : (
                <div className="wb-root-agent-list">
                  {rootAgentProfiles.map((profile) => {
                    const staticConfig = profile.static_config;
                    const dynamicContext = profile.dynamic_context;
                    const defaultToolGroups = staticConfig.default_tool_groups ?? [];
                    const staticCapabilities = staticConfig.capabilities ?? [];
                    const runtimeKinds = staticConfig.runtime_kinds ?? [];
                    const currentSelectedTools = dynamicContext.current_selected_tools ?? [];
                    const tone =
                      dynamicContext.attention_work_count > 0
                        ? "warning"
                        : dynamicContext.running_work_count > 0
                          ? "running"
                          : "neutral";
                    return (
                      <article
                        key={profile.profile_id}
                        className={`wb-root-agent-card ${
                          profile.warnings.length > 0 ? "has-warning" : ""
                        }`}
                      >
                        <div className="wb-root-agent-card-head">
                          <div>
                            <p className="wb-card-label">Root Agent Lens</p>
                            <h3>{profile.name || profile.profile_id}</h3>
                            <p className="wb-inline-note">
                              {profile.summary || "当前 profile 没有额外 summary。"}
                            </p>
                          </div>
                          <div className="wb-chip-row">
                            <span className="wb-chip">{formatScope(profile.scope)}</span>
                            <span className="wb-chip">{formatProfileMode(profile.mode)}</span>
                            <span className={`tone-chip ${tone}`}>
                              {dynamicContext.latest_work_status || "idle"}
                            </span>
                          </div>
                        </div>
                        <div className="wb-root-agent-console">
                          <section className="wb-root-agent-column">
                            <div className="wb-root-agent-column-head">
                              <strong>静态配置</strong>
                              <span>{profile.profile_id}</span>
                            </div>
                            <div className="wb-key-value-list">
                              <span>Archetype</span>
                              <strong>{staticConfig.base_archetype || "-"}</strong>
                              <span>Model</span>
                              <strong>{staticConfig.model_alias || "-"}</strong>
                              <span>Tool Profile</span>
                              <strong>{staticConfig.tool_profile || "-"}</strong>
                              <span>Runtime</span>
                              <strong>{runtimeKinds.join(", ") || "-"}</strong>
                            </div>
                            <div className="wb-root-agent-token-stack">
                              <div>
                                <p className="wb-card-label">默认工具组</p>
                                <div className="wb-chip-row">
                                  {defaultToolGroups.length > 0 ? (
                                    defaultToolGroups.map((toolGroup) => (
                                      <span key={toolGroup} className="wb-chip">
                                        {toolGroup}
                                      </span>
                                    ))
                                  ) : (
                                    <span className="wb-inline-note">未标记默认工具组</span>
                                  )}
                                </div>
                              </div>
                              <div>
                                <p className="wb-card-label">Capabilities</p>
                                <div className="wb-chip-row">
                                  {staticCapabilities.length > 0 ? (
                                    staticCapabilities.map((capability) => (
                                      <span key={capability} className="wb-chip is-warning">
                                        {capability}
                                      </span>
                                    ))
                                  ) : (
                                    <span className="wb-inline-note">未标记静态能力</span>
                                  )}
                                </div>
                              </div>
                            </div>
                          </section>
                          <section className="wb-root-agent-column">
                            <div className="wb-root-agent-column-head">
                              <strong>动态上下文</strong>
                              <span>
                                {dynamicContext.updated_at
                                  ? formatDateTime(dynamicContext.updated_at)
                                  : "未记录"}
                              </span>
                            </div>
                            <div className="wb-root-agent-context-grid">
                              <div className="wb-detail-block">
                                <span className="wb-card-label">Active</span>
                                <strong>{dynamicContext.active_work_count ?? 0}</strong>
                                <p>Running {dynamicContext.running_work_count ?? 0}</p>
                              </div>
                              <div className="wb-detail-block">
                                <span className="wb-card-label">Attention</span>
                                <strong>{dynamicContext.attention_work_count ?? 0}</strong>
                                <p>Target {dynamicContext.latest_target_kind || "-"}</p>
                              </div>
                            </div>
                            <div className="wb-key-value-list">
                              <span>Context</span>
                              <strong>
                                {dynamicContext.active_project_id || "-"} /{" "}
                                {dynamicContext.active_workspace_id || "-"}
                              </strong>
                              <span>Latest Work</span>
                              <strong>
                                {dynamicContext.latest_work_title || dynamicContext.latest_work_id || "-"}
                              </strong>
                              <span>Latest Task</span>
                              <strong>{dynamicContext.latest_task_id || "-"}</strong>
                            </div>
                            <div>
                              <p className="wb-card-label">当前选中工具</p>
                              <div className="wb-chip-row">
                                {currentSelectedTools.length > 0 ? (
                                  currentSelectedTools.map((tool) => (
                                    <span key={tool} className="wb-chip">
                                      {tool}
                                    </span>
                                  ))
                                ) : (
                                  <span className="wb-inline-note">当前没有记录 selected tools</span>
                                )}
                              </div>
                            </div>
                          </section>
                        </div>
                        {profile.capabilities.length > 0 ? (
                          <div className="wb-root-agent-cap-row">
                            <span className="wb-card-label">资源能力</span>
                            <div className="wb-chip-row">
                              {profile.capabilities.map((capability) => (
                                <span key={capability.capability_id} className="wb-chip">
                                  {capability.label}
                                </span>
                              ))}
                            </div>
                          </div>
                        ) : null}
                        {profile.warnings.length > 0 ? (
                          <div className="event-list">
                            {profile.warnings.map((warning) => (
                              <div key={warning} className="event-item">
                                <div>
                                  <strong>Warning</strong>
                                  <p>{warning}</p>
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : null}
                        <div className="action-row">
                          <button
                            type="button"
                            className="ghost-button"
                            onClick={() => setActiveSection("delegation")}
                          >
                            查看委派链路
                          </button>
                          {dynamicContext.latest_task_id ? (
                            <Link className="inline-link" to={`/tasks/${dynamicContext.latest_task_id}`}>
                              打开最近任务
                            </Link>
                          ) : null}
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </article>
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
                <span>Bundled Worker Archetypes {capabilityPack.pack.worker_profiles.length}</span>
                <span>Fallback {capabilityPack.pack.fallback_toolset.join(", ") || "-"}</span>
              </div>
            </article>
            {capabilityPack.pack.worker_profiles.map((profile) => (
              <article key={profile.worker_type} className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">Bundled Worker Archetype</p>
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
                      <p className="muted">
                        Entrypoints {tool.entrypoints.join(", ") || "-"} · Runtime{" "}
                        {tool.runtime_kinds.join(", ") || "-"}
                      </p>
                      {tool.availability_reason || tool.install_hint ? (
                        <p className="muted">
                          {tool.availability_reason || tool.install_hint}
                        </p>
                      ) : null}
                    </div>
                    <div style={{ display: "grid", gap: "0.25rem", justifyItems: "end" }}>
                      <span className={`tone-chip ${statusTone(tool.availability)}`}>
                        {tool.availability}
                      </span>
                      <small>{tool.tags.join(", ") || tool.tool_profile}</small>
                    </div>
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
            {delegationPlane.works.map((work) => {
              const freshnessPath = describeFreshnessWorkPath(work);
              return (
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
                  <span>Worker {formatWorkerType(work.selected_worker_type || "-")}</span>
                  <span>Target {work.target_kind || "-"}</span>
                  <span>Runtime {work.runtime_id || "-"}</span>
                  <span>Pipeline {work.pipeline_run_id || "-"}</span>
                  <span>Children {work.child_work_count}</span>
                  <span>Merge Ready {work.merge_ready ? "yes" : "no"}</span>
                  <span>
                    Requested Profile {work.requested_worker_profile_id || "archetype fallback"}
                  </span>
                  <span>
                    Revision {work.requested_worker_profile_version || "-"} / Snapshot{" "}
                    {work.effective_worker_snapshot_id || "-"}
                  </span>
                </div>
                <p>{work.route_reason || "无 route reason"}</p>
                <p className="muted">
                  Selected Tools: {work.selected_tools.join(", ") || "none"}
                </p>
                {freshnessPath ? <p className="muted">{freshnessPath}</p> : null}
                {work.runtime_summary &&
                Object.keys(work.runtime_summary).length > 0 ? (
                  <pre className="json-preview">{formatJson(work.runtime_summary)}</pre>
                ) : null}
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
                      void submitAction("work.split", {
                        work_id: work.work_id,
                        objectives: [
                          `${work.title || work.task_id} / child-1`,
                          `${work.title || work.task_id} / child-2`,
                        ],
                        worker_type: work.selected_worker_type || "general",
                        target_kind:
                          work.target_kind === "fallback" ? "subagent" : work.target_kind,
                      })
                    }
                    disabled={busyActionId === "work.split"}
                  >
                    拆分
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() =>
                      void submitAction("work.merge", {
                        work_id: work.work_id,
                        summary: "merged from control plane",
                      })
                    }
                    disabled={busyActionId === "work.merge" || !work.merge_ready}
                  >
                    合并
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
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() =>
                      void submitAction("worker.extract_profile_from_runtime", {
                        work_id: work.work_id,
                        name: `${work.title || formatWorkerType(work.selected_worker_type)} Root Agent`,
                      })
                    }
                    disabled={busyActionId === "worker.extract_profile_from_runtime"}
                  >
                    提炼 Root Agent
                  </button>
                </div>
                </article>
              );
            })}
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
                  <span>Runtime: {session.runtime_kind || "-"}</span>
                  <span>Parent Task: {session.parent_task_id || "-"}</span>
                </div>
                {session.execution_summary &&
                Object.keys(session.execution_summary).length > 0 ? (
                  <pre className="json-preview">
                    {formatJson(session.execution_summary)}
                  </pre>
                ) : null}
                <div className="action-row">
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      void submitAction("session.focus", {
                        session_id: session.session_id,
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
                        session_id: session.session_id,
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
                  <p className="eyebrow">记忆与敏感信息 / Memory Console</p>
                  <h3>{memory.active_project_id || "未绑定 Project"}</h3>
                </div>
                <span className={`tone-chip ${statusTone(memory.status)}`}>
                  {formatRelativeStatus(memory.status)}
                </span>
              </div>
              <p className="muted">
                先用“内容类型 + 关键词”缩小范围，再从结果里点“查看历史”。如果需要查看敏感内容，
                下方授权表单会自动带入你选中的条目。
              </p>
              <div className="meta-grid">
                <span>Workspace {memory.active_workspace_id || "-"}</span>
                <span>范围 {memory.summary.scope_count}</span>
                <span>片段 {memory.summary.fragment_count}</span>
                <span>当前结论 {memory.summary.sor_current_count}</span>
                <span>历史版本 {memory.summary.sor_history_count}</span>
                <span>敏感引用 {memory.summary.vault_ref_count}</span>
                <span>写入提议 {memory.summary.proposal_count}</span>
              </div>
              <div className="form-grid">
                <label>
                  关键词
                  <input
                    value={memoryQueryDraft.query}
                    onChange={(event) =>
                      setMemoryQueryDraft((current) => ({
                        ...current,
                        query: event.target.value,
                      }))
                    }
                    placeholder="例如 Alice / credential / 健康检查"
                  />
                </label>
                <label>
                  想看哪类内容
                  <select
                    value={memoryQueryDraft.partition}
                    onChange={(event) =>
                      setMemoryQueryDraft((current) => ({
                        ...current,
                        partition: event.target.value,
                      }))
                    }
                  >
                    <option value="">全部内容</option>
                    {memoryPartitionOptions.map((item) => (
                      <option key={item} value={item}>
                        {formatMemoryPartition(item)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  来自哪一层
                  <select
                    value={memoryQueryDraft.layer}
                    onChange={(event) =>
                      setMemoryQueryDraft((current) => ({
                        ...current,
                        layer: event.target.value,
                      }))
                    }
                  >
                    <option value="">全部来源</option>
                    {memoryLayerOptions.map((item) => (
                      <option key={item} value={item}>
                        {formatMemoryLayer(item)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  展示数量
                  <select
                    value={memoryQueryDraft.limit}
                    onChange={(event) =>
                      setMemoryQueryDraft((current) => ({
                        ...current,
                        limit: Number(event.target.value) || 50,
                      }))
                    }
                  >
                    {[20, 50, 100, 200].map((item) => (
                      <option key={item} value={item}>
                        {item}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <details className="disclosure-card">
                <summary>高级过滤</summary>
                <div className="form-grid">
                  <label>
                    限定 Scope
                    <select
                      value={memoryQueryDraft.scopeId}
                      onChange={(event) =>
                        setMemoryQueryDraft((current) => ({
                          ...current,
                          scopeId: event.target.value,
                        }))
                      }
                    >
                      <option value="">当前项目全部范围</option>
                      {memoryScopeOptions.map((item) => (
                        <option key={item} value={item}>
                          {item}
                        </option>
                      ))}
                    </select>
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
                    包含历史版本
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
              </details>
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
                  刷新授权与提议
                </button>
              </div>
              {memory.available_scopes.length > 0 ? (
                <p className="muted">
                  当前可用范围: {memory.available_scopes.join(", ")}
                </p>
              ) : null}
            </article>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">搜索结果 / Memory Records</p>
                  <h3>{memory.records.length}</h3>
                </div>
                <span className="tone-chip neutral">
                  {memoryLayerOptions.map((item) => formatMemoryLayer(item)).join(" / ") ||
                    "全部来源"}
                </span>
              </div>
              {selectedMemoryRecord ? (
                <p className="muted">
                  当前已选目标: {selectedMemoryRecord.summary || selectedMemoryRecord.subject_key}
                </p>
              ) : (
                <p className="muted">还没有选中条目。点任意一条记录即可查看历史并带入授权表单。</p>
              )}
              <div className="event-list">
                {memory.records.map((record) => (
                  <div key={record.record_id} className="event-item">
                    <div>
                      <strong>{record.summary || record.subject_key || record.record_id}</strong>
                      <p>
                        {formatMemoryLayer(record.layer)} / {formatMemoryPartition(record.partition)} /{" "}
                        {record.scope_id}
                      </p>
                      <small>
                        subject={record.subject_key || "-"} | 状态={record.status} | 版本=
                        {record.version ?? "-"}
                      </small>
                    </div>
                    <div className="action-row">
                      <span
                        className={`tone-chip ${
                          record.requires_vault_authorization ? "warning" : "neutral"
                        }`}
                      >
                        {record.requires_vault_authorization ? "需授权" : "普通记录"}
                      </span>
                      {record.subject_key ? (
                        <button
                          type="button"
                          className="ghost-button"
                          onClick={() => focusMemoryRecord(record)}
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
                    <p className="eyebrow">这条内容的变化 / Subject History</p>
                    <h3>{selectedSubjectHistory?.subject_key || "未选择条目"}</h3>
                  </div>
                  <span className="tone-chip neutral">
                    {selectedSubjectHistory?.history.length ?? 0} history
                  </span>
                </div>
                {selectedSubjectHistory ? (
                  <>
                    <p>
                      当前版本:{" "}
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
                          <small>{formatMemoryPartition(record.partition)}</small>
                        </div>
                      ))}
                    </div>
                  </>
                ) : (
                  <p className="muted">从上方结果里选一条记录，系统会自动带入历史和授权信息。</p>
                )}
              </article>

              <article className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">写入建议 / WriteProposal Audit</p>
                    <h3>{memoryProposals?.items.length ?? 0}</h3>
                  </div>
                  <span className="tone-chip neutral">
                    Pending {memoryProposals?.summary.pending ?? 0}
                  </span>
                </div>
                <div className="meta-grid">
                  <span>已校验 {memoryProposals?.summary.validated ?? 0}</span>
                  <span>已拒绝 {memoryProposals?.summary.rejected ?? 0}</span>
                  <span>已提交 {memoryProposals?.summary.committed ?? 0}</span>
                </div>
                <div className="event-list">
                  {(memoryProposals?.items ?? []).map((item) => (
                    <div key={item.proposal_id} className="event-item">
                      <div>
                        <strong>{item.subject_key || item.proposal_id}</strong>
                        <p>
                          {item.action} / {formatMemoryPartition(item.partition)} / {item.scope_id}
                        </p>
                        <small>{item.rationale || "没有额外说明"}</small>
                      </div>
                      <small>{item.status}</small>
                    </div>
                  ))}
                </div>
              </article>

              <article className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">授权申请 / Vault Authorization</p>
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
                          {item.scope_id} / {formatMemoryPartition(item.partition || "")} /{" "}
                          {item.status}
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

            <div className="section-grid">
              <article className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">申请查看敏感内容</p>
                    <h3>先申请，再查看</h3>
                  </div>
                  <span className="tone-chip neutral">
                    Requests {vaultAuthorization?.active_requests.length ?? 0}
                  </span>
                </div>
                <p className="muted">
                  选中上方记录后，这里会自动带入 scope、内容类型和目标条目。只需要补充查看原因。
                </p>
                <div className="form-grid">
                  <label>
                    申请范围
                    <select
                      value={memoryAccessDraft.scopeId}
                      onChange={(event) =>
                        setMemoryAccessDraft((current) => ({
                          ...current,
                          scopeId: event.target.value,
                        }))
                      }
                    >
                      <option value="">沿用当前结果范围</option>
                      {memoryScopeOptions.map((item) => (
                        <option key={item} value={item}>
                          {item}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    申请内容类型
                    <select
                      value={memoryAccessDraft.partition}
                      onChange={(event) =>
                        setMemoryAccessDraft((current) => ({
                          ...current,
                          partition: event.target.value,
                        }))
                      }
                    >
                      <option value="">未指定</option>
                      {memoryPartitionOptions.map((item) => (
                        <option key={item} value={item}>
                          {formatMemoryPartition(item)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    申请目标条目
                    <input
                      value={memoryAccessDraft.subjectKey}
                      onChange={(event) =>
                        setMemoryAccessDraft((current) => ({
                          ...current,
                          subjectKey: event.target.value,
                        }))
                      }
                      placeholder="例如 credential:db"
                    />
                  </label>
                  <label>
                    申请原因
                    <input
                      value={memoryAccessDraft.reason}
                      onChange={(event) =>
                        setMemoryAccessDraft((current) => ({
                          ...current,
                          reason: event.target.value,
                        }))
                      }
                      placeholder="例如 临时排障、核对配置"
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
                </div>
              </article>

              <article className="panel">
                <div className="panel-head">
                  <div>
                    <p className="eyebrow">检索敏感内容</p>
                    <h3>有授权后再精确搜索</h3>
                  </div>
                  <span className="tone-chip neutral">
                    Retrievals {vaultAuthorization?.recent_retrievals.length ?? 0}
                  </span>
                </div>
                <p className="muted">
                  如果只想查看某条敏感记录，直接填目标条目；如果结果较多，再用关键词缩小范围。
                </p>
                <div className="form-grid">
                  <label>
                    检索范围
                    <select
                      value={memoryRetrieveDraft.scopeId}
                      onChange={(event) =>
                        setMemoryRetrieveDraft((current) => ({
                          ...current,
                          scopeId: event.target.value,
                        }))
                      }
                    >
                      <option value="">沿用当前结果范围</option>
                      {memoryScopeOptions.map((item) => (
                        <option key={item} value={item}>
                          {item}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    检索内容类型
                    <select
                      value={memoryRetrieveDraft.partition}
                      onChange={(event) =>
                        setMemoryRetrieveDraft((current) => ({
                          ...current,
                          partition: event.target.value,
                        }))
                      }
                    >
                      <option value="">未指定</option>
                      {memoryPartitionOptions.map((item) => (
                        <option key={item} value={item}>
                          {formatMemoryPartition(item)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    检索目标条目
                    <input
                      value={memoryRetrieveDraft.subjectKey}
                      onChange={(event) =>
                        setMemoryRetrieveDraft((current) => ({
                          ...current,
                          subjectKey: event.target.value,
                        }))
                      }
                      placeholder="例如 credential:db"
                    />
                  </label>
                  <label>
                    检索关键词
                    <input
                      value={memoryRetrieveDraft.query}
                      onChange={(event) =>
                        setMemoryRetrieveDraft((current) => ({
                          ...current,
                          query: event.target.value,
                        }))
                      }
                      placeholder="例如 password / Database"
                    />
                  </label>
                </div>
                <details className="disclosure-card">
                  <summary>高级参数</summary>
                  <div className="form-grid">
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
                        placeholder="留空则自动匹配"
                      />
                    </label>
                  </div>
                </details>
                <div className="action-row">
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
            </div>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">高级工具</p>
                  <h3>导出与恢复</h3>
                </div>
                <span className="tone-chip neutral">
                  仅在需要迁移、审计或恢复时使用
                </span>
              </div>
              <details className="disclosure-card">
                <summary>打开导出与恢复参数</summary>
                <div className="form-grid">
                  <label>
                    导出 Scope IDs
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
                    导出包含历史
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
                    导出包含 Vault 引用
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
              </details>
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
