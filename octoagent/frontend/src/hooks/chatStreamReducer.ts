/**
 * Chat SSE 事件分支纯 reducer —— F143 件 1
 *
 * useChatStream 的 handleEvent 内联分支下沉为纯函数：
 *   (state, rawEvent) → state
 * 零 setState / ref / DOM / 网络 / 存储副作用；不确定性输入（占位消息 id）
 * 由调用方预生成注入。事件分支逻辑以本文件为唯一事实源：
 *   - hook 接线走 parseChatStreamEvent + deriveChatStreamEventOutcome +
 *     applyMessageOps（分布式 state 原子）；
 *   - L4 测试走 reduceChatStreamEvent（同一 derive/apply 的组合形态，
 *     不存在第二份分支实现）。
 */

import {
  AGENT_STREAM_PLACEHOLDER,
  extractAgentMessage,
  extractFailureMessage,
  isUserVisibleModelEvent,
} from "./chatStreamHelpers";
import type { ChatMessage, SSEApprovalSnapshot } from "./chatStreamTypes";

/** COMPLETED 事件正文为空时的兜底文案（复刻 baseline 行为） */
export const EMPTY_COMPLETION_FALLBACK_TEXT = "已收到回复，但没有可显示的正文。";

/** hook 需要监听的全部 SSE 事件类型（含审批全生命周期） */
export const CHAT_STREAM_EVENT_TYPES = [
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
] as const;

/** handleEvent 触碰的全部状态原子（原 activeAgentMessageIdRef 收编为字段） */
export interface ChatStreamEventState {
  messages: ChatMessage[];
  streaming: boolean;
  error: string | null;
  liveApproval: SSEApprovalSnapshot | null;
  approvalSignal: number;
  activeAgentMessageId: string | null;
}

/** 消息数组纯变换操作（封闭枚举） */
export type ChatMessageOp =
  | { kind: "appendPlaceholder"; messageId: string }
  | { kind: "complete"; messageId: string; content: string }
  | { kind: "markFailed"; messageId: string; failureMessage: string }
  | { kind: "markApproval"; messageId: string }
  | { kind: "clearStreaming"; messageId: string };

/**
 * 单事件派生结果。undefined 字段语义 = 保持不变；
 * liveApproval 的 null 是显式清除（区别于 undefined 不动）。
 */
export interface ChatStreamEventOutcome {
  nextActiveAgentMessageId: string | null;
  messageOps: ChatMessageOp[];
  streaming?: boolean;
  error?: string;
  liveApproval?: SSEApprovalSnapshot | null;
  approvalBump: boolean;
  shouldCloseStream: boolean;
}

export function makeAgentPlaceholderMessage(messageId: string): ChatMessage {
  return {
    id: messageId,
    role: "agent",
    content: AGENT_STREAM_PLACEHOLDER,
    isStreaming: true,
  };
}

/**
 * SSE 原始 data 解析。malformed（心跳等）与非对象 JSON 一律返回 null：
 * baseline 对 null JSON 依赖 try/catch 吞异常、对原始值/数组走空分支，
 * 二者净效果均为忽略，这里收敛为统一的 null。
 */
export function parseChatStreamEvent(raw: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return null;
    }
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** 从 approval:requested / APPROVAL_REQUESTED 的 payload 构造审批快照（无 approval_id 返回 null） */
export function buildLiveApprovalSnapshot(payload: unknown): SSEApprovalSnapshot | null {
  const p = (payload ?? {}) as Record<string, unknown>;
  const approvalId = typeof p.approval_id === "string" ? p.approval_id : "";
  if (!approvalId) {
    return null;
  }
  return {
    approvalId,
    taskId: typeof p.task_id === "string" ? p.task_id : "",
    toolName: typeof p.tool_name === "string" ? p.tool_name : "",
    toolArgsSummary: typeof p.tool_args_summary === "string" ? p.tool_args_summary : "",
    riskExplanation: typeof p.risk_explanation === "string" ? p.risk_explanation : "",
    sideEffectLevel: typeof p.side_effect_level === "string" ? p.side_effect_level : "",
    createdAt: typeof p.created_at === "string" ? p.created_at : "",
    expiresAt: typeof p.expires_at === "string" ? p.expires_at : "",
  };
}

/**
 * 事件分支唯一事实源。按 baseline handleEvent 相同顺序的 sequential-if
 * 复刻六类分支；占位消息 id 由调用方预生成注入（nextPlaceholderId），
 * 单个事件至多消费一次。
 */
export function deriveChatStreamEventOutcome(
  event: Record<string, unknown>,
  activeAgentMessageId: string | null,
  nextPlaceholderId: string
): ChatStreamEventOutcome {
  const type = typeof event.type === "string" ? event.type : "";
  const extractable = { type, payload: event.payload } as Parameters<
    typeof isUserVisibleModelEvent
  >[0];

  const messageOps: ChatMessageOp[] = [];
  let nextActiveId = activeAgentMessageId;
  let streaming: boolean | undefined;
  let error: string | undefined;
  let liveApproval: SSEApprovalSnapshot | null | undefined;
  let approvalBump = false;

  /** 等价 baseline ensureActiveAgentPlaceholder：无活跃占位则 spawn（附带 streaming=true） */
  const ensurePlaceholder = (): string => {
    if (nextActiveId) {
      return nextActiveId;
    }
    nextActiveId = nextPlaceholderId;
    messageOps.push({ kind: "appendPlaceholder", messageId: nextPlaceholderId });
    streaming = true;
    return nextPlaceholderId;
  };

  if (type === "MODEL_CALL_STARTED" && isUserVisibleModelEvent(extractable)) {
    ensurePlaceholder();
  }

  if (type === "MODEL_CALL_COMPLETED" && isUserVisibleModelEvent(extractable)) {
    const content = extractAgentMessage(extractable);
    const messageId = ensurePlaceholder();
    messageOps.push({
      kind: "complete",
      messageId,
      content: content || EMPTY_COMPLETION_FALLBACK_TEXT,
    });
    nextActiveId = null;
    streaming = false;
  }

  if ((type === "MODEL_CALL_FAILED" && isUserVisibleModelEvent(extractable)) || type === "ERROR") {
    const failureMessage = extractFailureMessage(extractable);
    const messageId = ensurePlaceholder();
    error = failureMessage;
    messageOps.push({ kind: "markFailed", messageId, failureMessage });
    nextActiveId = null;
    streaming = false;
  }

  if (type === "approval:requested" || type === "APPROVAL_REQUESTED") {
    const messageId = ensurePlaceholder();
    messageOps.push({ kind: "markApproval", messageId });
    streaming = true;
    const snapshot = buildLiveApprovalSnapshot(event.payload);
    if (snapshot) {
      liveApproval = snapshot;
      approvalBump = true;
    }
  }

  if (
    type === "APPROVAL_EXPIRED" ||
    type === "APPROVAL_APPROVED" ||
    type === "APPROVAL_REJECTED" ||
    type === "approval:resolved" ||
    type === "approval:expired"
  ) {
    liveApproval = null;
    approvalBump = true;
  }

  return {
    nextActiveAgentMessageId: nextActiveId,
    messageOps,
    streaming,
    error,
    liveApproval,
    approvalBump,
    shouldCloseStream: Boolean(event.final),
  };
}

/**
 * EventSource onerror + readyState CLOSED 分支的纯化。
 *
 * 显式声明的行为修复（spec §7 HIGH 闭环）：baseline 在 updater 闭包内读
 * activeAgentMessageIdRef.current、随后同步清 null——React 18 批处理下
 * updater 执行时 ref 已为 null，清 isStreaming 沦为 no-op。此处先快照
 * id 再派 op，确定性清掉活跃消息的 isStreaming（即代码原本的意图）。
 */
export function deriveStreamClosedOutcome(
  activeAgentMessageId: string | null
): ChatStreamEventOutcome {
  return {
    nextActiveAgentMessageId: null,
    messageOps: activeAgentMessageId
      ? [{ kind: "clearStreaming", messageId: activeAgentMessageId }]
      : [],
    streaming: false,
    approvalBump: false,
    shouldCloseStream: false,
  };
}

/** 消息数组纯变换（op 逐个按序应用） */
export function applyMessageOps(
  messages: ChatMessage[],
  ops: ChatMessageOp[]
): ChatMessage[] {
  let next = messages;
  for (const op of ops) {
    if (op.kind === "appendPlaceholder") {
      next = [...next, makeAgentPlaceholderMessage(op.messageId)];
      continue;
    }
    next = next.map((msg) => {
      if (msg.id !== op.messageId) {
        return msg;
      }
      if (op.kind === "complete") {
        return { ...msg, content: op.content, isStreaming: false };
      }
      if (op.kind === "markFailed") {
        return {
          ...msg,
          content:
            msg.isStreaming || msg.content === AGENT_STREAM_PLACEHOLDER
              ? op.failureMessage
              : msg.content || op.failureMessage,
          isStreaming: false,
        };
      }
      if (op.kind === "markApproval") {
        return { ...msg, hasApproval: true, isStreaming: true };
      }
      return { ...msg, isStreaming: false };
    });
  }
  return next;
}

/** outcome 折叠到完整 state（与 hook 侧分布式原子应用同语义） */
export function applyChatStreamOutcome(
  state: ChatStreamEventState,
  outcome: ChatStreamEventOutcome
): ChatStreamEventState {
  return {
    messages:
      outcome.messageOps.length > 0
        ? applyMessageOps(state.messages, outcome.messageOps)
        : state.messages,
    streaming: outcome.streaming ?? state.streaming,
    error: outcome.error !== undefined ? outcome.error : state.error,
    liveApproval:
      outcome.liveApproval !== undefined ? outcome.liveApproval : state.liveApproval,
    approvalSignal: state.approvalSignal + (outcome.approvalBump ? 1 : 0),
    activeAgentMessageId: outcome.nextActiveAgentMessageId,
  };
}

export interface ChatStreamReduceResult {
  state: ChatStreamEventState;
  shouldCloseStream: boolean;
}

/**
 * 组合形态纯 reducer：(state, rawEvent) → state。
 * shouldCloseStream 是副作用指令（EventSource 关闭 + 兜底拉取），由调用方执行。
 */
export function reduceChatStreamEvent(
  state: ChatStreamEventState,
  raw: string,
  nextPlaceholderId: string
): ChatStreamReduceResult {
  const event = parseChatStreamEvent(raw);
  if (!event) {
    return { state, shouldCloseStream: false };
  }
  const outcome = deriveChatStreamEventOutcome(
    event,
    state.activeAgentMessageId,
    nextPlaceholderId
  );
  return {
    state: applyChatStreamOutcome(state, outcome),
    shouldCloseStream: outcome.shouldCloseStream,
  };
}
