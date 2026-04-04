import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ApiError,
  attachExecutionInput,
  fetchApprovals,
  fetchTaskDetail,
  fetchTaskExecutionSession,
} from "../api/client";
import { MessageBubble } from "../components/ChatUI/MessageBubble";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import {
  buildAgentActivity,
  buildAgentTraceEntries,
  buildWorkerActivity,
  buildWorkerTraceEntries,
  formatActivityStateLabel,
  formatActivityTone,
  formatActorSummary,
} from "../domains/chat/activity";
import type { ChatActivityItem } from "../domains/chat/activity";
import {
  buildExecutionSessionApprovalItem,
  buildSyntheticApprovalItem,
  formatCountdown,
  mapOperatorQuickAction,
  parseApprovalCommand,
  payloadMatchesWork,
  readLatestApprovalContext,
  readLatestExpiredApprovalContext,
} from "../domains/chat/approval";
import { formatAgentRoleLabel, formatTaskStatusLabel, formatTaskStatusTone } from "../domains/chat/presentation";
import {
  ensureArray,
  isAgentDirectExecution,
  readExecutionSessionDocument,
  readSummaryString,
  resolveProjectName,
  resolveRestorableTaskIds,
  resolveSessionOwnerProfileId,
  resolveWorkActor,
  resolveWorkStatus,
  sortWorksByUpdate,
} from "../domains/chat/session";
import { useChatStream } from "../hooks/useChatStream";
import { readStoredTaskId } from "../hooks/chatStreamHelpers";
import { useTaskLiveState } from "../hooks/useTaskLiveState";
import { HoverReveal, InlineCallout, StatusBadge } from "../ui/primitives";
import { formatSessionDisplayTitle } from "../workbench/utils";
import type {
  ApprovalListItem,
  ExecutionSessionDocument,
  OperatorActionKind,
  OperatorInboxItem,
  TaskDetailResponse,
} from "../types";

const TERMINAL_TASK_STATUSES = new Set(["SUCCEEDED", "FAILED", "CANCELLED", "REJECTED"]);
const CHAT_SLASH_COMMANDS = [
  {
    value: "/approve",
    description: "批准一次当前审批",
    action: "approve_once",
  },
  {
    value: "/approve always",
    description: "总是批准当前审批",
    action: "approve_always",
  },
  {
    value: "/deny",
    description: "拒绝当前审批",
    action: "deny",
  },
] as const;

export default function ChatWorkbench() {
  const { sessionId: routeSessionId } = useParams<{ sessionId?: string }>();
  const navigate = useNavigate();
  const { snapshot, refreshResources, submitAction, busyActionId } = useWorkbench();
  const sessionDocument = snapshot!.resources.sessions;
  const projectSelector = snapshot!.resources.project_selector;
  const availableProjects = ensureArray(projectSelector?.available_projects);

  const sessions = ensureArray(sessionDocument?.sessions);
  const workerProfilesDocument = snapshot!.resources.worker_profiles;
  const workerProfiles = ensureArray(workerProfilesDocument?.profiles);
  const delegationWorks = ensureArray(snapshot!.resources.delegation.works);
  const context = snapshot!.resources.context_continuity;
  const contextFrames = ensureArray(context.frames);
  const a2aConversations = ensureArray(context.a2a_conversations);
  const storedRestoreTaskId = useMemo(() => readStoredTaskId(), []);

  // 当 URL 指定了 sessionId 时，直接用该 session；否则用首个 web session 作为默认
  const webSessions = useMemo(
    () => {
      const web = sessions.filter((s) => s.channel === "web");
      return web.length > 0 ? web : sessions;
    },
    [sessions]
  );
  const routeSession = useMemo(
    () =>
      routeSessionId
        ? sessions.find((item) => item.session_id === routeSessionId) ?? null
        : null,
    [routeSessionId, sessions]
  );

  // 当 URL 指定了 session（Worker 会话路由）时，restoreTaskIds 必须完全隔离于
  // 全局 focused_session_id 和 sessions 轮询变化，避免 deps 变化导致 fallthrough
  // 到主 Agent 的 taskIds（根因：sessionDocument?.sessions 每次轮询是新引用，
  // 而 routeSession 可能在轮询间隙瞬间为 null）。
  const routeRestoreTaskIds = useMemo(
    () => (routeSession?.task_id ? [routeSession.task_id] : []),
    [routeSession?.task_id],
  );
  const defaultRestoreTaskIds = useMemo(() => {
    if (routeSessionId) return []; // route 模式下不使用，跳过计算
    const taskIds = sessionDocument ? resolveRestorableTaskIds(sessionDocument) : [];
    if (storedRestoreTaskId && !taskIds.includes(storedRestoreTaskId)) {
      taskIds.unshift(storedRestoreTaskId);
    }
    return taskIds;
  }, [
    routeSessionId,
    sessionDocument?.focused_session_id,
    sessionDocument?.focused_thread_id,
    sessionDocument?.new_conversation_token,
    sessionDocument?.sessions,
    storedRestoreTaskId,
  ]);
  const restoreTaskIds = routeSessionId ? routeRestoreTaskIds : defaultRestoreTaskIds;
  // 始终自动续接最近会话，不再弹出"继续还是新开"选择面板
  const restoreChoice = "continue" as const;
  const resumeSession =
    routeSessionId
      ? routeSession
      : webSessions[0] || null;

  // 当 URL 指向已有 session 或已有 restore task 时，不传 newConversationToken——
  // 避免 stale token 把消息路由到错误的 project。
  // Token 只在通过 NewSessionModal 创建全新会话时使用。
  const shouldContinueRestore = restoreChoice === "continue" && restoreTaskIds.length > 0;
  const hasExistingSession = Boolean(routeSessionId) || shouldContinueRestore;
  const { messages, sendMessage, resetConversation, streaming, restoring, error, taskId, liveApproval, approvalSignal } = useChatStream(
    shouldContinueRestore ? { taskIds: restoreTaskIds } : null,
    {
      activeProjectId: resumeSession?.project_id || projectSelector?.current_project_id || "",
      newConversationToken: hasExistingSession ? "" : (sessionDocument?.new_conversation_token ?? ""),
      newConversationProjectId: hasExistingSession ? "" : (sessionDocument?.new_conversation_project_id ?? ""),
      newConversationAgentProfileId: hasExistingSession
        ? ""
        : (sessionDocument?.new_conversation_agent_profile_id ?? ""),
    },
    {
      deferStoredTaskIdRestore:
        restoreTaskIds.length > 0 && restoreChoice !== "continue",
      skipSessionFocus: Boolean(routeSessionId),
    }
  );
  const [input, setInput] = useState("");
  const [showSessionInternalRefs, setShowSessionInternalRefs] = useState(false);
  const [showRestoreEscape, setShowRestoreEscape] = useState(false);
  const [isEditingSessionAlias, setIsEditingSessionAlias] = useState(false);
  const [sessionAliasDraft, setSessionAliasDraft] = useState("");

  // 消息列表变化时自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const { taskDetail, executionSession, pendingApprovals, refreshNow: refreshTaskLiveState } = useTaskLiveState({
    taskId,
    shouldPoll: streaming,
    approvalSignal,
    snapshotResources: snapshot?.resources ? {
      sessions: snapshot.resources.sessions,
      delegation: snapshot.resources.delegation,
      context_continuity: snapshot.resources.context_continuity,
    } : null,
    refreshResources,
  });
  const [chatActionNotice, setChatActionNotice] = useState<{
    tone: "info" | "success" | "error";
    title: string;
    message: string;
  } | null>(null);
  const [steeringBusy, setSteeringBusy] = useState(false);
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0);
  const [approvalNow, setApprovalNow] = useState(() => Date.now());
  const [freshTurnStartedAt, setFreshTurnStartedAt] = useState<number | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const sessionAliasInputRef = useRef<HTMLInputElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const defaultRootAgentId = readSummaryString(workerProfilesDocument?.summary ?? {}, "default_profile_id");
  const defaultRootAgent = workerProfiles.find((profile) => profile.profile_id === defaultRootAgentId);
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
  const activeWorkId = typeof activeExecutionSummary?.work_id === "string" ? activeExecutionSummary.work_id : "";
  const latestTaskWork =
    sortWorksByUpdate(delegationWorks.filter((item) => item.task_id === taskId))[0] ?? null;
  const activeWork = delegationWorks.find((item) => item.work_id === activeWorkId) ?? latestTaskWork;
  const activeContextFrame =
    contextFrames.find((item) => item.task_id === taskId) ??
    (activeSession ? contextFrames.find((item) => item.session_id === activeSession.session_id) ?? null : null);
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
      Boolean(activeConversationId || readSummaryString(activeWork?.runtime_summary ?? {}, "research_worker_id")));
  const activeConversationLatestType = activeA2AConversationRecord?.latest_message_type || "";
  const activeConversationWorkerSessionId =
    activeA2AConversationRecord?.target_agent_session_id ||
    readSummaryString(activeWork?.runtime_summary ?? {}, "research_worker_agent_session_id") ||
    activeWork?.worker_agent_session_id ||
    "";
  const normalizedTaskStatus = String(taskDetail?.task?.status ?? activeSession?.status ?? "").trim().toUpperCase();
  const hasLoadedTaskStatus = normalizedTaskStatus.length > 0;
  // shouldPollLiveState 已移入 useTaskLiveState hook 内部判断

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
  const latestRuntimeEvidenceMs = ensureArray(taskDetail?.events).reduce((latest, event) => {
    const parsed = Date.parse(String(event.ts ?? ""));
    return Number.isFinite(parsed) ? Math.max(latest, parsed) : latest;
  }, 0);
  const suppressHistoricalActivity =
    freshTurnStartedAt != null && latestRuntimeEvidenceMs < freshTurnStartedAt - 500;
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
  const activityItems: ChatActivityItem[] =
    taskId && suppressHistoricalActivity
      ? [
          {
            id: "butler-fresh-turn",
            actor: "主助手",
            stateLabel: "进行中",
            tone: "running",
            summary: "主助手正在开始处理这条新消息，稍后会替换成这轮真实的工具链和处理阶段。",
            traceTitle: "主助手的直连处理轨迹",
            traceEntries: [],
          },
        ]
      : taskId
        ? [
            {
              ...buildAgentActivity(
                normalizedTaskStatus,
                streaming,
                hasInternalCollaboration,
                activeConversationLatestType,
                isDirectExecution
              ),
              traceTitle: isDirectExecution ? "主助手的直连处理轨迹" : "主助手的委派轨迹",
              traceEntries: activeWork
                ? buildAgentTraceEntries(
                    activeWork,
                    workEvents,
                    activeConversationLatestType,
                    normalizedTaskStatus,
                    isDirectExecution
                  )
                : [],
            },
            ...workerActivities.slice(0, 2),
            ...fallbackWorkerActivity,
          ]
        : [];
  const isRestoringConversation = restoring && messages.length === 0;
  const isEmptyConversation =
    messages.length === 0 && !isRestoringConversation;
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
  const activeSessionProjectId = activeSession?.project_id ?? "";
  const activeSessionWorkId = readSummaryString(
    (activeSession?.execution_summary ?? {}) as Record<string, unknown>,
    "work_id"
  );
  const pendingConversationProjectId = sessionDocument?.new_conversation_project_id ?? "";
  const pendingConversationAgentProfileId =
    sessionDocument?.new_conversation_agent_profile_id ?? "";
  const effectiveProjectId =
    activeSessionProjectId || pendingConversationProjectId || (projectSelector?.current_project_id ?? "");
  const effectiveProjectLabel = effectiveProjectId
    ? resolveProjectName(availableProjects, effectiveProjectId)
    : "";
  const operatorItems = ensureArray(sessionDocument?.operator_items);
  const activeTaskOperatorItems = operatorItems.filter((item) => {
    if (item.state && String(item.state).trim().toLowerCase() !== "pending") {
      return false;
    }
    if (taskId && item.task_id === taskId) {
      return true;
    }
    if (activeSession?.thread_id && item.thread_id === activeSession.thread_id) {
      return true;
    }
    return false;
  });
  const activeApprovalItemFromInbox =
    activeTaskOperatorItems.find((item) =>
      item.quick_actions.some((action) =>
        ["approve_once", "approve_always", "deny"].includes(action.kind)
      )
    ) ?? null;
  const latestApprovalContext =
    taskDetail?.events && taskId
      ? readLatestApprovalContext(
          taskDetail.events,
          activeWork?.work_id || activeSessionWorkId
        )
      : null;
  const latestExpiredApprovalContext =
    taskDetail?.events && taskId
      ? readLatestExpiredApprovalContext(
          taskDetail.events,
          activeWork?.work_id || activeSessionWorkId
        )
      : null;
  const syntheticApprovalItem =
    taskId && pendingApprovals.length > 0 ? buildSyntheticApprovalItem(pendingApprovals[0]!) : null;
  const executionSessionApprovalItem =
    taskId && (executionSession?.pending_approval_id || latestApprovalContext?.approvalId)
      ? buildExecutionSessionApprovalItem(
          executionSession?.pending_approval_id || latestApprovalContext?.approvalId || "",
          {
            taskId,
            toolName: latestApprovalContext?.toolName,
            argsSummary: latestApprovalContext?.argsSummary,
            summary: latestApprovalContext?.summary,
            createdAt: latestApprovalContext?.createdAt,
            expiresAt: latestApprovalContext?.expiresAt,
          }
        )
      : null;
  // SSE 直通审批项：从 useChatStream 的 liveApproval 直接构造，不依赖 REST 轮询
  const liveApprovalItem =
    liveApproval && liveApproval.approvalId
      ? buildExecutionSessionApprovalItem(liveApproval.approvalId, {
          taskId: liveApproval.taskId || taskId || "",
          toolName: liveApproval.toolName,
          argsSummary: liveApproval.toolArgsSummary,
          summary: liveApproval.riskExplanation,
          createdAt: liveApproval.createdAt,
          expiresAt: liveApproval.expiresAt || null,
        })
      : null;
  const activeApprovalItem =
    activeApprovalItemFromInbox ?? syntheticApprovalItem ?? executionSessionApprovalItem ?? liveApprovalItem;
  const activeApprovalExpiresAtMs = activeApprovalItem?.expires_at
    ? Date.parse(activeApprovalItem.expires_at)
    : Number.NaN;
  const activeApprovalRemainingSeconds = Number.isFinite(activeApprovalExpiresAtMs)
    ? Math.max(0, Math.ceil((activeApprovalExpiresAtMs - approvalNow) / 1000))
    : null;
  const shouldShowApprovalBanner = Boolean(
    activeApprovalItem &&
      (activeApprovalRemainingSeconds == null || activeApprovalRemainingSeconds > 0)
  );
  const slashCommandMatches = useMemo(() => {
    const normalized = input.trim().toLowerCase();
    if (!normalized.startsWith("/")) {
      return [];
    }
    return CHAT_SLASH_COMMANDS.filter((item) => item.value.startsWith(normalized));
  }, [input]);
  const canSteerCurrentRun =
    Boolean(taskId) &&
    normalizedTaskStatus === "WAITING_INPUT" &&
    executionSession?.can_attach_input !== false;
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
  const sessionTitleLabel = sessionDisplayName;
  const editableSessionId =
    currentSession?.session_id || activeSession?.session_id || routeSessionId || "";
  const editableThreadId =
    currentSession?.thread_id || activeSession?.thread_id || routeSession?.thread_id || "";
  const canEditSessionAlias = Boolean(editableSessionId);
  const isSavingSessionAlias = busyActionId === "session.set_alias";
  const inputPlaceholder = canSteerCurrentRun
    ? executionSession?.requested_input?.trim() || "直接补充当前这轮需要的信息"
    : `告诉 ${conversationOwnerName} 你现在要做什么`;


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
    if (slashCommandMatches.length === 0) {
      setSelectedCommandIndex(0);
      return;
    }
    setSelectedCommandIndex((current) =>
      Math.min(Math.max(current, 0), slashCommandMatches.length - 1)
    );
  }, [slashCommandMatches]);

  useEffect(() => {
    if (!activeApprovalItem) {
      return;
    }
    setApprovalNow(Date.now());
    const timer = window.setInterval(() => {
      setApprovalNow(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [activeApprovalItem?.item_id, activeApprovalItem?.expires_at]);

  useEffect(() => {
    if (isEditingSessionAlias) {
      return;
    }
    setSessionAliasDraft(sessionDisplayName || conversationTitle);
  }, [conversationTitle, isEditingSessionAlias, sessionDisplayName]);

  useEffect(() => {
    if (!isEditingSessionAlias) {
      return;
    }
    sessionAliasInputRef.current?.focus();
    sessionAliasInputRef.current?.select();
  }, [isEditingSessionAlias]);

  useEffect(() => {
    if (freshTurnStartedAt == null) {
      return;
    }
    if (latestRuntimeEvidenceMs >= freshTurnStartedAt - 500 || error) {
      setFreshTurnStartedAt(null);
    }
  }, [error, freshTurnStartedAt, latestRuntimeEvidenceMs]);

  // taskDetail / executionSession / pendingApprovals 的加载、轮询、审批刷新
  // 已统一由 useTaskLiveState hook 管理

  // 轮询 + SSE 审批刷新逻辑已由 useTaskLiveState hook 管理

  useEffect(() => {
    setChatActionNotice(null);
  }, [taskId]);

  async function handleOperatorAction(item: OperatorInboxItem, kind: OperatorActionKind) {
    const mapped = mapOperatorQuickAction(item, kind);
    if (!mapped) {
      return;
    }
    const result = await submitAction(mapped.actionId, mapped.params);
    if (!result) {
      setChatActionNotice({
        tone: "error",
        title: "这次确认没有成功",
        message: "审批区还没处理完成，你可以再试一次。",
      });
      return;
    }
    setChatActionNotice({
      tone: "success",
      title: "这轮确认已经处理",
      message: result.message || "主助手会基于你的确认继续往下执行。",
    });
    if (taskId) {
      void refreshResources([
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
      ]);
    }
  }

  async function handleResetLegacySession() {
    if (!currentSession?.session_id) {
      return;
    }
    const result = await submitAction("session.reset", {
      session_id: currentSession.session_id,
    });
    if (!result) {
      setChatActionNotice({
        tone: "error",
        title: "这条历史会话还没有重置成功",
        message: "你可以再试一次，或直接新开一条 新会话。",
      });
      return;
    }
    setChatActionNotice({
      tone: "success",
      title: "这条历史会话已经重置",
      message: result.message || "旧 continuity 已清空，接下来会按新的会话语义继续。",
    });
    await resetConversation();
    if (routeSessionId) {
      navigate("/chat");
    }
  }

  async function handleAttachInput(text: string) {
    if (!taskId) {
      return false;
    }
    setSteeringBusy(true);
    try {
      const response = await attachExecutionInput(taskId, {
        text,
        approval_id: executionSession?.pending_approval_id ?? undefined,
        actor: "user:web",
      });
      refreshTaskLiveState();
      setChatActionNotice({
        tone: "success",
        title: "已经把你的补充发给当前执行流程",
        message: response.result.delivered_live
          ? "这轮正在继续执行。"
          : "系统已经接收你的补充，并会继续恢复这轮执行。",
      });
      return true;
    } catch (error) {
      const apiError = error instanceof ApiError ? error : null;
      setChatActionNotice({
        tone: "error",
        title: "这条补充还没发进去",
        message:
          apiError?.message ||
          "当前这轮还没有进入可接收 steering 的状态，请稍后再试。",
      });
      return false;
    } finally {
      setSteeringBusy(false);
    }
  }

  async function resolveCurrentApprovalItem(): Promise<OperatorInboxItem | null> {
    if (activeApprovalItem) {
      return activeApprovalItem;
    }
    if (!taskId) {
      return null;
    }

    let nextExecutionSession = executionSession;
    let nextTaskDetail = taskDetail;
    let nextApprovals = pendingApprovals;

    // 直接 fetch 最新数据（不走 hook 因为需要立即判断结果）
    const [sessionResult, detailResult, approvalsResult] = await Promise.allSettled([
      fetchTaskExecutionSession(taskId),
      fetchTaskDetail(taskId),
      fetchApprovals(),
    ]);
    // 同时触发 hook 下一轮刷新
    refreshTaskLiveState();

    if (sessionResult.status === "fulfilled") {
      nextExecutionSession = readExecutionSessionDocument(sessionResult.value);
    }
    if (detailResult.status === "fulfilled") {
      nextTaskDetail = detailResult.value;
    }
    if (approvalsResult.status === "fulfilled") {
      nextApprovals = approvalsResult.value.approvals.filter((item) => item.task_id === taskId);
      if (nextApprovals.length > 0) {
        return buildSyntheticApprovalItem(nextApprovals[0]!);
      }
    }

    if (nextExecutionSession?.pending_approval_id) {
      const refreshedApprovalContext =
        nextTaskDetail?.events && taskId
          ? readLatestApprovalContext(nextTaskDetail.events, activeWork?.work_id || activeSessionWorkId)
          : latestApprovalContext;
      return buildExecutionSessionApprovalItem(nextExecutionSession.pending_approval_id, {
        taskId,
        toolName: refreshedApprovalContext?.toolName,
        argsSummary: refreshedApprovalContext?.argsSummary,
        summary: refreshedApprovalContext?.summary,
        createdAt: refreshedApprovalContext?.createdAt,
      });
    }

    const approvalIdFromEvents =
      nextTaskDetail?.events && taskId
        ? readLatestApprovalContext(
            nextTaskDetail.events,
            activeWork?.work_id || activeSessionWorkId
          )?.approvalId
        : latestApprovalContext?.approvalId;
    if (approvalIdFromEvents) {
      return buildExecutionSessionApprovalItem(approvalIdFromEvents, {
        taskId,
        toolName: latestApprovalContext?.toolName,
        argsSummary: latestApprovalContext?.argsSummary,
        summary: latestApprovalContext?.summary,
        createdAt: latestApprovalContext?.createdAt,
      });
    }

    return null;
  }

  function applySlashCommandSuggestion(value: string) {
    setInput(value);
    setSelectedCommandIndex(0);
    window.requestAnimationFrame(() => {
      if (!inputRef.current) {
        return;
      }
      inputRef.current.focus();
      inputRef.current.setSelectionRange(value.length, value.length);
    });
  }

  const handleStartSessionAliasEdit = useCallback(() => {
    if (!canEditSessionAlias || isSavingSessionAlias) {
      return;
    }
    setSessionAliasDraft(sessionTitleLabel);
    setIsEditingSessionAlias(true);
  }, [canEditSessionAlias, isSavingSessionAlias, sessionTitleLabel]);

  const handleCancelSessionAliasEdit = useCallback(() => {
    setSessionAliasDraft(sessionTitleLabel);
    setIsEditingSessionAlias(false);
  }, [sessionTitleLabel]);

  const handleSaveSessionAlias = useCallback(async () => {
    if (!canEditSessionAlias) {
      setIsEditingSessionAlias(false);
      return;
    }
    const normalizedDraft = sessionAliasDraft.trim();
    const normalizedBaseTitle = sessionTitleBase.trim();
    const nextAlias =
      !normalizedDraft || (normalizedBaseTitle && normalizedDraft === normalizedBaseTitle)
        ? ""
        : normalizedDraft;
    if (nextAlias === currentSessionAlias.trim()) {
      setSessionAliasDraft(nextAlias || normalizedBaseTitle || conversationTitle);
      setIsEditingSessionAlias(false);
      return;
    }
    const result = await submitAction("session.set_alias", {
      session_id: editableSessionId,
      thread_id: editableThreadId,
      alias: nextAlias,
    });
    if (result) {
      setSessionAliasDraft(nextAlias || normalizedBaseTitle || conversationTitle);
      setIsEditingSessionAlias(false);
    }
  }, [
    canEditSessionAlias,
    conversationTitle,
    currentSessionAlias,
    editableSessionId,
    editableThreadId,
    sessionAliasDraft,
    sessionTitleBase,
    submitAction,
  ]);

  function handleSessionAliasInputKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      void handleSaveSessionAlias();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      handleCancelSessionAliasEdit();
    }
  }

  async function submitCurrentInput() {
    const text = input.trim();
    if (!text || restoring || steeringBusy) {
      return;
    }
    const approvalCommand = parseApprovalCommand(text);
    if (approvalCommand) {
      const approvalItem = await resolveCurrentApprovalItem();
      if (!approvalItem) {
        setChatActionNotice({
          tone: "error",
          title: latestExpiredApprovalContext ? "刚才那条审批已经超时" : "当前没有可处理的审批",
          message: latestExpiredApprovalContext
            ? `${latestExpiredApprovalContext.toolName || "这一步"} 没等到你的确认，已经自动拒绝。请先重试这一步，再重新批准。`
            : "如果你刚才看到需要确认，可能这条审批已经超时，或者当前页面还没拿到最新审批状态。",
        });
        return;
      }
      setInput("");
      await handleOperatorAction(approvalItem, approvalCommand);
      return;
    }
    setInput("");
    if (canSteerCurrentRun) {
      const attached = await handleAttachInput(text);
      if (!attached) {
        setInput(text);
      }
      return;
    }
    setFreshTurnStartedAt(Date.now());
    // taskDetail/executionSession/pendingApprovals 的重置由
    // useTaskLiveState 在 taskId 变化时自动处理
    setChatActionNotice(null);
    await sendMessage(text, {
      agentProfileId: currentSessionOwnerProfileId || undefined,
      sessionId: !taskId ? currentSession?.session_id || routeSessionId || undefined : undefined,
      threadId: !taskId ? currentSession?.thread_id || undefined : undefined,
    });
  }

  function handleInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // IME 正在组合（如中文输入法按 Enter 确认候选词）时，不拦截按键
    if (event.nativeEvent.isComposing || event.key === "Process") {
      return;
    }
    if (event.key === "ArrowDown") {
      if (slashCommandMatches.length === 0) {
        return;
      }
      event.preventDefault();
      setSelectedCommandIndex((current) => (current + 1) % slashCommandMatches.length);
      return;
    }
    if (event.key === "ArrowUp") {
      if (slashCommandMatches.length === 0) {
        return;
      }
      event.preventDefault();
      setSelectedCommandIndex(
        (current) => (current - 1 + slashCommandMatches.length) % slashCommandMatches.length
      );
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      if (slashCommandMatches.length > 0) {
        const highlighted = slashCommandMatches[selectedCommandIndex] ?? slashCommandMatches[0];
        if (highlighted && input.trim() !== highlighted.value) {
          event.preventDefault();
          applySlashCommandSuggestion(highlighted.value);
          return;
        }
      }
      event.preventDefault();
      void submitCurrentInput();
      return;
    }
    if (slashCommandMatches.length === 0) {
      return;
    }
    const highlighted = slashCommandMatches[selectedCommandIndex] ?? slashCommandMatches[0];
    if (!highlighted) {
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      applySlashCommandSuggestion(highlighted.value);
      return;
    }
    if (event.key === "Enter" && input.trim() !== highlighted.value) {
      event.preventDefault();
      applySlashCommandSuggestion(highlighted.value);
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    await submitCurrentInput();
  }

  const pendingCommandAction = parseApprovalCommand(input);
  const hasSlashSuggestionOpen = slashCommandMatches.length > 0;
  const submitLabel = pendingCommandAction
    ? "执行命令"
    : canSteerCurrentRun
      ? "继续这轮"
      : streaming
        ? "加入队列"
        : "发送";
  const legacyResetCallout = legacyResetRecommended ? (
    <InlineCallout title="这条历史会话建议先重置再继续" tone="muted">
      <div className="wb-chat-inline-actions">
        <span>{compatibilityMessage}</span>
        <div className="wb-action-bar">
          <button
            type="button"
            className="wb-button wb-button-secondary"
            onClick={() => void handleResetLegacySession()}
            disabled={busyActionId === "session.reset"}
          >
            重置 continuity
          </button>
          <button
            type="button"
            className="wb-button wb-button-ghost"
            onClick={() => {
              void resetConversation();
            }}
          >
            新开 新会话
          </button>
        </div>
      </div>
    </InlineCallout>
  ) : null;

  return (
    <div className="wb-page wb-chat-page">
      <section
        className={`wb-panel wb-chat-panel wb-chat-shell ${isEmptyConversation ? "is-empty" : ""}`}
      >
        <div className="wb-panel-head wb-chat-head">
          <div className="wb-chat-head-copy">
            <div className="wb-chat-head-inline">
              <h3 className="wb-chat-head-title-heading">
                {isEditingSessionAlias ? (
                  <input
                    ref={sessionAliasInputRef}
                    className="wb-chat-head-title-input"
                    value={sessionAliasDraft}
                    onChange={(event) => setSessionAliasDraft(event.target.value)}
                    onBlur={() => {
                      void handleSaveSessionAlias();
                    }}
                    onKeyDown={handleSessionAliasInputKeyDown}
                    disabled={isSavingSessionAlias}
                    aria-label="编辑会话名称"
                  />
                ) : (
                  <button
                    type="button"
                    className="wb-chat-head-title-button"
                    onClick={handleStartSessionAliasEdit}
                    disabled={!canEditSessionAlias || isSavingSessionAlias}
                    aria-label="编辑会话名称"
                  >
                    <span className="wb-chat-head-title-label">{sessionTitleLabel}</span>
                  </button>
                )}
              </h3>
              <p className="wb-chat-head-summary">{conversationOwnerName}</p>
            </div>
          </div>
          <div className="wb-chat-head-actions">
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
                  <span>如果这一步还没结束，你可以从左侧开始一段新对话。</span>
                </div>
              ) : null}
            </div>
          </div>
        ) : isEmptyConversation ? (
          <div className="wb-chat-empty-stage">
            {error ? (
              <InlineCallout title="刚才没有发送成功" tone="error">
                {error}
              </InlineCallout>
            ) : null}
            {legacyResetCallout}
            <form className="wb-chat-form is-empty" onSubmit={handleSubmit}>
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleInputKeyDown}
                placeholder={inputPlaceholder}
                disabled={streaming}
                rows={3}
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
              <div ref={messagesEndRef} />
            </div>

            {error ? (
              <InlineCallout title="刚才没有发送成功" tone="error">
                {error}
              </InlineCallout>
            ) : null}
            {legacyResetCallout}

            {shouldShowApprovalBanner && activeApprovalItem ? (() => {
              const item = activeApprovalItem;
              return <>
                <InlineCallout
                  title={
                    activeApprovalRemainingSeconds != null
                      ? `等待你批准 ${item.metadata.tool_name || item.title} · ${formatCountdown(activeApprovalRemainingSeconds)} 后超时`
                      : "这轮正在等你确认"
                  }
                  tone="error"
                  actions={
                    <div className="wb-inline-actions wb-inline-actions-wrap">
                    {item.quick_actions.map((action) => (
                      <button
                        key={`${item.item_id}-${action.kind}`}
                        type="button"
                        className={
                          action.style === "primary"
                            ? "wb-button wb-button-primary"
                            : "wb-button wb-button-secondary"
                        }
                        disabled={
                          !action.enabled ||
                          busyActionId === mapOperatorQuickAction(item, action.kind)?.actionId
                        }
                        onClick={() => void handleOperatorAction(item, action.kind)}
                      >
                        {action.label}
                      </button>
                    ))}
                    </div>
                  }
                >
                  <>
                    <span className="wb-chat-approval-banner-line">{item.summary}</span>
                    {item.metadata.tool_args_summary ? (
                      <span className="wb-chat-approval-banner-line">
                        <code>{item.metadata.tool_args_summary}</code>
                      </span>
                    ) : null}
                  </>
                </InlineCallout>
                <p className="wb-chat-form-hint">
                  也可以直接输入 <code>/approve</code>、<code>/approve always</code> 或{" "}
                  <code>/deny</code>。
                </p>
              </>;
            })() : null}

            {chatActionNotice ? (
              <InlineCallout
                title={chatActionNotice.title}
                tone={chatActionNotice.tone === "error" ? "error" : "muted"}
              >
                {chatActionNotice.message}
              </InlineCallout>
            ) : null}

            <form className="wb-chat-form" onSubmit={handleSubmit}>
              <textarea
                ref={inputRef}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleInputKeyDown}
                placeholder={inputPlaceholder}
                disabled={restoring || steeringBusy}
                rows={3}
              />
              <button
                type="submit"
                className="wb-button wb-button-primary"
                disabled={restoring || steeringBusy || !input.trim()}
              >
                {steeringBusy ? "处理中" : submitLabel}
              </button>
            </form>
            {hasSlashSuggestionOpen ? (
              <div className="wb-chat-command-menu" role="listbox" aria-label="聊天命令建议">
                {slashCommandMatches.map((command, index) => (
                  <button
                    key={command.value}
                    type="button"
                    role="option"
                    aria-selected={index === selectedCommandIndex}
                    className={`wb-chat-command-option${
                      index === selectedCommandIndex ? " is-active" : ""
                    }`}
                    onClick={() => applySlashCommandSuggestion(command.value)}
                  >
                    <strong>{command.value}</strong>
                    <span>{command.description}</span>
                  </button>
                ))}
              </div>
            ) : null}
            {canSteerCurrentRun ? (
              <p className="wb-chat-form-hint">
                这条输入会直接作为当前执行的补充，不会新开一轮。
              </p>
            ) : streaming ? (
              <p className="wb-chat-form-hint">
                当前这轮还在处理；你现在继续发的消息会进入同一条会话队列，主助手会接着处理。
              </p>
            ) : null}
          </>
        )}
      </section>
    </div>
  );
}
