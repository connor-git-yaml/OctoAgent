import {
  fetchControlSnapshot,
  fetchWorkbenchResource,
} from "../../api/client";
import type {
  ControlPlaneResourceRef,
  ControlPlaneSnapshot,
} from "../../types";
import {
  CONTROL_RESOURCE_QUERY_REGISTRY,
  SNAPSHOT_RESOURCE_KEY_BY_ROUTE,
  isControlResourceDocument,
  resolveResourceRoutes,
  type SnapshotResourceLoadOptions,
  type SnapshotResourcePayload,
  type WorkbenchResourceRoute,
} from "../contracts";

export interface RefreshSnapshotResult {
  snapshot: ControlPlaneSnapshot;
  mode: "resource-refs" | "full-snapshot";
  routes: WorkbenchResourceRoute[];
}

export async function fetchWorkbenchSnapshot(
  mode?: "lite" | "full"
): Promise<ControlPlaneSnapshot> {
  return fetchControlSnapshot(mode ? { mode } : undefined);
}

export async function fetchSnapshotResource(
  route: WorkbenchResourceRoute,
  options?: SnapshotResourceLoadOptions
): Promise<SnapshotResourcePayload> {
  return fetchWorkbenchResource(route, options);
}

export function buildSnapshotRefreshOptions(
  snapshot: ControlPlaneSnapshot,
  overrides: SnapshotResourceLoadOptions = {}
): SnapshotResourceLoadOptions {
  const memoryQuery =
    overrides.memoryQuery ??
    (snapshot.resources.memory != null
      ? {
          projectId: snapshot.resources.memory.active_project_id,
          scopeId: snapshot.resources.memory.filters.scope_id || undefined,
          partition: snapshot.resources.memory.filters.partition || undefined,
          layer: snapshot.resources.memory.filters.layer || undefined,
          query: snapshot.resources.memory.filters.query || undefined,
          includeHistory: snapshot.resources.memory.filters.include_history,
          includeVaultRefs: snapshot.resources.memory.filters.include_vault_refs,
          limit: snapshot.resources.memory.filters.limit,
        }
      : undefined);
  return {
    memoryQuery,
  };
}

export async function refreshWorkbenchSnapshotResources(
  snapshot: ControlPlaneSnapshot,
  refs: ControlPlaneResourceRef[] = [],
  options: SnapshotResourceLoadOptions = {}
): Promise<RefreshSnapshotResult> {
  const routes = resolveResourceRoutes(refs);
  if (routes.length === 0) {
    return {
      snapshot: await fetchWorkbenchSnapshot(),
      mode: "full-snapshot",
      routes,
    };
  }

  const loadOptions = buildSnapshotRefreshOptions(snapshot, options);

  try {
    const payloads = await Promise.all(
      routes.map((route) =>
        fetchSnapshotResource(route, {
          memoryQuery: route === "memory" ? loadOptions.memoryQuery : undefined,
        })
      )
    );

    if (!payloads.every((item) => isControlResourceDocument(item))) {
      throw new Error("control resource refresh returned malformed payload");
    }

    return {
      snapshot: mergeSnapshotResources(snapshot, routes, payloads),
      mode: "resource-refs",
      routes,
    };
  } catch {
    return {
      snapshot: await fetchWorkbenchSnapshot(),
      mode: "full-snapshot",
      routes,
    };
  }
}

export function mergeSnapshotResources(
  snapshot: ControlPlaneSnapshot,
  routes: WorkbenchResourceRoute[],
  payloads: SnapshotResourcePayload[]
): ControlPlaneSnapshot {
  const nextResources = {
    ...snapshot.resources,
  } as Record<keyof ControlPlaneSnapshot["resources"], SnapshotResourcePayload>;

  routes.forEach((route, index) => {
    const key = SNAPSHOT_RESOURCE_KEY_BY_ROUTE[route];
    nextResources[key] = payloads[index]!;
  });

  return {
    ...snapshot,
    resources: nextResources as ControlPlaneSnapshot["resources"],
    generated_at: new Date().toISOString(),
  };
}

export function listWorkbenchResourceRoutes(): WorkbenchResourceRoute[] {
  return Object.keys(CONTROL_RESOURCE_QUERY_REGISTRY) as WorkbenchResourceRoute[];
}
