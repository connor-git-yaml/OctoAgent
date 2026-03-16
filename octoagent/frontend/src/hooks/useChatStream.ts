/**
 * useChatStream Hook -- T047
 *
 * SSE EventSource 订阅 message:chunk/message:complete + 流式内容拼接
 * + 审批通知检测 + 重连逻辑
 * 对齐 FR-024
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  buildFrontDoorSseUrl,
  executeControlAction,
  fetchTaskDetail,
  frontDoorRequest,
} from "../api/client";
import type { SSEEventData, TaskDetailResponse } from "../types";
import {
  AGENT_STREAM_PLACEHOLDER,
  buildMessagesFromTaskDetail,
  buildRestoreCandidateTaskIds,
  extractAgentMessage,
  extractFailureMessage,
  isUserVisibleModelEvent,
  persistTaskId,
  readStoredTaskId,
} from "./chatStreamHelpers";
import type { ChatMessage, ChatRestoreTarget, ChatSendOptions } from "./chatStreamTypes";
export type { ChatMessage, ChatRestoreTarget, ChatSendOptions, MessageRole } from "./chatStreamTypes";

const RESTORE_TASK_DETAIL_TIMEOUT_MS = 3_000;

/** Hook 返回值 */
export interface UseChatStreamReturn {
  /** 消息列表 */
  messages: ChatMessage[];
  /** 是否正在流式接收 */
  streaming: boolean;
  /** 是否正在恢复历史对话 */
  restoring: boolean;
  /** 错误信息 */
  error: string | null;
  /** 发送消息 */
  sendMessage: (text: string, options?: ChatSendOptions) => Promise<void>;
  /** 开始新对话 */
  resetConversation: () => Promise<void>;
  /** 当前 task ID */
  taskId: string | null;
}

interface UseChatStreamOptions {
  deferStoredTaskIdRestore?: boolean;
}

export interface ChatSessionScopeSnapshot {
  activeProjectId?: string | null;
  activeWorkspaceId?: string | null;
  newConversationToken?: string | null;
  newConversationProjectId?: string | null;
  newConversationWorkspaceId?: string | null;
  newConversationAgentProfileId?: string | null;
}

interface PendingConversationScope {
  token: string;
  projectId: string;
  workspaceId: string;
  agentProfileId: string;
}

function makeControlActionRequest(
  actionId: string,
  params: Record<string, unknown>
) {
  return {
    request_id:
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `req-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    action_id: actionId,
    surface: "web" as const,
    actor: {
      actor_id: "user:web",
      actor_label: "Owner",
    },
    params,
  };
}

function normalizeTaskId(taskId: string | null | undefined): string | null {
  if (!taskId) {
    return null;
  }
  const normalized = taskId.trim();
  return normalized ? normalized : null;
}

function buildPendingConversationScope(
  snapshot: ChatSessionScopeSnapshot | null | undefined
): PendingConversationScope | null {
  const token = String(snapshot?.newConversationToken ?? "").trim();
  if (!token) {
    return null;
  }
  return {
    token,
    projectId: String(snapshot?.newConversationProjectId ?? "").trim(),
    workspaceId: String(snapshot?.newConversationWorkspaceId ?? "").trim(),
    agentProfileId: String(snapshot?.newConversationAgentProfileId ?? "").trim(),
  };
}

async function fetchTaskDetailWithTimeout(taskId: string): Promise<TaskDetailResponse> {
  let timer: number | null = null;
  try {
    return await Promise.race([
      fetchTaskDetail(taskId),
      new Promise<TaskDetailResponse>((_, reject) => {
        timer = window.setTimeout(() => {
          reject(new Error("RESTORE_TIMEOUT"));
        }, RESTORE_TASK_DETAIL_TIMEOUT_MS);
      }),
    ]);
  } finally {
    if (timer != null) {
      window.clearTimeout(timer);
    }
  }
}

export function useChatStream(
  restoreTarget: ChatRestoreTarget | null = null,
  sessionScope: ChatSessionScopeSnapshot | null = null,
  options: UseChatStreamOptions = {}
): UseChatStreamReturn {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(() =>
    options.deferStoredTaskIdRestore ? null : readStoredTaskId()
  );
  const [pendingConversationScope, setPendingConversationScope] =
    useState<PendingConversationScope | null>(() => buildPendingConversationScope(sessionScope));
  const [suppressedRestoreSignature, setSuppressedRestoreSignature] = useState<string | null>(
    null
  );
  const eventSourceRef = useRef<EventSource | null>(null);
  const activeAgentMessageIdRef = useRef<string | null>(null);
  const attemptedRestoreSignatureRef = useRef<string | null>(null);
  const restoreTaskIdSignature = (restoreTarget?.taskIds ?? []).join("|");

  const spawnAgentPlaceholder = useCallback(() => {
    const placeholderId = `agent-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    activeAgentMessageIdRef.current = placeholderId;
    const agentMsg: ChatMessage = {
      id: placeholderId,
      role: "agent",
      content: AGENT_STREAM_PLACEHOLDER,
      isStreaming: true,
    };
    setMessages((prev) => [...prev, agentMsg]);
    setStreaming(true);
    return placeholderId;
  }, []);

  const ensureActiveAgentPlaceholder = useCallback(() => {
    if (activeAgentMessageIdRef.current) {
      return activeAgentMessageIdRef.current;
    }
    return spawnAgentPlaceholder();
  }, [spawnAgentPlaceholder]);

  /** 关闭 EventSource */
  const closeStream = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    activeAgentMessageIdRef.current = null;
    setStreaming(false);
  }, []);

  const requestSessionFocus = useCallback(async (nextTaskId: string) => {
    const normalized = normalizeTaskId(nextTaskId);
    if (!normalized) {
      return;
    }
    await executeControlAction(
      makeControlActionRequest("session.focus", {
        task_id: normalized,
      })
    );
  }, []);

  const requestNewConversation = useCallback(async (currentTaskId: string | null) => {
    return await executeControlAction(
      makeControlActionRequest("session.new", currentTaskId ? { task_id: currentTaskId } : {})
    );
  }, []);

  /** 发送消息 */
  const sendMessage = useCallback(
    async (text: string, options?: ChatSendOptions) => {
      if (!text.trim()) return;

      // 添加用户消息
      const userMsg: ChatMessage = {
        id: `user-${Date.now()}`,
        role: "user",
        content: text,
        isStreaming: false,
      };
      setMessages((prev) => [...prev, userMsg]);

      // 发送到后端
      try {
        setError(null);
        setRestoring(false);
        setSuppressedRestoreSignature(null);
        const reuseLiveStream =
          Boolean(taskId) &&
          eventSourceRef.current != null &&
          eventSourceRef.current.readyState !== EventSource.CLOSED;
        const effectiveProjectId =
          pendingConversationScope?.projectId ||
          String(sessionScope?.activeProjectId ?? "").trim();
        const effectiveWorkspaceId =
          pendingConversationScope?.workspaceId ||
          String(sessionScope?.activeWorkspaceId ?? "").trim();
        const effectiveAgentProfileId =
          (!taskId && pendingConversationScope?.token
            ? pendingConversationScope.agentProfileId
            : "") || options?.agentProfileId?.trim() || "";
        const resp = await frontDoorRequest("/api/chat/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: text,
            task_id: taskId,
            agent_profile_id: effectiveAgentProfileId || undefined,
            new_conversation_token:
              !taskId && pendingConversationScope?.token
                ? pendingConversationScope.token
                : undefined,
            project_id: !taskId && effectiveProjectId ? effectiveProjectId : undefined,
            workspace_id: !taskId && effectiveWorkspaceId ? effectiveWorkspaceId : undefined,
          }),
        });

        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}`);
        }

        const data = await resp.json();
        const newTaskId = data.task_id;
        setTaskId(newTaskId);
        if (!taskId) {
          setPendingConversationScope(null);
        }
        void requestSessionFocus(newTaskId).catch(() => {
          // 这里不阻断聊天主链，focus 同步失败只影响 restore/focus 体验。
        });

        if (reuseLiveStream) {
          return;
        }

        closeStream();
        ensureActiveAgentPlaceholder();

        // 连接 SSE 流
        const streamUrl = buildFrontDoorSseUrl(data.stream_url);
        const es = new EventSource(streamUrl);
        eventSourceRef.current = es;

        // 监听消息事件
        const handleEvent = (e: MessageEvent) => {
          try {
            const eventData = JSON.parse(e.data) as Omit<SSEEventData, "type"> & {
              type: string;
            };

            if (eventData.type === "MODEL_CALL_STARTED" && isUserVisibleModelEvent(eventData)) {
              ensureActiveAgentPlaceholder();
            }

            // 检查是否是模型回复事件
            if (eventData.type === "MODEL_CALL_COMPLETED" && isUserVisibleModelEvent(eventData)) {
              const content = extractAgentMessage(eventData);
              const currentAgentMsgId = activeAgentMessageIdRef.current ?? ensureActiveAgentPlaceholder();
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === currentAgentMsgId
                    ? {
                        ...msg,
                        content: content || "已收到回复，但没有可显示的正文。",
                        isStreaming: false,
                      }
                    : msg
                )
              );
              activeAgentMessageIdRef.current = null;
              setStreaming(false);
            }

            if (
              (eventData.type === "MODEL_CALL_FAILED" && isUserVisibleModelEvent(eventData)) ||
              eventData.type === "ERROR"
            ) {
              const failureMessage = extractFailureMessage(eventData);
              const currentAgentMsgId = activeAgentMessageIdRef.current ?? ensureActiveAgentPlaceholder();
              setError(failureMessage);
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === currentAgentMsgId
                    ? {
                        ...msg,
                        content:
                          msg.isStreaming || msg.content === AGENT_STREAM_PLACEHOLDER
                            ? failureMessage
                            : msg.content || failureMessage,
                        isStreaming: false,
                      }
                    : msg
                )
              );
              activeAgentMessageIdRef.current = null;
              setStreaming(false);
            }

            // 检查审批请求
            if (
              eventData.type === "approval:requested" ||
              eventData.type === "APPROVAL_REQUESTED"
            ) {
              const currentAgentMsgId = activeAgentMessageIdRef.current ?? ensureActiveAgentPlaceholder();
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === currentAgentMsgId
                    ? { ...msg, hasApproval: true, isStreaming: true }
                    : msg
                )
              );
              setStreaming(true);
            }

            // 终态检测
            if (eventData.final) {
              closeStream();
            }
          } catch {
            // 忽略解析错误（心跳等）
          }
        };

        // 监听各类事件
        const eventTypes = [
          "MODEL_CALL_COMPLETED",
          "MODEL_CALL_STARTED",
          "MODEL_CALL_FAILED",
          "STATE_TRANSITION",
          "APPROVAL_REQUESTED",
          "approval:requested",
          "ERROR",
        ];
        for (const type of eventTypes) {
          es.addEventListener(type, handleEvent);
        }
        es.onmessage = handleEvent;

        es.onerror = () => {
          if (es.readyState === EventSource.CLOSED) {
            setStreaming(false);
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === activeAgentMessageIdRef.current
                  ? { ...msg, isStreaming: false }
                  : msg
              )
            );
            activeAgentMessageIdRef.current = null;
          }
        };
      } catch (err) {
        setError(err instanceof Error ? err.message : "发送失败");
        setStreaming(false);
      }
    },
    [
      taskId,
      closeStream,
      ensureActiveAgentPlaceholder,
      pendingConversationScope,
      requestSessionFocus,
      sessionScope,
    ]
  );

  const resetConversation = useCallback(async () => {
    const currentTaskId = taskId;
    closeStream();
    attemptedRestoreSignatureRef.current = null;
    setSuppressedRestoreSignature(restoreTaskIdSignature);
    setMessages([]);
    setError(null);
    setRestoring(false);
    setTaskId(null);
    activeAgentMessageIdRef.current = null;
    persistTaskId(null);
    try {
      const result = await requestNewConversation(currentTaskId);
      const resultData =
        result && typeof result === "object" && result.data && typeof result.data === "object"
          ? result.data
          : {};
      const nextScope = {
        newConversationToken: String(resultData.new_conversation_token ?? ""),
        newConversationProjectId: String(resultData.project_id ?? ""),
        newConversationWorkspaceId: String(resultData.workspace_id ?? ""),
        newConversationAgentProfileId: String(resultData.agent_profile_id ?? ""),
      };
      setPendingConversationScope(buildPendingConversationScope(nextScope));
    } catch {
      setError("本地已切到新对话，但服务端还没确认新的会话起点。");
    }
  }, [closeStream, requestNewConversation, restoreTaskIdSignature, taskId]);

  useEffect(() => {
    if (taskId) {
      return;
    }
    setPendingConversationScope(buildPendingConversationScope(sessionScope));
  }, [
    sessionScope?.newConversationToken,
    sessionScope?.newConversationProjectId,
    sessionScope?.newConversationWorkspaceId,
    sessionScope?.newConversationAgentProfileId,
    taskId,
  ]);

  // 组件卸载时清理
  useEffect(() => {
    let cancelled = false;

    async function restoreConversation() {
      const candidateTaskIds = buildRestoreCandidateTaskIds(taskId, restoreTarget);
      if (candidateTaskIds.length === 0 || messages.length > 0 || streaming) {
        setRestoring(false);
        return;
      }
      if (!taskId && suppressedRestoreSignature === restoreTaskIdSignature) {
        setRestoring(false);
        return;
      }
      const restoreSignature = candidateTaskIds.join("|");
      if (attemptedRestoreSignatureRef.current === restoreSignature) {
        setRestoring(false);
        return;
      }
      attemptedRestoreSignatureRef.current = restoreSignature;
      setRestoring(true);
      try {
        for (const candidateTaskId of candidateTaskIds) {
          try {
            const detail = await fetchTaskDetailWithTimeout(candidateTaskId);
            if (cancelled) {
              return;
            }
            const restoredMessages = buildMessagesFromTaskDetail(detail);
            if (restoredMessages.length === 0) {
              continue;
            }
            setTaskId(candidateTaskId);
            setMessages(restoredMessages);
            setError(null);
            return;
          } catch {
            if (cancelled) {
              return;
            }
          }
        }

        if (!cancelled) {
          const primaryCandidateTaskId = candidateTaskIds[0] ?? null;
          if (taskId === primaryCandidateTaskId) {
            setTaskId(null);
          }
          persistTaskId(null);
          setMessages([]);
        }
      } finally {
        if (!cancelled) {
          setRestoring(false);
        }
      }
    }

    void restoreConversation();
    return () => {
      cancelled = true;
    };
  }, [messages.length, restoreTaskIdSignature, streaming, suppressedRestoreSignature, taskId]);

  useEffect(() => {
    persistTaskId(taskId);
  }, [taskId]);

  useEffect(() => {
    return () => {
      closeStream();
    };
  }, [closeStream]);

  return {
    messages,
    streaming,
    restoring,
    error,
    sendMessage,
    resetConversation,
    taskId,
  };
}
