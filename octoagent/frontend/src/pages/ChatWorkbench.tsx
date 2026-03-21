import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ApiError,
  attachExecutionInput,
  fetchApprovals,
  fetchTaskDetail,
  fetchTaskExecutionSession,
} from "../api/client";
import { MessageBubble } from "../components/ChatUI/MessageBubble";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import { formatAgentRoleLabel, formatTaskStatusLabel, formatTaskStatusTone } from "../domains/chat/presentation";
import { useChatStream } from "../hooks/useChatStream";
import { readStoredTaskId } from "../hooks/chatStreamHelpers";
import { HoverReveal, InlineCallout, StatusBadge } from "../ui/primitives";
import { formatSessionDisplayTitle } from "../workbench/utils";
import type {
  ApprovalListItem,
  ExecutionSessionDocument,
  OperatorActionKind,
  OperatorInboxItem,
  ProjectOption,
  SessionProjectionDocument,
  TaskDetailResponse,
  TaskEvent,
  WorkProjectionItem,
} from "../types";

const TERMINAL_TASK_STATUSES = new Set(["SUCCEEDED", "FAILED", "CANCELLED", "REJECTED"]);
const CHAT_SLASH_COMMANDS = [
  {
    value: "/approve",
    description: "批准一次当前审批",
    action: "approve_once",
  },
  {
    value: "/approve always",
    description: "总是批准当前审批",
    action: "approve_always",
  },
  {
    value: "/deny",
    description: "拒绝当前审批",
    action: "deny",
  },
] as const;

function ensureArray<T>(value: T[] | null | undefined): T[] {
  return Array.isArray(value) ? value : [];
}

function mapOperatorQuickAction(
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

function parseApprovalCommand(
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

function buildSyntheticApprovalItem(approval: ApprovalListItem): OperatorInboxItem {
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

function buildExecutionSessionApprovalItem(
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

function readExecutionSessionDocument(
  response: { session?: ExecutionSessionDocument | null } | null | undefined
): ExecutionSessionDocument | null {
  return response?.session ?? null;
}

function resolveProjectName(projects: ProjectOption[], projectId: string): string {
  return projects.find((item) => item.project_id === projectId)?.name ?? projectId;
}

function pushRestoreTaskId(taskIds: string[], taskId: string | undefined): void {
  if (!taskId || taskIds.includes(taskId)) {
    return;
  }
  taskIds.push(taskId);
}

function resolveRestorableTaskIds(sessions: SessionProjectionDocument): string[] {
  if (
    typeof sessions.new_conversation_token === "string" &&
    sessions.new_conversation_token.trim()
  ) {
    return [];
  }
  const sessionItems = ensureArray(sessions.sessions);
  const cleanSessionItems = sessionItems.filter((item) => !item.reset_recommended);
  const webSessions = cleanSessionItems.filter(
    (item) => item.channel === "web" && !item.reset_recommended
  );
  const candidates = webSessions.length > 0 ? webSessions : cleanSessionItems;
  const taskIds: string[] = [];

  if (sessions.focused_session_id) {
    const focused = candidates.find((item) => item.session_id === sessions.focused_session_id);
    if (focused) {
      pushRestoreTaskId(taskIds, focused.task_id);
    }
  }
  if (sessions.focused_thread_id) {
    const focused = candidates.find((item) => item.thread_id === sessions.focused_thread_id);
    if (focused) {
      pushRestoreTaskId(taskIds, focused.task_id);
    }
  }

  for (const item of candidates) {
    pushRestoreTaskId(taskIds, item.task_id);
  }
  for (const item of cleanSessionItems) {
    pushRestoreTaskId(taskIds, item.task_id);
  }

  return taskIds;
}

function resolveSessionOwnerProfileId(session: SessionProjectionDocument["sessions"][number] | null | undefined): string {
  return session?.session_owner_profile_id?.trim() || session?.agent_profile_id?.trim() || "";
}

function readSummaryString(summary: Record<string, unknown>, key: string): string {
  const value = summary[key];
  return typeof value === "string" ? value : "";
}

function sortWorksByUpdate(works: WorkProjectionItem[]): WorkProjectionItem[] {
  return [...works].sort((left, right) =>
    String(right.updated_at ?? "").localeCompare(String(left.updated_at ?? ""))
  );
}

function resolveAgentUri(agentId: string, fallback: string): string {
  const normalized = agentId.trim();
  if (!normalized) {
    return fallback;
  }
  return normalized.startsWith("agent://") ? normalized : `agent://${normalized}`;
}

function resolveWorkActor(work: WorkProjectionItem): string {
  return resolveAgentUri(
    readSummaryString(work.runtime_summary, "research_worker_id") ||
      work.runtime_id ||
      work.selected_worker_type ||
      "",
    "worker"
  );
}

function resolveWorkStatus(work: WorkProjectionItem): string {
  return (
    readSummaryString(work.runtime_summary, "research_worker_status") ||
    work.status ||
    ""
  )
    .trim()
    .toUpperCase();
}

function isAgentDirectExecution(work: WorkProjectionItem | null | undefined): boolean {
  if (!work) {
    return false;
  }
  const runtimeId = work.runtime_id.trim().toLowerCase();
  const selectedWorkerType = work.selected_worker_type.trim().toLowerCase();
  const targetKind = work.target_kind.trim().toLowerCase();
  const routeReason = work.route_reason.trim().toLowerCase();
  return (
    runtimeId === "worker.llm.default" &&
    selectedWorkerType === "general" &&
    (targetKind === "fallback" || routeReason.includes("single_worker"))
  );
}

function formatActorSummary(actor: string, workTitle: string, status: string, latestMessageType: string): string {
  const normalizedActor = actor.trim().toLowerCase();
  const normalizedStatus = status.trim().toUpperCase();
  const normalizedMessageType = latestMessageType.trim().toUpperCase();

  if (normalizedStatus === "WAITING_INPUT") {
    return "还差一条关键信息，这轮才能继续。";
  }
  if (normalizedStatus === "WAITING_APPROVAL") {
    return "这一步需要你点一次确认，系统才会继续。";
  }
  if (normalizedStatus === "FAILED") {
    return "这轮没有拿到可用结果，主助手会继续处理影响。";
  }
  if (normalizedStatus === "CANCELLED") {
    return "这轮已经停止，不会再继续往下执行。";
  }
  if (normalizedStatus === "SUCCEEDED" || normalizedMessageType === "RESULT") {
    return "这轮查询已经完成，但主助手还在整理最终答复。";
  }
  if (normalizedActor.includes("research")) {
    return workTitle ? `正在查资料：${workTitle}` : "正在查资料和核对事实。";
  }
  if (normalizedActor.includes("ops")) {
    return workTitle ? `正在执行或检查：${workTitle}` : "正在检查系统状态和执行步骤。";
  }
  if (normalizedActor.includes("dev")) {
    return workTitle ? `正在实现或验证：${workTitle}` : "正在改代码和验证实现。";
  }
  return workTitle ? `正在处理：${workTitle}` : "正在理解问题、安排下一步，并整理最终回复。";
}

function formatActivityStateLabel(status: string, latestMessageType: string): string {
  const normalizedStatus = status.trim().toUpperCase();
  const normalizedMessageType = latestMessageType.trim().toUpperCase();

  switch (normalizedStatus) {
    case "RUNNING":
      return "进行中";
    case "WAITING_INPUT":
      return "等你补充";
    case "WAITING_APPROVAL":
      return "等你确认";
    case "SUCCEEDED":
      return "已完成";
    case "FAILED":
      return "失败";
    case "CANCELLED":
      return "已取消";
    case "QUEUED":
    case "CREATED":
      return "准备中";
    default:
      if (normalizedMessageType === "RESULT") {
        return "已完成";
      }
      if (normalizedMessageType === "TASK") {
        return "已接手";
      }
      return "处理中";
  }
}

function formatActivityTone(status: string, latestMessageType: string): "success" | "warning" | "danger" | "running" | "draft" {
  const normalizedStatus = status.trim().toUpperCase();
  const normalizedMessageType = latestMessageType.trim().toUpperCase();

  if (normalizedStatus === "WAITING_INPUT" || normalizedStatus === "WAITING_APPROVAL") {
    return "warning";
  }
  if (normalizedStatus === "FAILED") {
    return "danger";
  }
  if (normalizedStatus === "SUCCEEDED" || normalizedMessageType === "RESULT") {
    return "success";
  }
  if (normalizedStatus === "CANCELLED") {
    return "draft";
  }
  return "running";
}

interface ChatActivityItem {
  id: string;
  actor: string;
  stateLabel: string;
  tone: "success" | "warning" | "danger" | "running" | "draft";
  summary: string;
  traceTitle?: string;
  traceEntries?: ChatTraceEntry[];
}

interface ChatTraceEntry {
  id: string;
  label: string;
  summary: string;
  stateLabel?: string;
  tone?: "success" | "warning" | "danger" | "running" | "draft";
  detailInput?: string;
  detailOutput?: string;
}

type RestoreChoice = "continue" | "new";

function summarizeDelegationIntent(work: WorkProjectionItem): string {
  const toolNames = ensureArray(work.selected_tools);
  const workerType = (work.selected_worker_type || "").trim().toLowerCase();
  const workTitle = summarizeText(work.title || "", 72);

  if (toolNames.some((tool) => tool.startsWith("web."))) {
    return "核实外部事实，优先使用受治理的 Web 工具，再把可直接回复用户的结论带回主助手。";
  }
  if (workerType === "research") {
    return "先核对事实和上下文，再把整理好的结论和限制条件回给主助手。";
  }
  if (workTitle) {
    return `围绕“${workTitle}”补足信息并收口成可直接回复的结果。`;
  }
  return "把这轮问题交给内部角色处理，并要求带回可直接使用的结果。";
}

function summarizeText(value: string, maxLength = 120): string {
  const normalized = value.trim().replace(/\s+/g, " ");
  if (!normalized) {
    return "";
  }
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 1)}…`;
}

function readPayloadString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value : "";
}

function readLatestApprovalContext(
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
      expiresAt: new Date(Date.parse(latestPendingApproval.ts) + 120 * 1000).toISOString(),
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
    expiresAt: new Date(Date.parse(latestToolStarted.ts) + 120 * 1000).toISOString(),
  };
}

function readLatestExpiredApprovalContext(
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

function formatCountdown(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const remainSeconds = safe % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainSeconds).padStart(2, "0")}`;
}

function payloadMatchesWork(event: TaskEvent, workId: string): boolean {
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

function readPendingApprovalEvent(events: TaskEvent[]): TaskEvent | null {
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

function summarizeToolList(toolNames: string[], limit = 4): string {
  if (toolNames.length === 0) {
    return "这轮没有挂出额外工具。";
  }
  const visible = toolNames.slice(0, limit);
  const suffix =
    toolNames.length > limit ? `，另外还有 ${toolNames.length - limit} 个工具` : "";
  return `${visible.join("、")}${suffix}`;
}

interface ToolTraceRecord {
  id: string;
  toolName: string;
  argsSummary: string;
  outputSummary: string;
  stateLabel: string;
  tone: "success" | "warning" | "danger" | "running" | "draft";
  summary: string;
  taskSeq: number;
}

function buildToolTimelineRecords(workEvents: TaskEvent[]): ToolTraceRecord[] {
  const relevant = workEvents.filter((event) => {
    const eventType = String(event.type);
    return (
      eventType === "TOOL_CALL_STARTED" ||
      eventType === "TOOL_CALL_COMPLETED" ||
      eventType === "TOOL_CALL_FAILED"
    );
  });
  const pendingStarts = new Map<string, Array<{ argsSummary: string; taskSeq: number }>>();
  const records: ToolTraceRecord[] = [];

  for (const event of relevant) {
    const eventType = String(event.type);
    const payload = event.payload ?? {};
    const toolName = readPayloadString(payload, "tool_name");
    if (!toolName) {
      continue;
    }
    const argsSummary = summarizeText(readPayloadString(payload, "args_summary"), 96);
    if (eventType === "TOOL_CALL_STARTED") {
      const queue = pendingStarts.get(toolName) ?? [];
      queue.push({ argsSummary, taskSeq: event.task_seq });
      pendingStarts.set(toolName, queue);
      continue;
    }

    const queue = pendingStarts.get(toolName) ?? [];
    const started = queue.shift();
    if (queue.length > 0) {
      pendingStarts.set(toolName, queue);
    } else {
      pendingStarts.delete(toolName);
    }
    const effectiveArgs = started?.argsSummary || argsSummary;
    if (eventType === "TOOL_CALL_FAILED") {
      const errorMessage = summarizeText(readPayloadString(payload, "error_message"), 96);
      records.push({
        id: `${toolName}-${event.task_seq}`,
        toolName,
        argsSummary: effectiveArgs,
        outputSummary: errorMessage,
        stateLabel: "失败",
        tone: "danger",
        summary: errorMessage || "这次工具调用失败了。",
        taskSeq: event.task_seq,
      });
      continue;
    }
    const outputSummary = summarizeText(readPayloadString(payload, "output_summary"), 96);
    records.push({
      id: `${toolName}-${event.task_seq}`,
      toolName,
      argsSummary: effectiveArgs,
      outputSummary,
      stateLabel: "已返回",
      tone: "success",
      summary: outputSummary || (effectiveArgs ? `参数：${effectiveArgs}` : "工具已返回结果。"),
      taskSeq: event.task_seq,
    });
  }

  for (const [toolName, queue] of pendingStarts.entries()) {
    for (const pending of queue) {
      records.push({
        id: `${toolName}-${pending.taskSeq}`,
        toolName,
        argsSummary: pending.argsSummary,
        outputSummary: "",
        stateLabel: "调用中",
        tone: "running",
        summary: pending.argsSummary ? `参数：${pending.argsSummary}` : "这次调用还没返回结果。",
        taskSeq: pending.taskSeq,
      });
    }
  }

  records.sort((left, right) => left.taskSeq - right.taskSeq);
  return records;
}

function buildToolTraceEntries(workEvents: TaskEvent[]): ChatTraceEntry[] {
  const records = buildToolTimelineRecords(workEvents);
  if (records.length === 0) {
    return [
      {
        id: "tool-stage-none",
        label: "工具调用",
        stateLabel: "未记录",
        tone: "draft",
        summary: "这轮没有记录到受治理工具事件。",
        detailInput: "没有记录到当前任务链内的受治理工具调用。",
        detailOutput: "如果这轮本应使用工具，请继续查看模型或审批轨迹，确认是否被策略阻断。",
      },
    ];
  }
  return records.map((record, index) => ({
    id: `tool-stage-${record.id}`,
    label: `${index + 1}. ${record.toolName}`,
    stateLabel: record.stateLabel,
    tone: record.tone,
    summary: record.summary,
    detailInput: record.argsSummary || "这次工具调用没有记录额外参数。",
    detailOutput: record.outputSummary || (record.stateLabel === "调用中" ? "结果尚未返回。" : record.summary),
  }));
}

function formatInboundMessageLabel(messageType: string, fallbackLatestMessageType: string): string {
  const normalized = (messageType || fallbackLatestMessageType || "").trim().toUpperCase();
  switch (normalized) {
    case "RESULT":
      return "内部结果";
    case "HEARTBEAT":
      return "内部进度";
    case "UPDATE":
      return "内部更新";
    case "ERROR":
      return "内部错误";
    default:
      return normalized ? `内部回执 · ${normalized}` : "内部回执";
  }
}

function buildApprovalTraceEntry(workEvents: TaskEvent[]): ChatTraceEntry | null {
  const expired = [...workEvents]
    .reverse()
    .find((event) => String(event.type) === "APPROVAL_EXPIRED");
  if (expired) {
    return {
      id: `approval-${expired.task_seq}`,
      label: "审批",
      stateLabel: "已超时",
      tone: "danger",
      summary: "这一步需要你的确认，但审批超时后已经自动拒绝。",
      detailInput: readPayloadString(expired.payload ?? {}, "tool_name") || "某个高风险动作",
      detailOutput: "审批超时，系统已经自动拒绝这一步。",
    };
  }
  const requested = [...workEvents]
    .reverse()
    .find((event) => String(event.type) === "APPROVAL_REQUESTED");
  if (requested) {
    const toolName = readPayloadString(requested.payload ?? {}, "tool_name");
    return {
      id: `approval-${requested.task_seq}`,
      label: "审批",
      stateLabel: "等你确认",
      tone: "warning",
      summary: toolName
        ? `这一步想调用 ${toolName}，但继续前需要你确认一次。`
        : "这一步继续前需要你确认一次。",
      detailInput: toolName || "某个高风险动作",
      detailOutput: "等待你确认后才会继续往下执行。",
    };
  }
  return null;
}

function buildModelTraceEntry(workEvents: TaskEvent[]): ChatTraceEntry | null {
  const completed = [...workEvents]
    .reverse()
    .find((event) => String(event.type) === "MODEL_CALL_COMPLETED");
  if (completed) {
    const payload = completed.payload ?? {};
    const attempt = readPayloadString(payload, "attempt");
    const step = readPayloadString(payload, "step");
    return {
      id: `model-${completed.task_seq}`,
      label: "模型处理",
      stateLabel: "已完成",
      tone: "success",
      summary:
        attempt || step
          ? `已完成一轮模型处理${attempt ? `（attempt ${attempt}` : ""}${step ? ` / step ${step}）` : attempt ? "）" : ""}。`
          : "已完成一轮模型处理。",
      detailInput:
        step || attempt
          ? `step=${step || "unknown"}${attempt ? `, attempt=${attempt}` : ""}`
          : "模型已接手当前问题。",
      detailOutput: "本轮模型处理已经完成，并把结果继续交给后续阶段。",
    };
  }
  const failed = [...workEvents]
    .reverse()
    .find((event) => String(event.type) === "MODEL_CALL_FAILED");
  if (failed) {
    return {
      id: `model-${failed.task_seq}`,
      label: "模型处理",
      stateLabel: "失败",
      tone: "danger",
      summary: summarizeText(readPayloadString(failed.payload ?? {}, "error_message") || "模型调用失败。", 120),
      detailInput: "模型已尝试处理当前问题。",
      detailOutput:
        summarizeText(readPayloadString(failed.payload ?? {}, "error_message") || "模型调用失败。", 240),
    };
  }
  const started = [...workEvents]
    .reverse()
    .find((event) => String(event.type) === "MODEL_CALL_STARTED");
  if (started) {
    return {
      id: `model-${started.task_seq}`,
      label: "模型处理",
      stateLabel: "处理中",
      tone: "running",
      summary: "Worker 已进入模型处理阶段。",
      detailInput: "模型已经接到这轮问题，正在组织下一步输出。",
      detailOutput: "结果尚未返回。",
    };
  }
  return null;
}

function buildAgentTraceEntries(
  work: WorkProjectionItem,
  workEvents: TaskEvent[],
  latestMessageType: string,
  taskStatus: string,
  isDirectExecution: boolean
): ChatActivityItem["traceEntries"] {
  if (isDirectExecution) {
    const entries: ChatTraceEntry[] = [
      {
        id: `${work.work_id}-direct`,
        label: "处理方式",
        stateLabel: "直连工具",
        tone: "running",
        summary: "这轮由主助手直接调用当前挂载的工具处理，没有另起 specialist worker。",
        detailInput: "当前问题被判定为可以由主助手直接处理。",
        detailOutput: "这轮不会再另起 specialist worker。",
      },
      {
        id: `${work.work_id}-tools`,
        label: "可用工具",
        stateLabel: "已挂载",
        tone: "draft",
        summary: summarizeToolList(ensureArray(work.selected_tools)),
        detailInput: "主助手当前可直接调用的工具集合。",
        detailOutput: summarizeToolList(ensureArray(work.selected_tools)),
      },
    ];
    const modelEntry = buildModelTraceEntry(workEvents);
    if (modelEntry) {
      entries.push(modelEntry);
    }
    entries.push(...buildToolTraceEntries(workEvents));
    const approvalEntry = buildApprovalTraceEntry(workEvents);
    if (approvalEntry) {
      entries.push(approvalEntry);
    }
    const normalizedTaskStatus = taskStatus.trim().toUpperCase();
    if (normalizedTaskStatus === "SUCCEEDED") {
      entries.push({
        id: `${work.work_id}-finalize`,
        label: "最终收口",
        stateLabel: "已回复",
        tone: "success",
        summary: "主助手已经直接整理好工具结果，并把最终答复返回给用户。",
        detailInput: "主助手已经拿到足够结果。",
        detailOutput: "最终答复已经返回给用户。",
      });
    } else if (normalizedTaskStatus === "FAILED") {
      entries.push({
        id: `${work.work_id}-finalize`,
        label: "最终收口",
        stateLabel: "失败",
        tone: "danger",
        summary: "这轮直连处理没有顺利完成，主助手没有拿到可直接返回的最终结果。",
        detailInput: "主助手尝试收口这轮处理。",
        detailOutput: "这轮没有形成可直接返回的最终答复。",
      });
    }
    return entries;
  }

  const entries: ChatTraceEntry[] = [];
  entries.push({
    id: `${work.work_id}-dispatch`,
    label: "委派目标",
    stateLabel: "已发送",
    tone: "running",
    summary: summarizeDelegationIntent(work),
    detailInput: "Agent 已经把这轮问题改写成内部执行目标。",
    detailOutput: summarizeDelegationIntent(work),
  });
  entries.push({
    id: `${work.work_id}-tools`,
    label: "授权工具",
    stateLabel: "已挂载",
    tone: "draft",
    summary: summarizeToolList(ensureArray(work.selected_tools)),
    detailInput: "这轮委派允许 Worker 使用的工具。",
    detailOutput: summarizeToolList(ensureArray(work.selected_tools)),
  });
  const latestInbound = [...workEvents]
    .reverse()
    .find((event) => ["A2A_MESSAGE_RECEIVED", "WORKER_RETURNED"].includes(String(event.type)));
  if (latestInbound) {
    const inboundType = String(latestInbound.type);
    const messageType = readPayloadString(latestInbound.payload, "message_type");
    const latestLabel =
      inboundType === "WORKER_RETURNED"
        ? "Worker 返回"
        : formatInboundMessageLabel(messageType, latestMessageType);
    const latestSummary =
      inboundType === "WORKER_RETURNED"
        ? summarizeText(readPayloadString(latestInbound.payload, "summary") || "内部角色已经返回结果。", 120)
        : summarizeText(
            messageType === "RESULT"
              ? "内部结果已经返回主助手，主助手还在继续整理。"
              : "内部角色还在继续推进，主助手会根据回执更新下一步。",
            120
          );
    entries.push({
      id: `${work.work_id}-latest`,
      label: latestLabel,
      stateLabel: messageType === "RESULT" ? "已收到" : "处理中",
      tone: messageType === "RESULT" ? "success" : "running",
      summary: latestSummary,
      detailInput:
        inboundType === "WORKER_RETURNED"
          ? "Worker 已经把处理状态返回给主助手。"
          : `收到一条 ${messageType || latestMessageType || "UPDATE"} 回执。`,
      detailOutput: latestSummary,
    });
  }
  const normalizedTaskStatus = taskStatus.trim().toUpperCase();
  if (latestMessageType.trim().toUpperCase() === "RESULT" || normalizedTaskStatus === "SUCCEEDED") {
    entries.push({
      id: `${work.work_id}-finalize`,
      label: "最终收口",
      stateLabel: normalizedTaskStatus === "SUCCEEDED" ? "已回复" : "整理中",
      tone: normalizedTaskStatus === "SUCCEEDED" ? "success" : "running",
      summary:
        normalizedTaskStatus === "SUCCEEDED"
          ? "主助手已经把内部结果整理成最终答复并返回给用户。"
          : "主助手已经拿到内部结果，正在继续核对和整理最终答复。",
      detailInput:
        normalizedTaskStatus === "SUCCEEDED"
          ? "主助手已经拿到内部结果。"
          : "主助手已经收到 Worker 的回传结果。",
      detailOutput:
        normalizedTaskStatus === "SUCCEEDED"
          ? "最终答复已经返回给用户。"
          : "主助手还在继续核对并整理最终回复。",
    });
  }
  return entries;
}

function buildWorkerTraceEntries(
  work: WorkProjectionItem,
  workEvents: TaskEvent[]
): ChatActivityItem["traceEntries"] {
  const entries: ChatTraceEntry[] = [
    {
      id: `${work.work_id}-scope`,
      label: "接手执行",
      stateLabel: formatActivityStateLabel(resolveWorkStatus(work), ""),
      tone: formatActivityTone(resolveWorkStatus(work), ""),
      summary:
        resolveWorkStatus(work) === "RUNNING"
          ? "Worker 已接手这轮内部任务，开始推进执行。"
          : "Worker 已经接手并推进过这轮内部任务。",
      detailInput: "Worker 已收到主助手下发的任务目标。",
      detailOutput:
        resolveWorkStatus(work) === "RUNNING"
          ? "正在推进执行。"
          : "这轮内部任务已经进入执行或完成阶段。",
    },
  ];
  const modelEntry = buildModelTraceEntry(workEvents);
  if (modelEntry) {
    entries.push(modelEntry);
  }
  entries.push(...buildToolTraceEntries(workEvents));
  const returnedEvent = [...workEvents]
    .reverse()
    .find((event) => String(event.type) === "WORKER_RETURNED");
  if (returnedEvent) {
    const status = readPayloadString(returnedEvent.payload, "status").toUpperCase();
    entries.push({
      id: `${work.work_id}-returned`,
      label: "返回主助手",
      stateLabel:
        status === "SUCCEEDED" ? "已完成" : status === "FAILED" ? "失败" : "处理中",
      tone:
        status === "SUCCEEDED" ? "success" : status === "FAILED" ? "danger" : "running",
      summary: summarizeText(
        readPayloadString(returnedEvent.payload, "summary") || "内部角色已把这轮处理状态回传给主助手。",
        120
      ),
      detailInput: "Worker 已完成这一轮处理并准备回传。",
      detailOutput: summarizeText(
        readPayloadString(returnedEvent.payload, "summary") || "内部角色已把这轮处理状态回传给主助手。",
        240
      ),
    });
  } else {
    entries.push({
      id: `${work.work_id}-returned-pending`,
      label: "返回主助手",
      stateLabel: "待返回",
      tone: "draft",
      summary: "Worker 还没有把最终处理状态回传给主助手。",
      detailInput: "Worker 还在执行中。",
      detailOutput: "主助手暂时还没收到这轮最终状态。",
    });
  }
  return entries;
}

function buildAgentActivity(
  taskStatus: string,
  streaming: boolean,
  hasInternalCollaboration: boolean,
  latestMessageType: string,
  isDirectExecution: boolean
): ChatActivityItem {
  const normalizedStatus = taskStatus.trim().toUpperCase();
  const normalizedMessageType = latestMessageType.trim().toUpperCase();

  if (normalizedStatus === "WAITING_INPUT") {
    return {
      id: "butler",
      actor: "主助手",
      stateLabel: "等你补充",
      tone: "warning",
      summary: "主助手已经识别到还差关键信息，补一句就能继续。",
    };
  }
  if (normalizedStatus === "WAITING_APPROVAL") {
    return {
      id: "butler",
      actor: "主助手",
      stateLabel: "等你确认",
      tone: "warning",
      summary: "这一步需要你确认，系统才会继续往下执行。",
    };
  }
  if (hasInternalCollaboration && normalizedMessageType === "RESULT") {
    return {
      id: "butler",
      actor: "主助手",
      stateLabel: "整理中",
      tone: "running",
      summary: "内部结果已经拿到，但主助手还在整理、核对并收口最终回复。",
    };
  }
  if (hasInternalCollaboration) {
    return {
      id: "butler",
      actor: "主助手",
      stateLabel: "协调中",
      tone: "running",
      summary: "主助手正在协调内部角色、补充上下文和收口结果。",
    };
  }
  if (isDirectExecution) {
    return {
      id: "butler",
      actor: "主助手",
      stateLabel: normalizedStatus === "WAITING_APPROVAL" ? "等你确认" : "进行中",
      tone: normalizedStatus === "WAITING_APPROVAL" ? "warning" : "running",
      summary:
        normalizedStatus === "WAITING_APPROVAL"
          ? "主助手已经找到下一步，但其中有一步需要你确认后才能继续。"
          : "主助手正在直接调用工具处理这条消息。",
    };
  }
  if (streaming || normalizedStatus === "RUNNING" || normalizedStatus === "QUEUED") {
    return {
      id: "butler",
      actor: "主助手",
      stateLabel: "进行中",
      tone: "running",
      summary: "主助手正在直接处理这条消息。",
    };
  }
  return {
    id: "butler",
    actor: "主助手",
    stateLabel: "准备中",
    tone: "draft",
    summary: "这轮对话已经进入处理流程，稍后会继续推进。",
  };
}

function buildWorkerActivity(
  work: WorkProjectionItem,
  fallbackLatestMessageType: string
): ChatActivityItem | null {
  if (isAgentDirectExecution(work)) {
    return null;
  }
  const actor = formatAgentRoleLabel(resolveWorkActor(work));
  if (actor === "主助手") {
    return null;
  }
  const status = resolveWorkStatus(work);
  return {
    id: work.work_id,
    actor,
    stateLabel: formatActivityStateLabel(status, fallbackLatestMessageType),
    tone: formatActivityTone(status, fallbackLatestMessageType),
    summary: formatActorSummary(actor, work.title, status, fallbackLatestMessageType),
  };
}

export default function ChatWorkbench() {
  const { sessionId: routeSessionId } = useParams<{ sessionId?: string }>();
  const navigate = useNavigate();
  const { snapshot, refreshResources, submitAction, busyActionId } = useWorkbench();
  const sessionDocument = snapshot!.resources.sessions;
  const projectSelector = snapshot!.resources.project_selector;
  const availableProjects = ensureArray(projectSelector?.available_projects);

  const sessions = ensureArray(sessionDocument?.sessions);
  const workerProfilesDocument = snapshot!.resources.worker_profiles;
  const workerProfiles = ensureArray(workerProfilesDocument?.profiles);
  const delegationWorks = ensureArray(snapshot!.resources.delegation.works);
  const context = snapshot!.resources.context_continuity;
  const contextFrames = ensureArray(context.frames);
  const a2aConversations = ensureArray(context.a2a_conversations);
  const storedRestoreTaskId = useMemo(() => readStoredTaskId(), []);

  // 当 URL 指定了 sessionId 时，直接用该 session；否则用首个 web session 作为默认
  const webSessions = useMemo(
    () => {
      const web = sessions.filter((s) => s.channel === "web");
      return web.length > 0 ? web : sessions;
    },
    [sessions]
  );
  const routeSession = useMemo(
    () =>
      routeSessionId
        ? sessions.find((item) => item.session_id === routeSessionId) ?? null
        : null,
    [routeSessionId, sessions]
  );

  // 当 URL 指定了 session（Worker 会话路由）时，restoreTaskIds 必须完全隔离于
  // 全局 focused_session_id 和 sessions 轮询变化，避免 deps 变化导致 fallthrough
  // 到主 Agent 的 taskIds（根因：sessionDocument?.sessions 每次轮询是新引用，
  // 而 routeSession 可能在轮询间隙瞬间为 null）。
  const routeRestoreTaskIds = useMemo(
    () => (routeSession?.task_id ? [routeSession.task_id] : []),
    [routeSession?.task_id],
  );
  const defaultRestoreTaskIds = useMemo(() => {
    if (routeSessionId) return []; // route 模式下不使用，跳过计算
    const taskIds = sessionDocument ? resolveRestorableTaskIds(sessionDocument) : [];
    if (storedRestoreTaskId && !taskIds.includes(storedRestoreTaskId)) {
      taskIds.unshift(storedRestoreTaskId);
    }
    return taskIds;
  }, [
    routeSessionId,
    sessionDocument?.focused_session_id,
    sessionDocument?.focused_thread_id,
    sessionDocument?.new_conversation_token,
    sessionDocument?.sessions,
    storedRestoreTaskId,
  ]);
  const restoreTaskIds = routeSessionId ? routeRestoreTaskIds : defaultRestoreTaskIds;
  // 始终自动续接最近会话，不再弹出"继续还是新开"选择面板
  const restoreChoice: RestoreChoice = "continue";
  const resumeSession =
    routeSessionId
      ? routeSession
      : webSessions[0] || null;

  // 当 URL 指向已有 session 或已有 restore task 时，不传 newConversationToken——
  // 避免 stale token 把消息路由到错误的 project。
  // Token 只在通过 NewSessionModal 创建全新会话时使用。
  const shouldContinueRestore = restoreChoice === "continue" && restoreTaskIds.length > 0;
  const hasExistingSession = Boolean(routeSessionId) || shouldContinueRestore;
  const { messages, sendMessage, resetConversation, streaming, restoring, error, taskId, liveApproval, approvalSignal } = useChatStream(
    shouldContinueRestore ? { taskIds: restoreTaskIds } : null,
    {
      activeProjectId: resumeSession?.project_id || projectSelector?.current_project_id || "",
      activeWorkspaceId: resumeSession?.workspace_id || projectSelector?.current_workspace_id || "",
      newConversationToken: hasExistingSession ? "" : (sessionDocument?.new_conversation_token ?? ""),
      newConversationProjectId: hasExistingSession ? "" : (sessionDocument?.new_conversation_project_id ?? ""),
      newConversationWorkspaceId: hasExistingSession ? "" : (sessionDocument?.new_conversation_workspace_id ?? ""),
      newConversationAgentProfileId: hasExistingSession
        ? ""
        : (sessionDocument?.new_conversation_agent_profile_id ?? ""),
    },
    {
      deferStoredTaskIdRestore:
        restoreTaskIds.length > 0 && restoreChoice !== "continue",
      skipSessionFocus: Boolean(routeSessionId),
    }
  );
  const [input, setInput] = useState("");
  const [showSessionInternalRefs, setShowSessionInternalRefs] = useState(false);
  const [showRestoreEscape, setShowRestoreEscape] = useState(false);
  const [isEditingSessionAlias, setIsEditingSessionAlias] = useState(false);
  const [sessionAliasDraft, setSessionAliasDraft] = useState("");

  // 消息列表变化时自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const [taskDetail, setTaskDetail] = useState<TaskDetailResponse | null>(null);
  const [executionSession, setExecutionSession] = useState<ExecutionSessionDocument | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState<ApprovalListItem[]>([]);
  const [chatActionNotice, setChatActionNotice] = useState<{
    tone: "info" | "success" | "error";
    title: string;
    message: string;
  } | null>(null);
  const [steeringBusy, setSteeringBusy] = useState(false);
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0);
  const [approvalNow, setApprovalNow] = useState(() => Date.now());
  const [freshTurnStartedAt, setFreshTurnStartedAt] = useState<number | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const sessionAliasInputRef = useRef<HTMLInputElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const defaultRootAgentId = readSummaryString(workerProfilesDocument?.summary ?? {}, "default_profile_id");
  const defaultRootAgent = workerProfiles.find((profile) => profile.profile_id === defaultRootAgentId);
  const activeSession = sessions.find((item) => item.task_id === taskId) ?? null;
  const currentSession =
    taskId != null
      ? activeSession ?? (routeSessionId ? routeSession : webSessions[0] || null)
      : routeSessionId
        ? routeSession
        : restoreChoice === "continue"
          ? webSessions[0] || null
          : null;
  const activeExecutionSummary =
    activeSession?.execution_summary &&
    typeof activeSession.execution_summary === "object" &&
    !Array.isArray(activeSession.execution_summary)
      ? (activeSession.execution_summary as Record<string, unknown>)
      : null;
  const activeWorkId = typeof activeExecutionSummary?.work_id === "string" ? activeExecutionSummary.work_id : "";
  const latestTaskWork =
    sortWorksByUpdate(delegationWorks.filter((item) => item.task_id === taskId))[0] ?? null;
  const activeWork = delegationWorks.find((item) => item.work_id === activeWorkId) ?? latestTaskWork;
  const activeContextFrame =
    contextFrames.find((item) => item.task_id === taskId) ??
    (activeSession ? contextFrames.find((item) => item.session_id === activeSession.session_id) ?? null : null);
  const activeSessionOwnerProfileId = resolveSessionOwnerProfileId(activeSession);
  const activeCompatibilityFlags = ensureArray(currentSession?.compatibility_flags);
  const legacyResetRecommended = Boolean(currentSession?.reset_recommended);
  const compatibilityMessage =
    currentSession?.compatibility_message?.trim() ||
    (activeCompatibilityFlags.includes("legacy_context_polluted")
      ? "这条历史会话仍沿用旧版 owner/profile 继承语义，建议先重置 continuity，再继续新的对话。"
      : "");
  const isDirectExecution = isAgentDirectExecution(activeWork);
  const activeConversationId =
    readSummaryString(activeWork?.runtime_summary ?? {}, "research_a2a_conversation_id") ||
    activeWork?.a2a_conversation_id ||
    "";
  const activeA2AConversationRecord =
    (activeConversationId
      ? a2aConversations.find((item) => item.a2a_conversation_id === activeConversationId) ?? null
      : null) ??
    (activeWork?.work_id
      ? a2aConversations.find((item) => item.work_id === activeWork.work_id) ?? null
      : null) ??
    (taskId ? a2aConversations.find((item) => item.task_id === taskId) ?? null : null);

  const hasInternalCollaboration =
    !isDirectExecution &&
    (activeA2AConversationRecord != null ||
      Boolean(activeConversationId || readSummaryString(activeWork?.runtime_summary ?? {}, "research_worker_id")));
  const activeConversationLatestType = activeA2AConversationRecord?.latest_message_type || "";
  const activeConversationWorkerSessionId =
    activeA2AConversationRecord?.target_agent_session_id ||
    readSummaryString(activeWork?.runtime_summary ?? {}, "research_worker_agent_session_id") ||
    activeWork?.worker_agent_session_id ||
    "";
  const normalizedTaskStatus = String(taskDetail?.task?.status ?? activeSession?.status ?? "").trim().toUpperCase();
  const hasLoadedTaskStatus = normalizedTaskStatus.length > 0;
  const shouldPollLiveState =
    Boolean(taskId) &&
    (streaming ||
      (hasLoadedTaskStatus &&
        !TERMINAL_TASK_STATUSES.has(normalizedTaskStatus) &&
        (hasInternalCollaboration ||
          ["QUEUED", "RUNNING", "WAITING_APPROVAL", "WAITING_INPUT"].includes(normalizedTaskStatus))));

  const relatedWorks = sortWorksByUpdate(
    delegationWorks.filter((item) => {
      if (!activeWork?.work_id) {
        return false;
      }
      if (item.work_id === activeWork.work_id) {
        return true;
      }
      if (item.parent_work_id === activeWork.work_id) {
        return true;
      }
      return false;
    })
  );
  const workEvents = ensureArray(taskDetail?.events).filter((event) =>
    activeWork?.work_id ? payloadMatchesWork(event, activeWork.work_id) : false
  );
  const latestRuntimeEvidenceMs = ensureArray(taskDetail?.events).reduce((latest, event) => {
    const parsed = Date.parse(String(event.ts ?? ""));
    return Number.isFinite(parsed) ? Math.max(latest, parsed) : latest;
  }, 0);
  const suppressHistoricalActivity =
    freshTurnStartedAt != null && latestRuntimeEvidenceMs < freshTurnStartedAt - 500;
  const workerActivities: ChatActivityItem[] = relatedWorks.reduce<ChatActivityItem[]>((items, work) => {
      const activity = buildWorkerActivity(work, activeConversationLatestType);
      if (!activity) {
        return items;
      }
      items.push({
        ...activity,
        traceTitle: `${activity.actor} 的处理轨迹`,
        traceEntries: buildWorkerTraceEntries(
          work,
          workEvents.filter((event) => payloadMatchesWork(event, work.work_id))
        ),
      });
      return items;
    }, []);
  const fallbackWorkerActor = activeA2AConversationRecord?.target_agent
    ? formatAgentRoleLabel(activeA2AConversationRecord.target_agent)
    : activeWork
      ? formatAgentRoleLabel(resolveWorkActor(activeWork))
      : "";
  const fallbackWorkerActivity: ChatActivityItem[] =
    hasInternalCollaboration &&
    workerActivities.length === 0 &&
    fallbackWorkerActor !== "主助手" &&
    fallbackWorkerActor
      ? [
          {
            id: "worker-fallback",
            actor: fallbackWorkerActor,
            stateLabel: formatActivityStateLabel(
              activeWork ? resolveWorkStatus(activeWork) : "",
              activeConversationLatestType || "TASK"
            ),
            tone: formatActivityTone(
              activeWork ? resolveWorkStatus(activeWork) : "",
              activeConversationLatestType || "TASK"
            ),
            summary: formatActorSummary(
              fallbackWorkerActor,
              activeWork?.title ?? "",
              activeWork ? resolveWorkStatus(activeWork) : "",
              activeConversationLatestType || "TASK"
            ),
          },
        ]
      : [];
  const activityItems: ChatActivityItem[] =
    taskId && suppressHistoricalActivity
      ? [
          {
            id: "butler-fresh-turn",
            actor: "主助手",
            stateLabel: "进行中",
            tone: "running",
            summary: "主助手正在开始处理这条新消息，稍后会替换成这轮真实的工具链和处理阶段。",
            traceTitle: "主助手的直连处理轨迹",
            traceEntries: [],
          },
        ]
      : taskId
        ? [
            {
              ...buildAgentActivity(
                normalizedTaskStatus,
                streaming,
                hasInternalCollaboration,
                activeConversationLatestType,
                isDirectExecution
              ),
              traceTitle: isDirectExecution ? "主助手的直连处理轨迹" : "主助手的委派轨迹",
              traceEntries: activeWork
                ? buildAgentTraceEntries(
                    activeWork,
                    workEvents,
                    activeConversationLatestType,
                    normalizedTaskStatus,
                    isDirectExecution
                  )
                : [],
            },
            ...workerActivities.slice(0, 2),
            ...fallbackWorkerActivity,
          ]
        : [];
  const isRestoringConversation = restoring && messages.length === 0;
  const isEmptyConversation =
    messages.length === 0 && !isRestoringConversation;
  const shouldShowInlineActivity =
    Boolean(taskId) &&
    (streaming || !hasLoadedTaskStatus || !TERMINAL_TASK_STATUSES.has(normalizedTaskStatus));
  const loadingLabel = hasInternalCollaboration ? "正在整理回复" : "正在处理这条消息";
  const activeStreamingMessageId =
    [...messages]
      .reverse()
      .find((message) => message.role === "agent" && message.isStreaming)?.id ?? null;
  const shouldShowSyntheticProgressBubble =
    shouldShowInlineActivity &&
    activityItems.length > 0 &&
    !activeStreamingMessageId &&
    !isRestoringConversation &&
    !isEmptyConversation;
  const taskStatusLabel = formatTaskStatusLabel(normalizedTaskStatus);
  const taskStatusTone = formatTaskStatusTone(normalizedTaskStatus);
  const conversationTitle =
    taskDetail?.task?.title ||
    activeSession?.title ||
    activeSession?.latest_message_summary ||
    (taskId ? "这轮对话正在处理中" : "开始一段对话");
  const techRefs = [
    taskId ? { label: "任务 ID", value: taskId } : null,
    activeSession?.session_id ? { label: "会话 ID", value: activeSession.session_id } : null,
    activeWork?.work_id ? { label: "Work ID", value: activeWork.work_id } : null,
    activeConversationId ? { label: "协作链路 ID", value: activeConversationId } : null,
    activeConversationWorkerSessionId ? { label: "执行会话", value: activeConversationWorkerSessionId } : null,
    activeContextFrame?.context_frame_id ? { label: "上下文帧 ID", value: activeContextFrame.context_frame_id } : null,
  ].filter((item): item is { label: string; value: string } => Boolean(item));
  const activeSessionProjectId = activeSession?.project_id ?? "";
  const activeSessionWorkId = readSummaryString(
    (activeSession?.execution_summary ?? {}) as Record<string, unknown>,
    "work_id"
  );
  const pendingConversationProjectId = sessionDocument?.new_conversation_project_id ?? "";
  const pendingConversationAgentProfileId =
    sessionDocument?.new_conversation_agent_profile_id ?? "";
  const effectiveProjectId =
    activeSessionProjectId || pendingConversationProjectId || (projectSelector?.current_project_id ?? "");
  const effectiveProjectLabel = effectiveProjectId
    ? resolveProjectName(availableProjects, effectiveProjectId)
    : "";
  const operatorItems = ensureArray(sessionDocument?.operator_items);
  const activeTaskOperatorItems = operatorItems.filter((item) => {
    if (item.state && String(item.state).trim().toLowerCase() !== "pending") {
      return false;
    }
    if (taskId && item.task_id === taskId) {
      return true;
    }
    if (activeSession?.thread_id && item.thread_id === activeSession.thread_id) {
      return true;
    }
    return false;
  });
  const activeApprovalItemFromInbox =
    activeTaskOperatorItems.find((item) =>
      item.quick_actions.some((action) =>
        ["approve_once", "approve_always", "deny"].includes(action.kind)
      )
    ) ?? null;
  const latestApprovalContext =
    taskDetail?.events && taskId
      ? readLatestApprovalContext(
          taskDetail.events,
          activeWork?.work_id || activeSessionWorkId
        )
      : null;
  const latestExpiredApprovalContext =
    taskDetail?.events && taskId
      ? readLatestExpiredApprovalContext(
          taskDetail.events,
          activeWork?.work_id || activeSessionWorkId
        )
      : null;
  const syntheticApprovalItem =
    taskId && pendingApprovals.length > 0 ? buildSyntheticApprovalItem(pendingApprovals[0]!) : null;
  const executionSessionApprovalItem =
    taskId && (executionSession?.pending_approval_id || latestApprovalContext?.approvalId)
      ? buildExecutionSessionApprovalItem(
          executionSession?.pending_approval_id || latestApprovalContext?.approvalId || "",
          {
            taskId,
            toolName: latestApprovalContext?.toolName,
            argsSummary: latestApprovalContext?.argsSummary,
            summary: latestApprovalContext?.summary,
            createdAt: latestApprovalContext?.createdAt,
            expiresAt: latestApprovalContext?.expiresAt,
          }
        )
      : null;
  // SSE 直通审批项：从 useChatStream 的 liveApproval 直接构造，不依赖 REST 轮询
  const liveApprovalItem =
    liveApproval && liveApproval.approvalId
      ? buildExecutionSessionApprovalItem(liveApproval.approvalId, {
          taskId: liveApproval.taskId || taskId || "",
          toolName: liveApproval.toolName,
          argsSummary: liveApproval.toolArgsSummary,
          summary: liveApproval.riskExplanation,
          createdAt: liveApproval.createdAt,
          expiresAt: liveApproval.expiresAt || null,
        })
      : null;
  const activeApprovalItem =
    activeApprovalItemFromInbox ?? syntheticApprovalItem ?? executionSessionApprovalItem ?? liveApprovalItem;
  const activeApprovalExpiresAtMs = activeApprovalItem?.expires_at
    ? Date.parse(activeApprovalItem.expires_at)
    : Number.NaN;
  const activeApprovalRemainingSeconds = Number.isFinite(activeApprovalExpiresAtMs)
    ? Math.max(0, Math.ceil((activeApprovalExpiresAtMs - approvalNow) / 1000))
    : null;
  const latestExpiredApprovalAgeSeconds = latestExpiredApprovalContext?.expiredAt
    ? Math.max(0, Math.floor((approvalNow - Date.parse(latestExpiredApprovalContext.expiredAt)) / 1000))
    : null;
  const slashCommandMatches = useMemo(() => {
    const normalized = input.trim().toLowerCase();
    if (!normalized.startsWith("/")) {
      return [];
    }
    return CHAT_SLASH_COMMANDS.filter((item) => item.value.startsWith(normalized));
  }, [input]);
  const canSteerCurrentRun =
    Boolean(taskId) &&
    normalizedTaskStatus === "WAITING_INPUT" &&
    executionSession?.can_attach_input !== false;
  // 会话 owner = 用户首先选择与之对话的 Agent；不是本轮执行 target。
  const currentSessionOwnerProfileId =
    activeSessionOwnerProfileId ||
    resolveSessionOwnerProfileId(currentSession) ||
    pendingConversationAgentProfileId ||
    defaultRootAgentId;
  const conversationOwnerName =
    (activeSession ?? currentSession)?.session_owner_name?.trim() ||
    workerProfiles.find((p) => p.profile_id === currentSessionOwnerProfileId)?.name ||
    defaultRootAgent?.name ||
    "OctoAgent";
  const currentSessionAlias =
    currentSession?.alias?.trim() || activeSession?.alias?.trim() || "";
  const sessionTitleBase =
    (currentSession?.title?.trim()) ||
    (activeSession?.title?.trim()) ||
    effectiveProjectLabel ||
    "";
  const sessionDisplayName = formatSessionDisplayTitle({
    alias: currentSessionAlias,
    title: sessionTitleBase,
    fallbackTitle: conversationTitle,
  });
  const sessionTitleLabel = sessionDisplayName;
  const editableSessionId =
    currentSession?.session_id || activeSession?.session_id || routeSessionId || "";
  const editableThreadId =
    currentSession?.thread_id || activeSession?.thread_id || routeSession?.thread_id || "";
  const canEditSessionAlias = Boolean(editableSessionId);
  const isSavingSessionAlias = busyActionId === "session.set_alias";
  const inputPlaceholder = canSteerCurrentRun
    ? executionSession?.requested_input?.trim() || "直接补充当前这轮需要的信息"
    : `告诉 ${conversationOwnerName} 你现在要做什么`;


  useEffect(() => {
    if (!isRestoringConversation) {
      setShowRestoreEscape(false);
      return;
    }
    const timer = window.setTimeout(() => {
      setShowRestoreEscape(true);
    }, 1600);
    return () => {
      window.clearTimeout(timer);
    };
  }, [isRestoringConversation]);

  useEffect(() => {
    if (slashCommandMatches.length === 0) {
      setSelectedCommandIndex(0);
      return;
    }
    setSelectedCommandIndex((current) =>
      Math.min(Math.max(current, 0), slashCommandMatches.length - 1)
    );
  }, [slashCommandMatches]);

  useEffect(() => {
    if (!activeApprovalItem) {
      return;
    }
    setApprovalNow(Date.now());
    const timer = window.setInterval(() => {
      setApprovalNow(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [activeApprovalItem?.item_id, activeApprovalItem?.expires_at]);

  useEffect(() => {
    if (isEditingSessionAlias) {
      return;
    }
    setSessionAliasDraft(sessionDisplayName || conversationTitle);
  }, [conversationTitle, isEditingSessionAlias, sessionDisplayName]);

  useEffect(() => {
    if (!isEditingSessionAlias) {
      return;
    }
    sessionAliasInputRef.current?.focus();
    sessionAliasInputRef.current?.select();
  }, [isEditingSessionAlias]);

  useEffect(() => {
    if (freshTurnStartedAt == null) {
      return;
    }
    if (latestRuntimeEvidenceMs >= freshTurnStartedAt - 500 || error) {
      setFreshTurnStartedAt(null);
    }
  }, [error, freshTurnStartedAt, latestRuntimeEvidenceMs]);

  useEffect(() => {
    let cancelled = false;

    async function loadDetail() {
      if (!taskId) {
        setTaskDetail(null);
        setExecutionSession(null);
        setPendingApprovals([]);
        return;
      }
      const [detailResult, sessionResult, approvalsResult] = await Promise.allSettled([
        fetchTaskDetail(taskId),
        fetchTaskExecutionSession(taskId),
        fetchApprovals(),
      ]);
      if (cancelled) {
        return;
      }
      setTaskDetail(detailResult.status === "fulfilled" ? detailResult.value : null);
      setExecutionSession(
        sessionResult.status === "fulfilled"
          ? readExecutionSessionDocument(sessionResult.value)
          : null
      );
      setPendingApprovals(
        approvalsResult.status === "fulfilled"
          ? approvalsResult.value.approvals.filter((item) => item.task_id === taskId)
          : []
      );
    }

    void loadDetail();
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  // 用 ref 持有最新 snapshot 资源引用，避免将频繁变化的值放入 useEffect 依赖
  // 导致 setInterval 被反复重建，产生请求风暴
  const snapshotResourcesRef = useRef(snapshot!.resources);
  snapshotResourcesRef.current = snapshot!.resources;
  const refreshResourcesRef = useRef(refreshResources);
  refreshResourcesRef.current = refreshResources;

  useEffect(() => {
    if (!taskId) {
      return;
    }
    let cancelled = false;
    const currentTaskId = taskId;

    async function refreshLiveState() {
      const res = snapshotResourcesRef.current;
      const resources = [
        {
          resource_type: res.sessions.resource_type,
          resource_id: res.sessions.resource_id,
          schema_version: res.sessions.schema_version,
        },
        {
          resource_type: res.delegation.resource_type,
          resource_id: res.delegation.resource_id,
          schema_version: res.delegation.schema_version,
        },
        {
          resource_type: res.context_continuity.resource_type,
          resource_id: res.context_continuity.resource_id,
          schema_version: res.context_continuity.schema_version,
        },
      ];

      const [detailResult, sessionResult, approvalsResult] = await Promise.allSettled([
        fetchTaskDetail(currentTaskId),
        fetchTaskExecutionSession(currentTaskId),
        fetchApprovals(),
      ]);
      if (cancelled) {
        return;
      }
      setTaskDetail(detailResult.status === "fulfilled" ? detailResult.value : null);
      setExecutionSession(
        sessionResult.status === "fulfilled"
          ? readExecutionSessionDocument(sessionResult.value)
          : null
      );
      setPendingApprovals(
        approvalsResult.status === "fulfilled"
          ? approvalsResult.value.approvals.filter((item) => item.task_id === currentTaskId)
          : []
      );
      if (cancelled) {
        return;
      }
      await refreshResourcesRef.current(resources);
    }

    void refreshLiveState();
    if (!shouldPollLiveState) {
      return;
    }

    const timer = window.setInterval(() => {
      void refreshLiveState();
    }, 3000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [taskId, shouldPollLiveState]);

  // SSE 审批事件触发立即刷新（不等 3s 轮询）
  useEffect(() => {
    if (approvalSignal === 0 || !taskId) {
      return;
    }
    let cancelled = false;
    (async () => {
      const [detailResult, sessionResult, approvalsResult] = await Promise.allSettled([
        fetchTaskDetail(taskId),
        fetchTaskExecutionSession(taskId),
        fetchApprovals(),
      ]);
      if (cancelled) {
        return;
      }
      if (detailResult.status === "fulfilled") {
        setTaskDetail(detailResult.value);
      }
      if (sessionResult.status === "fulfilled") {
        setExecutionSession(readExecutionSessionDocument(sessionResult.value));
      }
      if (approvalsResult.status === "fulfilled") {
        setPendingApprovals(
          approvalsResult.value.approvals.filter((item) => item.task_id === taskId)
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [approvalSignal, taskId]);

  useEffect(() => {
    setChatActionNotice(null);
  }, [taskId]);

  async function handleOperatorAction(item: OperatorInboxItem, kind: OperatorActionKind) {
    const mapped = mapOperatorQuickAction(item, kind);
    if (!mapped) {
      return;
    }
    const result = await submitAction(mapped.actionId, mapped.params);
    if (!result) {
      setChatActionNotice({
        tone: "error",
        title: "这次确认没有成功",
        message: "审批区还没处理完成，你可以再试一次。",
      });
      return;
    }
    setChatActionNotice({
      tone: "success",
      title: "这轮确认已经处理",
      message: result.message || "主助手会基于你的确认继续往下执行。",
    });
    if (taskId) {
      void refreshResources([
        {
          resource_type: snapshot!.resources.sessions.resource_type,
          resource_id: snapshot!.resources.sessions.resource_id,
          schema_version: snapshot!.resources.sessions.schema_version,
        },
        {
          resource_type: snapshot!.resources.delegation.resource_type,
          resource_id: snapshot!.resources.delegation.resource_id,
          schema_version: snapshot!.resources.delegation.schema_version,
        },
        {
          resource_type: snapshot!.resources.context_continuity.resource_type,
          resource_id: snapshot!.resources.context_continuity.resource_id,
          schema_version: snapshot!.resources.context_continuity.schema_version,
        },
      ]);
    }
  }

  async function handleResetLegacySession() {
    if (!currentSession?.session_id) {
      return;
    }
    const result = await submitAction("session.reset", {
      session_id: currentSession.session_id,
    });
    if (!result) {
      setChatActionNotice({
        tone: "error",
        title: "这条历史会话还没有重置成功",
        message: "你可以再试一次，或直接新开一条 新会话。",
      });
      return;
    }
    setChatActionNotice({
      tone: "success",
      title: "这条历史会话已经重置",
      message: result.message || "旧 continuity 已清空，接下来会按新的会话语义继续。",
    });
    await resetConversation();
    if (routeSessionId) {
      navigate("/chat");
    }
  }

  async function handleAttachInput(text: string) {
    if (!taskId) {
      return false;
    }
    setSteeringBusy(true);
    try {
      const response = await attachExecutionInput(taskId, {
        text,
        approval_id: executionSession?.pending_approval_id ?? undefined,
        actor: "user:web",
      });
      setExecutionSession(readExecutionSessionDocument(response));
      setChatActionNotice({
        tone: "success",
        title: "已经把你的补充发给当前执行流程",
        message: response.result.delivered_live
          ? "这轮正在继续执行。"
          : "系统已经接收你的补充，并会继续恢复这轮执行。",
      });
      return true;
    } catch (error) {
      const apiError = error instanceof ApiError ? error : null;
      setChatActionNotice({
        tone: "error",
        title: "这条补充还没发进去",
        message:
          apiError?.message ||
          "当前这轮还没有进入可接收 steering 的状态，请稍后再试。",
      });
      return false;
    } finally {
      setSteeringBusy(false);
    }
  }

  async function resolveCurrentApprovalItem(): Promise<OperatorInboxItem | null> {
    if (activeApprovalItem) {
      return activeApprovalItem;
    }
    if (!taskId) {
      return null;
    }

    let nextExecutionSession = executionSession;
    let nextTaskDetail = taskDetail;
    let nextApprovals = pendingApprovals;

    const [sessionResult, detailResult, approvalsResult] = await Promise.allSettled([
      fetchTaskExecutionSession(taskId),
      fetchTaskDetail(taskId),
      fetchApprovals(),
    ]);

    if (sessionResult.status === "fulfilled") {
      nextExecutionSession = readExecutionSessionDocument(sessionResult.value);
      setExecutionSession(nextExecutionSession);
    }
    if (detailResult.status === "fulfilled") {
      nextTaskDetail = detailResult.value;
      setTaskDetail(nextTaskDetail);
    }
    if (approvalsResult.status === "fulfilled") {
      nextApprovals = approvalsResult.value.approvals.filter((item) => item.task_id === taskId);
      setPendingApprovals(nextApprovals);
      if (nextApprovals.length > 0) {
        return buildSyntheticApprovalItem(nextApprovals[0]!);
      }
    }

    if (nextExecutionSession?.pending_approval_id) {
      const refreshedApprovalContext =
        nextTaskDetail?.events && taskId
          ? readLatestApprovalContext(nextTaskDetail.events, activeWork?.work_id || activeSessionWorkId)
          : latestApprovalContext;
      return buildExecutionSessionApprovalItem(nextExecutionSession.pending_approval_id, {
        taskId,
        toolName: refreshedApprovalContext?.toolName,
        argsSummary: refreshedApprovalContext?.argsSummary,
        summary: refreshedApprovalContext?.summary,
        createdAt: refreshedApprovalContext?.createdAt,
      });
    }

    const approvalIdFromEvents =
      nextTaskDetail?.events && taskId
        ? readLatestApprovalContext(
            nextTaskDetail.events,
            activeWork?.work_id || activeSessionWorkId
          )?.approvalId
        : latestApprovalContext?.approvalId;
    if (approvalIdFromEvents) {
      return buildExecutionSessionApprovalItem(approvalIdFromEvents, {
        taskId,
        toolName: latestApprovalContext?.toolName,
        argsSummary: latestApprovalContext?.argsSummary,
        summary: latestApprovalContext?.summary,
        createdAt: latestApprovalContext?.createdAt,
      });
    }

    return null;
  }

  function applySlashCommandSuggestion(value: string) {
    setInput(value);
    setSelectedCommandIndex(0);
    window.requestAnimationFrame(() => {
      if (!inputRef.current) {
        return;
      }
      inputRef.current.focus();
      inputRef.current.setSelectionRange(value.length, value.length);
    });
  }

  const handleStartSessionAliasEdit = useCallback(() => {
    if (!canEditSessionAlias || isSavingSessionAlias) {
      return;
    }
    setSessionAliasDraft(sessionTitleLabel);
    setIsEditingSessionAlias(true);
  }, [canEditSessionAlias, isSavingSessionAlias, sessionTitleLabel]);

  const handleCancelSessionAliasEdit = useCallback(() => {
    setSessionAliasDraft(sessionTitleLabel);
    setIsEditingSessionAlias(false);
  }, [sessionTitleLabel]);

  const handleSaveSessionAlias = useCallback(async () => {
    if (!canEditSessionAlias) {
      setIsEditingSessionAlias(false);
      return;
    }
    const normalizedDraft = sessionAliasDraft.trim();
    const normalizedBaseTitle = sessionTitleBase.trim();
    const nextAlias =
      !normalizedDraft || (normalizedBaseTitle && normalizedDraft === normalizedBaseTitle)
        ? ""
        : normalizedDraft;
    if (nextAlias === currentSessionAlias.trim()) {
      setSessionAliasDraft(nextAlias || normalizedBaseTitle || conversationTitle);
      setIsEditingSessionAlias(false);
      return;
    }
    const result = await submitAction("session.set_alias", {
      session_id: editableSessionId,
      thread_id: editableThreadId,
      alias: nextAlias,
    });
    if (result) {
      setSessionAliasDraft(nextAlias || normalizedBaseTitle || conversationTitle);
      setIsEditingSessionAlias(false);
    }
  }, [
    canEditSessionAlias,
    conversationTitle,
    currentSessionAlias,
    editableSessionId,
    editableThreadId,
    sessionAliasDraft,
    sessionTitleBase,
    submitAction,
  ]);

  function handleSessionAliasInputKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      void handleSaveSessionAlias();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      handleCancelSessionAliasEdit();
    }
  }

  async function submitCurrentInput() {
    const text = input.trim();
    if (!text || restoring || steeringBusy) {
      return;
    }
    const approvalCommand = parseApprovalCommand(text);
    if (approvalCommand) {
      const approvalItem = await resolveCurrentApprovalItem();
      if (!approvalItem) {
        setChatActionNotice({
          tone: "error",
          title: latestExpiredApprovalContext ? "刚才那条审批已经超时" : "当前没有可处理的审批",
          message: latestExpiredApprovalContext
            ? `${latestExpiredApprovalContext.toolName || "这一步"} 没等到你的确认，已经自动拒绝。请先重试这一步，再重新批准。`
            : "如果你刚才看到需要确认，可能这条审批已经超时，或者当前页面还没拿到最新审批状态。",
        });
        return;
      }
      setInput("");
      await handleOperatorAction(approvalItem, approvalCommand);
      return;
    }
    setInput("");
    if (canSteerCurrentRun) {
      const attached = await handleAttachInput(text);
      if (!attached) {
        setInput(text);
      }
      return;
    }
    setFreshTurnStartedAt(Date.now());
    setTaskDetail(null);
    setExecutionSession(null);
    setPendingApprovals([]);
    setChatActionNotice(null);
    await sendMessage(text, {
      agentProfileId: currentSessionOwnerProfileId || undefined,
      sessionId: !taskId ? currentSession?.session_id || routeSessionId || undefined : undefined,
      threadId: !taskId ? currentSession?.thread_id || undefined : undefined,
    });
  }

  function handleInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // IME 正在组合（如中文输入法按 Enter 确认候选词）时，不拦截按键
    if (event.nativeEvent.isComposing || event.key === "Process") {
      return;
    }
    if (event.key === "ArrowDown") {
      if (slashCommandMatches.length === 0) {
        return;
      }
      event.preventDefault();
      setSelectedCommandIndex((current) => (current + 1) % slashCommandMatches.length);
      return;
    }
    if (event.key === "ArrowUp") {
      if (slashCommandMatches.length === 0) {
        return;
      }
      event.preventDefault();
      setSelectedCommandIndex(
        (current) => (current - 1 + slashCommandMatches.length) % slashCommandMatches.length
      );
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      if (slashCommandMatches.length > 0) {
        const highlighted = slashCommandMatches[selectedCommandIndex] ?? slashCommandMatches[0];
        if (highlighted && input.trim() !== highlighted.value) {
          event.preventDefault();
          applySlashCommandSuggestion(highlighted.value);
          return;
        }
      }
      event.preventDefault();
      void submitCurrentInput();
      return;
    }
    if (slashCommandMatches.length === 0) {
      return;
    }
    const highlighted = slashCommandMatches[selectedCommandIndex] ?? slashCommandMatches[0];
    if (!highlighted) {
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      applySlashCommandSuggestion(highlighted.value);
      return;
    }
    if (event.key === "Enter" && input.trim() !== highlighted.value) {
      event.preventDefault();
      applySlashCommandSuggestion(highlighted.value);
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    await submitCurrentInput();
  }

  const pendingCommandAction = parseApprovalCommand(input);
  const hasSlashSuggestionOpen = slashCommandMatches.length > 0;
  const submitLabel = pendingCommandAction
    ? "执行命令"
    : canSteerCurrentRun
      ? "继续这轮"
      : streaming
        ? "加入队列"
        : "发送";
  const legacyResetCallout = legacyResetRecommended ? (
    <InlineCallout title="这条历史会话建议先重置再继续" tone="muted">
      <div className="wb-chat-inline-actions">
        <span>{compatibilityMessage}</span>
        <div className="wb-action-bar">
          <button
            type="button"
            className="wb-button wb-button-secondary"
            onClick={() => void handleResetLegacySession()}
            disabled={busyActionId === "session.reset"}
          >
            重置 continuity
          </button>
          <button
            type="button"
            className="wb-button wb-button-ghost"
            onClick={() => {
              void resetConversation();
            }}
          >
            新开 新会话
          </button>
        </div>
      </div>
    </InlineCallout>
  ) : null;

  return (
    <div className="wb-page wb-chat-page">
      <section
        className={`wb-panel wb-chat-panel wb-chat-shell ${isEmptyConversation ? "is-empty" : ""}`}
      >
        <div className="wb-panel-head wb-chat-head">
          <div className="wb-chat-head-copy">
            <div className="wb-chat-head-inline">
              <h3 className="wb-chat-head-title-heading">
                {isEditingSessionAlias ? (
                  <input
                    ref={sessionAliasInputRef}
                    className="wb-chat-head-title-input"
                    value={sessionAliasDraft}
                    onChange={(event) => setSessionAliasDraft(event.target.value)}
                    onBlur={() => {
                      void handleSaveSessionAlias();
                    }}
                    onKeyDown={handleSessionAliasInputKeyDown}
                    disabled={isSavingSessionAlias}
                    aria-label="编辑会话名称"
                  />
                ) : (
                  <button
                    type="button"
                    className="wb-chat-head-title-button"
                    onClick={handleStartSessionAliasEdit}
                    disabled={!canEditSessionAlias || isSavingSessionAlias}
                    aria-label="编辑会话名称"
                  >
                    <span className="wb-chat-head-title-label">{sessionTitleLabel}</span>
                  </button>
                )}
              </h3>
              <p className="wb-chat-head-summary">{conversationOwnerName}</p>
            </div>
          </div>
          <div className="wb-chat-head-actions">
            {taskId ? <StatusBadge tone={taskStatusTone}>{taskStatusLabel}</StatusBadge> : null}
            {taskId ? (
              <Link className="wb-button wb-button-secondary wb-button-inline" to={`/tasks/${taskId}`}>
                打开任务
              </Link>
            ) : null}
            {techRefs.length > 0 ? (
              <HoverReveal
                label="技术详情"
                expanded={showSessionInternalRefs}
                onToggle={setShowSessionInternalRefs}
                ariaLabel="当前会话技术详情"
                triggerClassName="wb-button-inline"
              >
                {techRefs.map((item) => (
                  <div key={item.label} className="wb-hover-reveal-row">
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                  </div>
                ))}
              </HoverReveal>
            ) : null}
          </div>
        </div>

        {isRestoringConversation ? (
          <div className="wb-chat-empty-stage is-restoring">
            <div className="wb-empty-state wb-chat-empty-card wb-chat-restore-card">
              <strong>正在恢复最近对话</strong>
              <span>稍等，我们在读取历史消息和当前任务状态。</span>
              {showRestoreEscape ? (
                <div className="wb-action-bar wb-chat-restore-actions">
                  <span>如果这一步还没结束，你可以从左侧开始一段新对话。</span>
                </div>
              ) : null}
            </div>
          </div>
        ) : isEmptyConversation ? (
          <div className="wb-chat-empty-stage">
            {error ? (
              <InlineCallout title="刚才没有发送成功" tone="error">
                {error}
              </InlineCallout>
            ) : null}
            {legacyResetCallout}
            <form className="wb-chat-form is-empty" onSubmit={handleSubmit}>
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleInputKeyDown}
                placeholder={inputPlaceholder}
                disabled={streaming}
                rows={3}
              />
              <button
                type="submit"
                className="wb-button wb-button-primary"
                disabled={streaming || !input.trim()}
              >
                {streaming ? "发送中" : "发送"}
              </button>
            </form>
          </div>
        ) : (
          <>
            <div className="wb-chat-messages">
              {messages.map((message) => (
                <MessageBubble
                  key={message.id}
                  message={message}
                  loadingLabel={loadingLabel}
                  activityItems={
                    message.id === activeStreamingMessageId && shouldShowInlineActivity
                      ? activityItems
                      : []
                  }
                />
              ))}
              {shouldShowSyntheticProgressBubble ? (
                <MessageBubble
                  message={{
                    id: "agent-progress-stage",
                    role: "agent",
                    content: "",
                    isStreaming: true,
                  }}
                  loadingLabel={loadingLabel}
                  activityItems={activityItems}
                />
              ) : null}
              <div ref={messagesEndRef} />
            </div>

            {error ? (
              <InlineCallout title="刚才没有发送成功" tone="error">
                {error}
              </InlineCallout>
            ) : null}
            {legacyResetCallout}

            {activeApprovalItem ? (
              <>
                <InlineCallout
                  title={
                    activeApprovalRemainingSeconds != null
                      ? `等待你批准 ${activeApprovalItem.metadata.tool_name || activeApprovalItem.title} · ${formatCountdown(activeApprovalRemainingSeconds)} 后超时`
                      : "这轮正在等你确认"
                  }
                  tone="error"
                  actions={
                    <div className="wb-inline-actions wb-inline-actions-wrap">
                    {activeApprovalItem.quick_actions.map((action) => (
                      <button
                        key={`${activeApprovalItem.item_id}-${action.kind}`}
                        type="button"
                        className={
                          action.style === "primary"
                            ? "wb-button wb-button-primary"
                            : "wb-button wb-button-secondary"
                        }
                        disabled={
                          !action.enabled ||
                          busyActionId === mapOperatorQuickAction(activeApprovalItem, action.kind)?.actionId
                        }
                        onClick={() => void handleOperatorAction(activeApprovalItem, action.kind)}
                      >
                        {action.label}
                      </button>
                    ))}
                    </div>
                  }
                >
                  <>
                    <span className="wb-chat-approval-banner-line">{activeApprovalItem.summary}</span>
                    {activeApprovalItem.metadata.tool_args_summary ? (
                      <span className="wb-chat-approval-banner-line">
                        <code>{activeApprovalItem.metadata.tool_args_summary}</code>
                      </span>
                    ) : null}
                  </>
                </InlineCallout>
                <p className="wb-chat-form-hint">
                  也可以直接输入 <code>/approve</code>、<code>/approve always</code> 或{" "}
                  <code>/deny</code>。
                </p>
              </>
            ) : null}

            {!activeApprovalItem && latestExpiredApprovalContext && latestExpiredApprovalAgeSeconds != null && latestExpiredApprovalAgeSeconds < 300 ? (
              <InlineCallout title="刚才有一步审批已经超时" tone="error">
                {`${latestExpiredApprovalContext.toolName || "这一步"} 在 ${Math.max(
                  1,
                  latestExpiredApprovalAgeSeconds
                )} 秒前因为没等到确认而自动拒绝了。你可以重试这轮，或换成不需要审批的路径。`}
              </InlineCallout>
            ) : null}

            {chatActionNotice ? (
              <InlineCallout
                title={chatActionNotice.title}
                tone={chatActionNotice.tone === "error" ? "error" : "muted"}
              >
                {chatActionNotice.message}
              </InlineCallout>
            ) : null}

            <form className="wb-chat-form" onSubmit={handleSubmit}>
              <textarea
                ref={inputRef}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleInputKeyDown}
                placeholder={inputPlaceholder}
                disabled={restoring || steeringBusy}
                rows={3}
              />
              <button
                type="submit"
                className="wb-button wb-button-primary"
                disabled={restoring || steeringBusy || !input.trim()}
              >
                {steeringBusy ? "处理中" : submitLabel}
              </button>
            </form>
            {hasSlashSuggestionOpen ? (
              <div className="wb-chat-command-menu" role="listbox" aria-label="聊天命令建议">
                {slashCommandMatches.map((command, index) => (
                  <button
                    key={command.value}
                    type="button"
                    role="option"
                    aria-selected={index === selectedCommandIndex}
                    className={`wb-chat-command-option${
                      index === selectedCommandIndex ? " is-active" : ""
                    }`}
                    onClick={() => applySlashCommandSuggestion(command.value)}
                  >
                    <strong>{command.value}</strong>
                    <span>{command.description}</span>
                  </button>
                ))}
              </div>
            ) : null}
            {canSteerCurrentRun ? (
              <p className="wb-chat-form-hint">
                这条输入会直接作为当前执行的补充，不会新开一轮。
              </p>
            ) : streaming ? (
              <p className="wb-chat-form-hint">
                当前这轮还在处理；你现在继续发的消息会进入同一条会话队列，主助手会接着处理。
              </p>
            ) : null}
          </>
        )}
      </section>
    </div>
  );
}
