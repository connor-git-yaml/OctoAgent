/**
 * PhaseCard -- 单个阶段卡片
 *
 * 左侧 4px 彩色 border + 阶段图标/名称/时间范围 + 事件摘要列表。
 * 按 PhaseId 差异化提取摘要，>5 条事件折叠，SSE 新增事件 slide-in 动画。
 */

import { useState, useRef, useEffect } from "react";
import type { PhaseState, TaskEvent, PhaseId } from "../../types";
import { formatTime } from "../../utils/formatTime";

interface PhaseCardProps {
  phase: PhaseState;
}

/** 阶段对应的图标（Unicode） */
const PHASE_ICONS: Record<PhaseId, string> = {
  received: "\u{1F4E8}",   // 信封
  thinking: "\u{1F9E0}",   // 大脑
  executing: "\u{2699}\uFE0F", // 齿轮
  completed: "\u{2705}",   // 勾号
  system: "\u{2699}\uFE0F",   // 齿轮
};

const COLLAPSE_THRESHOLD = 5;
const COLLAPSED_VISIBLE = 3;

/** 截断文本 */
function truncate(text: string, max: number): string {
  return text.length > max ? text.slice(0, max) + "..." : text;
}

/** 安全提取字符串字段 */
function str(val: unknown, fallback = ""): string {
  return typeof val === "string" ? val : fallback;
}

/** 格式化耗时 */
function fmtDuration(ms: unknown): string | undefined {
  return typeof ms === "number" ? `${(ms / 1000).toFixed(1)}s` : undefined;
}

/** 计算时间范围文本 */
function getTimeRange(events: TaskEvent[]): string {
  if (events.length === 0) return "";
  const first = formatTime(events[0].ts);
  if (events.length === 1) return first;
  return `${first} ~ ${formatTime(events[events.length - 1].ts)}`;
}

/** 动画 class */
function eventCls(isNew: boolean): string {
  return isNew ? "tv-phase-event tv-phase-event-enter" : "tv-phase-event";
}

// ─── 通用事件行渲染 ──────────────────────────────────────────────

type BadgeVariant = "success" | "danger" | "warning";

function eventRow(
  event: TaskEvent,
  isNew: boolean,
  parts: {
    badge?: { text: string; variant: BadgeVariant };
    label?: string;
    value?: string;
  },
): React.ReactNode {
  return (
    <div className={eventCls(isNew)} key={event.event_id}>
      {parts.badge && (
        <span className={`tv-phase-event-badge tv-phase-event-badge--${parts.badge.variant}`}>
          {parts.badge.text}
        </span>
      )}
      {parts.label && (
        <span className="tv-phase-event-label">
          {parts.badge ? ` ${parts.label}` : parts.label}
        </span>
      )}
      {parts.value && <span className="tv-phase-event-value">{parts.value}</span>}
      <span className="tv-phase-event-time">{formatTime(event.ts)}</span>
    </div>
  );
}

// ─── 各阶段事件摘要 ──────────────────────────────────────────────

function renderReceivedEvent(event: TaskEvent, isNew: boolean): React.ReactNode {
  if (event.type === "USER_MESSAGE") {
    const content = str(event.payload.content);
    return eventRow(event, isNew, {
      label: content ? truncate(content, 80) : undefined,
      value: event.actor ? `via ${event.actor}` : undefined,
    });
  }
  return eventRow(event, isNew, { label: "任务已创建" });
}

function renderThinkingEvent(event: TaskEvent, isNew: boolean): React.ReactNode {
  const p = event.payload;
  const model = str(p.model, "LLM");
  const usage = p.usage as Record<string, unknown> | undefined;
  const tokens = usage?.total_tokens ?? usage?.totalTokens;

  switch (event.type) {
    case "MODEL_CALL_COMPLETED":
      return eventRow(event, isNew, {
        label: model,
        value: [
          tokens != null ? `${tokens} tokens` : "",
          fmtDuration(p.duration_ms) ?? "",
        ].filter(Boolean).join(" · ") || undefined,
      });
    case "MODEL_CALL_FAILED":
      return eventRow(event, isNew, {
        badge: { text: "失败", variant: "danger" },
        label: model,
        value: str(p.error) ? truncate(str(p.error), 60) : "调用失败",
      });
    case "MODEL_CALL_STARTED":
      return eventRow(event, isNew, { label: `${model} 调用中...` });
    case "MEMORY_RECALL_COMPLETED":
      return eventRow(event, isNew, { label: "记忆检索完成" });
    case "MEMORY_RECALL_FAILED":
      return eventRow(event, isNew, { badge: { text: "记忆检索失败", variant: "warning" } });
    case "CONTEXT_COMPACTION_COMPLETED":
      return eventRow(event, isNew, { label: "上下文压缩完成" });
    default:
      return eventRow(event, isNew, { label: event.type });
  }
}

function renderExecutingEvent(event: TaskEvent, isNew: boolean): React.ReactNode {
  const p = event.payload;

  switch (event.type) {
    case "TOOL_CALL_STARTED":
      return eventRow(event, isNew, { label: str(p.tool_name, "工具"), value: "执行中..." });
    case "TOOL_CALL_COMPLETED":
      return eventRow(event, isNew, {
        badge: { text: "完成", variant: "success" },
        label: str(p.tool_name, "工具"),
        value: fmtDuration(p.duration_ms),
      });
    case "TOOL_CALL_FAILED":
      return eventRow(event, isNew, {
        badge: { text: "失败", variant: "danger" },
        label: str(p.tool_name, "工具"),
      });
    case "SKILL_STARTED":
      return eventRow(event, isNew, { label: str(p.skill_name, "Skill"), value: "执行中..." });
    case "SKILL_COMPLETED":
      return eventRow(event, isNew, {
        badge: { text: "完成", variant: "success" },
        label: str(p.skill_name, "Skill"),
        value: fmtDuration(p.duration_ms),
      });
    case "SKILL_FAILED":
      return eventRow(event, isNew, {
        badge: { text: "失败", variant: "danger" },
        label: str(p.skill_name, "Skill"),
      });
    case "WORKER_DISPATCHED":
      return eventRow(event, isNew, { label: "Worker 已派发" });
    case "WORKER_RETURNED":
      return eventRow(event, isNew, { label: "Worker 已返回" });
    default:
      return eventRow(event, isNew, { label: event.type });
  }
}

function renderCompletedEvent(event: TaskEvent, isNew: boolean): React.ReactNode {
  if (event.type === "STATE_TRANSITION") {
    const toStatus = str(event.payload.to_status);
    const statusLabels: Record<string, string> = {
      SUCCEEDED: "成功", FAILED: "失败", CANCELLED: "已取消", REJECTED: "已拒绝",
    };
    return eventRow(event, isNew, {
      badge: {
        text: statusLabels[toStatus] || toStatus,
        variant: toStatus === "SUCCEEDED" ? "success" : "danger",
      },
    });
  }
  if (event.type === "ARTIFACT_CREATED") {
    return eventRow(event, isNew, { label: `产出: ${str(event.payload.name, "产物")}` });
  }
  return eventRow(event, isNew, { label: event.type });
}

/** 按 PhaseId 分发事件渲染函数 */
const PHASE_RENDERERS: Record<PhaseId, (event: TaskEvent, isNew: boolean) => React.ReactNode> = {
  received: renderReceivedEvent,
  thinking: renderThinkingEvent,
  executing: renderExecutingEvent,
  completed: renderCompletedEvent,
  system: (event, isNew) => eventRow(event, isNew, { label: event.type }),
};

// ─── PhaseCard 主组件 ────────────────────────────────────────────

export default function PhaseCard({ phase }: PhaseCardProps) {
  const [expanded, setExpanded] = useState(false);
  const { config, status, events } = phase;
  const renderer = PHASE_RENDERERS[config.id];

  // 跟踪已知事件 ID：初始加载的事件不触发 slide-in 动画
  const knownIdsRef = useRef<Set<string> | null>(null);
  if (knownIdsRef.current === null) {
    knownIdsRef.current = new Set(events.map((e) => e.event_id));
  }

  // SSE 新增事件渲染后，同步更新已知集合（避免 setTimeout 竞态）
  useEffect(() => {
    for (const e of events) {
      knownIdsRef.current!.add(e.event_id);
    }
  }, [events]);

  if (events.length === 0) return null;

  const shouldCollapse = events.length > COLLAPSE_THRESHOLD && !expanded;
  const visibleEvents = shouldCollapse
    ? events.slice(events.length - COLLAPSED_VISIBLE)
    : events;
  const hiddenCount = events.length - COLLAPSED_VISIBLE;

  const errorClass = status === "error" ? " tv-phase-card--error" : "";

  return (
    <div className={`tv-phase-card tv-phase-card--${config.id}${errorClass}`}>
      <div className="tv-phase-card-header">
        <span className="tv-phase-icon">{PHASE_ICONS[config.id]}</span>
        <span className="tv-phase-title">{config.label}</span>
        <span className="tv-phase-time-range">{getTimeRange(events)}</span>
      </div>

      <div className="tv-phase-events">
        {shouldCollapse && (
          <button className="tv-phase-expand-btn" onClick={() => setExpanded(true)} type="button">
            + 展开全部 {hiddenCount} 条早期事件
          </button>
        )}

        {visibleEvents.map((event) => {
          const isNew = !knownIdsRef.current!.has(event.event_id);
          return renderer(event, isNew);
        })}

        {expanded && events.length > COLLAPSE_THRESHOLD && (
          <button className="tv-phase-expand-btn" onClick={() => setExpanded(false)} type="button">
            收起
          </button>
        )}
      </div>
    </div>
  );
}
