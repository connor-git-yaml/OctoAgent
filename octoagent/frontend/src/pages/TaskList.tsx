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
import OperatorInboxPanel from "../components/OperatorInboxPanel";
import RecoveryPanel from "../components/RecoveryPanel";
import type { TaskSummary } from "../types";
import { formatDateTime } from "../utils/formatTime";

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
          setError(err instanceof Error ? err.message : "加载任务失败");
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
    return (
      <div>
        <h1>当前工作</h1>
        <OperatorInboxPanel />
        <RecoveryPanel />
        <div className="loading">正在加载任务列表…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h1>当前工作</h1>
        <OperatorInboxPanel />
        <RecoveryPanel />
        <div className="error">加载失败：{error}</div>
      </div>
    );
  }

  if (tasks.length === 0) {
    return (
      <div>
        <h1>当前工作</h1>
        <OperatorInboxPanel />
        <RecoveryPanel />
        <div className="card" style={{ textAlign: "center", color: "var(--color-text-secondary)" }}>
          暂无进行中的工作
        </div>
      </div>
    );
  }

  return (
    <div>
      <h1>当前工作</h1>
      <OperatorInboxPanel />
      <RecoveryPanel />
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
              {formatDateTime(task.created_at)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
