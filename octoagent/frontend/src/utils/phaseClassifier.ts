/**
 * 阶段归类引擎 -- 将后端 EventType 映射到用户可理解的阶段
 *
 * 覆盖后端 EventType 枚举全部值 + 前端特有 SESSION_STATUS_CHANGED。
 * 未识别的事件类型一律归入 "system" 阶段。
 */

import type {
  TaskEvent,
  TaskStatus,
  PhaseId,
  PhaseConfig,
  PhaseState,
  ClassifiedResult,
} from "../types";

// ─── 阶段配置（静态） ──────────────────────────────────────────

export const PHASE_CONFIGS: PhaseConfig[] = [
  { id: "received", label: "接收", color: "var(--cp-secondary)", userVisible: true },
  { id: "thinking", label: "思考", color: "var(--cp-primary)", userVisible: true },
  { id: "executing", label: "执行", color: "var(--cp-secondary-ink)", userVisible: true },
  { id: "completed", label: "完成", color: "var(--cp-success)", userVisible: true },
  { id: "system", label: "系统", color: "var(--cp-muted)", userVisible: false },
];

/** 按 PhaseId 快速查找配置 */
export const PHASE_CONFIG_MAP: Record<PhaseId, PhaseConfig> = Object.fromEntries(
  PHASE_CONFIGS.map((c) => [c.id, c]),
) as Record<PhaseId, PhaseConfig>;

// ─── EventType -> PhaseId 映射表 ───────────────────────────────

/**
 * 映射表：覆盖后端 EventType 枚举全部值。
 * STATE_TRANSITION 不在此表中，由 classifyStateTransition() 特殊处理。
 */
export const PHASE_MAP: Record<string, PhaseId> = {
  // 接收阶段
  TASK_CREATED: "received",
  USER_MESSAGE: "received",

  // 思考阶段
  MODEL_CALL_STARTED: "thinking",
  MODEL_CALL_COMPLETED: "thinking",
  MODEL_CALL_FAILED: "thinking",
  CONTEXT_COMPACTION_COMPLETED: "thinking",
  MEMORY_RECALL_SCHEDULED: "thinking",
  MEMORY_RECALL_COMPLETED: "thinking",
  MEMORY_RECALL_FAILED: "thinking",
  ORCH_DECISION: "thinking",

  // 执行阶段
  TOOL_CALL_STARTED: "executing",
  TOOL_CALL_COMPLETED: "executing",
  TOOL_CALL_FAILED: "executing",
  SKILL_STARTED: "executing",
  SKILL_COMPLETED: "executing",
  SKILL_FAILED: "executing",
  WORKER_DISPATCHED: "executing",
  WORKER_RETURNED: "executing",
  WORK_CREATED: "executing",
  WORK_STATUS_CHANGED: "executing",
  EXECUTION_STATUS_CHANGED: "executing",
  EXECUTION_LOG: "executing",
  EXECUTION_STEP: "executing",
  EXECUTION_INPUT_REQUESTED: "executing",
  EXECUTION_INPUT_ATTACHED: "executing",
  EXECUTION_CANCEL_REQUESTED: "executing",
  A2A_MESSAGE_SENT: "executing",
  A2A_MESSAGE_RECEIVED: "executing",
  PIPELINE_RUN_UPDATED: "executing",
  PIPELINE_CHECKPOINT_SAVED: "executing",
  TOOL_INDEX_SELECTED: "executing",

  // 完成阶段（STATE_TRANSITION 到终态 + ARTIFACT_CREATED）
  ARTIFACT_CREATED: "completed",

  // 系统阶段 -- 策略/审批/凭证/OAuth/检查点/恢复/心跳/漂移/操作记录/备份/导入/控制平面/错误
  POLICY_DECISION: "system",
  POLICY_CONFIG_CHANGED: "system",
  APPROVAL_REQUESTED: "system",
  APPROVAL_APPROVED: "system",
  APPROVAL_REJECTED: "system",
  APPROVAL_EXPIRED: "system",
  CREDENTIAL_LOADED: "system",
  CREDENTIAL_EXPIRED: "system",
  CREDENTIAL_FAILED: "system",
  OAUTH_STARTED: "system",
  OAUTH_SUCCEEDED: "system",
  OAUTH_FAILED: "system",
  OAUTH_REFRESHED: "system",
  CHECKPOINT_SAVED: "system",
  RESUME_STARTED: "system",
  RESUME_SUCCEEDED: "system",
  RESUME_FAILED: "system",
  TASK_HEARTBEAT: "system",
  TASK_MILESTONE: "system",
  TASK_DRIFT_DETECTED: "system",
  OPERATOR_ACTION_RECORDED: "system",
  BACKUP_STARTED: "system",
  BACKUP_COMPLETED: "system",
  BACKUP_FAILED: "system",
  CHAT_IMPORT_STARTED: "system",
  CHAT_IMPORT_COMPLETED: "system",
  CHAT_IMPORT_FAILED: "system",
  CONTROL_PLANE_RESOURCE_PROJECTED: "system",
  CONTROL_PLANE_RESOURCE_REMOVED: "system",
  CONTROL_PLANE_ACTION_REQUESTED: "system",
  CONTROL_PLANE_ACTION_COMPLETED: "system",
  CONTROL_PLANE_ACTION_REJECTED: "system",
  CONTROL_PLANE_ACTION_DEFERRED: "system",
  ERROR: "system",

  // 前端特有事件
  SESSION_STATUS_CHANGED: "system",
};

// ─── 终态集合 ───────────────────────────────────────────────────

export const TERMINAL_STATUSES: Set<string> = new Set([
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
  "REJECTED",
]);

// 失败终态集合
const FAILURE_TERMINAL_STATUSES: Set<string> = new Set([
  "FAILED",
  "CANCELLED",
  "REJECTED",
]);

// ─── 失败事件类型集合 ──────────────────────────────────────────

const FAILURE_EVENT_TYPES: Set<string> = new Set([
  "MODEL_CALL_FAILED",
  "TOOL_CALL_FAILED",
  "SKILL_FAILED",
  "MEMORY_RECALL_FAILED",
  "ERROR",
]);

// ─── STATE_TRANSITION 特殊处理 ─────────────────────────────────

/**
 * STATE_TRANSITION 事件根据 payload.to_status 判断归类：
 * - to_status 为终态 -> "completed"
 * - 否则 -> "system"
 */
export function classifyStateTransition(event: TaskEvent): PhaseId {
  const toStatus = event.payload?.to_status;
  if (typeof toStatus === "string" && TERMINAL_STATUSES.has(toStatus)) {
    return "completed";
  }
  return "system";
}

// ─── 主归类函数 ────────────────────────────────────────────────

/**
 * 将事件列表归类到阶段，计算每个阶段的状态。
 *
 * @param events - 事件列表（按时间顺序）
 * @param taskStatus - 当前任务状态
 * @returns ClassifiedResult 包含所有阶段的状态和事件
 */
export function classifyEvents(
  events: TaskEvent[],
  taskStatus: TaskStatus,
): ClassifiedResult {
  // 初始化阶段容器
  const phaseEvents: Record<PhaseId, TaskEvent[]> = {
    received: [],
    thinking: [],
    executing: [],
    completed: [],
    system: [],
  };

  // 归类每个事件
  for (const event of events) {
    let phaseId: PhaseId;
    if (event.type === "STATE_TRANSITION") {
      phaseId = classifyStateTransition(event);
    } else {
      phaseId = PHASE_MAP[event.type] ?? "system";
    }
    phaseEvents[phaseId].push(event);
  }

  // 判断任务是否处于终态
  const isTerminal = TERMINAL_STATUSES.has(taskStatus);
  const isFailureTerminal = FAILURE_TERMINAL_STATUSES.has(taskStatus);

  // 用户可见阶段 ID 顺序（排除 system）
  const visiblePhaseIds: PhaseId[] = ["received", "thinking", "executing", "completed"];

  // 找到最后一个有事件的可见阶段索引
  let lastActiveIndex = -1;
  for (let i = visiblePhaseIds.length - 1; i >= 0; i--) {
    if (phaseEvents[visiblePhaseIds[i]].length > 0) {
      lastActiveIndex = i;
      break;
    }
  }

  // 检查某阶段是否包含失败事件
  function hasFailureEvents(phaseId: PhaseId): boolean {
    return phaseEvents[phaseId].some((e) => FAILURE_EVENT_TYPES.has(e.type));
  }

  // 计算每个阶段的状态
  const phases: PhaseState[] = PHASE_CONFIGS.map((config) => {
    const evts = phaseEvents[config.id];

    // 系统阶段不参与进度计算
    if (config.id === "system") {
      return { config, status: "pending" as const, events: evts };
    }

    const phaseIndex = visiblePhaseIds.indexOf(config.id);
    const hasEvents = evts.length > 0;

    if (!hasEvents) {
      return { config, status: "pending" as const, events: evts };
    }

    // 任务已终态
    if (isTerminal) {
      // 失败终态：最后活跃阶段标 error（如果有失败事件或就是最后活跃阶段）
      if (isFailureTerminal && phaseIndex === lastActiveIndex) {
        return { config, status: "error" as const, events: evts };
      }
      // 有失败事件且是失败终态的非最后阶段
      if (isFailureTerminal && hasFailureEvents(config.id)) {
        return { config, status: "error" as const, events: evts };
      }
      return { config, status: "done" as const, events: evts };
    }

    // 任务未终态
    if (phaseIndex === lastActiveIndex) {
      // 最后一个有事件的阶段 -> active
      return { config, status: "active" as const, events: evts };
    }

    // 非最后活跃阶段但有失败事件 -> 仍然标 done（任务还在跑，可能在重试）
    return { config, status: "done" as const, events: evts };
  });

  return { phases };
}

// ─── 工具函数 ──────────────────────────────────────────────────

/**
 * 将字节数格式化为友好大小（B / KB / MB / GB）
 */
export function formatFileSize(bytes: number): string {
  if (bytes < 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}
