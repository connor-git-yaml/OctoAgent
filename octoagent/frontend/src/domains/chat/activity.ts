import type { TaskEvent, WorkProjectionItem } from "../../types";
import { readPayloadString } from "./approval";
import { formatAgentRoleLabel } from "./presentation";
import { ensureArray, resolveWorkActor, resolveWorkStatus, isAgentDirectExecution } from "./session";

// ---------------------------------------------------------------------------
// Interfaces
// ---------------------------------------------------------------------------

export interface ChatActivityItem {
  id: string;
  actor: string;
  stateLabel: string;
  tone: "success" | "warning" | "danger" | "running" | "draft";
  summary: string;
  traceTitle?: string;
  traceEntries?: ChatTraceEntry[];
}

export interface ChatTraceEntry {
  id: string;
  label: string;
  summary: string;
  stateLabel?: string;
  tone?: "success" | "warning" | "danger" | "running" | "draft";
  detailInput?: string;
  detailOutput?: string;
}

export interface ToolTraceRecord {
  id: string;
  toolName: string;
  argsSummary: string;
  outputSummary: string;
  stateLabel: string;
  tone: "success" | "warning" | "danger" | "running" | "draft";
  summary: string;
  taskSeq: number;
}

// ---------------------------------------------------------------------------
// Text helpers
// ---------------------------------------------------------------------------

export function summarizeText(value: string, maxLength = 120): string {
  const normalized = value.trim().replace(/\s+/g, " ");
  if (!normalized) {
    return "";
  }
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 1)}…`;
}

export function summarizeToolList(toolNames: string[], limit = 4): string {
  if (toolNames.length === 0) {
    return "这轮没有挂出额外工具。";
  }
  const visible = toolNames.slice(0, limit);
  const suffix =
    toolNames.length > limit ? `，另外还有 ${toolNames.length - limit} 个工具` : "";
  return `${visible.join("、")}${suffix}`;
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

export function formatActorSummary(actor: string, workTitle: string, status: string, latestMessageType: string): string {
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

export function formatActivityStateLabel(status: string, latestMessageType: string): string {
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

export function formatActivityTone(status: string, latestMessageType: string): "success" | "warning" | "danger" | "running" | "draft" {
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

export function formatInboundMessageLabel(messageType: string, fallbackLatestMessageType: string): string {
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

export function summarizeDelegationIntent(work: WorkProjectionItem): string {
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
    return `围绕"${workTitle}"补足信息并收口成可直接回复的结果。`;
  }
  return "把这轮问题交给内部角色处理，并要求带回可直接使用的结果。";
}

// ---------------------------------------------------------------------------
// Trace / timeline builders
// ---------------------------------------------------------------------------

export function buildToolTimelineRecords(workEvents: TaskEvent[]): ToolTraceRecord[] {
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

export function buildToolTraceEntries(workEvents: TaskEvent[]): ChatTraceEntry[] {
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

export function buildApprovalTraceEntry(workEvents: TaskEvent[]): ChatTraceEntry | null {
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

export function buildModelTraceEntry(workEvents: TaskEvent[]): ChatTraceEntry | null {
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

export function buildAgentTraceEntries(
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

export function buildWorkerTraceEntries(
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

export function buildAgentActivity(
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
      id: "main-agent",
      actor: "主助手",
      stateLabel: "等你补充",
      tone: "warning",
      summary: "主助手已经识别到还差关键信息，补一句就能继续。",
    };
  }
  if (normalizedStatus === "WAITING_APPROVAL") {
    return {
      id: "main-agent",
      actor: "主助手",
      stateLabel: "等你确认",
      tone: "warning",
      summary: "这一步需要你确认，系统才会继续往下执行。",
    };
  }
  if (hasInternalCollaboration && normalizedMessageType === "RESULT") {
    return {
      id: "main-agent",
      actor: "主助手",
      stateLabel: "整理中",
      tone: "running",
      summary: "内部结果已经拿到，但主助手还在整理、核对并收口最终回复。",
    };
  }
  if (hasInternalCollaboration) {
    return {
      id: "main-agent",
      actor: "主助手",
      stateLabel: "协调中",
      tone: "running",
      summary: "主助手正在协调内部角色、补充上下文和收口结果。",
    };
  }
  if (isDirectExecution) {
    return {
      id: "main-agent",
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
      id: "main-agent",
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

export function buildWorkerActivity(
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
