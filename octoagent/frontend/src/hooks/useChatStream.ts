/**
 * useChatStream Hook -- T047
 *
 * SSE EventSource 订阅 message:chunk/message:complete + 流式内容拼接
 * + 审批通知检测 + 重连逻辑
 * 对齐 FR-024
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { buildFrontDoorSseUrl, fetchTaskDetail, frontDoorRequest } from "../api/client";
import type { Artifact, SSEEventData, TaskDetailResponse, TaskEvent } from "../types";

/** 消息角色 */
export type MessageRole = "user" | "agent";

/** 聊天消息 */
export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  isStreaming: boolean;
  /** 是否包含审批提示 */
  hasApproval?: boolean;
}

/** Hook 返回值 */
export interface UseChatStreamReturn {
  /** 消息列表 */
  messages: ChatMessage[];
  /** 是否正在流式接收 */
  streaming: boolean;
  /** 错误信息 */
  error: string | null;
  /** 发送消息 */
  sendMessage: (text: string) => Promise<void>;
  /** 当前 task ID */
  taskId: string | null;
}

export interface ChatRestoreTarget {
  taskId: string;
}

const ACTIVE_CHAT_TASK_STORAGE_KEY = "octoagent.chat.activeTaskId";

function readStoredTaskId(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  const raw = window.sessionStorage.getItem(ACTIVE_CHAT_TASK_STORAGE_KEY);
  return raw && raw.trim() ? raw.trim() : null;
}

function persistTaskId(taskId: string | null): void {
  if (typeof window === "undefined") {
    return;
  }
  if (taskId && taskId.trim()) {
    window.sessionStorage.setItem(ACTIVE_CHAT_TASK_STORAGE_KEY, taskId);
    return;
  }
  window.sessionStorage.removeItem(ACTIVE_CHAT_TASK_STORAGE_KEY);
}

function extractAgentMessage(
  eventData: Pick<SSEEventData, "payload"> & { type: string }
): string {
  const payload = eventData.payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return "";
  }
  if (typeof payload.response === "string" && payload.response.trim()) {
    return payload.response;
  }
  if (
    typeof payload.response_summary === "string" &&
    payload.response_summary.trim()
  ) {
    return payload.response_summary;
  }
  if (typeof payload.summary === "string" && payload.summary.trim()) {
    return payload.summary;
  }
  return "";
}

function extractUserMessage(event: TaskEvent): string {
  const payload = event.payload;
  if (typeof payload.text === "string" && payload.text.trim()) {
    return payload.text;
  }
  if (typeof payload.text_preview === "string" && payload.text_preview.trim()) {
    return payload.text_preview;
  }
  return "";
}

function extractArtifactText(artifact: Artifact | undefined): string {
  if (!artifact) {
    return "";
  }
  for (const part of artifact.parts) {
    if (typeof part.content === "string" && part.content.trim()) {
      return part.content;
    }
  }
  return "";
}

function buildMessagesFromTaskDetail(detail: TaskDetailResponse): ChatMessage[] {
  const llmArtifacts = detail.artifacts.filter((artifact) => artifact.name === "llm-response");
  const orderedEvents = [...detail.events].sort((left, right) => left.task_seq - right.task_seq);
  const restored: ChatMessage[] = [];
  let artifactIndex = 0;

  for (const event of orderedEvents) {
    if (event.type === "USER_MESSAGE") {
      const content = extractUserMessage(event);
      if (!content) {
        continue;
      }
      restored.push({
        id: `restore-user-${event.event_id}`,
        role: "user",
        content,
        isStreaming: false,
      });
      continue;
    }

    if (event.type === "MODEL_CALL_COMPLETED") {
      const content =
        extractAgentMessage(event as TaskEvent & { type: string }) ||
        extractArtifactText(llmArtifacts[artifactIndex]);
      artifactIndex += 1;
      if (!content) {
        continue;
      }
      restored.push({
        id: `restore-agent-${event.event_id}`,
        role: "agent",
        content,
        isStreaming: false,
      });
      continue;
    }

    if (event.type === "MODEL_CALL_FAILED") {
      restored.push({
        id: `restore-agent-${event.event_id}`,
        role: "agent",
        content: "本次回复失败，请重试。",
        isStreaming: false,
      });
    }
  }

  return restored;
}

export function useChatStream(restoreTarget: ChatRestoreTarget | null = null): UseChatStreamReturn {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(() => readStoredTaskId());
  const eventSourceRef = useRef<EventSource | null>(null);
  const restoredTaskIdRef = useRef<string | null>(null);

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
    async (text: string) => {
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
        const resp = await frontDoorRequest("/api/chat/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: text,
            task_id: taskId,
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
          content: "",
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
            if (eventData.type === "MODEL_CALL_COMPLETED") {
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

            if (eventData.type === "MODEL_CALL_FAILED" || eventData.type === "ERROR") {
              setError("本次回复没有成功完成，请稍后重试。");
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === agentMsgId
                    ? {
                        ...msg,
                        content: msg.content || "本次回复失败，请重试。",
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
      const candidateTaskId = taskId || restoreTarget?.taskId || "";
      if (!candidateTaskId || messages.length > 0 || streaming) {
        return;
      }
      if (restoredTaskIdRef.current === candidateTaskId) {
        return;
      }
      restoredTaskIdRef.current = candidateTaskId;
      try {
        const detail = await fetchTaskDetail(candidateTaskId);
        if (cancelled) {
          return;
        }
        setTaskId(candidateTaskId);
        setMessages(buildMessagesFromTaskDetail(detail));
      } catch {
        if (!cancelled) {
          if (taskId === candidateTaskId) {
            setTaskId(null);
          }
          persistTaskId(null);
          setMessages([]);
        }
      }
    }

    void restoreConversation();
    return () => {
      cancelled = true;
    };
  }, [messages.length, restoreTarget?.taskId, streaming, taskId]);

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
    error,
    sendMessage,
    taskId,
  };
}
