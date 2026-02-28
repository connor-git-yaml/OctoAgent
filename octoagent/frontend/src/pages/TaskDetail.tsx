/**
 * TaskDetail 页面 -- 展示任务详情 + 事件时间线
 *
 * 功能：
 * 1. 调用 GET /api/tasks/{id} 获取任务信息 + 事件 + artifacts
 * 2. 展示任务基本信息
 * 3. 事件时间线（类型、时间、payload 摘要）
 * 4. 进行中任务通过 useSSE 实时追加新事件
 */

import { useEffect, useState, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import { fetchTaskDetail } from "../api/client";
import { useSSE } from "../hooks/useSSE";
import type { TaskDetail as TaskDetailType, TaskEvent, Artifact, SSEEventData, TaskStatus } from "../types";

/** 终态集合 */
const TERMINAL_STATES: Set<TaskStatus> = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);

/** 格式化时间 */
function formatTime(isoString: string): string {
  const d = new Date(isoString);
  return d.toLocaleString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

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

  // 加载初始数据
  useEffect(() => {
    if (!taskId) return;

    let cancelled = false;

    async function load() {
      try {
        const data = await fetchTaskDetail(taskId!);
        if (!cancelled) {
          setTask(data.task);
          setEvents(data.events);
          setArtifacts(data.artifacts);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load task");
          setLoading(false);
        }
      }
    }

    load();
    return () => { cancelled = true; };
  }, [taskId]);

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
  const isTerminal = task ? TERMINAL_STATES.has(task.status) : true;
  const { status: sseStatus } = useSSE({
    taskId: taskId || "",
    enabled: !isTerminal && !loading,
    onEvent: handleSSEEvent,
  });

  if (loading) {
    return <div className="loading">Loading task...</div>;
  }

  if (error || !task) {
    return (
      <div>
        <Link to="/" className="back-link">&larr; Back to tasks</Link>
        <div className="error">Error: {error || "Task not found"}</div>
      </div>
    );
  }

  return (
    <div>
      <Link to="/" className="back-link">&larr; Back to tasks</Link>

      {/* 任务头部 */}
      <div className="task-header">
        <h1>{task.title}</h1>
        <span className={`status-badge ${task.status}`}>{task.status}</span>
        {!isTerminal && (
          <span
            className={`sse-indicator ${sseStatus === "connected" ? "connected" : "disconnected"}`}
            title={`SSE: ${sseStatus}`}
          />
        )}
      </div>

      {/* 任务信息 */}
      <div className="card">
        <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: "var(--space-xs)" }}>
          <span style={{ color: "var(--color-text-secondary)" }}>Task ID:</span>
          <span style={{ fontFamily: "monospace", fontSize: "12px" }}>{task.task_id}</span>
          <span style={{ color: "var(--color-text-secondary)" }}>Channel:</span>
          <span>{task.requester.channel}</span>
          <span style={{ color: "var(--color-text-secondary)" }}>Created:</span>
          <span>{new Date(task.created_at).toLocaleString("zh-CN")}</span>
          <span style={{ color: "var(--color-text-secondary)" }}>Updated:</span>
          <span>{new Date(task.updated_at).toLocaleString("zh-CN")}</span>
        </div>
      </div>

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
    </div>
  );
}
