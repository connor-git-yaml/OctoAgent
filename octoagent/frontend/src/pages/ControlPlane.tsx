import {
  startTransition,
  useDeferredValue,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ApiError,
  fetchControlEvents,
  fetchImportRun,
  fetchImportSource,
  fetchMemoryProposals,
  fetchMemorySubjectHistory,
  fetchVaultAuthorization,
  isFrontDoorApiError,
} from "../api/client";
import FrontDoorGate from "../components/FrontDoorGate";
import { useOptionalWorkbench } from "../components/shell/WorkbenchLayout";
import AutomationSection from "../domains/advanced/AutomationSection";
import AdvancedMemorySection from "../domains/advanced/AdvancedMemorySection";
import CapabilitySection from "../domains/advanced/CapabilitySection";
import ChannelManagementSection from "../domains/advanced/ChannelManagementSection";
import ConfigCenterSection from "../domains/advanced/ConfigCenterSection";
import DashboardSection from "../domains/advanced/DashboardSection";
import DelegationSection from "../domains/advanced/DelegationSection";
import DiagnosticsSection from "../domains/advanced/DiagnosticsSection";
import ImportWorkbenchSection from "../domains/advanced/ImportWorkbenchSection";
import OperatorInboxSection from "../domains/advanced/OperatorInboxSection";
import PipelineSection from "../domains/advanced/PipelineSection";
import ProjectsSection from "../domains/advanced/ProjectsSection";
import SessionCenterSection from "../domains/advanced/SessionCenterSection";
import type {
  ActionResultEnvelope,
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
import { formatWorkerTemplateName } from "../workbench/utils";
import {
  executeWorkbenchActionWithRefresh,
  shouldRefreshFullSnapshot,
} from "../platform/actions";
import { resolveResourceRoutes } from "../platform/contracts";
import {
  fetchWorkbenchSnapshot,
  refreshWorkbenchSnapshotResources,
} from "../platform/queries";

type SessionLaneFilter = "all" | "running" | "queue" | "history";

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
  general: "General Agent",
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

function formatA2AMessageType(type: string): string {
  switch (type.trim().toUpperCase()) {
    case "TASK":
      return "任务下发";
    case "UPDATE":
      return "进度更新";
    case "RESULT":
      return "结果回传";
    case "ERROR":
      return "错误回传";
    case "HEARTBEAT":
      return "心跳";
    case "CANCEL":
      return "取消";
    default:
      return type || "-";
  }
}

function formatA2ADirection(direction: string): string {
  switch (direction.trim().toLowerCase()) {
    case "outbound":
      return "Butler -> Worker";
    case "inbound":
      return "Worker -> Butler";
    default:
      return direction || "-";
  }
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

function resolveSessionLane(item: SessionProjectionItem): Exclude<SessionLaneFilter, "all"> {
  const lane = String(item.lane ?? "").trim().toLowerCase();
  if (lane === "running" || lane === "queue" || lane === "history") {
    return lane;
  }
  const status = String(item.status).trim().toUpperCase();
  if (status === "RUNNING") {
    return "running";
  }
  if (["SUCCEEDED", "FAILED", "CANCELLED", "REJECTED"].includes(status)) {
    return "history";
  }
  return "queue";
}

function sessionMatchesLane(item: SessionProjectionItem, lane: SessionLaneFilter): boolean {
  return lane === "all" ? true : resolveSessionLane(item) === lane;
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

interface ControlPlaneProps {
  initialSnapshot?: ControlPlaneSnapshot | null;
}

export default function ControlPlane({
  initialSnapshot = null,
}: ControlPlaneProps) {
  const sharedWorkbench = useOptionalWorkbench();
  const sharedSnapshot = sharedWorkbench?.snapshot ?? initialSnapshot;
  const [localSnapshot, setLocalSnapshot] = useState<ControlPlaneSnapshot | null>(
    sharedSnapshot
  );
  const [events, setEvents] = useState<ControlPlaneEvent[]>([]);
  const [activeSection, setActiveSection] = useState<SectionId>("dashboard");
  const [loading, setLoading] = useState(sharedSnapshot === null);
  const [error, setError] = useState<string | null>(null);
  const [busyActionId, setBusyActionId] = useState<string | null>(null);
  const [lastAction, setLastAction] = useState<ActionResultEnvelope | null>(null);
  const [sessionFilter, setSessionFilter] = useState("");
  const [sessionLane, setSessionLane] = useState<SessionLaneFilter>("all");
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
  const snapshot = localSnapshot;

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

  useEffect(() => {
    if (!sharedSnapshot) {
      return;
    }
    setLocalSnapshot(sharedSnapshot);
  }, [sharedSnapshot?.generated_at]);

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
      fetchWorkbenchSnapshot(),
      fetchControlEvents(undefined, 50),
    ]);
    clearPageError();
    startTransition(() => {
      setLocalSnapshot(nextSnapshot);
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
    if (!snapshot) {
      await reloadData({ preserveConfigDraft });
      return;
    }

    try {
      const result = await refreshWorkbenchSnapshotResources(snapshot, refs, {
        memoryQuery:
          snapshot.resources.memory != null
            ? buildMemoryQueryFromSnapshot(
                snapshot.resources.memory.active_project_id,
                snapshot.resources.memory.active_workspace_id,
                memoryQueryDraft
              )
            : undefined,
        importQuery:
          snapshot.resources.imports != null
            ? {
                projectId: snapshot.resources.imports.active_project_id,
                workspaceId: snapshot.resources.imports.active_workspace_id,
              }
            : undefined,
      });

      startTransition(() => {
        setLocalSnapshot(() => {
          const nextSnapshot = result.snapshot;

          if (
            !preserveConfigDraft ||
            !configDirtyRef.current ||
            result.routes.includes("config") ||
            result.mode === "full-snapshot"
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
          sharedSnapshot ? Promise.resolve(sharedSnapshot) : fetchWorkbenchSnapshot(),
          fetchControlEvents(undefined, 50),
        ]);
        if (cancelled) {
          return;
        }
        clearPageError();
        startTransition(() => {
          setLocalSnapshot(nextSnapshot);
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
      const refreshTask = sharedWorkbench ? refreshEvents() : reloadData();
      void refreshTask.catch((err) => {
        applyPageError(err, "控制台刷新失败");
      });
    }, 15000);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [sharedSnapshot?.generated_at]);

  useEffect(() => {
    configDirtyRef.current = configDirty;
  }, [configDirty]);

  useEffect(() => {
    if (!snapshot || configDirtyRef.current) {
      return;
    }
    setConfigDraft(formatJson(snapshot.resources.config.current_value));
  }, [snapshot?.resources.config.generated_at]);

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

  const allSessions = snapshot?.resources.sessions.sessions ?? [];
  const filteredSessions = allSessions.filter(
    (item) =>
      sessionMatches(item, deferredSessionFilter) &&
      sessionMatchesLane(item, sessionLane)
  );

  async function bootControlPlane() {
    clearPageError();
    setLoading(true);
    try {
      const [nextSnapshot, eventPayload] = await Promise.all([
        sharedSnapshot ? Promise.resolve(sharedSnapshot) : fetchWorkbenchSnapshot(),
        fetchControlEvents(undefined, 50),
      ]);
      startTransition(() => {
        setLocalSnapshot(nextSnapshot);
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
      const result = await executeWorkbenchActionWithRefresh(
        snapshot?.contract_version,
        actionId,
        params,
        {
          refreshSnapshot: () =>
            reloadData({
              preserveConfigDraft: !(options?.refreshConfigDraft ?? false),
            }),
          refreshResources: (refs) =>
            refreshResources(refs, {
              preserveConfigDraft: !(options?.refreshConfigDraft ?? false),
            }),
        }
      );
      setLastAction(result);
      const touchedRoutes = resolveResourceRoutes(result.resource_refs);
      const touchesAdvancedLocalOnlyData = touchedRoutes.some(
        (route) => route === "memory" || route === "import-workbench"
      );
      if (sharedWorkbench) {
        try {
          if (shouldRefreshFullSnapshot(actionId)) {
            await sharedWorkbench.refreshSnapshot();
          } else if (!touchesAdvancedLocalOnlyData) {
            await sharedWorkbench.refreshResources(result.resource_refs);
          }
        } catch {
          // Advanced 页本地状态已更新；shared workbench 同步失败时不覆盖本地成功结果。
        }
      }
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
  const rootAgentSummary = rootAgentProfilesDocument.summary ?? {};
  const defaultRootAgentId =
    typeof rootAgentSummary.default_profile_id === "string"
      ? rootAgentSummary.default_profile_id
      : "";
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
  const contextAgentRuntimes = context_continuity?.agent_runtimes ?? [];
  const contextAgentSessions = context_continuity?.agent_sessions ?? [];
  const contextMemoryNamespaces = context_continuity?.memory_namespaces ?? [];
  const contextRecallFrames = context_continuity?.recall_frames ?? [];
  const contextA2AConversations = context_continuity?.a2a_conversations ?? [];
  const contextA2AMessages = context_continuity?.a2a_messages ?? [];
  const latestA2AConversation =
    [...contextA2AConversations].sort((left, right) =>
      String(right.updated_at ?? "").localeCompare(String(left.updated_at ?? ""))
    )[0] ?? null;
  const latestA2AMessage =
    latestA2AConversation == null
      ? null
      : [...contextA2AMessages]
          .filter(
            (item) => item.a2a_conversation_id === latestA2AConversation.a2a_conversation_id
          )
          .sort((left, right) => right.message_seq - left.message_seq)[0] ?? null;
  const latestWorkerRecall =
    latestA2AConversation == null
      ? null
      : [...contextRecallFrames]
          .filter(
            (item) => item.agent_session_id === latestA2AConversation.target_agent_session_id
          )
          .sort((left, right) =>
            String(right.created_at ?? "").localeCompare(String(left.created_at ?? ""))
          )[0] ?? null;
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

  function updateRestoreDraft(key: "bundle" | "targetRoot", value: string) {
    setRestoreDraft((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function updateImportDraft(
    key: "sourceType" | "inputPath" | "mediaRoot" | "formatHint",
    value: string
  ) {
    setImportDraft((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function generateDefaultImportMapping() {
    if (!importSourceDetail) {
      return;
    }
    setImportMappingDraft(formatJson(buildDefaultImportMappings(importSourceDetail)));
  }

  function saveImportMapping() {
    if (!importSourceDetail) {
      return;
    }
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
  }

  function previewImportSource() {
    if (!importSourceDetail) {
      return;
    }
    void submitAction(
      "import.preview",
      { source_id: importSourceDetail.source_id },
      { sourceId: importSourceDetail.source_id }
    );
  }

  function runImportSource() {
    if (!importSourceDetail) {
      return;
    }
    void submitAction(
      "import.run",
      { source_id: importSourceDetail.source_id },
      { sourceId: importSourceDetail.source_id }
    );
  }

  function updateAutomationDraft(
    key: "name" | "actionId" | "scheduleKind" | "scheduleExpr" | "enabled",
    value: string | boolean
  ) {
    setAutomationDraft((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function updateMemoryQueryDraft(
    key:
      | "scopeId"
      | "partition"
      | "layer"
      | "query"
      | "includeHistory"
      | "includeVaultRefs"
      | "limit",
    value: string | number | boolean
  ) {
    setMemoryQueryDraft((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function updateMemoryAccessDraft(
    key: "scopeId" | "partition" | "subjectKey" | "reason",
    value: string
  ) {
    setMemoryAccessDraft((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function updateMemoryRetrieveDraft(
    key: "scopeId" | "partition" | "subjectKey" | "query" | "grantId",
    value: string
  ) {
    setMemoryRetrieveDraft((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function updateMemoryExportDraft(
    key: "scopeIds" | "includeHistory" | "includeVaultRefs",
    value: string | boolean
  ) {
    setMemoryExportDraft((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function updateMemoryRestoreDraft(
    key: "snapshotRef" | "targetScopeMode" | "scopeIds",
    value: string
  ) {
    setMemoryRestoreDraft((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function triggerOperatorQuickAction(item: OperatorInboxItem, kind: OperatorActionKind) {
    const mapped = mapQuickAction(item, kind);
    if (!mapped) {
      return;
    }
    void submitAction(mapped.actionId, mapped.params);
  }

  function isOperatorQuickActionBusy(item: OperatorInboxItem, kind: OperatorActionKind): boolean {
    const mapped = mapQuickAction(item, kind);
    if (!mapped) {
      return false;
    }
    return busyActionId === mapped.actionId;
  }

  function runMemoryQuery() {
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
    });
  }

  function resolveVaultRequest(requestId: string, decision: "approve" | "reject") {
    void submitAction("vault.access.resolve", {
      request_id: requestId,
      decision,
      ...(decision === "approve" ? { expires_in_seconds: 3600 } : {}),
    });
  }

  function requestVaultAccess() {
    void submitAction("vault.access.request", {
      project_id: memory.active_project_id,
      workspace_id: memory.active_workspace_id,
      scope_id: memoryAccessDraft.scopeId,
      partition: memoryAccessDraft.partition,
      subject_key: memoryAccessDraft.subjectKey,
      reason: memoryAccessDraft.reason,
    });
  }

  function retrieveVault() {
    void submitAction("vault.retrieve", {
      project_id: memory.active_project_id,
      workspace_id: memory.active_workspace_id,
      scope_id: memoryRetrieveDraft.scopeId || memoryAccessDraft.scopeId,
      partition: memoryRetrieveDraft.partition || memoryAccessDraft.partition,
      subject_key: memoryRetrieveDraft.subjectKey || memoryAccessDraft.subjectKey,
      query: memoryRetrieveDraft.query,
      grant_id: memoryRetrieveDraft.grantId,
    });
  }

  function inspectMemoryExport() {
    void submitAction("memory.export.inspect", {
      project_id: memory.active_project_id,
      workspace_id: memory.active_workspace_id,
      scope_ids: parseCsvList(memoryExportDraft.scopeIds),
      include_history: memoryExportDraft.includeHistory,
      include_vault_refs: memoryExportDraft.includeVaultRefs,
    });
  }

  function verifyMemoryRestore() {
    void submitAction("memory.restore.verify", {
      project_id: memory.active_project_id,
      workspace_id: memory.active_workspace_id,
      snapshot_ref: memoryRestoreDraft.snapshotRef,
      target_scope_mode: memoryRestoreDraft.targetScopeMode,
      scope_ids: parseCsvList(memoryRestoreDraft.scopeIds),
    });
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
              <p className="eyebrow">默认 Worker 模板</p>
              <strong>
                {primaryRootAgentProfile
                  ? formatWorkerTemplateName(
                      primaryRootAgentProfile.name,
                      primaryRootAgentProfile.static_config.base_archetype
                    )
                  : "未接入"}
              </strong>
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
          <DashboardSection
            wizard={wizard}
            currentProjectName={currentProject?.name ?? project_selector.current_project_id}
            currentProjectId={currentProject?.project_id ?? project_selector.current_project_id}
            currentWorkspaceName={
              currentWorkspace?.name ?? project_selector.current_workspace_id
            }
            currentWorkspaceId={
              currentWorkspace?.workspace_id ?? project_selector.current_workspace_id
            }
            workspaceCount={availableWorkspaces.length}
            fallbackReason={project_selector.fallback_reason}
            recentSessions={sessions.sessions.slice(0, 2)}
            operatorPendingCount={sessions.operator_summary?.total_pending ?? 0}
            latestContextFrame={latestContextFrame}
            latestMemoryRecall={latestMemoryRecall}
            latestMemoryCitations={latestMemoryCitations}
            latestA2AConversation={latestA2AConversation}
            latestA2AMessage={latestA2AMessage}
            latestWorkerRecall={latestWorkerRecall}
            contextAgentRuntimes={contextAgentRuntimes}
            contextAgentSessions={contextAgentSessions}
            freshnessReadiness={freshnessReadiness}
            rootAgentLabel={
              primaryRootAgentProfile
                ? formatWorkerTemplateName(
                    primaryRootAgentProfile.name,
                    primaryRootAgentProfile.static_config.base_archetype
                  )
                : "未接入"
            }
            rootAgentSummary={
              primaryRootAgentProfile
                ? `运行中 ${rootAgentRunningCount} / 需关注 ${rootAgentAttentionCount}`
                : "等待 worker_profiles canonical resource"
            }
            capabilityToolCount={capabilityPack.pack.tools.length}
            capabilitySkillCount={capabilityPack.pack.skills.length}
            capabilityWorkerProfileCount={capabilityPack.pack.worker_profiles.length}
            capabilityBootstrapFileCount={capabilityPack.pack.bootstrap_files.length}
            capabilityDegradedReason={capabilityPack.pack.degraded_reason}
            delegationItems={delegationPlane.works.slice(0, 2)}
            pipelineCount={skillPipelines.runs.length}
            diagnosticsSubsystems={diagnostics.subsystems}
            diagnosticsOverallStatus={diagnostics.overall_status}
            diagnosticTone={diagnosticTone}
            automationJobCount={automation.jobs.length}
            automationRunHistoryCursor={automation.run_history_cursor}
            busyActionId={busyActionId}
            onRefreshWizard={() => void submitAction("wizard.refresh", {})}
            onRestartWizard={() => void submitAction("wizard.restart", {})}
            onRefreshContext={() =>
              void refreshResources([
                {
                  resource_type: "context_continuity",
                  resource_id: "context:overview",
                  schema_version: 1,
                },
              ])
            }
            onCreateBackup={() => void submitAction("backup.create", {})}
            onDryRunUpdate={() => void submitAction("update.dry_run", {})}
            onApplyUpdate={() => void submitAction("update.apply", {})}
            onVerifyRuntime={() => void submitAction("runtime.verify", {})}
            formatA2ADirection={formatA2ADirection}
            formatA2AMessageType={formatA2AMessageType}
            formatWorkerType={formatWorkerType}
            formatFreshnessLimitations={formatFreshnessLimitations}
            statusTone={statusTone}
          />
        ) : null}

        {activeSection === "projects" ? (
          <ProjectsSection
            availableProjects={availableProjects}
            availableWorkspaces={availableWorkspaces}
            currentProjectId={project_selector.current_project_id}
            busyActionId={busyActionId}
            onSelectWorkspace={(projectId, workspaceId) =>
              void submitAction("project.select", {
                project_id: projectId,
                workspace_id: workspaceId,
              })
            }
            formatRelativeStatus={formatRelativeStatus}
          />
        ) : null}

        {activeSection === "capability" ? (
          <CapabilitySection
            rootAgentProfilesDocument={rootAgentProfilesDocument}
            defaultRootAgentId={defaultRootAgentId}
            capabilityPack={capabilityPack}
            busyActionId={busyActionId}
            onRefreshCapabilityPack={() => void submitAction("capability.refresh", {})}
            onOpenDelegation={() => setActiveSection("delegation")}
            formatScope={formatScope}
            formatProfileMode={formatProfileMode}
            formatDateTime={formatDateTime}
            formatWorkerTemplateName={formatWorkerTemplateName}
            statusTone={statusTone}
          />
        ) : null}

        {activeSection === "delegation" ? (
          <DelegationSection
            delegationPlane={delegationPlane}
            busyActionId={busyActionId}
            onRefreshDelegation={() => void submitAction("work.refresh", {})}
            onCancelWork={(work) =>
              void submitAction("work.cancel", { work_id: work.work_id })
            }
            onRetryWork={(work) =>
              void submitAction("work.retry", { work_id: work.work_id })
            }
            onSplitWork={(work) =>
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
            onMergeWork={(work) =>
              void submitAction("work.merge", {
                work_id: work.work_id,
                summary: "merged from control plane",
              })
            }
            onEscalateWork={(work) =>
              void submitAction("work.escalate", { work_id: work.work_id })
            }
            onExtractProfileFromRuntime={(work) =>
              void submitAction("worker.extract_profile_from_runtime", {
                work_id: work.work_id,
                name: `${work.title || formatWorkerType(work.selected_worker_type)} Worker 模板`,
              })
            }
            formatJson={formatJson}
            formatWorkerType={formatWorkerType}
            describeFreshnessWorkPath={describeFreshnessWorkPath}
            statusTone={statusTone}
          />
        ) : null}

        {activeSection === "pipelines" ? (
          <PipelineSection
            skillPipelines={skillPipelines}
            busyActionId={busyActionId}
            onResumeRun={(workId) =>
              void submitAction("pipeline.resume", { work_id: workId })
            }
            onRetryNode={(workId) =>
              void submitAction("pipeline.retry_node", { work_id: workId })
            }
            formatDateTime={formatDateTime}
            formatJson={formatJson}
            statusTone={statusTone}
          />
        ) : null}

        {activeSection === "sessions" ? (
          <SessionCenterSection
            sessionFilter={sessionFilter}
            onSessionFilterChange={setSessionFilter}
            sessionLane={sessionLane}
            onSessionLaneChange={setSessionLane}
            sessionSummary={snapshot?.resources.sessions.summary ?? null}
            contextA2AConversations={contextA2AConversations}
            contextA2AMessages={contextA2AMessages}
            contextRecallFrames={contextRecallFrames}
            contextMemoryNamespaceCount={contextMemoryNamespaces.length}
            filteredSessions={filteredSessions}
            focusedSessionId={snapshot?.resources.sessions.focused_session_id ?? ""}
            busyActionId={busyActionId}
            onNewSession={(session) =>
              void submitAction("session.new", {
                session_id: session?.session_id,
                thread_id: session?.thread_id,
              })
            }
            onFocusSession={(session) =>
              void submitAction("session.focus", {
                session_id: session.session_id,
                thread_id: session.thread_id,
              })
            }
            onUnfocusSession={() => void submitAction("session.unfocus", {})}
            onResetSession={(session) =>
              void submitAction("session.reset", {
                session_id: session.session_id,
                thread_id: session.thread_id,
                task_id: session.task_id,
              })
            }
            onExportSession={(session) =>
              void submitAction("session.export", {
                session_id: session.session_id,
                thread_id: session.thread_id,
                task_id: session.task_id,
              })
            }
            onInterruptSession={(session) =>
              void submitAction("session.interrupt", { task_id: session.task_id })
            }
            onResumeSession={(session) =>
              void submitAction("session.resume", { task_id: session.task_id })
            }
            projectNameForId={(projectId) =>
              availableProjects.find((item) => item.project_id === projectId)?.name ?? projectId
            }
            workspaceNameForId={(workspaceId) =>
              availableWorkspaces.find((item) => item.workspace_id === workspaceId)?.name ?? workspaceId
            }
            formatDateTime={formatDateTime}
            formatA2ADirection={formatA2ADirection}
            formatA2AMessageType={formatA2AMessageType}
            formatJson={formatJson}
            statusTone={statusTone}
          />
        ) : null}

        {activeSection === "operator" ? (
          <OperatorInboxSection
            summary={sessions.operator_summary}
            operatorItems={operatorItems}
            onTriggerQuickAction={triggerOperatorQuickAction}
            isQuickActionBusy={isOperatorQuickActionBusy}
            formatDateTime={formatDateTime}
            statusTone={statusTone}
          />
        ) : null}

        {activeSection === "automation" ? (
          <AutomationSection
            automation={automation}
            automationDraft={automationDraft}
            busyActionId={busyActionId}
            onUpdateAutomationDraft={updateAutomationDraft}
            onCreateAutomation={() =>
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
            onRunAutomation={(jobId) =>
              void submitAction("automation.run", { job_id: jobId })
            }
            onPauseAutomation={(jobId) =>
              void submitAction("automation.pause", { job_id: jobId })
            }
            onResumeAutomation={(jobId) =>
              void submitAction("automation.resume", { job_id: jobId })
            }
            onDeleteAutomation={(jobId) =>
              void submitAction("automation.delete", { job_id: jobId })
            }
            formatDateTime={formatDateTime}
            statusTone={statusTone}
          />
        ) : null}

        {activeSection === "diagnostics" ? (
          <DiagnosticsSection
            diagnostics={diagnostics}
            diagnosticTone={diagnosticTone}
            restoreDraft={restoreDraft}
            importDraft={importDraft}
            events={events}
            busyActionId={busyActionId}
            onUpdateRestoreDraft={updateRestoreDraft}
            onUpdateImportDraft={updateImportDraft}
            onPlanRestore={() =>
              void submitAction("restore.plan", {
                bundle: restoreDraft.bundle,
                target_root: restoreDraft.targetRoot,
              })
            }
            onDetectImportSource={() =>
              void submitAction("import.source.detect", {
                source_type: importDraft.sourceType,
                input_path: importDraft.inputPath,
                media_root: importDraft.mediaRoot,
                format_hint: importDraft.formatHint,
              })
            }
            onOpenImports={() => setActiveSection("imports")}
            onRestartRuntime={() => void submitAction("runtime.restart", {})}
            formatDateTime={formatDateTime}
            statusTone={statusTone}
          />
        ) : null}

        {activeSection === "imports" ? (
          <ImportWorkbenchSection
            imports={imports}
            importDraft={importDraft}
            importBusy={importBusy}
            selectedImportSourceId={selectedImportSourceId}
            selectedImportRunId={selectedImportRunId}
            importSourceDetail={importSourceDetail}
            importRunDetail={importRunDetail}
            importMappingDraft={importMappingDraft}
            busyActionId={busyActionId}
            onUpdateImportDraft={updateImportDraft}
            onDetectSource={() =>
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
            onRefreshWorkbench={() =>
              void refreshImportDetails(selectedImportSourceId, selectedImportRunId)
            }
            onSelectSource={(sourceId) =>
              void refreshImportDetails(sourceId, selectedImportRunId)
            }
            onSelectRun={(runId) =>
              void refreshImportDetails(selectedImportSourceId, runId)
            }
            onResumeImport={(resumeId) =>
              void submitAction("import.resume", { resume_id: resumeId }, { runId: selectedImportRunId })
            }
            onImportMappingDraftChange={setImportMappingDraft}
            onGenerateDefaultMapping={generateDefaultImportMapping}
            onSaveMapping={saveImportMapping}
            onPreviewImport={previewImportSource}
            onRunImport={runImportSource}
            formatDateTime={formatDateTime}
            formatJson={formatJson}
            statusTone={statusTone}
          />
        ) : null}

        {activeSection === "memory" ? (
          <AdvancedMemorySection
            memory={memory}
            memoryBusy={memoryBusy}
            busyActionId={busyActionId}
            memoryQueryDraft={memoryQueryDraft}
            memoryAccessDraft={memoryAccessDraft}
            memoryRetrieveDraft={memoryRetrieveDraft}
            memoryExportDraft={memoryExportDraft}
            memoryRestoreDraft={memoryRestoreDraft}
            memoryScopeOptions={memoryScopeOptions}
            memoryPartitionOptions={memoryPartitionOptions}
            memoryLayerOptions={memoryLayerOptions}
            selectedMemoryRecord={selectedMemoryRecord}
            selectedSubjectHistory={selectedSubjectHistory}
            memoryProposals={memoryProposals}
            vaultAuthorization={vaultAuthorization}
            lastMemoryAction={lastMemoryAction}
            onUpdateMemoryQueryDraft={updateMemoryQueryDraft}
            onRefreshMemoryQuery={runMemoryQuery}
            onRefreshMemoryDetails={() => void refreshMemoryDetails(selectedMemorySubjectKey)}
            onFocusMemoryRecord={focusMemoryRecord}
            onResolveVaultRequest={resolveVaultRequest}
            onUpdateMemoryAccessDraft={updateMemoryAccessDraft}
            onRequestVaultAccess={requestVaultAccess}
            onUpdateMemoryRetrieveDraft={updateMemoryRetrieveDraft}
            onRetrieveVault={retrieveVault}
            onUpdateMemoryExportDraft={updateMemoryExportDraft}
            onUpdateMemoryRestoreDraft={updateMemoryRestoreDraft}
            onInspectMemoryExport={inspectMemoryExport}
            onVerifyMemoryRestore={verifyMemoryRestore}
            formatMemoryPartition={formatMemoryPartition}
            formatMemoryLayer={formatMemoryLayer}
            formatDateTime={formatDateTime}
            formatJson={formatJson}
            statusTone={statusTone}
          />
        ) : null}

        {activeSection === "config" ? (
          <ConfigCenterSection
            config={config}
            configDraft={configDraft}
            busyActionId={busyActionId}
            onSaveConfig={() => {
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
            onChangeDraft={(value) => {
              setConfigDraft(value);
              configDirtyRef.current = true;
              setConfigDirty(true);
            }}
          />
        ) : null}

        {activeSection === "channels" ? (
          <ChannelManagementSection
            diagnostics={diagnostics}
            pairingItems={pairingItems}
            busyActionId={busyActionId}
            onTriggerQuickAction={triggerOperatorQuickAction}
          />
        ) : null}
      </main>
    </div>
  );
}
