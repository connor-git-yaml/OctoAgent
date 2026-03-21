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

import { useEffect, useState, useCallback, useMemo } from "react";
import { useParams, Link } from "react-router-dom";
import {
  ApiError,
  fetchTaskDetail,
  isFrontDoorApiError,
} from "../api/client";
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
import type { TaskDetail as TaskDetailType, TaskEvent, Artifact, SSEEventData, TaskStatus } from "../types";

/** 状态 badge 的用户友好文案 */
const STATUS_LABEL: Record<string, string> = {
  CREATED: "已创建",
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

export default function TaskDetail() {
  const { taskId } = useParams<{ taskId: string }>();
  const [task, setTask] = useState<TaskDetailType | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [authError, setAuthError] = useState<ApiError | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("visual");
  const [selectedNode, setSelectedNode] = useState<FlowNode | null>(null);

  const loadTask = useCallback(async () => {
    if (!taskId) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await fetchTaskDetail(taskId);
      setTask(data.task);
      setEvents(data.events);
      setArtifacts(data.artifacts);
      setAuthError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load task");
      setAuthError(isFrontDoorApiError(err) ? err : null);
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  // 加载初始数据
  useEffect(() => {
    void loadTask();
  }, [loadTask]);

  // SSE 事件回调
  const handleSSEEvent = useCallback((eventData: SSEEventData) => {
    // 追加新事件到列表（去重）
    setEvents((prev) => {
      const exists = prev.some((e) => e.event_id === eventData.event_id);
      if (exists) return prev;
      return [...prev, {
        event_id: eventData.event_id,
        task_seq: eventData.task_seq,
        ts: eventData.ts,
        type: eventData.type,
        actor: eventData.actor,
        payload: eventData.payload,
      }];
    });

    // 如果是状态变更，更新任务状态
    if (eventData.type === "STATE_TRANSITION" && eventData.payload.to_status) {
      setTask((prev) =>
        prev ? { ...prev, status: eventData.payload.to_status as TaskStatus } : prev
      );
    }
  }, []);

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
    return <div className="loading">Loading task...</div>;
  }

  if (authError) {
    return <FrontDoorGate error={authError} title="Task Detail" onRetry={loadTask} />;
  }

  if (error || !task) {
    return (
      <div className="tv-page">
        <Link to="/" className="tv-detail-back">&larr;</Link>
        <div className="error">Error: {error || "Task not found"}</div>
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

  return (
    <div className="tv-page">
      {/* 单行头部：← 标题 | 元信息 … 状态badge + 视图切换 */}
      <div className="tv-detail-header">
        <div className="tv-detail-header-row">
          <Link to="/" className="tv-detail-back" aria-label="返回任务列表">&larr;</Link>
          <h1 className="tv-detail-title">{task.title}</h1>
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
                title={`SSE: ${sseStatus}`}
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
              round={round}
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
          <h2>Events ({events.length})</h2>
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

          {/* Artifacts */}
          {artifacts.length > 0 && (
            <>
              <h2 style={{ marginTop: "var(--space-lg)" }}>Artifacts ({artifacts.length})</h2>
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
