import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { fetchTaskDetail } from "../api/client";
import { MessageBubble } from "../components/ChatUI/MessageBubble";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import { formatAgentRoleLabel, formatTaskStatusLabel, formatTaskStatusTone } from "../domains/chat/presentation";
import { useChatStream } from "../hooks/useChatStream";
import { HoverReveal, InlineCallout, StatusBadge } from "../ui/primitives";
import type { SessionProjectionDocument, TaskDetailResponse, WorkProjectionItem } from "../types";

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
    return "结果已经交回主助手，正在整理成你能直接用的答复。";
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
      return "已回传";
    case "FAILED":
      return "失败";
    case "CANCELLED":
      return "已取消";
    case "QUEUED":
    case "CREATED":
      return "准备中";
    default:
      if (normalizedMessageType === "RESULT") {
        return "已回传";
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
      summary: "专门角色的结果已经回来，主助手正在整理成最终回复。",
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
  const activeWork = delegationWorks.find((item) => item.work_id === activeWorkId) ?? null;
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
      if (!taskId) {
        return false;
      }
      if (item.task_id === taskId) {
        return true;
      }
      if (activeWork?.work_id && item.parent_work_id === activeWork.work_id) {
        return true;
      }
      return Boolean(activeWork?.child_work_ids.includes(item.work_id));
    })
  );
  const workerActivities = relatedWorks
    .map((work) => buildWorkerActivity(work, activeConversationLatestType))
    .filter((item): item is ChatActivityItem => item != null);
  const fallbackWorkerActor = activeA2AConversationRecord?.target_agent
    ? formatAgentRoleLabel(activeA2AConversationRecord.target_agent)
    : activeWork
      ? formatAgentRoleLabel(resolveWorkActor(activeWork))
      : "";
  const fallbackWorkerActivity =
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
  const activityItems = taskId
    ? [buildButlerActivity(normalizedTaskStatus, streaming, hasInternalCollaboration, activeConversationLatestType), ...workerActivities.slice(0, 2), ...fallbackWorkerActivity]
    : [];
  const shouldShowActivityRail =
    Boolean(taskId) &&
    (streaming || !hasLoadedTaskStatus || !TERMINAL_TASK_STATUSES.has(normalizedTaskStatus));
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
  const isRestoringConversation = restoring && messages.length === 0;
  const isEmptyConversation = messages.length === 0 && !isRestoringConversation;

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
                className="wb-button wb-button-tertiary"
                onClick={() => void resetConversation()}
              >
                开始新对话
              </button>
            ) : null}
            {taskId ? <StatusBadge tone={taskStatusTone}>{taskStatusLabel}</StatusBadge> : null}
            {taskId ? (
              <Link className="wb-button wb-button-tertiary" to={`/tasks/${taskId}`}>
                打开任务
              </Link>
            ) : null}
            {techRefs.length > 0 ? (
              <HoverReveal
                label="技术详情"
                expanded={showSessionInternalRefs}
                onToggle={setShowSessionInternalRefs}
                ariaLabel="当前会话技术详情"
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

        {shouldShowActivityRail && activityItems.length > 0 ? (
          <section className="wb-chat-activity" aria-label="当前处理进度">
            <div className="wb-chat-activity-list">
              {activityItems.map((item, index) => (
                <div key={item.id} className="wb-chat-activity-item">
                  <div className="wb-chat-activity-line" aria-hidden={index === activityItems.length - 1} />
                  <span className={`wb-chat-activity-dot is-${item.tone}`} aria-hidden="true" />
                  <div className="wb-chat-activity-copy">
                    <strong>{item.actor}</strong>
                    <span>{item.summary}</span>
                  </div>
                  <StatusBadge tone={item.tone}>{item.stateLabel}</StatusBadge>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {isRestoringConversation ? (
          <div className="wb-chat-empty-stage is-restoring">
            <div className="wb-empty-state wb-chat-empty-card wb-chat-restore-card">
              <strong>正在恢复最近对话</strong>
              <span>稍等，我们在读取历史消息和当前任务状态。</span>
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
                <MessageBubble key={message.id} message={message} />
              ))}
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
