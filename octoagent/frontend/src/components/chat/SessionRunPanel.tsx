import { useState } from "react";
import { Link } from "react-router-dom";
import { StatusBadge, HoverReveal } from "../../ui/primitives";
import { TERMINAL_TASK_STATUSES } from "../../domains/chat/constants";
import type { Artifact, TaskEvent } from "../../types";

/**
 * F148 右栏「本会话运行状态」（1a）。
 * 纯展示组件——消费 ChatWorkbench 已有的 useTaskLiveState 数据（不重复轮询）。
 * 只读镜像：状态 / 进度 / 事件流 / 工件 / 「打开任务」。停止控制暂缺——现有前端
 * 无直连 cancel action（仅 operator inbox item_id 路径），造一个需碰 backend，
 * 超 F148 纯前端红线，故 defer（见 spec §10 L6）。
 */

const FAILURE_STATUSES = new Set(["FAILED", "CANCELLED", "TIMED_OUT"]);

// 事件类型 → 面向非技术用户的友好描述 + tone
function describeEvent(event: TaskEvent): { title: string; tone: "normal" | "pending" | "error" } {
  const type = String(event.type ?? "").toUpperCase();
  const payload = (event.payload ?? {}) as Record<string, unknown>;
  const toolName = typeof payload.tool_name === "string" ? payload.tool_name : "";
  if (type.startsWith("TOOL_CALL")) {
    const done = type.includes("COMPLETED");
    return { title: `${done ? "完成工具" : "调用工具"}${toolName ? ` · ${toolName}` : ""}`, tone: "normal" };
  }
  if (type.startsWith("MODEL_CALL")) {
    return { title: "模型推理", tone: "normal" };
  }
  if (type.startsWith("APPROVAL")) {
    return { title: "等待确认", tone: "pending" };
  }
  if (type === "ARTIFACT_CREATED") {
    return { title: "生成产出文件", tone: "normal" };
  }
  if (type === "STATE_TRANSITION") {
    const to = typeof payload.to_status === "string" ? payload.to_status : "";
    return { title: `状态更新${to ? ` · ${to}` : ""}`, tone: "normal" };
  }
  if (type === "ERROR") {
    return { title: "遇到错误", tone: "error" };
  }
  if (type === "USER_MESSAGE") {
    return { title: "收到你的消息", tone: "normal" };
  }
  // 兜底：把 SNAKE_CASE 类型转成可读文案
  return { title: type ? type.toLowerCase().replace(/_/g, " ") : "运行事件", tone: "normal" };
}

function formatEventTime(ts: string): string {
  if (!ts) return "";
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export interface SessionRunPanelProps {
  taskId: string | null;
  statusLabel: string;
  statusTone: string;
  normalizedTaskStatus: string;
  currentStep: string;
  streaming: boolean;
  events: TaskEvent[];
  artifacts: Artifact[];
  techRefs: Array<{ label: string; value: string }>;
}

export function SessionRunPanel({
  taskId,
  statusLabel,
  statusTone,
  normalizedTaskStatus,
  currentStep,
  streaming,
  events,
  artifacts,
  techRefs,
}: SessionRunPanelProps) {
  const [techExpanded, setTechExpanded] = useState(false);
  const isFailed = FAILURE_STATUSES.has(normalizedTaskStatus);
  const isTerminal = TERMINAL_TASK_STATUSES.has(normalizedTaskStatus);
  const isActive = Boolean(taskId);

  // 空态（交互态①）：本会话还没有跑任何任务
  if (!isActive) {
    return (
      <aside className="v2-run-panel" data-testid="session-run-panel" aria-label="本会话运行状态">
        <div className="v2-run-panel-head">
          <span className="v2-run-panel-kicker">本会话运行状态</span>
        </div>
        <div className="v2-run-empty" data-testid="session-run-empty">
          <img className="octo-jelly" src="/octo-mark.svg" alt="" width={56} height={56} />
          <strong>就绪</strong>
          <span>发一条消息，这里会实时显示进度、步骤和产出文件。</span>
        </div>
      </aside>
    );
  }

  const recentEvents = events.slice(-16);

  return (
    <aside
      className={`v2-run-panel ${isFailed ? "is-failed" : ""}`}
      data-testid="session-run-panel"
      aria-label="本会话运行状态"
    >
      <div className="v2-run-panel-head">
        <h3>本会话运行状态</h3>
        <StatusBadge tone={statusTone}>{statusLabel}</StatusBadge>
      </div>

      {isFailed ? (
        <div className="v2-run-banner-failed" data-testid="session-run-failed">
          这一轮没有正常完成。你可以在下方看到发生了什么，或重新发一条消息再试。
        </div>
      ) : null}

      <div className="v2-run-section">
        <span className="v2-run-section-label">进度</span>
        <div className="v2-run-progress">
          <strong>
            {isTerminal ? statusLabel : streaming ? "正在处理这一轮" : statusLabel}
          </strong>
          <span>{currentStep?.trim() || (streaming ? "主 Agent 正在推进当前步骤…" : "暂无更多步骤信息。")}</span>
        </div>
      </div>

      {recentEvents.length > 0 ? (
        <div className="v2-run-section">
          <span className="v2-run-section-label">最近动作</span>
          <div className="v2-run-events">
            {recentEvents.map((event, index) => {
              const described = describeEvent(event);
              return (
                <div
                  key={`${event.event_id || event.type}-${event.task_seq}-${index}`}
                  className={`v2-run-event ${described.tone === "pending" ? "is-pending" : ""} ${
                    described.tone === "error" ? "is-error" : ""
                  }`}
                >
                  <span className="v2-run-event-dot" aria-hidden="true" />
                  <span className="v2-run-event-copy">
                    <span className="v2-run-event-title">{described.title}</span>
                    <span className="v2-run-event-meta">{formatEventTime(event.ts)}</span>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {artifacts.length > 0 ? (
        <div className="v2-run-section">
          <span className="v2-run-section-label">产出文件</span>
          {artifacts.map((artifact) => (
            <div key={artifact.artifact_id} className="v2-run-artifact">
              <i className="ri-file-3-line" aria-hidden="true" />
              <span>{artifact.name}</span>
            </div>
          ))}
        </div>
      ) : null}

      <div className="v2-run-controls">
        <Link className="wb-button wb-button-secondary wb-button-inline" to={`/tasks/${taskId}`}>
          打开任务详情
        </Link>
      </div>

      {techRefs.length > 0 ? (
        <HoverReveal
          label="技术详情"
          ariaLabel="本会话技术详情"
          triggerClassName="wb-button-inline"
          expanded={techExpanded}
          onToggle={setTechExpanded}
        >
          {techRefs.map((item) => (
            <div key={item.label} className="wb-hover-reveal-row">
              <span>{item.label}</span>
              <strong>{item.value}</strong>
            </div>
          ))}
        </HoverReveal>
      ) : null}
    </aside>
  );
}

export default SessionRunPanel;
