import type {
  ControlPlaneResourceRef,
  ControlPlaneSnapshot,
} from "../../types";

export type WorkbenchResourceRoute =
  | "wizard"
  | "config"
  | "project-selector"
  | "sessions"
  | "worker-profiles"
  | "context-frames"
  | "policy-profiles"
  | "capability-pack"
  | "skill-governance"
  | "skill-provider-catalog"
  | "mcp-provider-catalog"
  | "setup-governance"
  | "delegation"
  | "pipelines"
  | "automation"
  | "diagnostics"
  | "memory"
  | "import-workbench";

export type SnapshotResourceKey = keyof ControlPlaneSnapshot["resources"];
export type SnapshotResourcePayload =
  ControlPlaneSnapshot["resources"][SnapshotResourceKey];

export interface MemoryResourceQuery {
  projectId?: string;
  workspaceId?: string;
  scopeId?: string;
  partition?: string;
  layer?: string;
  query?: string;
  includeHistory?: boolean;
  includeVaultRefs?: boolean;
  limit?: number;
  status?: string;
  source?: string;
  subjectKey?: string;
}

export interface ImportWorkbenchQuery {
  projectId?: string;
  workspaceId?: string;
}

export interface SnapshotResourceLoadOptions {
  memoryQuery?: MemoryResourceQuery;
  importQuery?: ImportWorkbenchQuery;
}

export interface ResourceQueryDescriptor {
  route: WorkbenchResourceRoute;
  snapshotKey: SnapshotResourceKey;
  label: string;
  endpointPath: string;
  queryMode: "snapshot-resource" | "memory-query" | "import-query";
}

export const CANONICAL_CONTROL_RESOURCE_MANIFEST: Record<
  WorkbenchResourceRoute,
  ResourceQueryDescriptor
> = {
  wizard: {
    route: "wizard",
    snapshotKey: "wizard",
    label: "初始化向导",
    endpointPath: "/api/control/resources/wizard",
    queryMode: "snapshot-resource",
  },
  config: {
    route: "config",
    snapshotKey: "config",
    label: "平台配置",
    endpointPath: "/api/control/resources/config",
    queryMode: "snapshot-resource",
  },
  "project-selector": {
    route: "project-selector",
    snapshotKey: "project_selector",
    label: "项目与工作区",
    endpointPath: "/api/control/resources/project-selector",
    queryMode: "snapshot-resource",
  },
  sessions: {
    route: "sessions",
    snapshotKey: "sessions",
    label: "会话与任务",
    endpointPath: "/api/control/resources/sessions",
    queryMode: "snapshot-resource",
  },
  "worker-profiles": {
    route: "worker-profiles",
    snapshotKey: "worker_profiles",
    label: "Agent Profiles",
    endpointPath: "/api/control/resources/worker-profiles",
    queryMode: "snapshot-resource",
  },
  "context-frames": {
    route: "context-frames",
    snapshotKey: "context_continuity",
    label: "上下文帧",
    endpointPath: "/api/control/resources/context-frames",
    queryMode: "snapshot-resource",
  },
  "policy-profiles": {
    route: "policy-profiles",
    snapshotKey: "policy_profiles",
    label: "策略配置",
    endpointPath: "/api/control/resources/policy-profiles",
    queryMode: "snapshot-resource",
  },
  "capability-pack": {
    route: "capability-pack",
    snapshotKey: "capability_pack",
    label: "能力包",
    endpointPath: "/api/control/resources/capability-pack",
    queryMode: "snapshot-resource",
  },
  "skill-governance": {
    route: "skill-governance",
    snapshotKey: "skill_governance",
    label: "Skill 治理",
    endpointPath: "/api/control/resources/skill-governance",
    queryMode: "snapshot-resource",
  },
  "skill-provider-catalog": {
    route: "skill-provider-catalog",
    snapshotKey: "skill_provider_catalog",
    label: "Skill Providers",
    endpointPath: "/api/control/resources/skill-provider-catalog",
    queryMode: "snapshot-resource",
  },
  "mcp-provider-catalog": {
    route: "mcp-provider-catalog",
    snapshotKey: "mcp_provider_catalog",
    label: "MCP Providers",
    endpointPath: "/api/control/resources/mcp-provider-catalog",
    queryMode: "snapshot-resource",
  },
  "setup-governance": {
    route: "setup-governance",
    snapshotKey: "setup_governance",
    label: "Setup Review",
    endpointPath: "/api/control/resources/setup-governance",
    queryMode: "snapshot-resource",
  },
  delegation: {
    route: "delegation",
    snapshotKey: "delegation",
    label: "Work 与委派",
    endpointPath: "/api/control/resources/delegation",
    queryMode: "snapshot-resource",
  },
  pipelines: {
    route: "pipelines",
    snapshotKey: "pipelines",
    label: "Skill Pipeline",
    endpointPath: "/api/control/resources/pipelines",
    queryMode: "snapshot-resource",
  },
  automation: {
    route: "automation",
    snapshotKey: "automation",
    label: "自动任务",
    endpointPath: "/api/control/resources/automation",
    queryMode: "snapshot-resource",
  },
  diagnostics: {
    route: "diagnostics",
    snapshotKey: "diagnostics",
    label: "运行诊断",
    endpointPath: "/api/control/resources/diagnostics",
    queryMode: "snapshot-resource",
  },
  memory: {
    route: "memory",
    snapshotKey: "memory",
    label: "Memory Console",
    endpointPath: "/api/control/resources/memory",
    queryMode: "memory-query",
  },
  "import-workbench": {
    route: "import-workbench",
    snapshotKey: "imports",
    label: "导入工作台",
    endpointPath: "/api/control/resources/import-workbench",
    queryMode: "import-query",
  },
};

export const RESOURCE_ROUTE_BY_TYPE: Record<string, WorkbenchResourceRoute> = {
  wizard_session: "wizard",
  config_schema: "config",
  project_selector: "project-selector",
  session_projection: "sessions",
  worker_profiles: "worker-profiles",
  context_continuity: "context-frames",
  policy_profiles: "policy-profiles",
  capability_pack: "capability-pack",
  skill_governance: "skill-governance",
  skill_provider_catalog: "skill-provider-catalog",
  mcp_provider_catalog: "mcp-provider-catalog",
  setup_governance: "setup-governance",
  delegation_plane: "delegation",
  skill_pipeline: "pipelines",
  automation_job: "automation",
  diagnostics_summary: "diagnostics",
  memory_console: "memory",
  import_workbench: "import-workbench",
  import_source: "import-workbench",
  import_run: "import-workbench",
};

export const SNAPSHOT_RESOURCE_KEY_BY_ROUTE: Record<
  WorkbenchResourceRoute,
  SnapshotResourceKey
> = Object.fromEntries(
  Object.values(CANONICAL_CONTROL_RESOURCE_MANIFEST).map((entry) => [
    entry.route,
    entry.snapshotKey,
  ])
) as Record<WorkbenchResourceRoute, SnapshotResourceKey>;

export const CONTROL_RESOURCE_QUERY_REGISTRY: Record<
  WorkbenchResourceRoute,
  ResourceQueryDescriptor
> = CANONICAL_CONTROL_RESOURCE_MANIFEST;

export function resolveResourceRoutes(
  refs: ControlPlaneResourceRef[]
): WorkbenchResourceRoute[] {
  return Array.from(
    new Set(
      refs
        .map((ref) => RESOURCE_ROUTE_BY_TYPE[ref.resource_type])
        .filter((value): value is WorkbenchResourceRoute => Boolean(value))
    )
  );
}

export function isControlResourceDocument(
  value: unknown
): value is { resource_type: string; resource_id: string } {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.resource_type === "string" &&
    typeof candidate.resource_id === "string"
  );
}
