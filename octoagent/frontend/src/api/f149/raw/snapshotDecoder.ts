import type { components } from "../../../generated/f149/rest";

export const F149_SNAPSHOT_RESOURCE_NAMES = [
  "config",
  "project_selector",
  "sessions",
  "agent_profiles",
  "worker_profiles",
  "owner_profile",
  "bootstrap_session",
  "context_continuity",
  "capability_pack",
  "skill_governance",
  "mcp_provider_catalog",
  "setup_governance",
  "delegation",
  "diagnostics",
  "retrieval_platform",
  "memory",
] as const;

export type F149SnapshotResourceName =
  (typeof F149_SNAPSHOT_RESOURCE_NAMES)[number];

export type RawSnapshotScalar = string | number | boolean | null;
export type RawSnapshotValue =
  | RawSnapshotScalar
  | RawSnapshotObject
  | RawSnapshotValue[];
export interface RawSnapshotObject {
  [key: string]: RawSnapshotValue;
}

export interface DecodedSnapshotResource {
  name: F149SnapshotResourceName;
  resourceType: string;
  resourceId: string;
  status: string;
}

export interface DecodedSnapshotResourceError {
  name: F149SnapshotResourceName;
  code: string;
  errorType: string;
  message: string;
}

export interface DecodedF149Snapshot {
  status: "ready" | "degraded";
  contractVersion: string;
  generatedAt: string;
  resources: DecodedSnapshotResource[];
  degradedSections: F149SnapshotResourceName[];
  resourceErrors: DecodedSnapshotResourceError[];
  actionIds: string[];
}

interface SnapshotDecodeFailure {
  ok: false;
  error: {
    code: string;
    path: string;
  };
}

export type SnapshotDecodeResult =
  | { ok: true; value: DecodedF149Snapshot }
  | SnapshotDecodeFailure;

type F149SnapshotWire = components["schemas"]["F149SnapshotEnvelope"];
type DecodedPart<T> = { ok: true; value: T } | SnapshotDecodeFailure;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function invalid(code: string, path: string): SnapshotDecodeFailure {
  return {
    ok: false,
    error: {
      code,
      path,
    },
  };
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isResourceName(value: unknown): value is F149SnapshotResourceName {
  return (
    typeof value === "string" &&
    (F149_SNAPSHOT_RESOURCE_NAMES as readonly string[]).includes(value)
  );
}

function decodeResources(value: unknown): DecodedPart<DecodedSnapshotResource[]> {
  if (!isRecord(value)) {
    return invalid("F149_SNAPSHOT_RESOURCE_SET_INVALID", "$.resources");
  }
  const resourceNames = Object.keys(value);
  if (
    resourceNames.length !== F149_SNAPSHOT_RESOURCE_NAMES.length ||
    resourceNames.some((name) => !isResourceName(name))
  ) {
    return invalid("F149_SNAPSHOT_RESOURCE_SET_INVALID", "$.resources");
  }

  const resources: DecodedSnapshotResource[] = [];
  for (const name of F149_SNAPSHOT_RESOURCE_NAMES) {
    const resource = value[name];
    if (
      !isRecord(resource) ||
      !isNonEmptyString(resource.resource_type) ||
      !isNonEmptyString(resource.resource_id) ||
      !isNonEmptyString(resource.status)
    ) {
      return invalid("F149_SNAPSHOT_RESOURCE_INVALID", `$.resources.${name}`);
    }
    resources.push({
      name,
      resourceType: resource.resource_type,
      resourceId: resource.resource_id,
      status: resource.status,
    });
  }
  return { ok: true, value: resources };
}

function decodeDegradedSections(
  value: unknown
): DecodedPart<F149SnapshotResourceName[]> {
  if (
    !Array.isArray(value) ||
    value.some((name) => !isResourceName(name)) ||
    new Set(value).size !== value.length
  ) {
    return invalid("F149_SNAPSHOT_DEGRADED_SECTIONS_INVALID", "$.degraded_sections");
  }
  return { ok: true, value: value as F149SnapshotResourceName[] };
}

function decodeResourceErrors(
  value: unknown
): DecodedPart<DecodedSnapshotResourceError[]> {
  if (!isRecord(value)) {
    return invalid("F149_SNAPSHOT_RESOURCE_ERRORS_INVALID", "$.resource_errors");
  }
  const resourceErrors: DecodedSnapshotResourceError[] = [];
  for (const [name, error] of Object.entries(value)) {
    if (
      !isResourceName(name) ||
      !isRecord(error) ||
      !isNonEmptyString(error.code) ||
      !isNonEmptyString(error.error_type) ||
      typeof error.message !== "string"
    ) {
      return invalid(
        "F149_SNAPSHOT_RESOURCE_ERRORS_INVALID",
        `$.resource_errors.${name}`
      );
    }
    resourceErrors.push({
      name,
      code: error.code,
      errorType: error.error_type,
      message: error.message,
    });
  }
  return { ok: true, value: resourceErrors };
}

function decodeActionIds(value: unknown): DecodedPart<string[]> {
  if (!isRecord(value) || !Array.isArray(value.actions)) {
    return invalid("F149_SNAPSHOT_ACTION_REGISTRY_INVALID", "$.registry.actions");
  }
  const actionIds: string[] = [];
  for (const [index, action] of value.actions.entries()) {
    if (!isRecord(action) || !isNonEmptyString(action.action_id)) {
      return invalid(
        "F149_SNAPSHOT_ACTION_REGISTRY_INVALID",
        `$.registry.actions.${index}`
      );
    }
    actionIds.push(action.action_id);
  }
  if (new Set(actionIds).size !== actionIds.length) {
    return invalid("F149_SNAPSHOT_ACTION_REGISTRY_INVALID", "$.registry.actions");
  }
  return { ok: true, value: actionIds.sort() };
}

export function decodeF149Snapshot(input: unknown): SnapshotDecodeResult {
  void (input as F149SnapshotWire);
  if (!isRecord(input)) {
    return invalid("F149_SNAPSHOT_ENVELOPE_INVALID", "$");
  }
  const status = input.status;
  if (status !== "ready" && status !== "degraded") {
    return invalid("F149_SNAPSHOT_STATUS_INVALID", "$.status");
  }
  if (!isNonEmptyString(input.contract_version)) {
    return invalid("F149_SNAPSHOT_CONTRACT_VERSION_INVALID", "$.contract_version");
  }
  if (!isNonEmptyString(input.generated_at)) {
    return invalid("F149_SNAPSHOT_GENERATED_AT_INVALID", "$.generated_at");
  }
  const resources = decodeResources(input.resources);
  const degradedSections = decodeDegradedSections(input.degraded_sections);
  const resourceErrors = decodeResourceErrors(input.resource_errors);
  const actionIds = decodeActionIds(input.registry);
  if (!resources.ok) return resources;
  if (!degradedSections.ok) return degradedSections;
  if (!resourceErrors.ok) return resourceErrors;
  if (!actionIds.ok) return actionIds;

  return {
    ok: true,
    value: {
      status,
      contractVersion: input.contract_version,
      generatedAt: input.generated_at,
      resources: resources.value,
      degradedSections: degradedSections.value,
      resourceErrors: resourceErrors.value,
      actionIds: actionIds.value,
    },
  };
}
