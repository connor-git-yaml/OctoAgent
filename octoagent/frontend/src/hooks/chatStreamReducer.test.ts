/**
 * chatStreamReducer L4 直测 —— F143 AC-1
 *
 * 纯 reducer 按事件序列折叠：不 render hook、不 stub EventSource。
 * 覆盖：乱序 / 漏事件 + final 兜底信号 / 轮边界 / 审批生命周期 /
 * malformed 心跳忽略 / COMPLETED+final 同事件不触发兜底。
 */
import { describe, expect, it } from "vitest";
import { AGENT_STREAM_PLACEHOLDER } from "./chatStreamHelpers";
import {
  EMPTY_COMPLETION_FALLBACK_TEXT,
  applyChatStreamOutcome,
  applyMessageOps,
  deriveStreamClosedOutcome,
  makeAgentPlaceholderMessage,
  parseChatStreamEvent,
  reduceChatStreamEvent,
  type ChatStreamEventState,
} from "./chatStreamReducer";
import type { ChatMessage } from "./chatStreamTypes";

function initialState(overrides: Partial<ChatStreamEventState> = {}): ChatStreamEventState {
  return {
    messages: [],
    streaming: false,
    error: null,
    liveApproval: null,
    approvalSignal: 0,
    activeAgentMessageId: null,
    ...overrides,
  };
}

/** 事件序列折叠：占位 id 依次 ph-1 / ph-2 …，返回终态与每步 close 指令 */
function runSequence(
  state: ChatStreamEventState,
  events: Array<Record<string, unknown>>
): { state: ChatStreamEventState; closeSignals: boolean[] } {
  const closeSignals: boolean[] = [];
  let next = state;
  let placeholderSeq = 0;
  for (const event of events) {
    placeholderSeq += 1;
    const result = reduceChatStreamEvent(next, JSON.stringify(event), `ph-${placeholderSeq}`);
    next = result.state;
    closeSignals.push(result.shouldCloseStream);
  }
  return { state: next, closeSignals };
}

function lastMessage(state: ChatStreamEventState): ChatMessage | undefined {
  return state.messages[state.messages.length - 1];
}

describe("parseChatStreamEvent", () => {
  it("malformed JSON（心跳）返回 null", () => {
    expect(parseChatStreamEvent("not-json")).toBeNull();
    expect(parseChatStreamEvent("")).toBeNull();
  });

  it("非对象 JSON（null/原始值/数组）一律忽略", () => {
    expect(parseChatStreamEvent("null")).toBeNull();
    expect(parseChatStreamEvent("42")).toBeNull();
    expect(parseChatStreamEvent('"ping"')).toBeNull();
    expect(parseChatStreamEvent("[1,2]")).toBeNull();
  });

  it("对象 JSON 原样返回", () => {
    expect(parseChatStreamEvent('{"type":"ERROR","final":true}')).toEqual({
      type: "ERROR",
      final: true,
    });
  });
});

describe("reduceChatStreamEvent 单事件分支", () => {
  it("malformed 事件是恒等变换且不发 close 指令", () => {
    const state = initialState({ streaming: true, activeAgentMessageId: "agent-1" });
    const result = reduceChatStreamEvent(state, "not-json", "ph-1");
    expect(result.state).toBe(state);
    expect(result.shouldCloseStream).toBe(false);
  });

  it("可见 MODEL_CALL_STARTED 无活跃占位时 spawn 占位并开流", () => {
    const { state } = runSequence(initialState(), [
      { type: "MODEL_CALL_STARTED", payload: {} },
    ]);
    expect(state.messages).toHaveLength(1);
    expect(lastMessage(state)).toMatchObject({
      id: "ph-1",
      role: "agent",
      content: AGENT_STREAM_PLACEHOLDER,
      isStreaming: true,
    });
    expect(state.streaming).toBe(true);
    expect(state.activeAgentMessageId).toBe("ph-1");
  });

  it("可见 MODEL_CALL_STARTED 已有活跃占位时不重复 spawn 也不改 streaming", () => {
    const seeded = initialState({
      messages: [makeAgentPlaceholderMessage("agent-live")],
      activeAgentMessageId: "agent-live",
      streaming: false,
    });
    const { state } = runSequence(seeded, [{ type: "MODEL_CALL_STARTED", payload: {} }]);
    expect(state.messages).toHaveLength(1);
    expect(state.streaming).toBe(false);
    expect(state.activeAgentMessageId).toBe("agent-live");
  });

  it("内部调用（skill_id 无 artifact_ref）的 STARTED/COMPLETED 不可见、整体忽略", () => {
    const { state } = runSequence(initialState(), [
      { type: "MODEL_CALL_STARTED", payload: { skill_id: "chat.general.inline" } },
      {
        type: "MODEL_CALL_COMPLETED",
        payload: { skill_id: "chat.general.inline", response_summary: "内部结果" },
      },
    ]);
    expect(state.messages).toHaveLength(0);
    expect(state.streaming).toBe(false);
    expect(state.activeAgentMessageId).toBeNull();
  });

  it("COMPLETED 正文为空时写入兜底文案", () => {
    const { state } = runSequence(initialState(), [
      { type: "MODEL_CALL_COMPLETED", payload: {} },
    ]);
    expect(lastMessage(state)?.content).toBe(EMPTY_COMPLETION_FALLBACK_TEXT);
    expect(lastMessage(state)?.isStreaming).toBe(false);
  });

  it("ERROR 事件与可见 FAILED 同语义：写失败文案 + 置 error + 收流", () => {
    const { state } = runSequence(initialState(), [
      { type: "ERROR", payload: { error: "backend connection timeout" } },
    ]);
    expect(state.error).toBe(
      "这次卡在当前工具或运行环境上了，不是你不会问。稍后重试，或先检查联网和后台连接。"
    );
    expect(lastMessage(state)?.content).toBe(state.error);
    expect(state.streaming).toBe(false);
    expect(state.activeAgentMessageId).toBeNull();
  });
});

describe("事件序列（乱序 / 漏事件 / 轮边界）", () => {
  it("典型一轮：STARTED → COMPLETED 替换同一占位", () => {
    const { state, closeSignals } = runSequence(initialState(), [
      { type: "MODEL_CALL_STARTED", payload: {} },
      { type: "MODEL_CALL_COMPLETED", payload: { response_summary: "答案在此。" }, final: true },
    ]);
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]).toMatchObject({
      id: "ph-1",
      content: "答案在此。",
      isStreaming: false,
    });
    expect(state.streaming).toBe(false);
    expect(closeSignals).toEqual([false, true]);
  });

  it("乱序：漏 STARTED 直接 COMPLETED 也能落一条完整回复（自 spawn 自完成）", () => {
    const { state } = runSequence(initialState(), [
      { type: "MODEL_CALL_COMPLETED", payload: { response_summary: "直接完成。" } },
    ]);
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]).toMatchObject({ content: "直接完成。", isStreaming: false });
    expect(state.activeAgentMessageId).toBeNull();
  });

  it("内部 skill 失败不盖最终回复（隐藏失败 → 可见完成）", () => {
    const seeded = initialState({
      messages: [makeAgentPlaceholderMessage("agent-turn")],
      activeAgentMessageId: "agent-turn",
      streaming: true,
    });
    const { state } = runSequence(seeded, [
      {
        type: "MODEL_CALL_FAILED",
        payload: { skill_id: "chat.general.inline", error: "temporary upstream failure" },
        final: false,
      },
      {
        type: "MODEL_CALL_COMPLETED",
        payload: { response_summary: "深圳今天晴，约 20 摄氏度。" },
        final: true,
      },
    ]);
    expect(state.error).toBeNull();
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]).toMatchObject({
      content: "深圳今天晴，约 20 摄氏度。",
      isStreaming: false,
    });
  });

  it("漏 COMPLETED 只来 STATE_TRANSITION final：状态不动、只发 close 兜底信号", () => {
    const seeded = initialState({
      messages: [makeAgentPlaceholderMessage("agent-pending")],
      activeAgentMessageId: "agent-pending",
      streaming: true,
    });
    const result = reduceChatStreamEvent(
      seeded,
      JSON.stringify({
        type: "STATE_TRANSITION",
        payload: { from_status: "RUNNING", to_status: "SUCCEEDED" },
        final: true,
      }),
      "ph-1"
    );
    // 占位保留（等 closeStream 兜底拉详情），活跃 id 不被清——兜底才知道该填谁
    expect(result.state.messages[0]?.content).toBe(AGENT_STREAM_PLACEHOLDER);
    expect(result.state.activeAgentMessageId).toBe("agent-pending");
    expect(result.shouldCloseStream).toBe(true);
  });

  it("COMPLETED+final 同事件：活跃 id 已清 → 调用方 closeStream 不会触发兜底拉取", () => {
    const seeded = initialState({
      messages: [makeAgentPlaceholderMessage("agent-done")],
      activeAgentMessageId: "agent-done",
      streaming: true,
    });
    const result = reduceChatStreamEvent(
      seeded,
      JSON.stringify({
        type: "MODEL_CALL_COMPLETED",
        payload: { response_summary: "完成。" },
        final: true,
      }),
      "ph-1"
    );
    expect(result.shouldCloseStream).toBe(true);
    expect(result.state.activeAgentMessageId).toBeNull();
    expect(result.state.messages[0]?.content).toBe("完成。");
  });

  it("轮边界：上一轮已完成的消息不被第二轮事件改写", () => {
    const firstTurn = runSequence(initialState(), [
      { type: "MODEL_CALL_STARTED", payload: {} },
      { type: "MODEL_CALL_COMPLETED", payload: { response_summary: "第一轮答复。" } },
    ]);
    const withUserTurn: ChatStreamEventState = {
      ...firstTurn.state,
      messages: [
        ...firstTurn.state.messages,
        { id: "user-2", role: "user", content: "第二轮问题", isStreaming: false },
      ],
    };
    const { state } = runSequence(withUserTurn, [
      { type: "MODEL_CALL_STARTED", payload: {} },
      { type: "MODEL_CALL_FAILED", payload: { error: "boom" }, final: true },
    ]);
    expect(state.messages).toHaveLength(3);
    expect(state.messages[0]?.content).toBe("第一轮答复。");
    // "boom" 不命中 normalizeFailureMessage 任何归一化规则 → 原样透传
    expect(state.messages[2]?.content).toBe("boom");
    expect(state.messages[2]?.isStreaming).toBe(false);
  });
});

describe("审批生命周期", () => {
  const requestedPayload = {
    approval_id: "appr-1",
    task_id: "task-9",
    tool_name: "terminal.exec",
    tool_args_summary: "rm -rf /tmp/x",
    risk_explanation: "高危命令",
    side_effect_level: "high",
    created_at: "2026-07-13T10:00:00Z",
    expires_at: "2026-07-13T10:10:00Z",
  };

  it("approval:requested：标记消息 hasApproval + 构造快照 + 信号 +1", () => {
    const { state } = runSequence(initialState(), [
      { type: "MODEL_CALL_STARTED", payload: {} },
      { type: "approval:requested", payload: requestedPayload },
    ]);
    expect(lastMessage(state)).toMatchObject({ hasApproval: true, isStreaming: true });
    expect(state.streaming).toBe(true);
    expect(state.liveApproval).toMatchObject({
      approvalId: "appr-1",
      taskId: "task-9",
      toolName: "terminal.exec",
      toolArgsSummary: "rm -rf /tmp/x",
      riskExplanation: "高危命令",
      sideEffectLevel: "high",
      createdAt: "2026-07-13T10:00:00Z",
      expiresAt: "2026-07-13T10:10:00Z",
    });
    expect(state.approvalSignal).toBe(1);
  });

  it("APPROVAL_REQUESTED 无 approval_id：仍标记消息但不建快照、不加信号", () => {
    const { state } = runSequence(initialState(), [
      { type: "APPROVAL_REQUESTED", payload: { tool_name: "x" } },
    ]);
    expect(lastMessage(state)).toMatchObject({ hasApproval: true });
    expect(state.liveApproval).toBeNull();
    expect(state.approvalSignal).toBe(0);
  });

  it("requested → APPROVAL_APPROVED：快照清除、信号累计 2", () => {
    const { state } = runSequence(initialState(), [
      { type: "approval:requested", payload: requestedPayload },
      { type: "APPROVAL_APPROVED", payload: { approval_id: "appr-1" } },
    ]);
    expect(state.liveApproval).toBeNull();
    expect(state.approvalSignal).toBe(2);
  });

  it("乱序：resolved 先于 requested 到达时快照最终仍是 requested 的", () => {
    const { state } = runSequence(initialState(), [
      { type: "approval:resolved", payload: { approval_id: "appr-0" } },
      { type: "approval:requested", payload: requestedPayload },
    ]);
    expect(state.liveApproval?.approvalId).toBe("appr-1");
    expect(state.approvalSignal).toBe(2);
  });

  it("五种终态型事件都清快照（含 legacy 大写型）", () => {
    for (const type of [
      "APPROVAL_EXPIRED",
      "APPROVAL_APPROVED",
      "APPROVAL_REJECTED",
      "approval:resolved",
      "approval:expired",
    ]) {
      const seeded = runSequence(initialState(), [
        { type: "approval:requested", payload: requestedPayload },
      ]).state;
      const { state } = runSequence(seeded, [{ type, payload: {} }]);
      expect(state.liveApproval, type).toBeNull();
    }
  });
});

describe("deriveStreamClosedOutcome（onerror CLOSED 纯化，声明式修复）", () => {
  it("有活跃占位：清 streaming + 清该消息 isStreaming + 清活跃 id", () => {
    const seeded = initialState({
      messages: [makeAgentPlaceholderMessage("agent-x")],
      activeAgentMessageId: "agent-x",
      streaming: true,
    });
    const state = applyChatStreamOutcome(seeded, deriveStreamClosedOutcome("agent-x"));
    expect(state.streaming).toBe(false);
    expect(state.messages[0]?.isStreaming).toBe(false);
    expect(state.activeAgentMessageId).toBeNull();
  });

  it("无活跃占位：仅收流，不产生消息操作", () => {
    const outcome = deriveStreamClosedOutcome(null);
    expect(outcome.messageOps).toEqual([]);
    expect(outcome.streaming).toBe(false);
  });
});

describe("applyMessageOps 边界", () => {
  it("markFailed 对已有实内容且非流式的消息保留原文", () => {
    const messages: ChatMessage[] = [
      { id: "m1", role: "agent", content: "已有回答", isStreaming: false },
    ];
    const next = applyMessageOps(messages, [
      { kind: "markFailed", messageId: "m1", failureMessage: "失败了" },
    ]);
    expect(next[0]?.content).toBe("已有回答");
    expect(next[0]?.isStreaming).toBe(false);
  });

  it("op 只作用于目标 id，其余消息引用原样保留", () => {
    const other: ChatMessage = { id: "keep", role: "user", content: "问", isStreaming: false };
    const messages: ChatMessage[] = [other, makeAgentPlaceholderMessage("m2")];
    const next = applyMessageOps(messages, [
      { kind: "complete", messageId: "m2", content: "答" },
    ]);
    expect(next[0]).toBe(other);
    expect(next[1]).toMatchObject({ content: "答", isStreaming: false });
  });
});
