import type {
  ProjectOption,
  SessionProjectionItem,
  WorkProjectionItem,
  WorkerProfileItem,
} from "../../types";
import { formatSessionDisplayTitle } from "../../workbench/utils";
import { readSummaryString, resolveProjectName, resolveSessionOwnerProfileId } from "./session";

export function formatTaskStatusLabel(status: string): string {
  switch (String(status ?? "").trim().toUpperCase()) {
    case "QUEUED":
      return "排队中";
    case "RUNNING":
      return "进行中";
    case "WAITING_INPUT":
      return "等你补充";
    case "WAITING_APPROVAL":
      return "等你确认";
    case "PAUSED":
      return "已暂停";
    case "SUCCEEDED":
      return "已完成";
    case "FAILED":
      return "失败";
    case "CANCELLED":
      return "已取消";
    case "REJECTED":
      return "已拒绝";
    default:
      return status || "尚未创建";
  }
}

export function formatTaskStatusTone(status: string): string {
  switch (String(status ?? "").trim().toUpperCase()) {
    case "SUCCEEDED":
      return "success";
    case "RUNNING":
      return "running";
    case "QUEUED":
      return "draft";
    case "WAITING_INPUT":
    case "WAITING_APPROVAL":
      return "warning";
    case "FAILED":
    case "REJECTED":
      return "danger";
    case "CANCELLED":
    case "PAUSED":
      return "draft";
    default:
      return "draft";
  }
}

export function formatAgentRoleLabel(
  agent: string,
  opts?: { isMainAgent?: boolean },
): string {
  const normalized = String(agent ?? "").trim().toLowerCase();
  if (!normalized) {
    return "未分配";
  }
  if (opts?.isMainAgent) {
    return "主助手";
  }
  if (normalized.includes("research")) {
    return "Research Worker";
  }
  if (normalized.includes("ops")) {
    return "Ops Worker";
  }
  if (normalized.includes("dev")) {
    return "Dev Worker";
  }
  return agent.replace(/^agent:\/\//, "");
}

export function formatToolBoundaryLabel(mode: string): string {
  switch (String(mode ?? "").trim().toLowerCase()) {
    case "profile_first_core":
      return "优先沿用当前模板的工具范围";
    case "runtime_first":
      return "优先沿用这轮任务临时挂载的工具";
    case "core_only":
      return "只使用平台默认工具范围";
    default:
      return mode || "当前没有记录工具范围";
  }
}

export function formatDiscoveryEntrypointLabel(entrypoint: string): string {
  switch (String(entrypoint ?? "").trim().toLowerCase()) {
    case "work.plan":
      return "让系统重新评估是否需要额外角色";
    case "memory.search":
      return "补查已有背景记录";
    case "web.search":
      return "补查外部资料";
    default:
      return entrypoint || "未记录";
  }
}

export function formatCollaborationDirectionLabel(direction: string): string {
  return direction === "inbound" ? "专门角色 -> 主助手" : "主助手 -> 专门角色";
}

// ---------------------------------------------------------------------------
// ChatWorkbench 派生：会话头部展示（F143 件 2 块 D 下沉）
// ---------------------------------------------------------------------------

export interface ChatHeaderPresentationOptions {
  taskId: string | null;
  routeSessionId?: string;
  /** taskDetail?.task?.title */
  taskDetailTitle: string | undefined;
  activeSession: SessionProjectionItem | null;
  currentSession: SessionProjectionItem | null;
  /** routeSession?.thread_id */
  routeSessionThreadId: string | undefined;
  activeWork: WorkProjectionItem | null;
  activeConversationId: string;
  activeConversationWorkerSessionId: string;
  /** activeContextFrame?.context_frame_id */
  activeContextFrameId: string | undefined;
  activeSessionOwnerProfileId: string;
  workerProfiles: WorkerProfileItem[];
  /** workerProfilesDocument?.summary */
  workerProfilesSummary: Record<string, unknown>;
  availableProjects: ProjectOption[];
  /** projectSelector?.current_project_id ?? "" */
  currentProjectId: string;
  /** sessionDocument?.new_conversation_project_id ?? "" */
  pendingConversationProjectId: string;
  /** sessionDocument?.new_conversation_agent_profile_id ?? "" */
  pendingConversationAgentProfileId: string;
}

export interface ChatHeaderPresentation {
  conversationTitle: string;
  techRefs: Array<{ label: string; value: string }>;
  effectiveProjectId: string;
  effectiveProjectLabel: string;
  currentSessionOwnerProfileId: string;
  conversationOwnerName: string;
  currentSessionAlias: string;
  sessionTitleBase: string;
  sessionDisplayName: string;
  editableSessionId: string;
  editableThreadId: string;
  canEditSessionAlias: boolean;
}

export function deriveChatHeaderPresentation(
  options: ChatHeaderPresentationOptions
): ChatHeaderPresentation {
  const {
    taskId,
    routeSessionId,
    taskDetailTitle,
    activeSession,
    currentSession,
    routeSessionThreadId,
    activeWork,
    activeConversationId,
    activeConversationWorkerSessionId,
    activeContextFrameId,
    activeSessionOwnerProfileId,
    workerProfiles,
    workerProfilesSummary,
    availableProjects,
    currentProjectId,
    pendingConversationProjectId,
    pendingConversationAgentProfileId,
  } = options;

  const defaultRootAgentId = readSummaryString(workerProfilesSummary, "default_profile_id");
  const defaultRootAgent = workerProfiles.find(
    (profile) => profile.profile_id === defaultRootAgentId
  );
  // default_profile_name 由后端解析（包含 AgentProfile 回退），用于无会话时的名称降级
  const defaultRootAgentName = readSummaryString(workerProfilesSummary, "default_profile_name");

  const conversationTitle =
    taskDetailTitle ||
    activeSession?.title ||
    activeSession?.latest_message_summary ||
    (taskId ? "这轮对话正在处理中" : "开始一段对话");
  const techRefs = [
    taskId ? { label: "任务 ID", value: taskId } : null,
    activeSession?.session_id ? { label: "会话 ID", value: activeSession.session_id } : null,
    activeWork?.work_id ? { label: "Work ID", value: activeWork.work_id } : null,
    activeConversationId ? { label: "协作链路 ID", value: activeConversationId } : null,
    activeConversationWorkerSessionId
      ? { label: "执行会话", value: activeConversationWorkerSessionId }
      : null,
    activeContextFrameId ? { label: "上下文帧 ID", value: activeContextFrameId } : null,
  ].filter((item): item is { label: string; value: string } => Boolean(item));

  const activeSessionProjectId = activeSession?.project_id ?? "";
  const effectiveProjectId =
    activeSessionProjectId || pendingConversationProjectId || currentProjectId;
  const effectiveProjectLabel = effectiveProjectId
    ? resolveProjectName(availableProjects, effectiveProjectId)
    : "";

  // 会话 owner = 用户首先选择与之对话的 Agent；不是本轮执行 target。
  const currentSessionOwnerProfileId =
    activeSessionOwnerProfileId ||
    resolveSessionOwnerProfileId(currentSession) ||
    pendingConversationAgentProfileId ||
    defaultRootAgentId;
  const conversationOwnerName =
    (activeSession ?? currentSession)?.session_owner_name?.trim() ||
    workerProfiles.find((p) => p.profile_id === currentSessionOwnerProfileId)?.name ||
    defaultRootAgent?.name ||
    defaultRootAgentName ||
    "OctoAgent";
  const currentSessionAlias =
    currentSession?.alias?.trim() || activeSession?.alias?.trim() || "";
  const sessionTitleBase =
    (currentSession?.title?.trim()) ||
    (activeSession?.title?.trim()) ||
    effectiveProjectLabel ||
    "";
  const sessionDisplayName = formatSessionDisplayTitle({
    alias: currentSessionAlias,
    title: sessionTitleBase,
    fallbackTitle: conversationTitle,
  });
  const editableSessionId =
    currentSession?.session_id || activeSession?.session_id || routeSessionId || "";
  const editableThreadId =
    currentSession?.thread_id || activeSession?.thread_id || routeSessionThreadId || "";
  const canEditSessionAlias = Boolean(editableSessionId);

  return {
    conversationTitle,
    techRefs,
    effectiveProjectId,
    effectiveProjectLabel,
    currentSessionOwnerProfileId,
    conversationOwnerName,
    currentSessionAlias,
    sessionTitleBase,
    sessionDisplayName,
    editableSessionId,
    editableThreadId,
    canEditSessionAlias,
  };
}
