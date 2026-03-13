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
  /** 是否正在恢复历史对话 */
  restoring: boolean;
  /** 错误信息 */
  error: string | null;
  /** 发送消息 */
  sendMessage: (text: string, options?: ChatSendOptions) => Promise<void>;
  /** 当前 task ID */
  taskId: string | null;
}

export interface ChatRestoreTarget {
  taskIds: string[];
}

export interface ChatSendOptions {
  agentProfileId?: string | null;
}

const ACTIVE_CHAT_TASK_STORAGE_KEY = "octoagent.chat.activeTaskId";
const AGENT_STREAM_PLACEHOLDER = "主助手已接手，正在处理这条消息…";

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

function normalizeTaskId(taskId: string | null | undefined): string | null {
  if (!taskId) {
    return null;
  }
  const normalized = taskId.trim();
  return normalized ? normalized : null;
}

function buildRestoreCandidateTaskIds(
  currentTaskId: string | null,
  restoreTarget: ChatRestoreTarget | null
): string[] {
  const candidates: string[] = [];
  const seen = new Set<string>();

  for (const value of [currentTaskId, ...(restoreTarget?.taskIds ?? [])]) {
    const normalized = normalizeTaskId(value);
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    candidates.push(normalized);
  }

  return candidates;
}

function extractAgentMessage(
  eventData: Pick<SSEEventData, "payload"> & { type: string }
): string {
  const payload = extractEventPayload(eventData);
  if (!payload) {
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

function extractEventPayload(
  eventData: Pick<SSEEventData, "payload"> & { type: string }
): Record<string, unknown> | null {
  const payload = eventData.payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return null;
  }
  return payload;
}

function extractArtifactRef(
  eventData: Pick<SSEEventData, "payload"> & { type: string }
): string {
  const payload = extractEventPayload(eventData);
  if (!payload) {
    return "";
  }
  const artifactRef = payload.artifact_ref;
  return typeof artifactRef === "string" ? artifactRef.trim() : "";
}

function isUserVisibleModelEvent(
  eventData: Pick<SSEEventData, "payload"> & { type: string }
): boolean {
  const payload = extractEventPayload(eventData);
  if (!payload) {
    return true;
  }
  const skillId = typeof payload.skill_id === "string" ? payload.skill_id.trim() : "";
  const artifactRef = typeof payload.artifact_ref === "string" ? payload.artifact_ref.trim() : "";
  return !skillId || Boolean(artifactRef);
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

function normalizeFailureMessage(raw: string): string {
  const message = raw.trim();
  if (!message) {
    return "";
  }
  if (
    /城市|区县|地点|位置|补充|缺少|missing|clarify|location|city|where/i.test(message)
  ) {
    return "还差一条关键信息才能继续，请补充地点或你要查询的对象。";
  }
  if (
    /browser|web|network|proxy|docker|backend|connection|timeout|EOF|unreachable|fetch/i.test(
      message
    )
  ) {
    return "这次卡在当前工具或运行环境上了，不是你不会问。稍后重试，或先检查联网和后台连接。";
  }
  if (/degrad|fallback|降级/i.test(message)) {
    return "系统当前在降级运行，这次结果可能不完整。";
  }
  return message;
}

function extractFailureMessage(
  eventData: Pick<SSEEventData, "payload"> & { type: string }
): string {
  const payload = extractEventPayload(eventData);
  if (!payload) {
    return "本次回复没有成功完成，请稍后重试。";
  }
  const candidates = [
    payload.user_message,
    payload.message,
    payload.error,
    payload.reason,
    payload.detail,
    payload.response_summary,
    payload.summary,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) {
      return (
        normalizeFailureMessage(candidate) || "本次回复没有成功完成，请稍后重试。"
      );
    }
  }
  return "本次回复没有成功完成，请稍后重试。";
}

function buildMessagesFromTaskDetail(detail: TaskDetailResponse): ChatMessage[] {
  const llmArtifacts = detail.artifacts.filter((artifact) => artifact.name === "llm-response");
  const llmArtifactsById = new Map(llmArtifacts.map((artifact) => [artifact.artifact_id, artifact]));
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
      if (!isUserVisibleModelEvent(event as TaskEvent & { type: string })) {
        continue;
      }
      const artifactRef = extractArtifactRef(event as TaskEvent & { type: string });
      const artifact =
        (artifactRef ? llmArtifactsById.get(artifactRef) : undefined) ?? llmArtifacts[artifactIndex];
      const content =
        extractAgentMessage(event as TaskEvent & { type: string }) || extractArtifactText(artifact);
      if (!artifactRef && artifactIndex < llmArtifacts.length) {
        artifactIndex += 1;
      }
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
      if (!isUserVisibleModelEvent(event as TaskEvent & { type: string })) {
        continue;
      }
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
