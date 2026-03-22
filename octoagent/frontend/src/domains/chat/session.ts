import type {
  ExecutionSessionDocument,
  ProjectOption,
  SessionProjectionDocument,
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
  const runtimeId = work.runtime_id.trim().toLowerCase();
  const selectedWorkerType = work.selected_worker_type.trim().toLowerCase();
  const targetKind = work.target_kind.trim().toLowerCase();
  const routeReason = work.route_reason.trim().toLowerCase();
  return (
    runtimeId === "worker.llm.default" &&
    selectedWorkerType === "general" &&
    (targetKind === "fallback" || routeReason.includes("single_worker"))
  );
}
