export interface SnapshotProjectionSource {
  status: "ready" | "degraded";
  contractVersion: string;
  generatedAt: string;
  resources: Array<{
    name: string;
    resourceType: string;
    resourceId: string;
    status: string;
  }>;
  degradedSections: string[];
  resourceErrors: Array<{
    name: string;
    code: string;
    errorType: string;
    message: string;
  }>;
  actionIds: string[];
}

export interface ControlPlaneResourceProjection {
  status: "ready" | "degraded";
  contractVersion: string;
  generatedAt: string;
  resourceStates: Array<{
    name: string;
    available: boolean;
    errorCode: string | null;
  }>;
  availableActionIds: string[];
}

export function projectControlPlaneSnapshot(
  source: SnapshotProjectionSource
): ControlPlaneResourceProjection {
  const degraded = new Set(source.degradedSections);
  const errors = new Map(
    source.resourceErrors.map((error) => [error.name, error])
  );
  return {
    status: source.status,
    contractVersion: source.contractVersion,
    generatedAt: source.generatedAt,
    resourceStates: source.resources.map((resource) => {
      const error = errors.get(resource.name);
      const available =
        resource.status === "ready" &&
        !degraded.has(resource.name) &&
        error === undefined;
      return {
        name: resource.name,
        available,
        errorCode:
          error?.code ??
          (available ? null : `RESOURCE_${resource.status.toUpperCase()}`),
      };
    }),
    availableActionIds: [...new Set(source.actionIds)].sort(),
  };
}
