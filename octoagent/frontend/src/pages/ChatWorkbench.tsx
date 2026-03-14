import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { fetchTaskDetail } from "../api/client";
import { MessageBubble } from "../components/ChatUI/MessageBubble";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import { formatAgentRoleLabel, formatTaskStatusLabel, formatTaskStatusTone } from "../domains/chat/presentation";
import { useChatStream } from "../hooks/useChatStream";
import { HoverReveal, InlineCallout, StatusBadge } from "../ui/primitives";
import type { SessionProjectionDocument, TaskDetailResponse, TaskEvent, WorkProjectionItem } from "../types";

const TERMINAL_TASK_STATUSES = new Set(["SUCCEEDED", "FAILED", "CANCELLED", "REJECTED"]);

function ensureArray<T>(value: T[] | null | undefined): T[] {
  return Array.isArray(value) ? value : [];
}

function pushRestoreTaskId(taskIds: string[], taskId: string | undefined): void {
  if (!taskId || taskIds.includes(taskId)) {
    return;
  }
  taskIds.push(taskId);
}

function resolveRestorableTaskIds(sessions: SessionProjectionDocument): string[] {
  if (
    typeof sessions.new_conversation_token === "string" &&
    sessions.new_conversation_token.trim()
  ) {
    return [];
  }
  const sessionItems = ensureArray(sessions.sessions);
  const webSessions = sessionItems.filter((item) => item.channel === "web");
  const candidates = webSessions.length > 0 ? webSessions : sessionItems;
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
  for (const item of sessionItems) {
    pushRestoreTaskId(taskIds, item.task_id);
  }

  return taskIds;
}

function readSummaryString(summary: Record<string, unknown>, key: string): string {
  const value = summary[key];
  return typeof value === "string" ? value : "";
}

function sortWorksByUpdate(works: WorkProjectionItem[]): WorkProjectionItem[] {
  return [...works].sort((left, right) =>
    String(right.updated_at ?? "").localeCompare(String(left.updated_at ?? ""))
  );
}

function resolveAgentUri(agentId: string, fallback: string): string {
  const normalized = agentId.trim();
  if (!normalized) {
    return fallback;
  }
  return normalized.startsWith("agent://") ? normalized : `agent://${normalized}`;
}

function resolveWorkActor(work: WorkProjectionItem): string {
  return resolveAgentUri(
    readSummaryString(work.runtime_summary, "research_worker_id") ||
      work.runtime_id ||
      work.selected_worker_type ||
      "",
    "worker"
  );
}

function resolveWorkStatus(work: WorkProjectionItem): string {
  return (
    readSummaryString(work.runtime_summary, "research_worker_status") ||
    work.status ||
    ""
  )
    .trim()
    .toUpperCase();
}

function formatActorSummary(actor: string, workTitle: string, status: string, latestMessageType: string): string {
  const normalizedActor = actor.trim().toLowerCase();
  const normalizedStatus = status.trim().toUpperCase();
  const normalizedMessageType = latestMessageType.trim().toUpperCase();

  if (normalizedStatus === "WAITING_INPUT") {
    return "还差一条关键信息，这轮才能继续。";
  }
  if (normalizedStatus === "WAITING_APPROVAL") {
    return "这一步需要你点一次确认，系统才会继续。";
  }
  if (normalizedStatus === "FAILED") {
    return "这轮没有拿到可用结果，主助手会继续处理影响。";
  }
  if (normalizedStatus === "CANCELLED") {
    return "这轮已经停止，不会再继续往下执行。";
  }
  if (normalizedStatus === "SUCCEEDED" || normalizedMessageType === "RESULT") {
    return "这轮查询已经完成，但主助手还在整理最终答复。";
  }
  if (normalizedActor.includes("research")) {
    return workTitle ? `正在查资料：${workTitle}` : "正在查资料和核对事实。";
  }
  if (normalizedActor.includes("ops")) {
    return workTitle ? `正在执行或检查：${workTitle}` : "正在检查系统状态和执行步骤。";
  }
  if (normalizedActor.includes("dev")) {
    return workTitle ? `正在实现或验证：${workTitle}` : "正在改代码和验证实现。";
  }
  if (normalizedActor.includes("butler")) {
    return "正在理解问题、安排下一步，并整理最终回复。";
  }
  return workTitle ? `正在处理：${workTitle}` : "正在处理这轮任务。";
}

function formatActivityStateLabel(status: string, latestMessageType: string): string {
  const normalizedStatus = status.trim().toUpperCase();
  const normalizedMessageType = latestMessageType.trim().toUpperCase();

  switch (normalizedStatus) {
    case "RUNNING":
      return "进行中";
    case "WAITING_INPUT":
      return "等你补充";
    case "WAITING_APPROVAL":
      return "等你确认";
    case "SUCCEEDED":
      return "已完成";
    case "FAILED":
      return "失败";
    case "CANCELLED":
      return "已取消";
    case "QUEUED":
    case "CREATED":
      return "准备中";
    default:
      if (normalizedMessageType === "RESULT") {
        return "已完成";
      }
      if (normalizedMessageType === "TASK") {
        return "已接手";
      }
      return "处理中";
  }
}

function formatActivityTone(status: string, latestMessageType: string): "success" | "warning" | "danger" | "running" | "draft" {
  const normalizedStatus = status.trim().toUpperCase();
  const normalizedMessageType = latestMessageType.trim().toUpperCase();

  if (normalizedStatus === "WAITING_INPUT" || normalizedStatus === "WAITING_APPROVAL") {
    return "warning";
  }
  if (normalizedStatus === "FAILED") {
    return "danger";
  }
  if (normalizedStatus === "SUCCEEDED" || normalizedMessageType === "RESULT") {
    return "success";
  }
  if (normalizedStatus === "CANCELLED") {
    return "draft";
  }
  return "running";
}

interface ChatActivityItem {
  id: string;
  actor: string;
  stateLabel: string;
  tone: "success" | "warning" | "danger" | "running" | "draft";
  summary: string;
  traceTitle?: string;
  traceEntries?: Array<{
    id: string;
    label: string;
    summary: string;
    stateLabel?: string;
    tone?: "success" | "warning" | "danger" | "running" | "draft";
  }>;
}

function summarizeText(value: string, maxLength = 120): string {
  const normalized = value.trim().replace(/\s+/g, " ");
  if (!normalized) {
    return "";
  }
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 1)}…`;
}

function readPayloadString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value : "";
}

function payloadMatchesWork(event: TaskEvent, workId: string): boolean {
  if (!workId) {
    return false;
  }
  const payload = event.payload ?? {};
  const payloadWorkId = readPayloadString(payload, "work_id");
  if (payloadWorkId === workId) {
    return true;
  }
  const sessionId = readPayloadString(payload, "session_id");
  if (sessionId.includes(workId)) {
    return true;
  }
  const sourceSessionId = readPayloadString(payload, "source_agent_session_id");
  if (sourceSessionId.includes(workId)) {
    return true;
  }
  const targetSessionId = readPayloadString(payload, "target_agent_session_id");
  if (targetSessionId.includes(workId)) {
    return true;
  }
  return false;
}

function summarizeToolList(toolNames: string[], limit = 4): string {
  if (toolNames.length === 0) {
    return "这轮没有挂出额外工具。";
  }
  const visible = toolNames.slice(0, limit);
  const suffix =
    toolNames.length > limit ? `，另外还有 ${toolNames.length - limit} 个工具` : "";
  return `${visible.join("、")}${suffix}`;
}

function buildToolTraceEntries(workEvents: TaskEvent[]): NonNullable<ChatActivityItem["traceEntries"]> {
  const relevant = workEvents.filter((event) => {
    const eventType = String(event.type);
    return (
      eventType === "TOOL_CALL_STARTED" ||
      eventType === "TOOL_CALL_COMPLETED" ||
      eventType === "TOOL_CALL_FAILED"
    );
  });
  const latestByName = new Map<
    string,
    {
      toolName: string;
      argsSummary: string;
      stateLabel: string;
      tone: "success" | "warning" | "danger" | "running" | "draft";
      summary: string;
      taskSeq: number;
    }
  >();

  for (const event of relevant) {
    const eventType = String(event.type);
    const payload = event.payload ?? {};
    const toolName = readPayloadString(payload, "tool_name");
    if (!toolName) {
      continue;
    }
    const existing = latestByName.get(toolName);
    if (existing && existing.taskSeq > event.task_seq) {
      continue;
    }
    const argsSummary = summarizeText(readPayloadString(payload, "args_summary"), 96);
    if (eventType === "TOOL_CALL_STARTED") {
      latestByName.set(toolName, {
        toolName,
        argsSummary,
        stateLabel: "调用中",
        tone: "running",
        summary: argsSummary ? `参数：${argsSummary}` : "这次调用还没返回结果。",
        taskSeq: event.task_seq,
      });
      continue;
    }
    if (eventType === "TOOL_CALL_FAILED") {
      const errorMessage = summarizeText(readPayloadString(payload, "error_message"), 96);
      latestByName.set(toolName, {
        toolName,
        argsSummary,
        stateLabel: "失败",
        tone: "danger",
        summary: errorMessage || "这次工具调用失败了。",
        taskSeq: event.task_seq,
      });
      continue;
    }
    const outputSummary = summarizeText(readPayloadString(payload, "output_summary"), 96);
    latestByName.set(toolName, {
      toolName,
      argsSummary,
      stateLabel: "已返回",
      tone: "success",
      summary: outputSummary || (argsSummary ? `参数：${argsSummary}` : "工具已返回结果。"),
      taskSeq: event.task_seq,
    });
  }

  return [...latestByName.values()]
    .sort((left, right) => right.taskSeq - left.taskSeq)
    .slice(0, 4)
    .map((item) => ({
      id: `tool-${item.toolName}`,
      label: item.toolName,
      stateLabel: item.stateLabel,
      tone: item.tone,
      summary: item.summary,
    }));
}

function buildButlerTraceEntries(
  work: WorkProjectionItem,
  workEvents: TaskEvent[],
  latestMessageType: string
): ChatActivityItem["traceEntries"] {
  const entries: NonNullable<ChatActivityItem["traceEntries"]> = [];
  entries.push({
    id: `${work.work_id}-dispatch`,
    label: "委派主题",
    stateLabel: "已发送",
    tone: "running",
    summary: summarizeText(work.title || "Butler 已把这轮问题交给内部角色处理。", 120),
  });
  entries.push({
    id: `${work.work_id}-tools`,
    label: "允许工具",
    stateLabel: "已挂载",
    tone: "draft",
    summary: summarizeToolList(ensureArray(work.selected_tools)),
  });
  const latestInbound = [...workEvents]
    .reverse()
    .find((event) => ["A2A_MESSAGE_RECEIVED", "WORKER_RETURNED"].includes(String(event.type)));
  if (latestInbound) {
    const inboundType = String(latestInbound.type);
    const messageType = readPayloadString(latestInbound.payload, "message_type");
    const latestLabel =
      inboundType === "WORKER_RETURNED"
        ? "Worker 返回"
        : `最近回执 · ${messageType || latestMessageType || "UPDATE"}`;
    const latestSummary =
      inboundType === "WORKER_RETURNED"
        ? summarizeText(readPayloadString(latestInbound.payload, "summary") || "内部角色已经返回结果。", 120)
        : summarizeText(
            messageType === "RESULT"
              ? "内部结果已经返回主助手，主助手还在继续整理。"
              : "内部角色还在继续推进，主助手会根据回执更新下一步。",
            120
          );
    entries.push({
      id: `${work.work_id}-latest`,
      label: latestLabel,
      stateLabel: messageType === "RESULT" ? "已收到" : "处理中",
      tone: messageType === "RESULT" ? "success" : "running",
      summary: latestSummary,
    });
  }
  return entries;
}

function buildWorkerTraceEntries(
  work: WorkProjectionItem,
  workEvents: TaskEvent[]
): ChatActivityItem["traceEntries"] {
  const entries: NonNullable<ChatActivityItem["traceEntries"]> = [
    {
      id: `${work.work_id}-scope`,
      label: "当前处理",
      stateLabel: formatActivityStateLabel(resolveWorkStatus(work), ""),
      tone: formatActivityTone(resolveWorkStatus(work), ""),
      summary: summarizeText(work.title || "正在处理这轮查询。", 120),
    },
  ];
  entries.push(...buildToolTraceEntries(workEvents));
  const returnedEvent = [...workEvents]
    .reverse()
    .find((event) => String(event.type) === "WORKER_RETURNED");
  if (returnedEvent) {
    const status = readPayloadString(returnedEvent.payload, "status").toUpperCase();
    entries.push({
      id: `${work.work_id}-returned`,
      label: "返回主助手",
      stateLabel:
        status === "SUCCEEDED" ? "已完成" : status === "FAILED" ? "失败" : "处理中",
      tone:
        status === "SUCCEEDED" ? "success" : status === "FAILED" ? "danger" : "running",
      summary: summarizeText(
        readPayloadString(returnedEvent.payload, "summary") || "内部角色已把这轮处理状态回传给主助手。",
        120
      ),
    });
  }
  return entries;
}

function buildButlerActivity(
  taskStatus: string,
  streaming: boolean,
  hasInternalCollaboration: boolean,
  latestMessageType: string
): ChatActivityItem {
  const normalizedStatus = taskStatus.trim().toUpperCase();
  const normalizedMessageType = latestMessageType.trim().toUpperCase();

  if (normalizedStatus === "WAITING_INPUT") {
    return {
      id: "butler",
      actor: "主助手",
      stateLabel: "等你补充",
      tone: "warning",
      summary: "主助手已经识别到还差关键信息，补一句就能继续。",
    };
  }
  if (normalizedStatus === "WAITING_APPROVAL") {
    return {
      id: "butler",
      actor: "主助手",
      stateLabel: "等你确认",
      tone: "warning",
      summary: "这一步需要你确认，系统才会继续往下执行。",
    };
  }
  if (hasInternalCollaboration && normalizedMessageType === "RESULT") {
    return {
      id: "butler",
      actor: "主助手",
      stateLabel: "整理中",
      tone: "running",
      summary: "内部结果已经拿到，但主助手还在整理、核对并收口最终回复。",
    };
  }
  if (hasInternalCollaboration) {
    return {
      id: "butler",
      actor: "主助手",
      stateLabel: "协调中",
      tone: "running",
      summary: "主助手正在协调内部角色、补充上下文和收口结果。",
    };
  }
  if (streaming || normalizedStatus === "RUNNING" || normalizedStatus === "QUEUED") {
    return {
      id: "butler",
      actor: "主助手",
      stateLabel: "进行中",
      tone: "running",
      summary: "主助手正在直接处理这条消息。",
    };
  }
  return {
    id: "butler",
    actor: "主助手",
    stateLabel: "准备中",
    tone: "draft",
    summary: "这轮对话已经进入处理流程，稍后会继续推进。",
  };
}

function buildWorkerActivity(
  work: WorkProjectionItem,
  fallbackLatestMessageType: string
): ChatActivityItem | null {
  const actor = formatAgentRoleLabel(resolveWorkActor(work));
  if (actor === "主助手") {
    return null;
  }
  const status = resolveWorkStatus(work);
  return {
    id: work.work_id,
    actor,
    stateLabel: formatActivityStateLabel(status, fallbackLatestMessageType),
    tone: formatActivityTone(status, fallbackLatestMessageType),
    summary: formatActorSummary(actor, work.title, status, fallbackLatestMessageType),
  };
}

export default function ChatWorkbench() {
  const { snapshot, refreshResources } = useWorkbench();
  const sessionDocument = snapshot!.resources.sessions;
  const sessions = ensureArray(sessionDocument?.sessions);
  const workerProfilesDocument = snapshot!.resources.worker_profiles;
  const workerProfiles = ensureArray(workerProfilesDocument?.profiles);
  const delegationWorks = ensureArray(snapshot!.resources.delegation.works);
  const context = snapshot!.resources.context_continuity;
  const contextFrames = ensureArray(context.frames);
  const a2aConversations = ensureArray(context.a2a_conversations);
  const restoreTaskIds = sessionDocument ? resolveRestorableTaskIds(sessionDocument) : [];
  const { messages, sendMessage, resetConversation, streaming, restoring, error, taskId } = useChatStream(
    restoreTaskIds.length > 0 ? { taskIds: restoreTaskIds } : null
  );
  const [input, setInput] = useState("");
  const [showSessionInternalRefs, setShowSessionInternalRefs] = useState(false);
  const [showRestoreEscape, setShowRestoreEscape] = useState(false);
  const [taskDetail, setTaskDetail] = useState<TaskDetailResponse | null>(null);

  const defaultRootAgentId = readSummaryString(workerProfilesDocument?.summary ?? {}, "default_profile_id");
  const defaultRootAgent = workerProfiles.find((profile) => profile.profile_id === defaultRootAgentId);
  const activeSession = sessions.find((item) => item.task_id === taskId) ?? null;
  const activeExecutionSummary =
    activeSession?.execution_summary &&
    typeof activeSession.execution_summary === "object" &&
    !Array.isArray(activeSession.execution_summary)
      ? (activeSession.execution_summary as Record<string, unknown>)
      : null;
  const activeWorkId = typeof activeExecutionSummary?.work_id === "string" ? activeExecutionSummary.work_id : "";
  const latestTaskWork =
    sortWorksByUpdate(delegationWorks.filter((item) => item.task_id === taskId))[0] ?? null;
  const activeWork = delegationWorks.find((item) => item.work_id === activeWorkId) ?? latestTaskWork;
  const activeContextFrame =
    contextFrames.find((item) => item.task_id === taskId) ??
    (activeSession ? contextFrames.find((item) => item.session_id === activeSession.session_id) ?? null : null);
  const activeAgentProfileId =
    activeWork?.requested_worker_profile_id ||
    activeWork?.agent_profile_id ||
    activeContextFrame?.agent_profile_id ||
    defaultRootAgent?.profile_id ||
    "";
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
    activeA2AConversationRecord != null ||
    Boolean(activeConversationId || readSummaryString(activeWork?.runtime_summary ?? {}, "research_worker_id"));
  const activeConversationLatestType = activeA2AConversationRecord?.latest_message_type || "";
  const activeConversationWorkerSessionId =
    activeA2AConversationRecord?.target_agent_session_id ||
    readSummaryString(activeWork?.runtime_summary ?? {}, "research_worker_agent_session_id") ||
    activeWork?.worker_agent_session_id ||
    "";
  const normalizedTaskStatus = String(taskDetail?.task?.status ?? activeSession?.status ?? "").trim().toUpperCase();
  const hasLoadedTaskStatus = normalizedTaskStatus.length > 0;
  const shouldPollLiveState =
    Boolean(taskId) &&
    (streaming ||
      (hasLoadedTaskStatus &&
        !TERMINAL_TASK_STATUSES.has(normalizedTaskStatus) &&
        (hasInternalCollaboration ||
          ["QUEUED", "RUNNING", "WAITING_APPROVAL", "WAITING_INPUT"].includes(normalizedTaskStatus))));

  const relatedWorks = sortWorksByUpdate(
    delegationWorks.filter((item) => {
      if (!activeWork?.work_id) {
        return false;
      }
      if (item.work_id === activeWork.work_id) {
        return true;
      }
      if (item.parent_work_id === activeWork.work_id) {
        return true;
      }
      return false;
    })
  );
  const workEvents = ensureArray(taskDetail?.events).filter((event) =>
    activeWork?.work_id ? payloadMatchesWork(event, activeWork.work_id) : false
  );
  const workerActivities: ChatActivityItem[] = relatedWorks.reduce<ChatActivityItem[]>((items, work) => {
      const activity = buildWorkerActivity(work, activeConversationLatestType);
      if (!activity) {
        return items;
      }
      items.push({
        ...activity,
        traceTitle: `${activity.actor} 的处理轨迹`,
        traceEntries: buildWorkerTraceEntries(
          work,
          workEvents.filter((event) => payloadMatchesWork(event, work.work_id))
        ),
      });
      return items;
    }, []);
  const fallbackWorkerActor = activeA2AConversationRecord?.target_agent
    ? formatAgentRoleLabel(activeA2AConversationRecord.target_agent)
    : activeWork
      ? formatAgentRoleLabel(resolveWorkActor(activeWork))
      : "";
  const fallbackWorkerActivity: ChatActivityItem[] =
    hasInternalCollaboration &&
    workerActivities.length === 0 &&
    fallbackWorkerActor !== "主助手" &&
    fallbackWorkerActor
      ? [
          {
            id: "worker-fallback",
            actor: fallbackWorkerActor,
            stateLabel: formatActivityStateLabel(
              activeWork ? resolveWorkStatus(activeWork) : "",
              activeConversationLatestType || "TASK"
            ),
            tone: formatActivityTone(
              activeWork ? resolveWorkStatus(activeWork) : "",
              activeConversationLatestType || "TASK"
            ),
            summary: formatActorSummary(
              fallbackWorkerActor,
              activeWork?.title ?? "",
              activeWork ? resolveWorkStatus(activeWork) : "",
              activeConversationLatestType || "TASK"
            ),
          },
        ]
      : [];
  const activityItems: ChatActivityItem[] = taskId
    ? [
        {
          ...buildButlerActivity(
            normalizedTaskStatus,
            streaming,
            hasInternalCollaboration,
            activeConversationLatestType
          ),
          traceTitle: "主助手的委派轨迹",
          traceEntries: activeWork
            ? buildButlerTraceEntries(activeWork, workEvents, activeConversationLatestType)
            : [],
        },
        ...workerActivities.slice(0, 2),
        ...fallbackWorkerActivity,
      ]
    : [];
  const isRestoringConversation = restoring && messages.length === 0;
  const isEmptyConversation = messages.length === 0 && !isRestoringConversation;
  const shouldShowInlineActivity =
    Boolean(taskId) &&
    (streaming || !hasLoadedTaskStatus || !TERMINAL_TASK_STATUSES.has(normalizedTaskStatus));
  const loadingLabel = hasInternalCollaboration ? "正在整理回复" : "正在处理这条消息";
  const activeStreamingMessageId =
    [...messages]
      .reverse()
      .find((message) => message.role === "agent" && message.isStreaming)?.id ?? null;
  const shouldShowSyntheticProgressBubble =
    shouldShowInlineActivity &&
    activityItems.length > 0 &&
    !activeStreamingMessageId &&
    !isRestoringConversation &&
    !isEmptyConversation;
  const taskStatusLabel = formatTaskStatusLabel(normalizedTaskStatus);
  const taskStatusTone = formatTaskStatusTone(normalizedTaskStatus);
  const conversationTitle =
    taskDetail?.task?.title ||
    activeSession?.title ||
    activeSession?.latest_message_summary ||
    (taskId ? "这轮对话正在处理中" : "开始一段对话");
  const techRefs = [
    taskId ? { label: "任务 ID", value: taskId } : null,
    activeSession?.session_id ? { label: "会话 ID", value: activeSession.session_id } : null,
    activeWork?.work_id ? { label: "Work ID", value: activeWork.work_id } : null,
    activeConversationId ? { label: "协作链路 ID", value: activeConversationId } : null,
    activeConversationWorkerSessionId ? { label: "执行会话", value: activeConversationWorkerSessionId } : null,
    activeContextFrame?.context_frame_id ? { label: "上下文帧 ID", value: activeContextFrame.context_frame_id } : null,
  ].filter((item): item is { label: string; value: string } => Boolean(item));

  useEffect(() => {
    if (!isRestoringConversation) {
      setShowRestoreEscape(false);
      return;
    }
    const timer = window.setTimeout(() => {
      setShowRestoreEscape(true);
    }, 1600);
    return () => {
      window.clearTimeout(timer);
    };
  }, [isRestoringConversation]);

  useEffect(() => {
    let cancelled = false;

    async function loadDetail() {
      if (!taskId) {
        setTaskDetail(null);
        return;
      }
      try {
        const detail = await fetchTaskDetail(taskId);
        if (!cancelled) {
          setTaskDetail(detail);
        }
      } catch {
        if (!cancelled) {
          setTaskDetail(null);
        }
      }
    }

    void loadDetail();
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  useEffect(() => {
    if (!taskId) {
      return;
    }
    let cancelled = false;
    const currentTaskId = taskId;
    const resources = [
      {
        resource_type: snapshot!.resources.sessions.resource_type,
        resource_id: snapshot!.resources.sessions.resource_id,
        schema_version: snapshot!.resources.sessions.schema_version,
      },
      {
        resource_type: snapshot!.resources.delegation.resource_type,
        resource_id: snapshot!.resources.delegation.resource_id,
        schema_version: snapshot!.resources.delegation.schema_version,
      },
      {
        resource_type: snapshot!.resources.context_continuity.resource_type,
        resource_id: snapshot!.resources.context_continuity.resource_id,
        schema_version: snapshot!.resources.context_continuity.schema_version,
      },
    ];

    async function refreshLiveState() {
      try {
        const detail = await fetchTaskDetail(currentTaskId);
        if (cancelled) {
          return;
        }
        setTaskDetail(detail);
      } catch {
        if (cancelled) {
          return;
        }
        setTaskDetail(null);
      }
      if (cancelled) {
        return;
      }
      await refreshResources(resources);
    }

    void refreshLiveState();
    if (!shouldPollLiveState) {
      return;
    }

    const timer = window.setInterval(() => {
      void refreshLiveState();
    }, 1200);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    taskId,
    shouldPollLiveState,
    refreshResources,
    snapshot!.resources.context_continuity.resource_id,
    snapshot!.resources.context_continuity.schema_version,
    snapshot!.resources.delegation.resource_id,
    snapshot!.resources.delegation.schema_version,
    snapshot!.resources.sessions.resource_id,
    snapshot!.resources.sessions.schema_version,
  ]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!input.trim() || streaming) {
      return;
    }
    const text = input;
    setInput("");
    await sendMessage(text, {
      agentProfileId: activeAgentProfileId || null,
    });
  }

  return (
    <div className="wb-page wb-chat-page">
      <section
        className={`wb-panel wb-chat-panel wb-chat-shell ${isEmptyConversation ? "is-empty" : ""}`}
      >
        <div className="wb-panel-head wb-chat-head">
          <div className="wb-chat-head-copy">
            <p className="wb-card-label">Chat</p>
            <h3>{conversationTitle}</h3>
            <p className="wb-chat-head-summary">
              {isEmptyConversation
                ? "直接告诉 OctoAgent 你想完成什么。"
                : hasLoadedTaskStatus
                  ? `当前状态：${taskStatusLabel}`
                  : "这轮对话已经进入处理流程。"}
            </p>
          </div>
          <div className="wb-chat-head-actions">
            {taskId || messages.length > 0 ? (
              <button
                type="button"
                className="wb-button wb-button-secondary wb-button-inline"
                onClick={() => void resetConversation()}
              >
                开始新对话
              </button>
            ) : null}
            {taskId ? <StatusBadge tone={taskStatusTone}>{taskStatusLabel}</StatusBadge> : null}
            {taskId ? (
              <Link className="wb-button wb-button-secondary wb-button-inline" to={`/tasks/${taskId}`}>
                打开任务
              </Link>
            ) : null}
            {techRefs.length > 0 ? (
              <HoverReveal
                label="技术详情"
                expanded={showSessionInternalRefs}
                onToggle={setShowSessionInternalRefs}
                ariaLabel="当前会话技术详情"
                triggerClassName="wb-button-inline"
              >
                {techRefs.map((item) => (
                  <div key={item.label} className="wb-hover-reveal-row">
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                  </div>
                ))}
              </HoverReveal>
            ) : null}
          </div>
        </div>

        {isRestoringConversation ? (
          <div className="wb-chat-empty-stage is-restoring">
            <div className="wb-empty-state wb-chat-empty-card wb-chat-restore-card">
              <strong>正在恢复最近对话</strong>
              <span>稍等，我们在读取历史消息和当前任务状态。</span>
              {showRestoreEscape ? (
                <div className="wb-action-bar wb-chat-restore-actions">
                  <span>如果这一步还没结束，你可以先直接开始一段新对话。</span>
                  <button
                    type="button"
                    className="wb-button wb-button-secondary wb-button-inline"
                    onClick={() => void resetConversation()}
                  >
                    直接开始新对话
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        ) : isEmptyConversation ? (
          <div className="wb-chat-empty-stage">
            <div className="wb-empty-state wb-chat-empty-card">
              <strong>开始第一条消息</strong>
              <span>比如直接告诉 OctoAgent 你今天要完成什么，它会从这里接手。</span>
            </div>
            {error ? (
              <InlineCallout title="刚才没有发送成功" tone="error">
                {error}
              </InlineCallout>
            ) : null}
            <form className="wb-chat-form is-empty" onSubmit={handleSubmit}>
              <input
                type="text"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder="告诉 OctoAgent 你现在要做什么"
                disabled={streaming}
              />
              <button
                type="submit"
                className="wb-button wb-button-primary"
                disabled={streaming || !input.trim()}
              >
                {streaming ? "发送中" : "发送"}
              </button>
            </form>
          </div>
        ) : (
          <>
            <div className="wb-chat-messages">
              {messages.map((message) => (
                <MessageBubble
                  key={message.id}
                  message={message}
                  loadingLabel={loadingLabel}
                  activityItems={
                    message.id === activeStreamingMessageId && shouldShowInlineActivity
                      ? activityItems
                      : []
                  }
                />
              ))}
              {shouldShowSyntheticProgressBubble ? (
                <MessageBubble
                  message={{
                    id: "agent-progress-stage",
                    role: "agent",
                    content: "",
                    isStreaming: true,
                  }}
                  loadingLabel={loadingLabel}
                  activityItems={activityItems}
                />
              ) : null}
            </div>

            {error ? (
              <InlineCallout title="刚才没有发送成功" tone="error">
                {error}
              </InlineCallout>
            ) : null}

            <form className="wb-chat-form" onSubmit={handleSubmit}>
              <input
                type="text"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder="告诉 OctoAgent 你现在要做什么"
                disabled={streaming}
              />
              <button
                type="submit"
                className="wb-button wb-button-primary"
                disabled={streaming || !input.trim()}
              >
                {streaming ? "发送中" : "发送"}
              </button>
            </form>
          </>
        )}
      </section>
    </div>
  );
}
