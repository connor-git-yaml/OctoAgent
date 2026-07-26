import type { TaskStatus, TaskSummary } from "../types";

export type TaskListFilter = "all" | "active" | "succeeded" | "failed";

export interface TaskStatusPresentation {
  label: string;
  tone: "active" | "waiting" | "success" | "danger" | "muted";
  filter: Exclude<TaskListFilter, "all">;
}

export interface TaskListCounts {
  active: number;
  succeeded: number;
  failed: number;
}

const STATUS_PRESENTATIONS: Record<TaskStatus, TaskStatusPresentation> = {
  CREATED: { label: "等待开始", tone: "waiting", filter: "active" },
  QUEUED: { label: "等待开始", tone: "waiting", filter: "active" },
  RUNNING: { label: "进行中", tone: "active", filter: "active" },
  WAITING_INPUT: {
    label: "等待你补充",
    tone: "waiting",
    filter: "active",
  },
  WAITING_APPROVAL: {
    label: "等待你确认",
    tone: "waiting",
    filter: "active",
  },
  PAUSED: { label: "已暂停", tone: "muted", filter: "active" },
  SUCCEEDED: { label: "已完成", tone: "success", filter: "succeeded" },
  FAILED: { label: "未成功", tone: "danger", filter: "failed" },
  CANCELLED: { label: "已取消", tone: "muted", filter: "failed" },
  REJECTED: { label: "未成功", tone: "danger", filter: "failed" },
};

export function presentTaskStatus(status: TaskStatus): TaskStatusPresentation {
  return STATUS_PRESENTATIONS[status];
}

export function countTasks(tasks: TaskSummary[]): TaskListCounts {
  return tasks.reduce<TaskListCounts>(
    (counts, item) => {
      counts[presentTaskStatus(item.status).filter] += 1;
      return counts;
    },
    { active: 0, succeeded: 0, failed: 0 },
  );
}

export function summarizeTasks(tasks: TaskSummary[]): string {
  const counts = countTasks(tasks);
  return `${counts.active} 项进行中 · ${counts.succeeded} 项已完成 · ${counts.failed} 项未成功`;
}

export function filterTasks(
  tasks: TaskSummary[],
  filter: TaskListFilter,
): TaskSummary[] {
  const sorted = [...tasks].sort((left, right) =>
    right.updated_at.localeCompare(left.updated_at),
  );
  if (filter === "all") {
    return sorted;
  }
  return sorted.filter(
    (item) => presentTaskStatus(item.status).filter === filter,
  );
}
