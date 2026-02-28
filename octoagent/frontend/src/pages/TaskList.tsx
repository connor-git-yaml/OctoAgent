/**
 * TaskList 页面 -- 展示所有任务列表
 *
 * 功能：
 * 1. 调用 GET /api/tasks 获取任务列表
 * 2. 按创建时间倒序展示
 * 3. 每个任务显示标题、状态标记、创建时间
 * 4. 点击导航到详情页
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchTasks } from "../api/client";
import type { TaskSummary } from "../types";

/** 格式化时间为可读字符串 */
function formatTime(isoString: string): string {
  const d = new Date(isoString);
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function TaskList() {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await fetchTasks();
        if (!cancelled) {
          setTasks(data.tasks);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load tasks");
          setLoading(false);
        }
      }
    }

    load();

    // 每 5 秒刷新一次列表
    const interval = setInterval(load, 5000);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (loading) {
    return <div className="loading">Loading tasks...</div>;
  }

  if (error) {
    return <div className="error">Error: {error}</div>;
  }

  if (tasks.length === 0) {
    return (
      <div>
        <h1>Tasks</h1>
        <div className="card" style={{ textAlign: "center", color: "var(--color-text-secondary)" }}>
          No tasks yet
        </div>
      </div>
    );
  }

  return (
    <div>
      <h1>Tasks</h1>
      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        {tasks.map((task) => (
          <div
            key={task.task_id}
            className="task-item"
            onClick={() => navigate(`/tasks/${task.task_id}`)}
            style={{
              borderBottom: "1px solid var(--color-border)",
            }}
          >
            <span className="task-title">{task.title}</span>
            <span className={`status-badge ${task.status}`}>{task.status}</span>
            <span className="task-time" style={{ marginLeft: "var(--space-md)" }}>
              {formatTime(task.created_at)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
