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

// ─── 时间轴布局类型 (Feature 065) ────────────────────────────

/** 单个节点在时间轴上的布局信息 */
export interface NodeLayout {
  /** 节点水平起始位置（像素） */
  leftPx: number;
  /** 节点宽度（普通节点 = 48，展宽 Worker 节点 > 48） */
  widthPx: number;
  /** 所在泳道索引（0-based，对应 AgentLane[] 下标） */
  laneIndex: number;
}

/** 跨泳道连接线描述 */
export interface CrossLaneLink {
  /** 起始泳道索引 */
  fromLaneIndex: number;
  /** 起始节点 ID */
  fromNodeId: string;
  /** 目标泳道索引 */
  toLaneIndex: number;
  /** 目标节点 ID */
  toNodeId: string;
  /** 连接类型：dispatch（Orchestrator -> Worker）或 return（Worker -> Orchestrator） */
  type: "dispatch" | "return";
}

/** 时间刻度标记 */
export interface TimeTick {
  /** 刻度标签文本，如 "+0s", "+5s", "+1m30s" */
  label: string;
  /** 刻度水平位置（像素） */
  leftPx: number;
}

/** 时间轴布局计算的完整输出 */
export interface TimelineLayout {
  /** 时间轴总宽度（像素），所有泳道轨道的 width 都设为此值 */
  totalWidthPx: number;
  /** 节点布局映射：nodeId -> NodeLayout */
  nodeLayouts: Map<string, NodeLayout>;
  /** 跨泳道连接线描述列表 */
  crossLaneLinks: CrossLaneLink[];
  /** 时间刻度标记列表 */
  timeTicks: TimeTick[];
  /** 是否降级为等宽布局（有效时间戳 < 2 或时间范围 = 0） */
  degraded: boolean;
}

// ─── 布局计算常量 ──────────────────────────────────────────────

/** 节点最小宽度（像素） */
export const MIN_NODE_WIDTH = 48;
/** 相邻节点最小间距（像素） */
const MIN_GAP = 8;
/** 时间轴左右 padding（像素） */
const PADDING = 0;

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

// ─── Dispatch 模式检测 ──────────────────────────────────────

type DispatchMode = "direct" | "worker";

/**
 * 扫描事件流，检测本轮的 dispatch 模式：
 * - "direct": Butler 直接执行（无 WORKER_DISPATCHED 事件）
 * - "worker": 传统 Worker 路由（存在 WORKER_DISPATCHED 事件）
 */
function detectDispatchMode(nodes: FlowNode[]): DispatchMode {
  for (const node of nodes) {
    if (node.kind === "worker") return "worker";
  }
  return "direct";
}

// ─── Agent 分配 ─────────────────────────────────────────────

/** 按 actor 字段为每个节点分配所属 Agent 名称 */
function assignAgents(nodes: FlowNode[]): void {
  const mode = detectDispatchMode(nodes);

  if (mode === "direct") {
    // Butler 直接执行：所有事件归入 Orchestrator 单泳道
    for (const node of nodes) {
      node.agent = "Orchestrator";
    }
    return;
  }

  // Worker 模式：保留现有逻辑，从 WORKER_DISPATCHED 提取 Worker 名称
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
    const wt = ev.payload?.selected_worker_type || ev.payload?.worker_type || ev.payload?.worker_capability;
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
    // 注意：必须按 actor 匹配 COMPLETED，避免抢走内层 worker 的配对
    if (isWrapperModelCall(event, events, i)) {
      consumed.add(event.event_id);
      const comp = findCompletionByActor(events, i + 1, PAIR_MAP[event.type] || [], consumed, event.actor);
      if (comp) consumed.add(comp.event_id);
      continue;
    }
    if (isInlineSkill(event)) {
      consumed.add(event.event_id);
      const comp = findCompletionByActor(events, i + 1, PAIR_MAP[event.type] || [], consumed, event.actor);
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

/** 按 actor 匹配的 findCompletion，避免包装层抢走内层事件的配对 */
function findCompletionByActor(
  events: TaskEvent[],
  startIdx: number,
  completionTypes: string[],
  consumed: Set<string>,
  actor: string,
): TaskEvent | undefined {
  for (let j = startIdx; j < events.length; j++) {
    const c = events[j];
    if (consumed.has(c.event_id)) continue;
    if (completionTypes.includes(c.type) && c.actor === actor) return c;
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

    case "ORCH_DECISION": {
      const routeReason = String(event.payload?.route_reason || "");
      const isButlerDirect = routeReason.startsWith("butler_direct_execution:");
      return {
        ...base,
        id: event.event_id,
        kind: "decision",
        label: isButlerDirect
          ? "Butler 直接处理"
          : str(event.payload?.decision, 20) || "调度决策",
        status: "neutral",
        events: [event],
        ts: event.ts,
      };
    }

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

// ─── 时间轴布局计算 (Feature 065) ─────────────────────────────

/**
 * 降级布局：返回 degraded=true 的空布局，
 * 组件层据此走原有 flex 等宽渲染路径。
 */
function buildDegradedLayout(): TimelineLayout {
  return {
    totalWidthPx: 0,
    nodeLayouts: new Map(),
    crossLaneLinks: [],
    timeTicks: [],
    degraded: true,
  };
}

/**
 * 顺序对齐布局主入口：按节点顺序排列，Worker 泳道与 Orchestrator 的
 * Worker 胶囊条左右对齐。不按时间比例，只保证顺序正确。
 *
 * 降级条件：总节点 < 2 时走原有 flex 路径。
 */
export function computeTimelineLayout(
  lanes: AgentLane[],
  _startTime: string,
  _endTime?: string,
): TimelineLayout {
  const allNodes = lanes.flatMap((l) => l.nodes);
  if (allNodes.length < 2) return buildDegradedLayout();

  const STEP = MIN_NODE_WIDTH + MIN_GAP; // 每个普通节点的步进宽度
  const nodeLayouts = new Map<string, NodeLayout>();

  // ── 1. 找出 Orchestrator 泳道中的 worker 节点，记录它对应哪个 Worker 泳道 ──
  const orchLaneIdx = lanes.findIndex((l) => l.agent === "Orchestrator");
  // workerNodeId -> workerLaneIndex
  const workerSpanMap = new Map<string, number>();
  if (orchLaneIdx >= 0) {
    for (const node of lanes[orchLaneIdx].nodes) {
      if (node.kind !== "worker") continue;
      const targetLaneIdx = resolveWorkerLaneIndex(node, lanes);
      if (targetLaneIdx >= 0) workerSpanMap.set(node.id, targetLaneIdx);
    }
  }

  // ── 2. 计算每个 Worker 泳道所需的宽度（用于 Worker 胶囊展宽） ──
  const workerLaneWidthMap = new Map<number, number>();
  for (const [, wLaneIdx] of workerSpanMap) {
    const wLane = lanes[wLaneIdx];
    if (!wLane) continue;
    // Worker 泳道节点需要的总宽度
    const w = Math.max(wLane.nodes.length * STEP - MIN_GAP, MIN_NODE_WIDTH);
    workerLaneWidthMap.set(wLaneIdx, w);
  }

  // ── 3. 将 Orchestrator 中相邻的 worker 节点分组为并发组 ──
  // 相邻 worker 节点（中间只隔 decision/a2a）视为并发 dispatch
  const concurrentGroups: string[][] = []; // 每组包含 workerNodeId[]
  if (orchLaneIdx >= 0) {
    let currentGroup: string[] = [];
    for (const node of lanes[orchLaneIdx].nodes) {
      if (workerSpanMap.has(node.id)) {
        currentGroup.push(node.id);
      } else if (node.kind === "decision" || node.kind === "a2a") {
        // decision/a2a 不打断并发组
      } else {
        if (currentGroup.length > 0) {
          concurrentGroups.push(currentGroup);
          currentGroup = [];
        }
      }
    }
    if (currentGroup.length > 0) concurrentGroups.push(currentGroup);
  }

  // 并发组内所有胶囊共享最大宽度
  const sharedWidthMap = new Map<string, number>(); // workerNodeId -> sharedWidth
  const sharedGroupMap = new Map<string, string[]>(); // workerNodeId -> 同组所有 workerNodeId
  for (const group of concurrentGroups) {
    if (group.length <= 1) continue; // 单个不需要共享
    let maxW = MIN_NODE_WIDTH;
    for (const nid of group) {
      const wLaneIdx = workerSpanMap.get(nid)!;
      maxW = Math.max(maxW, workerLaneWidthMap.get(wLaneIdx) || MIN_NODE_WIDTH);
    }
    for (const nid of group) {
      sharedWidthMap.set(nid, maxW);
      sharedGroupMap.set(nid, group);
    }
  }

  // ── 4. 布局 Orchestrator 泳道（主轴） ──
  let cursor = PADDING;
  const concurrentPlaced = new Set<string>(); // 已布局的并发组首节点
  if (orchLaneIdx >= 0) {
    for (const node of lanes[orchLaneIdx].nodes) {
      const wLaneIdx = workerSpanMap.get(node.id);
      if (wLaneIdx !== undefined) {
        const group = sharedGroupMap.get(node.id);
        if (group && group.length > 1) {
          // 并发组：所有胶囊共享同一水平范围
          const groupKey = group[0];
          if (!concurrentPlaced.has(groupKey)) {
            concurrentPlaced.add(groupKey);
            const sharedWidth = sharedWidthMap.get(node.id) || MIN_NODE_WIDTH;
            const groupLeft = cursor;
            for (const gid of group) {
              nodeLayouts.set(gid, { leftPx: groupLeft, widthPx: sharedWidth, laneIndex: orchLaneIdx });
            }
            cursor += sharedWidth + MIN_GAP;
          }
          // 组内后续节点已在首次遇到时布局过，跳过
        } else {
          // 单独 Worker 胶囊
          const spanWidth = workerLaneWidthMap.get(wLaneIdx) || MIN_NODE_WIDTH;
          nodeLayouts.set(node.id, { leftPx: cursor, widthPx: spanWidth, laneIndex: orchLaneIdx });
          cursor += spanWidth + MIN_GAP;
        }
      } else {
        // 普通节点
        nodeLayouts.set(node.id, { leftPx: cursor, widthPx: MIN_NODE_WIDTH, laneIndex: orchLaneIdx });
        cursor += STEP;
      }
    }
  }

  // ── 5. 布局 Worker 泳道：对齐到 Orchestrator 的 Worker 胶囊条 ──
  for (const [workerNodeId, wLaneIdx] of workerSpanMap) {
    const wLane = lanes[wLaneIdx];
    const orchSpan = nodeLayouts.get(workerNodeId);
    if (!wLane || !orchSpan) continue;

    const spanLeft = orchSpan.leftPx;
    const spanWidth = orchSpan.widthPx;
    const nodeCount = wLane.nodes.length;

    if (nodeCount === 1) {
      // 单节点居中
      nodeLayouts.set(wLane.nodes[0].id, {
        leftPx: spanLeft + (spanWidth - MIN_NODE_WIDTH) / 2,
        widthPx: MIN_NODE_WIDTH,
        laneIndex: wLaneIdx,
      });
    } else {
      // 多节点：首节点对齐 spanLeft，末节点右边缘对齐 spanLeft + spanWidth
      const availableWidth = spanWidth - MIN_NODE_WIDTH;
      const step = availableWidth / (nodeCount - 1);
      for (let i = 0; i < nodeCount; i++) {
        nodeLayouts.set(wLane.nodes[i].id, {
          leftPx: spanLeft + i * step,
          widthPx: MIN_NODE_WIDTH,
          laneIndex: wLaneIdx,
        });
      }
    }
  }

  // ── 6. 布局无 Orchestrator 关联的独立泳道 ──
  for (let li = 0; li < lanes.length; li++) {
    if (li === orchLaneIdx) continue;
    // 跳过已被 workerSpanMap 布局的泳道
    const isHandled = [...workerSpanMap.values()].includes(li);
    if (isHandled) continue;

    let laneCursor = PADDING;
    for (const node of lanes[li].nodes) {
      if (!nodeLayouts.has(node.id)) {
        nodeLayouts.set(node.id, { leftPx: laneCursor, widthPx: MIN_NODE_WIDTH, laneIndex: li });
        laneCursor += STEP;
      }
    }
    cursor = Math.max(cursor, laneCursor);
  }

  // ── 7. 总宽度 ──
  let maxRight = 0;
  for (const [, nl] of nodeLayouts) {
    maxRight = Math.max(maxRight, nl.leftPx + nl.widthPx);
  }
  const totalWidthPx = maxRight + PADDING;

  return {
    totalWidthPx,
    nodeLayouts,
    crossLaneLinks: [],
    timeTicks: [], // 顺序布局不需要时间刻度
    degraded: false,
  };
}

/** 找到 worker 节点对应的 Worker 泳道索引 */
function resolveWorkerLaneIndex(workerNode: FlowNode, lanes: AgentLane[]): number {
  // 1. 从 payload 提取 agent_name，精确匹配
  const agentName = extractAgentName(workerNode);
  if (agentName) {
    const idx = lanes.findIndex((l) => l.agent === agentName);
    if (idx >= 0) return idx;
  }
  // 2. worker_id 短名匹配
  const wid = extractWorkerId(workerNode);
  if (wid) {
    const shortName = `Worker ${shortId(wid)}`;
    const idx = lanes.findIndex((l) => l.agent === shortName);
    if (idx >= 0) return idx;
  }
  // 3. 只有一个非 Orchestrator 泳道
  const nonOrch = lanes.map((l, i) => ({ l, i })).filter(({ l }) => l.agent !== "Orchestrator");
  if (nonOrch.length === 1) return nonOrch[0].i;
  return -1;
}

