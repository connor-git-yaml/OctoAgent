/**
 * useTaskLiveState — 统一任务实时状态轮询 hook
 *
 * 合并 ChatWorkbench 中 3 段重复的 fetch 逻辑：
 * 1. taskId 变化时初始加载
 * 2. 任务非终态或外部 streaming 时 3s 轮询
 * 3. approvalSignal 变化时立即刷新
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchApprovals,
  fetchTaskDetail,
  fetchTaskExecutionSession,
} from "../api/client";
import { readExecutionSessionDocument } from "../domains/chat/session";
import { TERMINAL_TASK_STATUSES } from "../domains/chat/constants";
import type {
  ApprovalListItem,
  ExecutionSessionDocument,
  TaskDetailResponse,
} from "../types";

interface SnapshotResourceRef {
  resource_type: string;
  resource_id: string;
  schema_version: string;
}

interface UseTaskLiveStateParams {
  taskId: string | undefined;
  /** 外部强制轮询信号（如 streaming 中） */
  shouldPoll: boolean;
  approvalSignal: number;
  /** 需要刷新的 snapshot resource refs */
  snapshotResourceRefs: SnapshotResourceRef[];
  /** useWorkbench().refreshResources */
  refreshResources: (refs: SnapshotResourceRef[]) => Promise<void>;
}

interface FetchedLiveState {
  taskDetail: TaskDetailResponse | null;
  executionSession: ExecutionSessionDocument | null;
  pendingApprovals: ApprovalListItem[];
}

interface TaskLiveState extends FetchedLiveState {
  /** 立即刷新，返回最新数据（同时更新 hook state） */
  refreshNow: () => Promise<FetchedLiveState>;
}

export function useTaskLiveState({
  taskId,
  shouldPoll,
  approvalSignal,
  snapshotResourceRefs,
  refreshResources,
}: UseTaskLiveStateParams): TaskLiveState {
  const [taskDetail, setTaskDetail] = useState<TaskDetailResponse | null>(null);
  const [executionSession, setExecutionSession] = useState<ExecutionSessionDocument | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState<ApprovalListItem[]>([]);
  // B1 fix: 用 useState 计数器替代 useRef，变化能触发 re-render
  const [manualRefreshCount, setManualRefreshCount] = useState(0);

  // ref 持有最新引用，避免 setInterval 因依赖变化被反复重建
  const snapshotResourceRefsRef = useRef(snapshotResourceRefs);
  snapshotResourceRefsRef.current = snapshotResourceRefs;
  const refreshResourcesRef = useRef(refreshResources);
  refreshResourcesRef.current = refreshResources;

  // B3 fix: fetchAll 接受 cancelled ref 参数，在 setState 前检查；返回最新数据
  const fetchAll = useCallback(async (
    currentTaskId: string,
    cancelledRef: { current: boolean },
  ): Promise<FetchedLiveState> => {
    const [detailResult, sessionResult, approvalsResult] = await Promise.allSettled([
      fetchTaskDetail(currentTaskId),
      fetchTaskExecutionSession(currentTaskId),
      fetchApprovals(),
    ]);
    const detail = detailResult.status === "fulfilled" ? detailResult.value : null;
    const session = sessionResult.status === "fulfilled"
      ? readExecutionSessionDocument(sessionResult.value)
      : null;
    const approvals = approvalsResult.status === "fulfilled"
      ? approvalsResult.value.approvals.filter((item) => item.task_id === currentTaskId)
      : [];
    if (!cancelledRef.current) {
      setTaskDetail(detail);
      setExecutionSession(session);
      setPendingApprovals(approvals);
    }
    return { taskDetail: detail, executionSession: session, pendingApprovals: approvals };
  }, []);

  // B2 fix: 非终态 + 未加载状态都要轮询
  const taskStatus = String(taskDetail?.task?.status ?? "").trim().toUpperCase();
  const isNonTerminal = taskStatus.length === 0 || !TERMINAL_TASK_STATUSES.has(taskStatus);
  const effectiveShouldPoll = Boolean(taskId) && (shouldPoll || isNonTerminal);

  // 1. taskId 变化时初始加载
  useEffect(() => {
    if (!taskId) {
      setTaskDetail(null);
      setExecutionSession(null);
      setPendingApprovals([]);
      return;
    }
    const cancelledRef = { current: false };
    void fetchAll(taskId, cancelledRef);
    return () => { cancelledRef.current = true; };
  }, [taskId, fetchAll]);

  // 2. 轮询：任务非终态或 streaming 时 3s 刷新
  // B4 fix: 用 initialFetchDone ref 避免与 effect 1 的首次 fetch 重叠
  const initialFetchDone = useRef(false);
  useEffect(() => {
    initialFetchDone.current = false;
  }, [taskId]);

  useEffect(() => {
    if (!taskId || !effectiveShouldPoll) return;
    const cancelledRef = { current: false };
    const currentTaskId = taskId;

    async function poll() {
      await fetchAll(currentTaskId, cancelledRef);
      if (cancelledRef.current) return;
      const refs = snapshotResourceRefsRef.current;
      if (refs.length > 0) {
        await refreshResourcesRef.current(refs);
      }
    }

    // 跳过首次立即 poll（effect 1 已经做了初始加载）
    if (!initialFetchDone.current) {
      initialFetchDone.current = true;
    } else {
      void poll();
    }

    const timer = window.setInterval(() => { void poll(); }, 3000);
    return () => { cancelledRef.current = true; window.clearInterval(timer); };
  }, [taskId, effectiveShouldPoll, fetchAll]);

  // 3. approvalSignal / 手动刷新时立即 fetch
  useEffect(() => {
    if (!taskId) return;
    if (approvalSignal === 0 && manualRefreshCount === 0) return;
    const cancelledRef = { current: false };
    void fetchAll(taskId, cancelledRef);
    return () => { cancelledRef.current = true; };
  }, [approvalSignal, manualRefreshCount, taskId, fetchAll]);

  // B1 fix: refreshNow 直接 fetch 并返回数据，同时更新 state
  const refreshNow = useCallback(async (): Promise<FetchedLiveState> => {
    const empty: FetchedLiveState = { taskDetail: null, executionSession: null, pendingApprovals: [] };
    if (!taskId) return empty;
    const cancelledRef = { current: false };
    const result = await fetchAll(taskId, cancelledRef);
    setManualRefreshCount((c) => c + 1);
    return result;
  }, [taskId, fetchAll]);

  return { taskDetail, executionSession, pendingApprovals, refreshNow };
}
