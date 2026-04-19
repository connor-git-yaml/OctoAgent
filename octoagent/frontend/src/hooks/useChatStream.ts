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
  findLastAgentContentInCurrentTurn,
  isUserVisibleModelEvent,
  persistTaskId,
  readStoredTaskId,
} from "./chatStreamHelpers";
import type { ChatMessage, ChatRestoreTarget, ChatSendOptions } from "./chatStreamTypes";
export type { ChatMessage, ChatRestoreTarget, ChatSendOptions, MessageRole } from "./chatStreamTypes";

const RESTORE_TASK_DETAIL_TIMEOUT_MS = 3_000;

/** SSE 推送的审批事件快照（直接从事件 payload 构造，不依赖 REST 轮询） */
export interface SSEApprovalSnapshot {
  approvalId: string;
  taskId: string;
  toolName: string;
  toolArgsSummary: string;
  riskExplanation: string;
  sideEffectLevel: string;
  createdAt: string;
  expiresAt: string;
}

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
  /** SSE 实时推送的待审批快照（非 null 时表示有活跃审批） */
  liveApproval: SSEApprovalSnapshot | null;
  /** 审批事件信号（每次审批状态变化时递增，可用于触发外部刷新） */
  approvalSignal: number;
}

interface UseChatStreamOptions {
  deferStoredTaskIdRestore?: boolean;
  /** 跳过发消息后的全局 session.focus 同步（在特定 Worker 会话路由下使用，避免抢走 focus） */
  skipSessionFocus?: boolean;
}

export interface ChatSessionScopeSnapshot {
  activeProjectId?: string | null;
  /** @deprecated workspace 概念已移除，保留字段仅为兼容 */
  activeWorkspaceId?: string | null;
  newConversationToken?: string | null;
  newConversationProjectId?: string | null;
  /** @deprecated workspace 概念已移除，保留字段仅为兼容 */
  newConversationWorkspaceId?: string | null;
  newConversationAgentProfileId?: string | null;
}

interface PendingConversationScope {
  token: string;
  projectId: string;
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
  const skipSessionFocus = Boolean(options.skipSessionFocus);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(() =>
    options.deferStoredTaskIdRestore ? null : readStoredTaskId()
  );
  const [liveApproval, setLiveApproval] = useState<SSEApprovalSnapshot | null>(null);
  const [approvalSignal, setApprovalSignal] = useState(0);
  const [pendingConversationScope, setPendingConversationScope] =
    useState<PendingConversationScope | null>(() => buildPendingConversationScope(sessionScope));
  const [suppressedRestoreSignature, setSuppressedRestoreSignature] = useState<string | null>(
    null
  );
  const eventSourceRef = useRef<EventSource | null>(null);
  const activeAgentMessageIdRef = useRef<string | null>(null);
  const taskIdRef = useRef<string | null>(taskId);
  const attemptedRestoreSignatureRef = useRef<string | null>(null);
  const restoreTaskIdSignature = (restoreTarget?.taskIds ?? []).join("|");
  const prevRestoreSignatureRef = useRef(restoreTaskIdSignature);

  useEffect(() => {
    taskIdRef.current = taskId;
  }, [taskId]);

  // Session 切换时清空旧状态，让 restore effect 能重新加载新会话消息
  useEffect(() => {
    const prev = prevRestoreSignatureRef.current;
    prevRestoreSignatureRef.current = restoreTaskIdSignature;
    if (prev === restoreTaskIdSignature) {
      return;
    }
    // 如果当前已有活跃的 taskId（刚发过消息），且 focused session 的首个候选 task 仍是当前 taskId，
    // 说明这只是轮询更新 session 列表导致引用变化，不是真正的 session 切换，不应该重置状态。
    // 注意：不能用 includes——因为 signature 包含所有 session 的 taskId，切换 session 后
    // 旧 taskId 仍然在 signature 里，会导致切换被误判为"未变化"。
    const primaryCandidateId = restoreTaskIdSignature.split("|")[0] || "";
    if (taskId && primaryCandidateId === taskId) {
      return;
    }
    // restoreTaskIdSignature 变化说明用户切换了 Session
    // 关闭旧 SSE 连接
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    activeAgentMessageIdRef.current = null;
    // 重置消息和任务状态，让后续 restore effect 重新触发
    setMessages([]);
    setTaskId(null);
    setStreaming(false);
    setError(null);
    setLiveApproval(null);
    setSuppressedRestoreSignature(null);
    attemptedRestoreSignatureRef.current = null;
  }, [restoreTaskIdSignature, taskId]);

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

  /** 关闭 EventSource
   *
   * 兜底策略：若关闭时 placeholder 仍未被替换（典型成因是 SSE 链路丢了关键的
   * MODEL_CALL_COMPLETED / FAILED 事件，或队列溢出被踢），主动拉一次任务详情
   * 从 artifact 里把最终 agent 回复补上，避免用户必须刷新页面才能看到结果。
   */
  const closeStream = useCallback(() => {
    const msgId = activeAgentMessageIdRef.current;
    const currentTaskId = taskIdRef.current;
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    activeAgentMessageIdRef.current = null;
    setStreaming(false);

    if (!msgId || !currentTaskId) {
      return;
    }
    void fetchTaskDetail(currentTaskId)
      .then((detail) => {
        const restored = buildMessagesFromTaskDetail(detail);
        // 限定在"最后一条 USER_MESSAGE 之后"，避免续聊时把上一轮 agent 回复
        // 错贴到当前占位消息上（CANCELLED/REJECTED 或当前轮无 agent 输出时）
        const lastAgentContent = findLastAgentContentInCurrentTurn(restored);
        if (!lastAgentContent) {
          return;
        }
        setMessages((prev) =>
          prev.map((msg) => {
            if (msg.id !== msgId) {
              return msg;
            }
            if (msg.isStreaming || msg.content === AGENT_STREAM_PLACEHOLDER) {
              return { ...msg, content: lastAgentContent, isStreaming: false };
            }
            return msg;
          })
        );
      })
      .catch(() => {
        // 兜底失败保持当前状态即可，不再冒泡错误
      });
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
            session_id: !taskId ? options?.sessionId?.trim() || undefined : undefined,
            thread_id: !taskId ? options?.threadId?.trim() || undefined : undefined,
            new_conversation_token:
              !taskId && pendingConversationScope?.token
                ? pendingConversationScope.token
                : undefined,
            project_id: !taskId && effectiveProjectId ? effectiveProjectId : undefined,
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
        // 在特定 session 路由（如 Worker 会话）下跳过 focus 同步，
        // 避免全局 focused_session_id 被抢走导致消息列表被替换。
        if (!skipSessionFocus) {
          void requestSessionFocus(newTaskId).catch(() => {
            // 这里不阻断聊天主链，focus 同步失败只影响 restore/focus 体验。
          });
        }

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

            // 检查审批请求——直接从 SSE payload 构造审批快照，不依赖 REST 轮询
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
              // 从 SSE 事件 payload 直接构造审批快照
              const p = (eventData.payload ?? {}) as Record<string, unknown>;
              const approvalId = typeof p.approval_id === "string" ? p.approval_id : "";
              if (approvalId) {
                setLiveApproval({
                  approvalId,
                  taskId: typeof p.task_id === "string" ? p.task_id : "",
                  toolName: typeof p.tool_name === "string" ? p.tool_name : "",
                  toolArgsSummary: typeof p.tool_args_summary === "string" ? p.tool_args_summary : "",
                  riskExplanation: typeof p.risk_explanation === "string" ? p.risk_explanation : "",
                  sideEffectLevel: typeof p.side_effect_level === "string" ? p.side_effect_level : "",
                  createdAt: typeof p.created_at === "string" ? p.created_at : "",
                  expiresAt: typeof p.expires_at === "string" ? p.expires_at : "",
                });
                setApprovalSignal((n) => n + 1);
              }
            }

            // 审批已解决/过期——清除 liveApproval
            if (
              eventData.type === "APPROVAL_EXPIRED" ||
              eventData.type === "APPROVAL_APPROVED" ||
              eventData.type === "APPROVAL_REJECTED" ||
              eventData.type === "approval:resolved" ||
              eventData.type === "approval:expired"
            ) {
              setLiveApproval(null);
              setApprovalSignal((n) => n + 1);
            }

            // 终态检测
            if (eventData.final) {
              closeStream();
            }
          } catch {
            // 忽略解析错误（心跳等）
          }
        };

        // 监听各类事件（含审批全生命周期）
        const eventTypes = [
          "MODEL_CALL_COMPLETED",
          "MODEL_CALL_STARTED",
          "MODEL_CALL_FAILED",
          "STATE_TRANSITION",
          "APPROVAL_REQUESTED",
          "APPROVAL_EXPIRED",
          "APPROVAL_APPROVED",
          "APPROVAL_REJECTED",
          "approval:requested",
          "approval:resolved",
          "approval:expired",
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
      skipSessionFocus,
    ]
  );

  const resetConversation = useCallback(async () => {
    const currentTaskId = taskId;
    // 主动切换会话时，先清掉 placeholder 引用，抑制 closeStream 的兜底拉详情，
    // 避免无谓地对旧 taskId 发请求
    activeAgentMessageIdRef.current = null;
    closeStream();
    attemptedRestoreSignatureRef.current = null;
    setSuppressedRestoreSignature(restoreTaskIdSignature);
    setMessages([]);
    setError(null);
    setRestoring(false);
    setTaskId(null);
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
      // 卸载时抑制 closeStream 的兜底拉详情，避免在已卸载组件上 setMessages
      activeAgentMessageIdRef.current = null;
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
    liveApproval,
    approvalSignal,
  };
}
