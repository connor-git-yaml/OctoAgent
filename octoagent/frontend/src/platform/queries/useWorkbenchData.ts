import { useEffect, useState } from "react";
import { ApiError, isFrontDoorApiError } from "../../api/client";
import type {
  ActionResultEnvelope,
  ControlPlaneResourceRef,
  ControlPlaneSnapshot,
} from "../../types";
import type { SnapshotResourceLoadOptions } from "../contracts";
import {
  executeWorkbenchActionWithRefresh,
} from "../actions";
import {
  fetchWorkbenchSnapshot,
  refreshWorkbenchSnapshotResources,
} from "./controlPlaneResources";

export interface WorkbenchDataState {
  snapshot: ControlPlaneSnapshot | null;
  loading: boolean;
  error: string | null;
  authError: ApiError | null;
  busyActionId: string | null;
  lastAction: ActionResultEnvelope | null;
  refreshSnapshot: () => Promise<void>;
  refreshResources: (
    refs?: ControlPlaneResourceRef[],
    options?: SnapshotResourceLoadOptions
  ) => Promise<void>;
  submitAction: (
    actionId: string,
    params: Record<string, unknown>
  ) => Promise<ActionResultEnvelope | null>;
  clearError: () => void;
}

interface UseWorkbenchDataOptions {
  initialSnapshot?: ControlPlaneSnapshot | null;
  autoRefresh?: boolean;
}

function readStringParam(
  params: Record<string, unknown>,
  key: string
): string | undefined {
  const value = params[key];
  if (typeof value !== "string") {
    return undefined;
  }
  const normalized = value.trim();
  return normalized ? normalized : undefined;
}

function readBooleanParam(
  params: Record<string, unknown>,
  key: string
): boolean | undefined {
  const value = params[key];
  return typeof value === "boolean" ? value : undefined;
}

function readNumberParam(
  params: Record<string, unknown>,
  key: string
): number | undefined {
  const value = params[key];
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function buildActionRefreshOptions(
  actionId: string,
  params: Record<string, unknown>,
  snapshot: ControlPlaneSnapshot | null
): SnapshotResourceLoadOptions | undefined {
  if (actionId !== "memory.query") {
    return undefined;
  }

  const currentMemory = snapshot?.resources.memory;
  return {
    memoryQuery: {
      projectId:
        readStringParam(params, "project_id") ?? currentMemory?.active_project_id,
      workspaceId:
        readStringParam(params, "workspace_id") ?? currentMemory?.active_workspace_id,
      scopeId:
        readStringParam(params, "scope_id") ??
        (currentMemory?.filters.scope_id || undefined),
      partition:
        readStringParam(params, "partition") ??
        (currentMemory?.filters.partition || undefined),
      layer:
        readStringParam(params, "layer") ??
        (currentMemory?.filters.layer || undefined),
      query:
        readStringParam(params, "query") ??
        (currentMemory?.filters.query || undefined),
      includeHistory:
        readBooleanParam(params, "include_history") ??
        currentMemory?.filters.include_history,
      includeVaultRefs:
        readBooleanParam(params, "include_vault_refs") ??
        currentMemory?.filters.include_vault_refs,
      limit: readNumberParam(params, "limit") ?? currentMemory?.filters.limit,
    },
  };
}

export function useWorkbenchData(
  options: UseWorkbenchDataOptions = {}
): WorkbenchDataState {
  const [snapshot, setSnapshot] = useState<ControlPlaneSnapshot | null>(
    options.initialSnapshot ?? null
  );
  const [loading, setLoading] = useState(options.initialSnapshot == null);
  const [error, setError] = useState<string | null>(null);
  const [authError, setAuthError] = useState<ApiError | null>(null);
  const [busyActionId, setBusyActionId] = useState<string | null>(null);
  const [lastAction, setLastAction] = useState<ActionResultEnvelope | null>(null);

  function clearError() {
    setError(null);
    setAuthError(null);
  }

  async function refreshSnapshot() {
    clearError();
    try {
      const nextSnapshot = await fetchWorkbenchSnapshot();
      setSnapshot(nextSnapshot);
    } catch (err) {
      setError(err instanceof Error ? err.message : "工作台加载失败");
      setAuthError(isFrontDoorApiError(err) ? err : null);
    } finally {
      setLoading(false);
    }
  }

  async function refreshResources(
    refs: ControlPlaneResourceRef[] = [],
    options?: SnapshotResourceLoadOptions
  ) {
    if (!snapshot || refs.length === 0) {
      await refreshSnapshot();
      return;
    }
    try {
      const result = await refreshWorkbenchSnapshotResources(snapshot, refs, options);
      setSnapshot(result.snapshot);
    } catch {
      await refreshSnapshot();
    }
  }

  async function submitAction(
    actionId: string,
    params: Record<string, unknown>
  ): Promise<ActionResultEnvelope | null> {
    setBusyActionId(actionId);
    clearError();
    try {
      const actionRefreshOptions = buildActionRefreshOptions(actionId, params, snapshot);
      const result = await executeWorkbenchActionWithRefresh(
        snapshot?.contract_version,
        actionId,
        params,
        {
          refreshSnapshot,
          refreshResources: (refs) => refreshResources(refs, actionRefreshOptions),
        }
      );
      setLastAction(result);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : `动作执行失败: ${actionId}`);
      setAuthError(isFrontDoorApiError(err) ? err : null);
      return null;
    } finally {
      setBusyActionId(null);
    }
  }

  useEffect(() => {
    if (options.autoRefresh === false) {
      setLoading(false);
      return;
    }
    void refreshSnapshot();
  }, []);

  return {
    snapshot,
    loading,
    error,
    authError,
    busyActionId,
    lastAction,
    refreshSnapshot,
    refreshResources,
    submitAction,
    clearError,
  };
}
