import type {
  A2AConversationItem,
  ContextFrameItem,
  ExecutionSessionDocument,
  ProjectOption,
  SessionProjectionDocument,
  SessionProjectionItem,
  WorkProjectionItem,
} from "../../types";

export function ensureArray<T>(value: T[] | null | undefined): T[] {
  return Array.isArray(value) ? value : [];
}

export function readExecutionSessionDocument(
  response: { session?: ExecutionSessionDocument | null } | null | undefined
): ExecutionSessionDocument | null {
  return response?.session ?? null;
}

export function resolveProjectName(projects: ProjectOption[], projectId: string): string {
  return projects.find((item) => item.project_id === projectId)?.name ?? projectId;
}

export function pushRestoreTaskId(taskIds: string[], taskId: string | undefined): void {
  if (!taskId || taskIds.includes(taskId)) {
    return;
  }
  taskIds.push(taskId);
}

export function resolveRestorableTaskIds(sessions: SessionProjectionDocument): string[] {
  if (
    typeof sessions.new_conversation_token === "string" &&
    sessions.new_conversation_token.trim()
  ) {
    return [];
  }
  const sessionItems = ensureArray(sessions.sessions);
  const cleanSessionItems = sessionItems.filter((item) => !item.reset_recommended);
  const webSessions = cleanSessionItems.filter(
    (item) => item.channel === "web" && !item.reset_recommended
  );
  const candidates = webSessions.length > 0 ? webSessions : cleanSessionItems;
  const taskIds: string[] = [];

  if (sessions.focused_session_id) {
    const focused = candidates.find((item) => item.session_id === sessions.focused_session_id);
    if (focused) {
      pushRestoreTaskId(taskIds, focused.task_id);
    }
  }
  if (sessions.focused_thread_id) {
    const focused = candidates.find((item) => item.thread_id === sessions.focused_thread_id);
    if (focused) {
      pushRestoreTaskId(taskIds, focused.task_id);
    }
  }

  for (const item of candidates) {
    pushRestoreTaskId(taskIds, item.task_id);
  }
  for (const item of cleanSessionItems) {
    pushRestoreTaskId(taskIds, item.task_id);
  }

  return taskIds;
}

export function resolveSessionOwnerProfileId(session: SessionProjectionDocument["sessions"][number] | null | undefined): string {
  return session?.session_owner_profile_id?.trim() || session?.agent_profile_id?.trim() || "";
}

export function readSummaryString(summary: Record<string, unknown>, key: string): string {
  const value = summary[key];
  return typeof value === "string" ? value : "";
}

export function sortWorksByUpdate(works: WorkProjectionItem[]): WorkProjectionItem[] {
  return [...works].sort((left, right) =>
    String(right.updated_at ?? "").localeCompare(String(left.updated_at ?? ""))
  );
}

export function resolveAgentUri(agentId: string, fallback: string): string {
  const normalized = agentId.trim();
  if (!normalized) {
    return fallback;
  }
  return normalized.startsWith("agent://") ? normalized : `agent://${normalized}`;
}

export function resolveWorkActor(work: WorkProjectionItem): string {
  return resolveAgentUri(
    readSummaryString(work.runtime_summary, "research_worker_id") ||
      work.runtime_id ||
      work.selected_worker_type ||
      "",
    "worker"
  );
}

export function resolveWorkStatus(work: WorkProjectionItem): string {
  return (
    readSummaryString(work.runtime_summary, "research_worker_status") ||
    work.status ||
    ""
  )
    .trim()
    .toUpperCase();
}

export function isAgentDirectExecution(work: WorkProjectionItem | null | undefined): boolean {
  if (!work) {
    return false;
  }
  const runtimeId = String(work.runtime_id ?? "").trim().toLowerCase();
  const selectedWorkerType = String(work.selected_worker_type ?? "").trim().toLowerCase();
  const targetKind = String(work.target_kind ?? "").trim().toLowerCase();
  const routeReason = String(work.route_reason ?? "").trim().toLowerCase();
  return (
    runtimeId === "worker.llm.default" &&
    selectedWorkerType === "general" &&
    (targetKind === "fallback" || routeReason.includes("single_worker"))
  );
}

// ---------------------------------------------------------------------------
// ChatWorkbench 派生：活跃 work / 会话 / A2A 协作上下文（F143 件 2 块 A 下沉）
// ---------------------------------------------------------------------------

export interface ActiveWorkContextOptions {
  sessions: SessionProjectionItem[];
  webSessions: SessionProjectionItem[];
  routeSessionId?: string;
  routeSession: SessionProjectionItem | null;
  restoreChoice: string;
  taskId: string | null;
  delegationWorks: WorkProjectionItem[];
  contextFrames: ContextFrameItem[];
  a2aConversations: A2AConversationItem[];
  /** taskDetail?.task?.status（可 undefined，与 session status 一起归一化） */
  taskDetailStatus?: unknown;
}

export interface ActiveWorkContext {
  activeSession: SessionProjectionItem | null;
  currentSession: SessionProjectionItem | null;
  activeWorkId: string;
  /** 与 activeWorkId 同源（execution_summary.work_id），保留 baseline 双命名 */
  activeSessionWorkId: string;
  activeWork: WorkProjectionItem | null;
  activeContextFrame: ContextFrameItem | null;
  activeSessionOwnerProfileId: string;
  activeCompatibilityFlags: string[];
  legacyResetRecommended: boolean;
  compatibilityMessage: string;
  isDirectExecution: boolean;
  activeConversationId: string;
  activeA2AConversationRecord: A2AConversationItem | null;
  hasInternalCollaboration: boolean;
  activeConversationLatestType: string;
  activeConversationWorkerSessionId: string;
  normalizedTaskStatus: string;
  hasLoadedTaskStatus: boolean;
}

export function deriveActiveWorkContext(options: ActiveWorkContextOptions): ActiveWorkContext {
  const {
    sessions,
    webSessions,
    routeSessionId,
    routeSession,
    restoreChoice,
    taskId,
    delegationWorks,
    contextFrames,
    a2aConversations,
    taskDetailStatus,
  } = options;

  const activeSession = sessions.find((item) => item.task_id === taskId) ?? null;
  const currentSession =
    taskId != null
      ? activeSession ?? (routeSessionId ? routeSession : webSessions[0] || null)
      : routeSessionId
        ? routeSession
        : restoreChoice === "continue"
          ? webSessions[0] || null
          : null;
  const activeExecutionSummary =
    activeSession?.execution_summary &&
    typeof activeSession.execution_summary === "object" &&
    !Array.isArray(activeSession.execution_summary)
      ? (activeSession.execution_summary as Record<string, unknown>)
      : null;
  const activeWorkId =
    typeof activeExecutionSummary?.work_id === "string" ? activeExecutionSummary.work_id : "";
  const activeSessionWorkId = readSummaryString(
    (activeSession?.execution_summary ?? {}) as Record<string, unknown>,
    "work_id"
  );
  const latestTaskWork =
    sortWorksByUpdate(delegationWorks.filter((item) => item.task_id === taskId))[0] ?? null;
  const activeWork = delegationWorks.find((item) => item.work_id === activeWorkId) ?? latestTaskWork;
  const activeContextFrame =
    contextFrames.find((item) => item.task_id === taskId) ??
    (activeSession
      ? contextFrames.find((item) => item.session_id === activeSession.session_id) ?? null
      : null);
  const activeSessionOwnerProfileId = resolveSessionOwnerProfileId(activeSession);
  const activeCompatibilityFlags = ensureArray(currentSession?.compatibility_flags);
  const legacyResetRecommended = Boolean(currentSession?.reset_recommended);
  const compatibilityMessage =
    currentSession?.compatibility_message?.trim() ||
    (activeCompatibilityFlags.includes("legacy_context_polluted")
      ? "这条历史会话仍沿用旧版 owner/profile 继承语义，建议先重置 continuity，再继续新的对话。"
      : "");
  const isDirectExecution = isAgentDirectExecution(activeWork);
  const activeConversationId =
    readSummaryString(activeWork?.runtime_summary ?? {}, "research_a2a_conversation_id") ||
    activeWork?.a2a_conversation_id ||
    "";
  const activeA2AConversationRecord =
    (activeConversationId
      ? a2aConversations.find((item) => item.a2a_conversation_id === activeConversationId) ?? null
      : null) ??
    (activeWork?.work_id
      ? a2aConversations.find((item) => item.work_id === activeWork.work_id) ?? null
      : null) ??
    (taskId ? a2aConversations.find((item) => item.task_id === taskId) ?? null : null);

  const hasInternalCollaboration =
    !isDirectExecution &&
    (activeA2AConversationRecord != null ||
      Boolean(
        activeConversationId ||
          readSummaryString(activeWork?.runtime_summary ?? {}, "research_worker_id")
      ));
  const activeConversationLatestType = activeA2AConversationRecord?.latest_message_type || "";
  const activeConversationWorkerSessionId =
    activeA2AConversationRecord?.target_agent_session_id ||
    readSummaryString(activeWork?.runtime_summary ?? {}, "research_worker_agent_session_id") ||
    activeWork?.worker_agent_session_id ||
    "";
  const normalizedTaskStatus = String(taskDetailStatus ?? activeSession?.status ?? "")
    .trim()
    .toUpperCase();
  const hasLoadedTaskStatus = normalizedTaskStatus.length > 0;

  return {
    activeSession,
    currentSession,
    activeWorkId,
    activeSessionWorkId,
    activeWork,
    activeContextFrame,
    activeSessionOwnerProfileId,
    activeCompatibilityFlags,
    legacyResetRecommended,
    compatibilityMessage,
    isDirectExecution,
    activeConversationId,
    activeA2AConversationRecord,
    hasInternalCollaboration,
    activeConversationLatestType,
    activeConversationWorkerSessionId,
    normalizedTaskStatus,
    hasLoadedTaskStatus,
  };
}
