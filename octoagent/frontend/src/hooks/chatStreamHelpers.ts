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
    return sanitizeAgentVisibleText(payload.response);
  }
  if (typeof payload.response_summary === "string" && payload.response_summary.trim()) {
    return sanitizeAgentVisibleText(payload.response_summary);
  }
  if (typeof payload.summary === "string" && payload.summary.trim()) {
    return sanitizeAgentVisibleText(payload.summary);
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
  // 有 skill_id 但无 artifact_ref 的内部调用（如 inline skill 内层）不展示
  if (skillId && !artifactRef) {
    return false;
  }
  // 检查 response_summary 是否是内部结构化 JSON（如 memory recall plan）
  const responseSummary =
    typeof payload.response_summary === "string" ? payload.response_summary.trim() : "";
  if (responseSummary && looksLikeInternalJsonResponse(responseSummary)) {
    return false;
  }
  return true;
}

/**
 * 检测文本是否是内部结构化 JSON 响应（如 memory recall plan）。
 * 整段文本是一个 JSON 对象即判定为内部响应，不应直接展示给用户。
 */
function looksLikeInternalJsonResponse(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) {
    return false;
  }
  try {
    const parsed = JSON.parse(trimmed);
    return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed);
  } catch {
    return false;
  }
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
      return sanitizeAgentVisibleText(part.content);
    }
  }
  return "";
}

export function sanitizeAgentVisibleText(raw: string): string {
  const normalized = String(raw || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
  if (!normalized) {
    return "";
  }
  const toolTranscriptPattern = /\bto=[a-z0-9_.-]+/i;
  if (!toolTranscriptPattern.test(normalized)) {
    return normalized;
  }
  const rawJsonMarkers = [
    '"query":',
    '"matches":',
    '"memories":',
    '"scopes":',
    '"result":',
    '"ids":',
  ];

  const kept: string[] = [];
  let skipBlockKind: "" | "fence" | "json" = "";
  let jsonBalance = 0;
  let pendingJsonAfterTool = false;

  const isJsonishLine = (value: string): boolean => {
    const stripped = value.trim();
    if (!stripped) {
      return false;
    }
    if (stripped.startsWith("```")) {
      return true;
    }
    if (/^[\[{]/.test(stripped) || /^[\]}]/.test(stripped)) {
      return true;
    }
    if (rawJsonMarkers.some((marker) => stripped.includes(marker))) {
      return true;
    }
    return /^".+?:/.test(stripped);
  };

  for (const rawLine of normalized.split("\n")) {
    const line = rawLine.trimEnd();
    const stripped = line.trim();
    if (!stripped) {
      if (!pendingJsonAfterTool && !skipBlockKind && kept.length > 0 && kept[kept.length - 1] !== "") {
        kept.push("");
      }
      continue;
    }

    if (skipBlockKind === "fence") {
      if (stripped.startsWith("```")) {
        skipBlockKind = "";
      }
      continue;
    }

    if (skipBlockKind === "json") {
      jsonBalance += (stripped.match(/[\[{]/g) ?? []).length;
      jsonBalance -= (stripped.match(/[\]}]/g) ?? []).length;
      if (jsonBalance <= 0) {
        skipBlockKind = "";
      }
      continue;
    }

    const transcriptMatch = line.match(toolTranscriptPattern);
    if (transcriptMatch && transcriptMatch.index !== undefined) {
      const prefix = line.slice(0, transcriptMatch.index).trim();
      if (prefix && !isJsonishLine(prefix)) {
        kept.push(prefix);
      }
      pendingJsonAfterTool = true;
      continue;
    }

    if (pendingJsonAfterTool) {
      if (isJsonishLine(stripped)) {
        if (stripped.startsWith("```")) {
          skipBlockKind = "fence";
        } else if (/^[\[{]/.test(stripped)) {
          jsonBalance = (stripped.match(/[\[{]/g) ?? []).length - (stripped.match(/[\]}]/g) ?? []).length;
          if (jsonBalance > 0) {
            skipBlockKind = "json";
          }
        }
        continue;
      }
      pendingJsonAfterTool = false;
    }

    kept.push(line);
  }

  const sanitized = kept.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  return sanitized || normalized;
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

/**
 * 在"当前轮次"内找到最近一条非空 agent 回复。
 *
 * 续聊场景下同一个 task 会累积多轮消息，直接取整条历史的最后一条 agent
 * 会在当前轮无新回复时把上一轮的答复误贴到新问题下面。限定"最后一条
 * USER_MESSAGE 之后"可以保证只读当前轮次的 agent 输出；若当前轮还没有
 * agent 产出就返回空串，由调用方决定是否保留 placeholder。
 */
export function findLastAgentContentInCurrentTurn(messages: ChatMessage[]): string {
  let lastUserIdx = -1;
  for (let idx = messages.length - 1; idx >= 0; idx -= 1) {
    if (messages[idx].role === "user") {
      lastUserIdx = idx;
      break;
    }
  }
  for (let idx = messages.length - 1; idx > lastUserIdx; idx -= 1) {
    const msg = messages[idx];
    if (msg.role === "agent" && typeof msg.content === "string" && msg.content.trim()) {
      return msg.content;
    }
  }
  return "";
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
      // 有 artifact_ref 但不是 llm-response 类型的（如 memory-recall-plan-response），跳过
      if (artifactRef && !llmArtifactsById.has(artifactRef)) {
        continue;
      }
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
