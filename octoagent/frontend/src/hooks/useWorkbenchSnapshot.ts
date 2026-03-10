import { useEffect, useState } from "react";
import {
  ApiError,
  executeControlAction,
  fetchControlResource,
  fetchControlSnapshot,
  isFrontDoorApiError,
} from "../api/client";
import type {
  ActionRequestEnvelope,
  ActionResultEnvelope,
  ControlPlaneResourceRef,
  ControlPlaneSnapshot,
} from "../types";
import {
  makeRequestId,
  RESOURCE_ROUTE_BY_TYPE,
  SNAPSHOT_RESOURCE_KEY_BY_ROUTE,
  type SnapshotResourceRoute,
} from "../workbench/utils";

type ResourcePayload =
  | ControlPlaneSnapshot["resources"][keyof ControlPlaneSnapshot["resources"]];

const FULL_SNAPSHOT_ACTIONS = new Set(["project.select"]);

export interface WorkbenchSnapshotState {
  snapshot: ControlPlaneSnapshot | null;
  loading: boolean;
  error: string | null;
  authError: ApiError | null;
  busyActionId: string | null;
  lastAction: ActionResultEnvelope | null;
  refreshSnapshot: () => Promise<void>;
  refreshResources: (refs?: ControlPlaneResourceRef[]) => Promise<void>;
  submitAction: (
    actionId: string,
    params: Record<string, unknown>
  ) => Promise<ActionResultEnvelope | null>;
  clearError: () => void;
}

async function fetchSnapshotResource(
  route: SnapshotResourceRoute
): Promise<ResourcePayload> {
  switch (route) {
    case "wizard":
      return fetchControlResource("wizard");
    case "config":
      return fetchControlResource("config");
    case "project-selector":
      return fetchControlResource("project-selector");
    case "sessions":
      return fetchControlResource("sessions");
    case "context-frames":
      return fetchControlResource("context-frames");
    case "policy-profiles":
      return fetchControlResource("policy-profiles");
    case "capability-pack":
      return fetchControlResource("capability-pack");
    case "skill-governance":
      return fetchControlResource("skill-governance");
    case "setup-governance":
      return fetchControlResource("setup-governance");
    case "delegation":
      return fetchControlResource("delegation");
    case "pipelines":
      return fetchControlResource("pipelines");
    case "automation":
      return fetchControlResource("automation");
    case "diagnostics":
      return fetchControlResource("diagnostics");
    case "memory":
      return fetchControlResource("memory");
    case "import-workbench":
      return fetchControlResource("import-workbench");
  }
}

export function useWorkbenchSnapshot(): WorkbenchSnapshotState {
  const [snapshot, setSnapshot] = useState<ControlPlaneSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
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
      const nextSnapshot = await fetchControlSnapshot();
      setSnapshot(nextSnapshot);
    } catch (err) {
      setError(err instanceof Error ? err.message : "工作台加载失败");
      setAuthError(isFrontDoorApiError(err) ? err : null);
    } finally {
      setLoading(false);
    }
  }

  async function refreshResources(refs: ControlPlaneResourceRef[] = []) {
    if (!snapshot || refs.length === 0) {
      await refreshSnapshot();
      return;
    }

    const routes = Array.from(
      new Set(
        refs
          .map((item) => RESOURCE_ROUTE_BY_TYPE[item.resource_type])
          .filter((item): item is SnapshotResourceRoute => Boolean(item))
      )
    );

    if (routes.length === 0) {
      await refreshSnapshot();
      return;
    }

    try {
      const payloads = await Promise.all(routes.map((route) => fetchSnapshotResource(route)));
      setSnapshot((current) => {
        if (!current) {
          return current;
        }
        const resources = {
          ...current.resources,
        } as Record<
          keyof ControlPlaneSnapshot["resources"],
          ResourcePayload
        >;
        routes.forEach((route, index) => {
          const key = SNAPSHOT_RESOURCE_KEY_BY_ROUTE[route];
          resources[key] = payloads[index];
        });
        return {
          ...current,
          resources: resources as ControlPlaneSnapshot["resources"],
          generated_at: new Date().toISOString(),
        };
      });
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
      const payload: ActionRequestEnvelope = {
        contract_version: snapshot?.contract_version,
        request_id: makeRequestId(),
        action_id: actionId,
        surface: "web",
        actor: {
          actor_id: "user:web",
          actor_label: "Owner",
        },
        params,
      };
      const result = await executeControlAction(payload);
      setLastAction(result);
      if (FULL_SNAPSHOT_ACTIONS.has(actionId)) {
        await refreshSnapshot();
      } else {
        await refreshResources(result.resource_refs);
      }
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
