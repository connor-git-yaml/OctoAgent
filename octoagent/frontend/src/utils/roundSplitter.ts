/**
 * 轮次拆分引擎 -- 将事件流按 USER_MESSAGE 切分为轮次，
 * 配对 started/completed 事件，生成流程图节点。
 *
 * 每轮以一条 USER_MESSAGE 为起点，包含后续的思考、执行、产物直至下一条 USER_MESSAGE。
 * 最终返回倒序排列（最新轮次在前）。
 */

import type { TaskEvent, Artifact } from "../types";
import { TERMINAL_STATUSES } from "./phaseClassifier";

// ─── 流程图节点类型 ──────────────────────────────────────────

export type FlowNodeKind =
  | "message"
  | "llm"
  | "tool"
  | "skill"
  | "worker"
  | "memory"
  | "artifact"
  | "completion"
  | "decision"
  | "a2a"
  | "approval"
  | "error"
  | "other";

export type FlowNodeStatus = "success" | "error" | "running" | "neutral";

export interface FlowNode {
  id: string;
  kind: FlowNodeKind;
  label: string;
  status: FlowNodeStatus;
  events: TaskEvent[];
  artifact?: Artifact;
  /** 附带的 Artifact 列表（从 ARTIFACT_CREATED 折叠而来） */
  artifacts: Artifact[];
  ts: string;
  /** 所属 Agent 名称，用于按 Agent 分行展示 */
  agent: string;
  /** 耗时（毫秒），0 表示无数据 */
  durationMs: number;
}

export interface Round {
  id: string;
  index: number;
  triggerMessage: string;
  nodes: FlowNode[];
  startTime: string;
  endTime?: string;
}

/** 按 Agent 分组后的节点泳道 */
export interface AgentLane {
  agent: string;
  nodes: FlowNode[];
  /** 泳道总耗时（毫秒） */
  totalDurationMs: number;
  /** 泳道最终状态 */
  laneStatus: FlowNodeStatus;
}

/** 将节点按唯一 agent 合并为泳道（同一 Agent 只有一行） */
export function groupByAgent(nodes: FlowNode[]): AgentLane[] {
  const map = new Map<string, FlowNode[]>();
  const order: string[] = [];

  for (const node of nodes) {
    const existing = map.get(node.agent);
    if (existing) {
      existing.push(node);
    } else {
      map.set(node.agent, [node]);
      order.push(node.agent);
    }
  }

  return order.map((agent) => {
    const nodes = map.get(agent)!;
    const totalDurationMs = nodes.reduce((sum, n) => sum + n.durationMs, 0);
    // 有任何 error 就是 error，有 running 就是 running，否则取最后节点状态
    const hasError = nodes.some((n) => n.status === "error");
    const hasRunning = nodes.some((n) => n.status === "running");
    const laneStatus: FlowNodeStatus = hasError
      ? "error"
      : hasRunning
        ? "running"
        : nodes[nodes.length - 1]?.status || "neutral";
    return { agent, nodes, totalDurationMs, laneStatus };
  });
}

// ─── 配对规则：STARTED -> COMPLETED/FAILED ──────────────────

const PAIR_MAP: Record<string, string[]> = {
  MODEL_CALL_STARTED: ["MODEL_CALL_COMPLETED", "MODEL_CALL_FAILED"],
  TOOL_CALL_STARTED: ["TOOL_CALL_COMPLETED", "TOOL_CALL_FAILED"],
  SKILL_STARTED: ["SKILL_COMPLETED", "SKILL_FAILED"],
  WORKER_DISPATCHED: ["WORKER_RETURNED"],
  MEMORY_RECALL_SCHEDULED: ["MEMORY_RECALL_COMPLETED", "MEMORY_RECALL_FAILED"],
};

const COMPLETION_TYPES = new Set(Object.values(PAIR_MAP).flat());

// 可见事件类型（其余一律隐藏）
const VISIBLE_TYPES = new Set([
  "USER_MESSAGE",
  "TASK_CREATED",
  "MODEL_CALL_STARTED",
  "MODEL_CALL_COMPLETED",
  "MODEL_CALL_FAILED",
  "TOOL_CALL_STARTED",
  "TOOL_CALL_COMPLETED",
  "TOOL_CALL_FAILED",
  "SKILL_STARTED",
  "SKILL_COMPLETED",
  "SKILL_FAILED",
  "WORKER_DISPATCHED",
  "WORKER_RETURNED",
  "MEMORY_RECALL_SCHEDULED",
  "MEMORY_RECALL_COMPLETED",
  "MEMORY_RECALL_FAILED",
  "ARTIFACT_CREATED",
  "STATE_TRANSITION",
  "ORCH_DECISION",
  "A2A_MESSAGE_SENT",
  "A2A_MESSAGE_RECEIVED",
  "APPROVAL_REQUESTED",
  "APPROVAL_APPROVED",
  "APPROVAL_REJECTED",
  "APPROVAL_EXPIRED",
  "ERROR",
]);

// ─── 主入口 ──────────────────────────────────────────────────

export function splitIntoRounds(
  events: TaskEvent[],
  artifacts: Artifact[],
): Round[] {
  const sorted = [...events].sort((a, b) => a.task_seq - b.task_seq);

  // 按 USER_MESSAGE 切分
  const rawRounds: TaskEvent[][] = [];
  let current: TaskEvent[] = [];

  for (const event of sorted) {
    if (event.type === "USER_MESSAGE" && current.length > 0) {
      rawRounds.push(current);
      current = [event];
    } else {
      current.push(event);
    }
  }
  if (current.length > 0) rawRounds.push(current);

  // 构建 artifact 映射
  const artifactMap = new Map(artifacts.map((a) => [a.artifact_id, a]));

  // 转换为 Round，倒序（最新在前）
  const rounds = rawRounds.map((roundEvents, i) =>
    buildRound(roundEvents, i + 1, artifactMap),
  );
  return rounds.reverse();
}

// ─── 构建单个 Round ──────────────────────────────────────────

function buildRound(
  events: TaskEvent[],
  index: number,
  artifactMap: Map<string, Artifact>,
): Round {
  const trigger =
    events.find((e) => e.type === "USER_MESSAGE") || events[0];
  const message =
    trigger.type === "USER_MESSAGE"
      ? str(trigger.payload?.content ?? trigger.payload?.text, 80)
      : "任务创建";

  const nodes = buildFlowNodes(events, artifactMap);
  assignAgents(nodes);

  return {
    id: trigger.event_id,
    index,
    triggerMessage: message,
    nodes,
    startTime: trigger.ts,
    endTime: events[events.length - 1]?.ts,
  };
}

// ─── Agent 分配 ─────────────────────────────────────────────

/** 按 actor 字段为每个节点分配所属 Agent 名称 */
function assignAgents(nodes: FlowNode[]): void {
  let lastWorkerName = "Worker";

  for (const node of nodes) {
    const actor = node.events[0]?.actor ?? "system";

    if (actor === "user" || actor === "kernel") {
      node.agent = "Orchestrator";
      // 从 ORCH_DECISION 或 WORKER_DISPATCHED payload 提取真实 Agent 名称
      if (node.kind === "decision") {
        const wType = extractWorkerType(node);
        if (wType) lastWorkerName = wType.charAt(0).toUpperCase() + wType.slice(1);
      }
      if (node.kind === "worker") {
        const name = extractAgentName(node);
        if (name) {
          lastWorkerName = name;
        } else {
          const wid = extractWorkerId(node);
          if (wid) lastWorkerName = `Worker ${shortId(wid)}`;
        }
      }
    } else if (actor === "worker" || actor === "tool") {
      node.agent = lastWorkerName;
    } else {
      node.agent = lastWorkerName === "Worker" ? "Orchestrator" : lastWorkerName;
    }
  }
}

/** 从 WORKER_DISPATCHED payload 提取 Agent 显示名称 */
function extractAgentName(node: FlowNode): string {
  for (const ev of node.events) {
    const name = ev.payload?.agent_name;
    if (typeof name === "string" && name) return name;
  }
  return "";
}

function extractWorkerType(node: FlowNode): string {
  for (const ev of node.events) {
    const wt = ev.payload?.selected_worker_type || ev.payload?.worker_type;
    if (typeof wt === "string" && wt) return wt;
  }
  return "";
}

function extractWorkerId(node: FlowNode): string {
  for (const ev of node.events) {
    const wid = ev.payload?.worker_id;
    if (typeof wid === "string" && wid) return wid;
  }
  return "";
}

function shortId(id: string): string {
  // 取前 4 位做短标识
  return id.length > 4 ? id.slice(0, 4) : id;
}

// ─── 构建流程图节点 ──────────────────────────────────────────

function buildFlowNodes(
  events: TaskEvent[],
  artifactMap: Map<string, Artifact>,
): FlowNode[] {
  const nodes: FlowNode[] = [];
  const consumed = new Set<string>();

  for (let i = 0; i < events.length; i++) {
    const event = events[i];
    if (consumed.has(event.event_id)) continue;

    // STATE_TRANSITION：只显示终态迁移
    if (event.type === "STATE_TRANSITION") {
      const toStatus = String(event.payload?.to_status || "");
      if (!TERMINAL_STATUSES.has(toStatus)) continue;
      consumed.add(event.event_id);
      nodes.push(makeCompletionNode(event, toStatus));
      continue;
    }

    // 隐藏不在可见列表中的事件
    if (!VISIBLE_TYPES.has(event.type)) continue;

    // A2A 心跳消息：内部簿记事件，不展示给用户
    if (event.type === "A2A_MESSAGE_RECEIVED" && isA2AHeartbeat(event)) continue;

    // 包装层过滤：task_service 外层 MODEL_CALL（actor=system）和
    // chat.*.inline Skill 都是不调模型的包装层，隐藏并消耗配对
    if (isWrapperModelCall(event, events, i)) {
      consumed.add(event.event_id);
      const comp = findCompletion(events, i + 1, PAIR_MAP[event.type] || [], consumed);
      if (comp) consumed.add(comp.event_id);
      continue;
    }
    if (isInlineSkill(event)) {
      consumed.add(event.event_id);
      const comp = findCompletion(events, i + 1, PAIR_MAP[event.type] || [], consumed);
      if (comp) consumed.add(comp.event_id);
      continue;
    }

    // 配对型事件（STARTED → 查找后续 COMPLETED/FAILED）
    const completionTypes = PAIR_MAP[event.type];
    if (completionTypes) {
      consumed.add(event.event_id);
      const completion = findCompletion(events, i + 1, completionTypes, consumed);
      if (completion) {
        consumed.add(completion.event_id);
        nodes.push(makePairedNode(event, completion));
      } else {
        nodes.push(makeRunningNode(event));
      }
      continue;
    }

    // 已被配对消耗的 completion 事件（孤儿完成事件 → 独立节点）
    if (COMPLETION_TYPES.has(event.type)) {
      if (consumed.has(event.event_id)) continue;
      consumed.add(event.event_id);
      nodes.push(makeOrphanNode(event));
      continue;
    }

    // TASK_CREATED：同轮有 USER_MESSAGE 时跳过
    if (event.type === "TASK_CREATED") {
      if (events.some((e) => e.type === "USER_MESSAGE")) continue;
      consumed.add(event.event_id);
      nodes.push({
        id: event.event_id,
        kind: "message",
        label: "任务创建",
        status: "neutral",
        events: [event],
        ts: event.ts,
        agent: "",
        artifacts: [],
        durationMs: 0,
      });
      continue;
    }

    // ARTIFACT_CREATED：折叠到前一个节点上，而非独立展示
    if (event.type === "ARTIFACT_CREATED") {
      consumed.add(event.event_id);
      const artifactId = String(event.payload?.artifact_id || "");
      const artifact = artifactMap.get(artifactId);
      if (artifact && nodes.length > 0) {
        nodes[nodes.length - 1].artifacts.push(artifact);
      }
      continue;
    }

    // 普通事件
    consumed.add(event.event_id);
    nodes.push(makeSingleNode(event, artifactMap));
  }

  return nodes;
}

function findCompletion(
  events: TaskEvent[],
  startIdx: number,
  completionTypes: string[],
  consumed: Set<string>,
): TaskEvent | undefined {
  for (let j = startIdx; j < events.length; j++) {
    const c = events[j];
    if (consumed.has(c.event_id)) continue;
    if (completionTypes.includes(c.type)) return c;
  }
  return undefined;
}

// ─── 节点构建器 ──────────────────────────────────────────────

function makePairedNode(start: TaskEvent, end: TaskEvent): FlowNode {
  const isFailed = end.type.endsWith("_FAILED");
  const kind = inferKind(start.type);
  return {
    id: start.event_id,
    kind,
    label: pairedLabel(kind, start, end),
    status: isFailed ? "error" : "success",
    events: [start, end],
    ts: start.ts,
    agent: "",
    artifacts: [],
    durationMs: extractDurationMs(start, end),
  };
}

function makeRunningNode(start: TaskEvent): FlowNode {
  const kind = inferKind(start.type);
  return {
    id: start.event_id,
    kind,
    label: runningLabel(kind, start),
    status: "running",
    events: [start],
    ts: start.ts,
    agent: "",
    artifacts: [],
    durationMs: 0,
  };
}

function makeOrphanNode(event: TaskEvent): FlowNode {
  const kind = inferKind(event.type);
  const isFailed = event.type.endsWith("_FAILED");
  return {
    id: event.event_id,
    kind,
    label: singleLabel(kind, event),
    status: isFailed ? "error" : "success",
    events: [event],
    ts: event.ts,
    agent: "",
    artifacts: [],
    durationMs: 0,
  };
}

function makeCompletionNode(event: TaskEvent, toStatus: string): FlowNode {
  const info: Record<string, { label: string; status: FlowNodeStatus }> = {
    SUCCEEDED: { label: "成功", status: "success" },
    FAILED: { label: "失败", status: "error" },
    CANCELLED: { label: "已取消", status: "error" },
    REJECTED: { label: "已拒绝", status: "error" },
  };
  const { label, status } = info[toStatus] || { label: toStatus, status: "neutral" as const };
  return {
    id: event.event_id,
    kind: "completion",
    label,
    status,
    events: [event],
    ts: event.ts,
    agent: "",
    artifacts: [],
    durationMs: 0,
  };
}

function makeSingleNode(
  event: TaskEvent,
  artifactMap: Map<string, Artifact>,
): FlowNode {
  const base = { agent: "", artifacts: [] as Artifact[], durationMs: 0 };
  switch (event.type) {
    case "USER_MESSAGE":
      return {
        ...base,
        id: event.event_id,
        kind: "message",
        label: str(event.payload?.content ?? event.payload?.text, 30) || "消息",
        status: "neutral",
        events: [event],
        ts: event.ts,
      };

    case "ARTIFACT_CREATED": {
      const artifactId = String(event.payload?.artifact_id || "");
      const artifact = artifactMap.get(artifactId);
      return {
        ...base,
        id: event.event_id,
        kind: "artifact",
        label:
          str(event.payload?.artifact_name ?? event.payload?.name ?? artifact?.name, 20) ||
          "产物",
        status: "success",
        events: [event],
        artifact,
        ts: event.ts,
      };
    }

    case "ORCH_DECISION":
      return {
        ...base,
        id: event.event_id,
        kind: "decision",
        label: str(event.payload?.decision, 20) || "调度决策",
        status: "neutral",
        events: [event],
        ts: event.ts,
      };

    case "A2A_MESSAGE_SENT":
      return {
        ...base,
        id: event.event_id,
        kind: "a2a",
        label: "A2A 发送",
        status: "neutral",
        events: [event],
        ts: event.ts,
      };

    case "A2A_MESSAGE_RECEIVED":
      return {
        ...base,
        id: event.event_id,
        kind: "a2a",
        label: a2aReceivedLabel(event),
        status: a2aReceivedStatus(event),
        events: [event],
        ts: event.ts,
      };

    case "APPROVAL_REQUESTED":
      return {
        ...base,
        id: event.event_id,
        kind: "approval",
        label: "等待审批",
        status: "running",
        events: [event],
        ts: event.ts,
      };
    case "APPROVAL_APPROVED":
      return {
        ...base,
        id: event.event_id,
        kind: "approval",
        label: "已审批",
        status: "success",
        events: [event],
        ts: event.ts,
      };
    case "APPROVAL_REJECTED":
    case "APPROVAL_EXPIRED":
      return {
        ...base,
        id: event.event_id,
        kind: "approval",
        label: event.type === "APPROVAL_REJECTED" ? "审批拒绝" : "审批过期",
        status: "error",
        events: [event],
        ts: event.ts,
      };

    case "ERROR":
      return {
        ...base,
        id: event.event_id,
        kind: "error",
        label: str(event.payload?.message ?? event.payload?.error, 20) || "错误",
        status: "error",
        events: [event],
        ts: event.ts,
      };

    default:
      return {
        ...base,
        id: event.event_id,
        kind: "other",
        label: event.type,
        status: "neutral",
        events: [event],
        ts: event.ts,
      };
  }
}

// ─── 辅助函数 ────────────────────────────────────────────────

/** 从配对事件中提取耗时 */
function extractDurationMs(start: TaskEvent, end: TaskEvent): number {
  const ms = Number(end.payload?.duration_ms);
  if (ms > 0) return ms;
  const diff = new Date(end.ts).getTime() - new Date(start.ts).getTime();
  return diff > 0 ? diff : 0;
}

/** 从事件中提取最优模型名称：model_name > model_alias，跳过空字符串 */
function bestModelName(...events: TaskEvent[]): string {
  // 优先从所有事件中找 model_name（实际解析后的模型名）
  for (const ev of events) {
    const name = str(ev.payload?.model_name, 16);
    if (name) return name;
  }
  // 回退到 model_alias（LiteLLM 别名）
  for (const ev of events) {
    const alias = str(ev.payload?.model_alias, 16);
    if (alias) return alias;
  }
  return "";
}

function inferKind(eventType: string): FlowNodeKind {
  if (eventType.startsWith("MODEL_CALL")) return "llm";
  if (eventType.startsWith("TOOL_CALL")) return "tool";
  if (eventType.startsWith("SKILL_")) return "skill";
  if (eventType.startsWith("WORKER_")) return "worker";
  if (eventType.startsWith("MEMORY_RECALL")) return "memory";
  return "other";
}

function pairedLabel(kind: FlowNodeKind, start: TaskEvent, end: TaskEvent): string {
  const dur = fmtDuration(start, end);
  switch (kind) {
    case "llm": {
      const model = bestModelName(end, start);
      return model ? `${model} ${dur}` : `LLM ${dur}`;
    }
    case "tool": {
      const tool = str(end.payload?.tool_name ?? start.payload?.tool_name, 22);
      return tool || `工具 ${dur}`;
    }
    case "skill": {
      const skill = str(end.payload?.skill_id ?? start.payload?.skill_id, 22);
      return skill || `Skill ${dur}`;
    }
    case "worker":
      return `Worker ${dur}`;
    case "memory":
      return `记忆检索 ${dur}`;
    default:
      return `${kind} ${dur}`;
  }
}

function runningLabel(kind: FlowNodeKind, event: TaskEvent): string {
  switch (kind) {
    case "llm":
      return bestModelName(event) || "LLM 调用中…";
    case "tool":
      return str(event.payload?.tool_name, 22) || "工具执行中…";
    case "skill":
      return str(event.payload?.skill_id, 22) || "Skill 执行中…";
    case "worker":
      return "Worker 执行中…";
    case "memory":
      return "记忆检索中…";
    default:
      return `${event.type}…`;
  }
}

function singleLabel(kind: FlowNodeKind, event: TaskEvent): string {
  if (kind === "llm") return bestModelName(event) || "LLM";
  if (kind === "tool") return str(event.payload?.tool_name, 22) || "工具";
  if (kind === "skill") return str(event.payload?.skill_id, 22) || "Skill";
  return event.type;
}

function str(value: unknown, maxLen?: number): string {
  if (value == null) return "";
  const s = String(value).trim();
  return maxLen && s.length > maxLen ? s.slice(0, maxLen) + "…" : s;
}

function fmtDuration(start: TaskEvent, end: TaskEvent): string {
  const ms = Number(end.payload?.duration_ms);
  if (ms > 0) return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
  const diff = new Date(end.ts).getTime() - new Date(start.ts).getTime();
  if (diff > 0) return diff >= 1000 ? `${(diff / 1000).toFixed(1)}s` : `${diff}ms`;
  return "";
}

// ─── A2A 消息类型识别 ───────────────────────────────────────

/** 从 payload.message_type 获取 A2A 消息类型 */
function a2aMessageType(event: TaskEvent): string {
  return String(event.payload?.message_type || "").toUpperCase();
}

/** task_service 外层包装 MODEL_CALL：actor=system 且后面紧跟 SKILL_STARTED */
function isWrapperModelCall(event: TaskEvent, events: TaskEvent[], idx: number): boolean {
  if (event.type !== "MODEL_CALL_STARTED") return false;
  if (event.actor !== "system") return false;
  // 往后找，如果在下一个 MODEL_CALL_COMPLETED 之前先遇到 SKILL_STARTED，就是包装层
  for (let j = idx + 1; j < events.length; j++) {
    const t = events[j].type;
    if (t === "SKILL_STARTED") return true;
    if (t === "MODEL_CALL_COMPLETED" || t === "MODEL_CALL_FAILED") return false;
  }
  return false;
}

/** chat.*.inline Skill：LLM 对话循环的包装层，本身不调模型 */
function isInlineSkill(event: TaskEvent): boolean {
  if (event.type !== "SKILL_STARTED") return false;
  const skillId = String(event.payload?.skill_id || "");
  return /^chat\..+\.inline$/.test(skillId);
}

/** 是否为心跳类型（内部簿记，不展示给用户） */
function isA2AHeartbeat(event: TaskEvent): boolean {
  return a2aMessageType(event) === "HEARTBEAT";
}

/** A2A 接收节点的用户可读标签 */
function a2aReceivedLabel(event: TaskEvent): string {
  const type = a2aMessageType(event);
  switch (type) {
    case "RESULT": return "A2A 完成";
    case "ERROR": return "A2A 失败";
    case "TASK": return "A2A 接收";
    case "UPDATE": return "A2A 更新";
    case "CANCEL": return "A2A 取消";
    // HEARTBEAT 已被过滤，不会走到这里
    default: return "A2A 接收";
  }
}

/** A2A 接收节点的状态颜色 */
function a2aReceivedStatus(event: TaskEvent): FlowNodeStatus {
  const type = a2aMessageType(event);
  switch (type) {
    case "RESULT": return "success";
    case "ERROR": return "error";
    default: return "neutral";
  }
}
