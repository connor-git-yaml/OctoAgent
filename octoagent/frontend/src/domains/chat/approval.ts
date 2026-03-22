import type {
  ApprovalListItem,
  OperatorActionKind,
  OperatorInboxItem,
  TaskEvent,
} from "../../types";

export function mapOperatorQuickAction(
  item: OperatorInboxItem,
  kind: OperatorActionKind
): { actionId: string; params: Record<string, unknown> } | null {
  const approvalId = item.item_id.split(":")[1] ?? "";
  if (kind === "approve_once") {
    return {
      actionId: "operator.approval.resolve",
      params: {
        approval_id: approvalId,
        mode: "once",
      },
    };
  }
  if (kind === "approve_always") {
    return {
      actionId: "operator.approval.resolve",
      params: {
        approval_id: approvalId,
        mode: "always",
      },
    };
  }
  if (kind === "deny") {
    return {
      actionId: "operator.approval.resolve",
      params: {
        approval_id: approvalId,
        mode: "deny",
      },
    };
  }
  if (kind === "cancel_task") {
    return { actionId: "operator.task.cancel", params: { item_id: item.item_id } };
  }
  if (kind === "retry_task") {
    return { actionId: "operator.task.retry", params: { item_id: item.item_id } };
  }
  if (kind === "ack_alert") {
    return { actionId: "operator.alert.ack", params: { item_id: item.item_id } };
  }
  if (kind === "approve_pairing") {
    return { actionId: "channel.pairing.approve", params: { item_id: item.item_id } };
  }
  if (kind === "reject_pairing") {
    return { actionId: "channel.pairing.reject", params: { item_id: item.item_id } };
  }
  return null;
}

export function parseApprovalCommand(
  input: string
): "approve_once" | "approve_always" | "deny" | null {
  const normalized = input.trim().toLowerCase();
  if (!normalized.startsWith("/")) {
    return null;
  }
  if (normalized === "/approve" || normalized === "/approve once") {
    return "approve_once";
  }
  if (normalized === "/approve always" || normalized === "/approve all") {
    return "approve_always";
  }
  if (normalized === "/deny" || normalized === "/reject") {
    return "deny";
  }
  return null;
}

export function buildSyntheticApprovalItem(approval: ApprovalListItem): OperatorInboxItem {
  const expiresAt = Number.isFinite(approval.remaining_seconds)
    ? new Date(Date.parse(approval.created_at) + Math.max(approval.remaining_seconds, 0) * 1000).toISOString()
    : null;
  return {
    item_id: `approval:${approval.approval_id}`,
    kind: "approval",
    state: "pending",
    title: `${approval.tool_name} 需要审批`,
    summary: approval.risk_explanation,
    task_id: approval.task_id,
    thread_id: "",
    source_ref: approval.approval_id,
    created_at: approval.created_at,
    expires_at: expiresAt,
    pending_age_seconds: null,
    suggested_actions: ["review_approval_request"],
    quick_actions: [
      { kind: "approve_once", label: "批准一次", style: "primary", enabled: true },
      { kind: "approve_always", label: "总是批准", style: "secondary", enabled: true },
      { kind: "deny", label: "拒绝", style: "danger", enabled: true },
    ],
    recent_action_result: null,
    metadata: {
      tool_name: approval.tool_name,
      tool_args_summary: approval.tool_args_summary,
      policy_label: approval.policy_label,
      side_effect_level: approval.side_effect_level,
    },
  };
}

export function buildExecutionSessionApprovalItem(
  approvalId: string,
  options: {
    taskId: string;
    toolName?: string;
    argsSummary?: string;
    summary?: string;
    createdAt?: string;
    expiresAt?: string | null;
  }
): OperatorInboxItem {
  const toolName = options.toolName?.trim() || "terminal.exec";
  return {
    item_id: `approval:${approvalId}`,
    kind: "approval",
    state: "pending",
    title: `${toolName} 需要审批`,
    summary:
      options.summary?.trim() || "这一步需要你确认后才能继续执行，当前聊天里可以直接批准或拒绝。",
    task_id: options.taskId,
    thread_id: "",
    source_ref: approvalId,
    created_at: options.createdAt || new Date().toISOString(),
    expires_at: options.expiresAt ?? null,
    pending_age_seconds: null,
    suggested_actions: ["review_approval_request"],
    quick_actions: [
      { kind: "approve_once", label: "批准一次", style: "primary", enabled: true },
      { kind: "approve_always", label: "总是批准", style: "secondary", enabled: true },
      { kind: "deny", label: "拒绝", style: "danger", enabled: true },
    ],
    recent_action_result: null,
    metadata: {
      tool_name: toolName,
      tool_args_summary: options.argsSummary?.trim() || "",
    },
  };
}

export function readPayloadString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value : "";
}

export function readLatestApprovalContext(
  events: TaskEvent[],
  workId?: string
): {
  approvalId?: string;
  toolName?: string;
  argsSummary?: string;
  summary?: string;
  createdAt?: string;
  expiresAt?: string | null;
} | null {
  const relevantEvents = workId
    ? events.filter((event) => payloadMatchesWork(event, workId))
    : events;
  const latestPendingApproval = readPendingApprovalEvent(relevantEvents);
  if (latestPendingApproval) {
    return {
      approvalId: readPayloadString(latestPendingApproval.payload ?? {}, "approval_id"),
      toolName: readPayloadString(latestPendingApproval.payload ?? {}, "tool_name"),
      argsSummary:
        readPayloadString(latestPendingApproval.payload ?? {}, "tool_args_summary") ||
        readPayloadString(latestPendingApproval.payload ?? {}, "args_summary"),
      summary:
        readPayloadString(latestPendingApproval.payload ?? {}, "risk_explanation") ||
        readPayloadString(latestPendingApproval.payload ?? {}, "summary"),
      createdAt: latestPendingApproval.ts,
      expiresAt: new Date(Date.parse(latestPendingApproval.ts) + 600 * 1000).toISOString(),
    };
  }
  const latestToolStarted = [...relevantEvents]
    .reverse()
    .find((event) => String(event.type) === "TOOL_CALL_STARTED");
  if (!latestToolStarted) {
    return null;
  }
  return {
    approvalId: "",
    toolName: readPayloadString(latestToolStarted.payload ?? {}, "tool_name"),
    argsSummary: readPayloadString(latestToolStarted.payload ?? {}, "args_summary"),
    summary: "系统已经进入高风险工具调用前的等待确认阶段。",
    createdAt: latestToolStarted.ts,
    expiresAt: new Date(Date.parse(latestToolStarted.ts) + 600 * 1000).toISOString(),
  };
}

export function readLatestExpiredApprovalContext(
  events: TaskEvent[],
  workId?: string
): {
  approvalId?: string;
  toolName?: string;
  argsSummary?: string;
  expiredAt?: string;
} | null {
  const relevantEvents = workId
    ? events.filter((event) => payloadMatchesWork(event, workId))
    : events;
  const latestExpired = [...relevantEvents]
    .reverse()
    .find((event) => String(event.type) === "APPROVAL_EXPIRED");
  if (!latestExpired) {
    return null;
  }
  return {
    approvalId: readPayloadString(latestExpired.payload ?? {}, "approval_id"),
    toolName: readPayloadString(latestExpired.payload ?? {}, "tool_name"),
    argsSummary:
      readPayloadString(latestExpired.payload ?? {}, "tool_args_summary") ||
      readPayloadString(latestExpired.payload ?? {}, "args_summary"),
    expiredAt: latestExpired.ts,
  };
}

export function formatCountdown(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const remainSeconds = safe % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainSeconds).padStart(2, "0")}`;
}

export function payloadMatchesWork(event: TaskEvent, workId: string): boolean {
  if (!workId) {
    return false;
  }
  const payload = event.payload ?? {};
  const payloadWorkId = readPayloadString(payload, "work_id");
  if (payloadWorkId === workId) {
    return true;
  }
  const sessionId = readPayloadString(payload, "session_id");
  if (sessionId.includes(workId)) {
    return true;
  }
  const sourceSessionId = readPayloadString(payload, "source_agent_session_id");
  if (sourceSessionId.includes(workId)) {
    return true;
  }
  const targetSessionId = readPayloadString(payload, "target_agent_session_id");
  if (targetSessionId.includes(workId)) {
    return true;
  }
  return false;
}

export function readPendingApprovalEvent(events: TaskEvent[]): TaskEvent | null {
  const approvalState = new Map<string, TaskEvent>();
  for (const event of events) {
    const eventType = String(event.type);
    const approvalId = readPayloadString(event.payload ?? {}, "approval_id");
    if (!approvalId) {
      continue;
    }
    if (eventType === "APPROVAL_REQUESTED") {
      approvalState.set(approvalId, event);
      continue;
    }
    if (
      eventType === "APPROVAL_APPROVED" ||
      eventType === "APPROVAL_REJECTED" ||
      eventType === "APPROVAL_EXPIRED"
    ) {
      approvalState.delete(approvalId);
    }
  }
  const pendingEvents = [...approvalState.values()];
  if (pendingEvents.length === 0) {
    return null;
  }
  pendingEvents.sort((left, right) => {
    const leftSeq = typeof left.task_seq === "number" ? left.task_seq : 0;
    const rightSeq = typeof right.task_seq === "number" ? right.task_seq : 0;
    return rightSeq - leftSeq;
  });
  return pendingEvents[0] ?? null;
}
