import type { Artifact, SSEEventData, TaskDetailResponse, TaskEvent } from "../types";
import type { ChatMessage, ChatRestoreTarget } from "./chatStreamTypes";

export const ACTIVE_CHAT_TASK_STORAGE_KEY = "octoagent.chat.activeTaskId";
export const AGENT_STREAM_PLACEHOLDER = "主助手已接手，正在处理这条消息…";

export function readStoredTaskId(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  const raw = window.sessionStorage.getItem(ACTIVE_CHAT_TASK_STORAGE_KEY);
  return raw && raw.trim() ? raw.trim() : null;
}

export function persistTaskId(taskId: string | null): void {
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

export function buildRestoreCandidateTaskIds(
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

function extractEventPayload(
  eventData: Pick<SSEEventData, "payload"> & { type: string }
): Record<string, unknown> | null {
  const payload = eventData.payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return null;
  }
  return payload;
}

export function extractAgentMessage(
  eventData: Pick<SSEEventData, "payload"> & { type: string }
): string {
  const payload = extractEventPayload(eventData);
  if (!payload) {
    return "";
  }
  if (typeof payload.response === "string" && payload.response.trim()) {
    return payload.response;
  }
  if (typeof payload.response_summary === "string" && payload.response_summary.trim()) {
    return payload.response_summary;
  }
  if (typeof payload.summary === "string" && payload.summary.trim()) {
    return payload.summary;
  }
  return "";
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

export function isUserVisibleModelEvent(
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
  if (/城市|区县|地点|位置|补充|缺少|missing|clarify|location|city|where/i.test(message)) {
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

export function extractFailureMessage(
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
      return normalizeFailureMessage(candidate) || "本次回复没有成功完成，请稍后重试。";
    }
  }
  return "本次回复没有成功完成，请稍后重试。";
}

export function buildMessagesFromTaskDetail(detail: TaskDetailResponse): ChatMessage[] {
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
