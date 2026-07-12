/**
 * useChatStream Hook -- T047
 *
 * SSE EventSource 订阅 message:chunk/message:complete + 流式内容拼接
 * + 审批通知检测 + 重连逻辑
 * 对齐 FR-024
 *
 * F143：事件分支逻辑下沉到 chatStreamReducer.ts 纯函数，本文件只剩接线
 * （SSE 连接生命周期 / REST 调用 / state 原子应用）。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  buildFrontDoorSseUrl,
  executeControlAction,
  fetchTaskDetail,
  frontDoorRequest,
} from "../api/client";
import {
  buildMessagesFromTaskDetail,
  buildPendingConversationScope,
  buildRestoreCandidateTaskIds,
  fetchTaskDetailWithTimeout,
  fillPendingAgentMessage,
  findLastAgentContentInCurrentTurn,
  makeControlActionRequest,
  makePlaceholderId,
  normalizeTaskId,
  persistTaskId,
  readStoredTaskId,
} from "./chatStreamHelpers";
import {
  CHAT_STREAM_EVENT_TYPES,
  applyMessageOps,
  deriveChatStreamEventOutcome,
  deriveStreamClosedOutcome,
  makeAgentPlaceholderMessage,
  parseChatStreamEvent,
  type ChatStreamEventOutcome,
} from "./chatStreamReducer";
import type {
  ChatMessage,
  ChatRestoreTarget,
  ChatSendOptions,
  ChatSessionScopeSnapshot,
  PendingConversationScope,
  SSEApprovalSnapshot,
} from "./chatStreamTypes";
// prettier-ignore
export type { ChatMessage, ChatRestoreTarget, ChatSendOptions, ChatSessionScopeSnapshot, MessageRole, SSEApprovalSnapshot } from "./chatStreamTypes";

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
    const placeholderId = makePlaceholderId();
    activeAgentMessageIdRef.current = placeholderId;
    setMessages((prev) => [...prev, makeAgentPlaceholderMessage(placeholderId)]);
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
        setMessages((prev) => fillPendingAgentMessage(prev, msgId, lastAgentContent));
      })
      .catch(() => {
        // 兜底失败保持当前状态即可，不再冒泡错误
      });
  }, []);

  /** reducer outcome 应用到分布式 state 原子（messages 走函数式更新防 stale） */
  const applyStreamOutcome = useCallback(
    (outcome: ChatStreamEventOutcome) => {
      activeAgentMessageIdRef.current = outcome.nextActiveAgentMessageId;
      if (outcome.messageOps.length > 0) {
        setMessages((prev) => applyMessageOps(prev, outcome.messageOps));
      }
      if (outcome.streaming !== undefined) {
        setStreaming(outcome.streaming);
      }
      if (outcome.error !== undefined) {
        setError(outcome.error);
      }
      if (outcome.liveApproval !== undefined) {
        setLiveApproval(outcome.liveApproval);
      }
      if (outcome.approvalBump) {
        setApprovalSignal((n) => n + 1);
      }
      if (outcome.shouldCloseStream) {
        closeStream();
      }
    },
    [closeStream]
  );

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

        // 事件分支逻辑全部在 chatStreamReducer（纯函数），这里只做接线
        const handleEvent = (e: MessageEvent) => {
          const event = parseChatStreamEvent(e.data);
          if (!event) {
            return; // 心跳 / malformed，忽略
          }
          applyStreamOutcome(
            deriveChatStreamEventOutcome(
              event,
              activeAgentMessageIdRef.current,
              makePlaceholderId()
            )
          );
        };

        // 监听各类事件（含审批全生命周期）
        for (const type of CHAT_STREAM_EVENT_TYPES) {
          es.addEventListener(type, handleEvent);
        }
        es.onmessage = handleEvent;

        es.onerror = () => {
          if (es.readyState === EventSource.CLOSED) {
            applyStreamOutcome(deriveStreamClosedOutcome(activeAgentMessageIdRef.current));
          }
        };
      } catch (err) {
        setError(err instanceof Error ? err.message : "发送失败");
        setStreaming(false);
      }
    },
    [
      taskId,
      applyStreamOutcome,
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
