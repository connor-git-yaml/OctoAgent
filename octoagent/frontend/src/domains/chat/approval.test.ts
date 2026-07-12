/**
 * domains/chat/approval L4 直测 —— F143 件 2/件 3
 */
import { describe, expect, it } from "vitest";
import type { ApprovalListItem, OperatorInboxItem, TaskEvent } from "../../types";
import {
  buildExecutionSessionApprovalItem,
  buildSyntheticApprovalItem,
  deriveActiveApprovalPresentation,
  formatCountdown,
  mapOperatorQuickAction,
  parseApprovalCommand,
  payloadMatchesWork,
  readLatestApprovalContext,
  readLatestExpiredApprovalContext,
  readPendingApprovalEvent,
} from "./approval";

function makeEvent(overrides: Partial<TaskEvent> & { type: string }): TaskEvent {
  return {
    event_id: `evt-${Math.random().toString(16).slice(2, 8)}`,
    task_id: "task-1",
    task_seq: 1,
    ts: "2026-07-13T10:00:00Z",
    actor: "system",
    payload: {},
    ...overrides,
  } as TaskEvent;
}

function makeApproval(overrides: Partial<ApprovalListItem> = {}): ApprovalListItem {
  return {
    approval_id: "appr-1",
    task_id: "task-1",
    tool_name: "terminal.exec",
    tool_args_summary: "ls /",
    risk_explanation: "读取目录",
    policy_label: "requires_approval",
    side_effect_level: "medium",
    remaining_seconds: 600,
    created_at: "2026-07-13T10:00:00Z",
    ...overrides,
  } as ApprovalListItem;
}

function makeInboxItem(overrides: Partial<OperatorInboxItem> = {}): OperatorInboxItem {
  return {
    item_id: "approval:appr-inbox",
    kind: "approval",
    state: "pending",
    title: "x 需要审批",
    summary: "s",
    task_id: "task-1",
    thread_id: "",
    source_ref: "appr-inbox",
    created_at: "2026-07-13T10:00:00Z",
    expires_at: null,
    pending_age_seconds: null,
    suggested_actions: [],
    quick_actions: [
      { kind: "approve_once", label: "批准一次", style: "primary", enabled: true },
    ],
    recent_action_result: null,
    metadata: {},
    ...overrides,
  } as OperatorInboxItem;
}

const NOW = Date.parse("2026-07-13T10:05:00Z");

function baseOptions() {
  return {
    taskId: "task-1" as string | null,
    operatorItems: [] as OperatorInboxItem[],
    activeSessionThreadId: "",
    taskDetailEvents: undefined as TaskEvent[] | null | undefined,
    approvalWorkId: "",
    pendingApprovals: [] as ApprovalListItem[],
    executionSessionPendingApprovalId: "",
    liveApproval: null,
    approvalNow: NOW,
  };
}

describe("parseApprovalCommand", () => {
  it("识别全部命令变体（大小写/空白不敏感）", () => {
    expect(parseApprovalCommand("/approve")).toBe("approve_once");
    expect(parseApprovalCommand("  /APPROVE ONCE ")).toBe("approve_once");
    expect(parseApprovalCommand("/approve always")).toBe("approve_always");
    expect(parseApprovalCommand("/approve all")).toBe("approve_always");
    expect(parseApprovalCommand("/deny")).toBe("deny");
    expect(parseApprovalCommand("/reject")).toBe("deny");
  });

  it("非斜杠输入与未知命令返回 null", () => {
    expect(parseApprovalCommand("approve")).toBeNull();
    expect(parseApprovalCommand("/unknown")).toBeNull();
    expect(parseApprovalCommand("")).toBeNull();
  });
});

describe("mapOperatorQuickAction", () => {
  it("审批三动作映射 operator.approval.resolve + item_id 提取 approval_id", () => {
    const item = makeInboxItem({ item_id: "approval:appr-42" });
    expect(mapOperatorQuickAction(item, "approve_once")).toEqual({
      actionId: "operator.approval.resolve",
      params: { approval_id: "appr-42", mode: "once" },
    });
    expect(mapOperatorQuickAction(item, "approve_always")?.params.mode).toBe("always");
    expect(mapOperatorQuickAction(item, "deny")?.params.mode).toBe("deny");
  });

  it("任务/告警/配对动作映射对应 action，未知 kind 返回 null", () => {
    const item = makeInboxItem({ item_id: "task:item-1" });
    expect(mapOperatorQuickAction(item, "cancel_task")?.actionId).toBe("operator.task.cancel");
    expect(mapOperatorQuickAction(item, "retry_task")?.actionId).toBe("operator.task.retry");
    expect(mapOperatorQuickAction(item, "ack_alert")?.actionId).toBe("operator.alert.ack");
    expect(mapOperatorQuickAction(item, "approve_pairing")?.actionId).toBe("channel.pairing.approve");
    expect(mapOperatorQuickAction(item, "reject_pairing")?.actionId).toBe("channel.pairing.reject");
    expect(mapOperatorQuickAction(item, "unknown" as never)).toBeNull();
  });
});

describe("审批事件读取", () => {
  it("readPendingApprovalEvent：requested 后被终态消除，取最新 pending", () => {
    const events = [
      makeEvent({ type: "APPROVAL_REQUESTED", task_seq: 1, payload: { approval_id: "a1" } }),
      makeEvent({ type: "APPROVAL_REQUESTED", task_seq: 2, payload: { approval_id: "a2" } }),
      makeEvent({ type: "APPROVAL_APPROVED", task_seq: 3, payload: { approval_id: "a2" } }),
    ];
    expect(readPendingApprovalEvent(events)?.payload.approval_id).toBe("a1");
    const cleared = [
      ...events,
      makeEvent({ type: "APPROVAL_EXPIRED", task_seq: 4, payload: { approval_id: "a1" } }),
    ];
    expect(readPendingApprovalEvent(cleared)).toBeNull();
  });

  it("readLatestApprovalContext：pending 优先，无 pending 回退 TOOL_CALL_STARTED", () => {
    const pending = readLatestApprovalContext([
      makeEvent({
        type: "APPROVAL_REQUESTED",
        payload: { approval_id: "a1", tool_name: "web.fetch", risk_explanation: "抓外链" },
      }),
    ]);
    expect(pending).toMatchObject({ approvalId: "a1", toolName: "web.fetch", summary: "抓外链" });

    const fallback = readLatestApprovalContext([
      makeEvent({ type: "TOOL_CALL_STARTED", payload: { tool_name: "terminal.exec" } }),
    ]);
    expect(fallback).toMatchObject({ approvalId: "", toolName: "terminal.exec" });
    expect(readLatestApprovalContext([])).toBeNull();
  });

  it("readLatestApprovalContext：workId 过滤只看该 work 的事件", () => {
    const events = [
      makeEvent({
        type: "APPROVAL_REQUESTED",
        payload: { approval_id: "a-other", work_id: "w-other" },
      }),
    ];
    expect(readLatestApprovalContext(events, "w-mine")).toBeNull();
    expect(readLatestApprovalContext(events, "w-other")?.approvalId).toBe("a-other");
  });

  it("readLatestExpiredApprovalContext：取最近一条 APPROVAL_EXPIRED", () => {
    const events = [
      makeEvent({ type: "APPROVAL_EXPIRED", task_seq: 1, payload: { approval_id: "a1", tool_name: "t1" } }),
      makeEvent({ type: "APPROVAL_EXPIRED", task_seq: 2, payload: { approval_id: "a2", tool_name: "t2" } }),
    ];
    expect(readLatestExpiredApprovalContext(events)?.approvalId).toBe("a2");
    expect(readLatestExpiredApprovalContext([])).toBeNull();
  });

  it("payloadMatchesWork：work_id 精确或 session id 包含匹配", () => {
    expect(payloadMatchesWork(makeEvent({ type: "X", payload: { work_id: "w-1" } }), "w-1")).toBe(true);
    expect(
      payloadMatchesWork(makeEvent({ type: "X", payload: { session_id: "sess-w-1-exec" } }), "w-1")
    ).toBe(true);
    expect(payloadMatchesWork(makeEvent({ type: "X", payload: {} }), "w-1")).toBe(false);
    expect(payloadMatchesWork(makeEvent({ type: "X", payload: { work_id: "w-1" } }), "")).toBe(false);
  });
});

describe("审批项构造", () => {
  it("buildSyntheticApprovalItem：expires_at 由 created_at+remaining 推导，三快捷动作齐全", () => {
    const item = buildSyntheticApprovalItem(
      makeApproval({ created_at: "2026-07-13T10:00:00Z", remaining_seconds: 60 })
    );
    expect(item.item_id).toBe("approval:appr-1");
    expect(item.expires_at).toBe("2026-07-13T10:01:00.000Z");
    expect(item.quick_actions.map((a) => a.kind)).toEqual([
      "approve_once",
      "approve_always",
      "deny",
    ]);
    expect(item.metadata.tool_name).toBe("terminal.exec");
  });

  it("buildExecutionSessionApprovalItem：默认 toolName 与默认 summary 兜底", () => {
    const item = buildExecutionSessionApprovalItem("appr-9", { taskId: "task-9" });
    expect(item.title).toBe("terminal.exec 需要审批");
    expect(item.summary).toContain("需要你确认后才能继续执行");
    expect(item.expires_at).toBeNull();
  });

  it("formatCountdown：mm:ss 补零 + 负值钳 0", () => {
    expect(formatCountdown(65)).toBe("01:05");
    expect(formatCountdown(0)).toBe("00:00");
    expect(formatCountdown(-5)).toBe("00:00");
  });
});

describe("deriveActiveApprovalPresentation（件 2 块 C）", () => {
  it("无任何来源：无横幅", () => {
    const view = deriveActiveApprovalPresentation(baseOptions());
    expect(view.activeApprovalItem).toBeNull();
    expect(view.shouldShowApprovalBanner).toBe(false);
    expect(view.activeApprovalRemainingSeconds).toBeNull();
  });

  it("四源优先级：inbox > synthetic(REST) > executionSession(事件) > live(SSE)", () => {
    const inbox = makeInboxItem({ item_id: "approval:from-inbox" });
    const both = deriveActiveApprovalPresentation({
      ...baseOptions(),
      operatorItems: [inbox],
      pendingApprovals: [makeApproval({ approval_id: "from-rest" })],
    });
    expect(both.activeApprovalItem?.item_id).toBe("approval:from-inbox");

    const restOnly = deriveActiveApprovalPresentation({
      ...baseOptions(),
      pendingApprovals: [makeApproval({ approval_id: "from-rest" })],
    });
    expect(restOnly.activeApprovalItem?.item_id).toBe("approval:from-rest");

    const liveOnly = deriveActiveApprovalPresentation({
      ...baseOptions(),
      liveApproval: {
        approvalId: "from-sse",
        taskId: "task-1",
        toolName: "web.fetch",
        toolArgsSummary: "GET /",
        riskExplanation: "外部请求",
        createdAt: "2026-07-13T10:04:00Z",
        expiresAt: "",
      },
    });
    expect(liveOnly.activeApprovalItem?.item_id).toBe("approval:from-sse");
    expect(liveOnly.activeApprovalItem?.metadata.tool_name).toBe("web.fetch");
  });

  it("inbox 过滤：非 pending 或 task/thread 不匹配的项不参与", () => {
    const foreign = makeInboxItem({ item_id: "approval:foreign", task_id: "task-other" });
    const resolved = makeInboxItem({ item_id: "approval:resolved", state: "handled" });
    const threadMatched = makeInboxItem({
      item_id: "approval:thread",
      task_id: "task-other",
      thread_id: "thread-1",
    });
    const view = deriveActiveApprovalPresentation({
      ...baseOptions(),
      operatorItems: [foreign, resolved, threadMatched],
      activeSessionThreadId: "thread-1",
    });
    expect(view.activeApprovalItem?.item_id).toBe("approval:thread");
  });

  it("executionSession pending_approval_id + 事件上下文合成审批项", () => {
    const events = [
      makeEvent({
        type: "APPROVAL_REQUESTED",
        ts: "2026-07-13T10:04:00Z",
        payload: { approval_id: "appr-exec", tool_name: "fs.write", risk_explanation: "写文件" },
      }),
    ];
    const view = deriveActiveApprovalPresentation({
      ...baseOptions(),
      taskDetailEvents: events,
      executionSessionPendingApprovalId: "appr-exec",
    });
    expect(view.activeApprovalItem?.item_id).toBe("approval:appr-exec");
    expect(view.activeApprovalItem?.metadata.tool_name).toBe("fs.write");
    expect(view.latestApprovalContext?.approvalId).toBe("appr-exec");
  });

  it("倒计时：expires_at 未来显示剩余秒并亮横幅，已过期隐藏", () => {
    const fresh = deriveActiveApprovalPresentation({
      ...baseOptions(),
      pendingApprovals: [
        makeApproval({ created_at: "2026-07-13T10:04:30Z", remaining_seconds: 60 }),
      ],
    });
    // 10:04:30 + 60s = 10:05:30，NOW=10:05:00 → 剩 30s
    expect(fresh.activeApprovalRemainingSeconds).toBe(30);
    expect(fresh.shouldShowApprovalBanner).toBe(true);

    const expired = deriveActiveApprovalPresentation({
      ...baseOptions(),
      pendingApprovals: [
        makeApproval({ created_at: "2026-07-13T10:00:00Z", remaining_seconds: 60 }),
      ],
    });
    expect(expired.activeApprovalRemainingSeconds).toBe(0);
    expect(expired.shouldShowApprovalBanner).toBe(false);
  });

  it("taskId 为空：REST/事件源全部旁路", () => {
    const view = deriveActiveApprovalPresentation({
      ...baseOptions(),
      taskId: null,
      pendingApprovals: [makeApproval()],
      taskDetailEvents: [
        makeEvent({ type: "APPROVAL_REQUESTED", payload: { approval_id: "a1" } }),
      ],
    });
    expect(view.activeApprovalItem).toBeNull();
    expect(view.latestApprovalContext).toBeNull();
  });
});
