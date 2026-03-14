/**
 * useChatStream Hook -- T047
 *
 * SSE EventSource 订阅 message:chunk/message:complete + 流式内容拼接
 * + 审批通知检测 + 重连逻辑
 * 对齐 FR-024
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { buildFrontDoorSseUrl, fetchTaskDetail, frontDoorRequest } from "../api/client";
import type { SSEEventData } from "../types";
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
  /** 当前 task ID */
  taskId: string | null;
}

export function useChatStream(restoreTarget: ChatRestoreTarget | null = null): UseChatStreamReturn {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(() => readStoredTaskId());
  const eventSourceRef = useRef<EventSource | null>(null);
  const attemptedRestoreSignatureRef = useRef<string | null>(null);
  const restoreTaskIdSignature = (restoreTarget?.taskIds ?? []).join("|");

  /** 关闭 EventSource */
  const closeStream = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setStreaming(false);
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
        const resp = await frontDoorRequest("/api/chat/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: text,
            task_id: taskId,
            agent_profile_id: options?.agentProfileId?.trim() || undefined,
          }),
        });

        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}`);
        }

        const data = await resp.json();
        const newTaskId = data.task_id;
        setTaskId(newTaskId);

        closeStream();

        // 创建 Agent 占位消息
        const agentMsgId = `agent-${Date.now()}`;
        const agentMsg: ChatMessage = {
          id: agentMsgId,
          role: "agent",
          content: AGENT_STREAM_PLACEHOLDER,
          isStreaming: true,
        };
        setMessages((prev) => [...prev, agentMsg]);
        setStreaming(true);

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

            // 检查是否是模型回复事件
            if (eventData.type === "MODEL_CALL_COMPLETED" && isUserVisibleModelEvent(eventData)) {
              const content = extractAgentMessage(eventData);
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === agentMsgId
                    ? {
                        ...msg,
                        content: content || "已收到回复，但没有可显示的正文。",
                        isStreaming: false,
                      }
                    : msg
                )
              );
              setStreaming(false);
            }

            if (
              (eventData.type === "MODEL_CALL_FAILED" && isUserVisibleModelEvent(eventData)) ||
              eventData.type === "ERROR"
            ) {
              const failureMessage = extractFailureMessage(eventData);
              setError(failureMessage);
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === agentMsgId
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
              setStreaming(false);
            }

            // 检查审批请求
            if (
              eventData.type === "approval:requested" ||
              eventData.type === "APPROVAL_REQUESTED"
            ) {
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === agentMsgId
                    ? { ...msg, hasApproval: true }
                    : msg
                )
              );
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
                msg.id === agentMsgId
                  ? { ...msg, isStreaming: false }
                  : msg
              )
            );
          }
        };
      } catch (err) {
        setError(err instanceof Error ? err.message : "发送失败");
        setStreaming(false);
      }
    },
    [taskId, closeStream]
  );

  // 组件卸载时清理
  useEffect(() => {
    let cancelled = false;

    async function restoreConversation() {
      const candidateTaskIds = buildRestoreCandidateTaskIds(taskId, restoreTarget);
      if (candidateTaskIds.length === 0 || messages.length > 0 || streaming) {
        setRestoring(false);
        return;
      }
      const restoreSignature = candidateTaskIds.join("|");
      if (attemptedRestoreSignatureRef.current === restoreSignature) {
        return;
      }
      attemptedRestoreSignatureRef.current = restoreSignature;
      setRestoring(true);
      try {
        for (const candidateTaskId of candidateTaskIds) {
          try {
            const detail = await fetchTaskDetail(candidateTaskId);
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
  }, [messages.length, restoreTaskIdSignature, streaming, taskId]);

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
    taskId,
  };
}
