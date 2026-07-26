import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { fetchTasks } from "../api/client";
import { mapF149ErrorOwnership } from "../api/f149/errorOwnership";
import type { TaskSummary } from "../types";
import { formatDateTime } from "../utils/formatTime";
import "./TaskList.css";
import {
  filterTasks,
  presentTaskStatus,
  summarizeTasks,
  type TaskListFilter,
} from "./taskListModel";
import TaskPendingSection from "./TaskPendingSection";

export default function TaskList() {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [filter, setFilter] = useState<TaskListFilter>("all");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchTasks();
      setTasks(data.tasks);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught : new Error("任务列表暂时不可用"),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const visibleTasks = useMemo(
    () => filterTasks(tasks, filter),
    [filter, tasks],
  );
  const ownership = error ? mapF149ErrorOwnership(error) : null;
  const isPermissionDenied =
    ownership?.owner === "surface" && ownership.state === "forbidden";
  const isGlobalAuth = ownership?.owner === "global-auth";

  return (
    <main className="f149-task-page">
      <TaskPendingSection />

      <header className="f149-task-hero">
        <div>
          <h1>任务</h1>
          <p>{summarizeTasks(tasks)}</p>
        </div>
        <nav className="f149-task-filters" aria-label="筛选任务">
          {(
            [
              ["all", "全部"],
              ["active", "进行中"],
              ["succeeded", "已完成"],
              ["failed", "未成功"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
            >
              {label}
            </button>
          ))}
        </nav>
      </header>

      <section className="f149-task-content" aria-label="任务列表">
        {loading ? (
          <div
            className="f149-task-state"
            role="status"
            aria-label="正在加载任务"
          >
            正在加载任务…
          </div>
        ) : isGlobalAuth ? null : error ? (
          <div
            className="f149-task-state is-error"
            role="alert"
            aria-label={isPermissionDenied ? "无法查看任务" : "任务暂时不可用"}
          >
            <strong>
              {isPermissionDenied ? "无法查看任务" : "任务暂时不可用"}
            </strong>
            <p>
              {isPermissionDenied
                ? "你没有查看这组任务的权限，请联系管理员。"
                : "这次没有加载成功，可以重新试一次。"}
            </p>
            {!isPermissionDenied ? (
              <button type="button" onClick={() => void load()}>
                重新加载
              </button>
            ) : null}
          </div>
        ) : tasks.length === 0 ? (
          <div
            className="f149-task-state"
            role="status"
            aria-label="还没有任务"
          >
            <strong>还没有任务</strong>
            <p>从聊天开始一项工作后，进度会出现在这里。</p>
          </div>
        ) : visibleTasks.length === 0 ? (
          <div className="f149-task-state" role="status">
            这个筛选下暂时没有任务。
          </div>
        ) : (
          visibleTasks.map((task) => {
            const status = presentTaskStatus(task.status);
            return (
              <article key={task.task_id} className="f149-task-card">
                <span
                  className={`f149-task-card-indicator is-${status.tone}`}
                  aria-hidden="true"
                />
                <div className="f149-task-card-copy">
                  <strong>{task.title}</strong>
                  <span>更新于 {formatDateTime(task.updated_at)}</span>
                </div>
                <span className={`f149-task-status is-${status.tone}`}>
                  {status.label}
                </span>
                <Link className="f149-task-open" to={`/tasks/${task.task_id}`}>
                  打开
                </Link>
                <details className="f149-task-advanced">
                  <summary>高级</summary>
                  <p>
                    status={task.status} · {task.task_id}
                  </p>
                </details>
              </article>
            );
          })
        )}
      </section>
    </main>
  );
}
