/**
 * useTaskLiveState — 统一任务实时状态轮询 hook
 *
 * 合并 ChatWorkbench 中 3 段重复的 fetch 逻辑：
 * 1. taskId 变化时初始加载
 * 2. shouldPoll 时 3s 轮询 + 刷新 snapshot resources
 * 3. approvalSignal 变化时立即刷新
 */

import { useEffect, useRef, useState } from "react";
import {
  fetchApprovals,
  fetchTaskDetail,
  fetchTaskExecutionSession,
} from "../api/client";
import { readExecutionSessionDocument } from "../domains/chat/session";
import type {
  ApprovalListItem,
  ExecutionSessionDocument,
  TaskDetailResponse,
} from "../types";

/** snapshot resource 引用（用于 refreshResources 调用） */
interface SnapshotResourceRef {
  resource_type: string;
  resource_id: string;
  schema_version: string;
}

const TERMINAL_STATUSES = new Set(["SUCCEEDED", "FAILED", "CANCELLED", "REJECTED"]);
const ACTIVE_STATUSES = new Set(["QUEUED", "RUNNING", "WAITING_APPROVAL", "WAITING_INPUT"]);

interface UseTaskLiveStateParams {
  taskId: string | undefined;
  /** 外部强制轮询信号（如 streaming 中） */
  shouldPoll: boolean;
  approvalSignal: number;
  /** snapshot.resources（通过 ref 传递避免 setInterval 重建） */
  snapshotResources: {
    sessions: SnapshotResourceRef;
    delegation: SnapshotResourceRef;
    context_continuity: SnapshotResourceRef;
  } | null;
  /** useWorkbench().refreshResources */
  refreshResources: (refs: SnapshotResourceRef[]) => Promise<void>;
}

interface TaskLiveState {
  taskDetail: TaskDetailResponse | null;
  executionSession: ExecutionSessionDocument | null;
  pendingApprovals: ApprovalListItem[];
  /** 手动触发一次立即刷新 */
  refreshNow: () => void;
}

export function useTaskLiveState({
  taskId,
  shouldPoll,
  approvalSignal,
  snapshotResources,
  refreshResources,
}: UseTaskLiveStateParams): TaskLiveState {
  const [taskDetail, setTaskDetail] = useState<TaskDetailResponse | null>(null);
  const [executionSession, setExecutionSession] = useState<ExecutionSessionDocument | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState<ApprovalListItem[]>([]);

  // ref 持有最新引用，避免 setInterval 因依赖变化被反复重建
  const snapshotResourcesRef = useRef(snapshotResources);
  snapshotResourcesRef.current = snapshotResources;
  const refreshResourcesRef = useRef(refreshResources);
  refreshResourcesRef.current = refreshResources;
  const manualRefreshRef = useRef(0);

  // 核心 fetch 函数（所有 3 段逻辑统一为一个）
  async function fetchAll(currentTaskId: string): Promise<boolean> {
    const [detailResult, sessionResult, approvalsResult] = await Promise.allSettled([
      fetchTaskDetail(currentTaskId),
      fetchTaskExecutionSession(currentTaskId),
      fetchApprovals(),
    ]);
    setTaskDetail(detailResult.status === "fulfilled" ? detailResult.value : null);
    setExecutionSession(
      sessionResult.status === "fulfilled"
        ? readExecutionSessionDocument(sessionResult.value)
        : null,
    );
    setPendingApprovals(
      approvalsResult.status === "fulfilled"
        ? approvalsResult.value.approvals.filter((item) => item.task_id === currentTaskId)
        : [],
    );
    return true;
  }

  // 1. taskId 变化时初始加载
  useEffect(() => {
    if (!taskId) {
      setTaskDetail(null);
      setExecutionSession(null);
      setPendingApprovals([]);
      return;
    }
    let cancelled = false;
    (async () => {
      await fetchAll(taskId);
      if (cancelled) return;
    })();
    return () => { cancelled = true; };
  }, [taskId]);

  // 内部判断是否需要轮询：外部 shouldPoll 或任务状态为非终态
  const taskStatus = String(taskDetail?.task?.status ?? "").trim().toUpperCase();
  const isTaskActive = taskStatus.length > 0 && !TERMINAL_STATUSES.has(taskStatus) && ACTIVE_STATUSES.has(taskStatus);
  const effectiveShouldPoll = shouldPoll || isTaskActive;

  // 2. shouldPoll / 任务活跃时 3s 轮询 + 刷新 snapshot resources
  useEffect(() => {
    if (!taskId || !effectiveShouldPoll) return;
    let cancelled = false;
    const currentTaskId = taskId;

    async function poll() {
      await fetchAll(currentTaskId);
      if (cancelled) return;
      const res = snapshotResourcesRef.current;
      if (res) {
        await refreshResourcesRef.current([
          { resource_type: res.sessions.resource_type, resource_id: res.sessions.resource_id, schema_version: res.sessions.schema_version },
          { resource_type: res.delegation.resource_type, resource_id: res.delegation.resource_id, schema_version: res.delegation.schema_version },
          { resource_type: res.context_continuity.resource_type, resource_id: res.context_continuity.resource_id, schema_version: res.context_continuity.schema_version },
        ]);
      }
    }

    void poll();
    const timer = window.setInterval(() => { void poll(); }, 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [taskId, effectiveShouldPoll]);

  // 3. approvalSignal / 手动刷新时立即 fetch
  useEffect(() => {
    if (!taskId) return;
    // approvalSignal=0 是初始值，跳过
    if (approvalSignal === 0 && manualRefreshRef.current === 0) return;
    let cancelled = false;
    (async () => {
      await fetchAll(taskId);
      if (cancelled) return;
    })();
    return () => { cancelled = true; };
  }, [approvalSignal, taskId, manualRefreshRef.current]);

  const refreshNow = () => {
    manualRefreshRef.current += 1;
  };

  return { taskDetail, executionSession, pendingApprovals, refreshNow };
}
