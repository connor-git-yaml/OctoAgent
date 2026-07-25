/**
 * TaskDetail 页面 -- 展示任务详情 + 可视化/Raw Data 双模式
 *
 * 功能：
 * 1. 调用 GET /api/tasks/{id} 获取任务信息 + 事件 + artifacts
 * 2. 展示任务基本信息（紧凑头部）
 * 3. 可视化模式：按轮次拆分的 Agent 泳道流程图 + 点击弹框
 * 4. Raw Data 模式：事件时间线（类型、时间、payload 摘要）
 * 5. 进行中任务通过 useSSE 实时追加新事件
 */

import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { useParams, Link } from "react-router-dom";
import {
  ApiError,
  fetchTaskDetail,
  isFrontDoorApiError,
} from "../api/client";
import { mapF149ErrorOwnership } from "../api/f149/errorOwnership";
import {
  decodeTaskSseFrame,
  type RawTaskSseFrame,
  type TaskSseProjection,
} from "../api/f149/taskSseDecoder";
import FrontDoorGate from "../components/FrontDoorGate";
import { useSSE } from "../hooks/useSSE";
import {
  SegmentedToggle,
  RoundFlowCard,
  NodeDetailModal,
} from "../components/TaskVisualization";
import type { ViewMode } from "../components/TaskVisualization";
import { classifyEvents, TERMINAL_STATUSES } from "../utils/phaseClassifier";
import { splitIntoRounds } from "../utils/roundSplitter";
import type { FlowNode } from "../utils/roundSplitter";
import { formatTime } from "../utils/formatTime";
import { formatSessionDisplayTitle } from "../workbench/utils";
import type {
  TaskDetail as TaskDetailType,
  TaskDetailResponse,
  TaskEvent,
  Artifact,
} from "../types";

/** 状态 badge 的用户友好文案 */
const STATUS_LABEL: Record<string, string> = {
  CREATED: "已创建",
  QUEUED: "排队中",
  RUNNING: "运行中",
  WAITING_INPUT: "等待输入",
  WAITING_APPROVAL: "等待审批",
  PAUSED: "已暂停",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
  REJECTED: "已拒绝",
};

/** 生成 payload 摘要 */
function payloadSummary(payload: Record<string, unknown>): string {
  const entries = Object.entries(payload);
  if (entries.length === 0) return "";

  const parts = entries.slice(0, 4).map(([k, v]) => {
    const val = typeof v === "string" ? v : JSON.stringify(v);
    const display = val.length > 60 ? val.slice(0, 60) + "..." : val;
    return `${k}: ${display}`;
  });

  return parts.join("\n");
}

function getLatestStateTransitionSeq(events: TaskEvent[]): number {
  return events.reduce((latest, event) => {
    if (event.type !== "STATE_TRANSITION") {
      return latest;
    }
    return Math.max(latest, event.task_seq);
  }, 0);
}

function projectTaskDetailEvents(events: TaskEvent[]): TaskEvent[] {
  return events.flatMap<TaskEvent>((event) => {
    const projection = {
      event_id: event.event_id,
      task_seq: event.task_seq,
      ts: event.ts,
      type: event.type,
      actor: event.actor,
    };
    if (
      event.type === "STATE_TRANSITION" &&
      typeof event.payload.to_status === "string" &&
      event.payload.to_status in STATUS_LABEL
    ) {
      return [{
        ...projection,
        payload: { to_status: event.payload.to_status },
      }];
    }
    if (event.type === "ARTIFACT_CREATED") {
      return [{
        ...projection,
        payload: { refresh_artifacts: true },
      }];
    }
    return [];
  });
}

function mergeTaskSnapshot(
  currentTask: TaskDetailType | null,
  nextTask: TaskDetailType,
  currentStatusSeq: number,
  nextStatusSeq: number,
): TaskDetailType {
  if (!currentTask || nextStatusSeq >= currentStatusSeq) {
    return nextTask;
  }
  return {
    ...nextTask,
    status: currentTask.status,
    updated_at: currentTask.updated_at > nextTask.updated_at
      ? currentTask.updated_at
      : nextTask.updated_at,
  };
}

type TaskSseDiagnostic = Extract<TaskSseProjection, { kind: "diagnostic" }>;

function taskEventFromProjection(
  event: Exclude<TaskSseProjection, { kind: "diagnostic" }>,
): TaskEvent {
  return {
    event_id: event.eventId,
    task_seq: event.taskSeq,
    ts: event.timestamp,
    type: event.sourceType,
    actor: event.actor,
    payload:
      event.kind === "state-transition"
        ? { to_status: event.toStatus }
        : { refresh_artifacts: true },
  };
}

export default function TaskDetail() {
  const { taskId } = useParams<{ taskId: string }>();
  const [task, setTask] = useState<TaskDetailType | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [diagnostics, setDiagnostics] = useState<TaskSseDiagnostic[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown | null>(null);
  const [authError, setAuthError] = useState<ApiError | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("visual");
  const [selectedNode, setSelectedNode] = useState<FlowNode | null>(null);
  const latestStatusSeqRef = useRef(0);
  const artifactRefreshInFlightRef = useRef(false);
  const artifactRefreshQueuedRef = useRef(false);

  useEffect(() => {
    latestStatusSeqRef.current = 0;
    artifactRefreshInFlightRef.current = false;
    artifactRefreshQueuedRef.current = false;
    setDiagnostics([]);
  }, [taskId]);

  const applyTaskDetail = useCallback((
    data: TaskDetailResponse,
    options?: { replaceEvents?: boolean },
  ) => {
    const projectedEvents = projectTaskDetailEvents(data.events);
    const currentStatusSeq = latestStatusSeqRef.current;
    const nextStatusSeq = getLatestStateTransitionSeq(projectedEvents);
    latestStatusSeqRef.current = Math.max(currentStatusSeq, nextStatusSeq);

    setTask((prev) =>
      mergeTaskSnapshot(prev, data.task, currentStatusSeq, nextStatusSeq)
    );
    if (options?.replaceEvents ?? true) {
      setEvents(projectedEvents);
    }
    setArtifacts(data.artifacts);
    setAuthError(null);
  }, []);

  const loadTask = useCallback(async () => {
    if (!taskId) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await fetchTaskDetail(taskId);
      applyTaskDetail(data);
    } catch (err) {
      setError(err);
      setAuthError(isFrontDoorApiError(err) ? err : null);
    } finally {
      setLoading(false);
    }
  }, [taskId, applyTaskDetail]);

  const refreshArtifacts = useCallback(async () => {
    if (!taskId) {
      return;
    }
    if (artifactRefreshInFlightRef.current) {
      artifactRefreshQueuedRef.current = true;
      return;
    }

    artifactRefreshInFlightRef.current = true;
    try {
      const data = await fetchTaskDetail(taskId);
      applyTaskDetail(data, { replaceEvents: false });
      setError(null);
    } catch {
      // 静默刷新失败时保持现状，避免打断进行中的页面交互。
    } finally {
      artifactRefreshInFlightRef.current = false;
      if (artifactRefreshQueuedRef.current) {
        artifactRefreshQueuedRef.current = false;
        void refreshArtifacts();
      }
    }
  }, [taskId, applyTaskDetail]);

  // 加载初始数据
  useEffect(() => {
    void loadTask();
  }, [loadTask]);

  // SSE 事件回调
  const handleSSEEvent = useCallback((rawEvent: RawTaskSseFrame): boolean => {
    const decoded = decodeTaskSseFrame(rawEvent);
    if (!decoded.ok) {
      return false;
    }
    const eventData = decoded.event;
    if (eventData.taskId !== taskId) {
      return false;
    }
    if (eventData.kind === "diagnostic") {
      setDiagnostics((prev) => {
        if (prev.some((event) => event.eventId === eventData.eventId)) {
          return prev;
        }
        return [...prev, eventData];
      });
      return eventData.final;
    }

    const taskEvent = taskEventFromProjection(eventData);
    setEvents((prev) => {
      const exists = prev.some((event) => event.event_id === eventData.eventId);
      if (exists) return prev;
      return [...prev, taskEvent];
    });

    // 只用当前任务自己的、且 task_seq 单调递增的 transition 更新头部状态，
    // 避免子任务冒泡事件或历史重放把 badge 回刷到旧状态。
    if (
      eventData.kind === "state-transition" &&
      eventData.taskSeq > latestStatusSeqRef.current
    ) {
      latestStatusSeqRef.current = eventData.taskSeq;
      setTask((prev) =>
        prev
          ? {
            ...prev,
            status: eventData.toStatus,
            updated_at: eventData.timestamp,
          }
          : prev
      );
    }

    if (eventData.kind === "artifact-refresh") {
      void refreshArtifacts();
    }
    return eventData.final;
  }, [taskId, refreshArtifacts]);

  // SSE 连接（仅非终态任务）
  const isTerminal = task ? TERMINAL_STATUSES.has(task.status) : true;
  const { status: sseStatus } = useSSE({
    taskId: taskId || "",
    enabled: !isTerminal && !loading,
    onEvent: handleSSEEvent,
  });

  // 可视化数据（hooks 必须在 early return 之前）
  const classified = useMemo(
    () => (task && viewMode === "visual" ? classifyEvents(events, task.status) : null),
    [events, task, viewMode],
  );
  const rounds = useMemo(
    () => (viewMode === "visual" ? splitIntoRounds(events, artifacts) : []),
    [events, artifacts, viewMode],
  );

  if (loading) {
    return <div className="loading">加载任务详情…</div>;
  }

  if (authError) {
    return <FrontDoorGate error={authError} title="任务详情" onRetry={loadTask} />;
  }

  if (error || !task) {
    const state = mapF149ErrorOwnership(
      error instanceof Error ? error : new Error("任务详情加载失败"),
    );
    const copy =
      state.state === "forbidden"
        ? {
            title: "无权查看这个任务",
            detail: "你没有查看此任务来源的权限，请联系管理员。",
          }
        : state.state === "not-found"
          ? {
              title: "找不到这个任务",
              detail: "这个任务可能已被移除，或链接已经失效。",
            }
          : {
              title: "暂时无法加载任务详情",
              detail: "请稍后重试；你当前的页面内容不会受到影响。",
            };
    return (
      <div className="tv-page">
        <Link to="/" className="tv-detail-back">&larr;</Link>
        <h1>{copy.title}</h1>
        <p>{copy.detail}</p>
        {state.state !== "not-found" && (
          <button type="button" onClick={() => void loadTask()}>
            重试
          </button>
        )}
      </div>
    );
  }

  // 计算耗时
  const duration = (() => {
    const start = new Date(task.created_at).getTime();
    const end = new Date(task.updated_at).getTime();
    const diff = end - start;
    if (diff < 1000) return "";
    if (diff < 60_000) return `${(diff / 1000).toFixed(0)}s`;
    return `${(diff / 60_000).toFixed(1)}min`;
  })();
  const taskDisplayTitle = formatSessionDisplayTitle({
    alias: task.alias,
    title: task.title,
    fallbackTitle: task.title,
  });

  return (
    <div className="tv-page">
      {/* 单行头部：← 标题 | 元信息 … 状态badge + 视图切换 */}
      <div className="tv-detail-header">
        <div className="tv-detail-header-row">
          <Link to="/" className="tv-detail-back" aria-label="返回任务列表">&larr;</Link>
          <h1 className="tv-detail-title">{taskDisplayTitle}</h1>
          <div className="tv-detail-meta">
            <span className="tv-detail-meta-item">{task.requester.channel}</span>
            <span className="tv-detail-meta-sep" />
            <span className="tv-detail-meta-item">
              {new Date(task.created_at).toLocaleString("zh-CN")}
            </span>
            {duration && (
              <>
                <span className="tv-detail-meta-sep" />
                <span className="tv-detail-meta-item">耗时 {duration}</span>
              </>
            )}
            <span className="tv-detail-meta-sep" />
            <span className="tv-detail-meta-item tv-detail-meta-id" title={task.task_id}>
              {task.task_id}
            </span>
          </div>
          <div className="tv-detail-header-right">
            <span className={`tv-detail-badge tv-detail-badge--${task.status.toLowerCase()}`}>
              {STATUS_LABEL[task.status] || task.status}
            </span>
            {!isTerminal && (
              <span
                className={`sse-indicator ${sseStatus === "connected" ? "connected" : "disconnected"}`}
                role="status"
                aria-label={
                  sseStatus === "disconnected"
                    ? "连接已断开，正在重试"
                    : sseStatus === "connecting"
                      ? "正在连接"
                      : sseStatus === "connected"
                        ? "已连接"
                        : "连接已关闭"
                }
                title={
                  sseStatus === "disconnected"
                    ? "连接已断开，正在重试"
                    : `SSE: ${sseStatus}`
                }
              />
            )}
            <SegmentedToggle value={viewMode} onChange={setViewMode} />
          </div>
        </div>
      </div>

      {/* 可视化模式 */}
      {viewMode === "visual" && classified && (
        <>
          {rounds.map((round) => (
            <RoundFlowCard
              key={round.id}
              round={{ ...round, taskId: task.task_id }}
              onNodeClick={setSelectedNode}
            />
          ))}
          <NodeDetailModal
            node={selectedNode}
            onClose={() => setSelectedNode(null)}
          />
        </>
      )}

      {/* Raw Data 模式 */}
      {viewMode === "raw" && (
        <>
          {/* 事件时间线 */}
          <h2>事件 ({events.length})</h2>
          <div className="timeline">
            {events.map((event) => (
              <div key={event.event_id} className="timeline-item">
                <span className="event-type">{event.type}</span>
                <span className="event-time">{formatTime(event.ts)}</span>
                {Object.keys(event.payload).length > 0 && (
                  <div className="event-payload">{payloadSummary(event.payload)}</div>
                )}
              </div>
            ))}
          </div>

          {diagnostics.length > 0 && (
            <details>
              <summary>高级诊断</summary>
              {diagnostics.map((diagnostic) => (
                <div key={diagnostic.eventId} className="timeline-item">
                  <span className="event-type">{diagnostic.sourceType}</span>
                  <span className="event-time">
                    {formatTime(diagnostic.timestamp)}
                  </span>
                  <div className="event-payload">
                    {JSON.stringify(diagnostic.diagnostic)}
                  </div>
                </div>
              ))}
            </details>
          )}

          {/* Artifacts */}
          {artifacts.length > 0 && (
            <>
              <h2 style={{ marginTop: "var(--space-lg)" }}>产出物 ({artifacts.length})</h2>
              {artifacts.map((artifact) => (
                <div key={artifact.artifact_id} className="card">
                  <div style={{ fontWeight: 600 }}>{artifact.name}</div>
                  <div style={{ fontSize: "12px", color: "var(--color-text-secondary)" }}>
                    {artifact.size} bytes
                  </div>
                  {artifact.parts.map((part, i) => (
                    <div key={i} style={{ marginTop: "var(--space-sm)" }}>
                      {part.content && (
                        <pre style={{
                          background: "var(--color-bg)",
                          padding: "var(--space-sm)",
                          borderRadius: "4px",
                          fontSize: "12px",
                          whiteSpace: "pre-wrap",
                          wordBreak: "break-all",
                        }}>
                          {part.content}
                        </pre>
                      )}
                    </div>
                  ))}
                </div>
              ))}
            </>
          )}
        </>
      )}
    </div>
  );
}
