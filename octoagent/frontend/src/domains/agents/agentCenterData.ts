import type {
  ControlPlaneSnapshot,
  ProjectOption,
  SkillGovernanceItem,
  WorkerProfileItem,
  WorkspaceOption,
} from "../../types";

export interface RootAgentStudioDraft {
  profileId: string;
  scope: string;
  projectId: string;
  name: string;
  summary: string;
  baseArchetype: string;
  modelAlias: string;
  toolProfile: string;
  defaultToolGroupsText: string;
  selectedToolsText: string;
  runtimeKindsText: string;
  policyRefsText: string;
  instructionOverlaysText: string;
  tagsText: string;
  metadata: Record<string, unknown>;
  capabilitySelection: Record<string, boolean>;
}

export interface RootAgentReviewResult {
  mode: string;
  can_save: boolean;
  ready: boolean;
  warnings: string[];
  save_errors: string[];
  blocking_reasons: string[];
  next_actions: string[];
  profile: Record<string, unknown>;
  existing_profile: Record<string, unknown>;
  source_profile: Record<string, unknown>;
  diff: {
    has_changes: boolean;
    changed_fields: Array<{
      field: string;
      before: unknown;
      after: unknown;
    }>;
  };
}

export interface CapabilityProviderEntry {
  providerId: string;
  label: string;
  description: string;
  selectionItemId: string;
  kind: "skill" | "mcp";
  defaultSelected: boolean;
  enabled: boolean;
  availability: string;
  editable: boolean;
  tags: string[];
}

export const TOKEN_LABELS: Record<string, string> = {
  llm_generation: "内容生成",
  general: "Butler 协调",
  planner: "任务规划",
  handoff: "任务交接",
  memory: "记忆整理",
  frontend: "前端界面",
  research: "资料调研",
  watchdog: "巡检恢复",
  worker: "Worker 运行",
  subagent: "子 Agent",
  project: "项目信息",
  session: "会话信息",
  supervision: "审批协助",
  filesystem: "文件操作",
  web: "网页访问",
  "project.inspect": "项目检查",
  "task.inspect": "任务检查",
  "artifact.list": "产出清单",
  "runtime.inspect": "运行检查",
  "work.inspect": "Work 检查",
  "agents.list": "Agent 清单",
  "session.status": "会话状态",
};

export function toRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function readNestedString(
  source: Record<string, unknown>,
  path: string[],
  fallback = ""
): string {
  let current: unknown = source;
  for (const segment of path) {
    if (!current || typeof current !== "object" || Array.isArray(current)) {
      return fallback;
    }
    current = (current as Record<string, unknown>)[segment];
  }
  return typeof current === "string" && current.trim() ? current : fallback;
}

export function uniqueStrings(values: Array<string | null | undefined>): string[] {
  return values
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean)
    .filter((item, index, all) => all.indexOf(item) === index);
}

export function findProjectName(projects: ProjectOption[], projectId: string): string {
  return projects.find((project) => project.project_id === projectId)?.name ?? projectId;
}

export function findWorkspaceName(workspaces: WorkspaceOption[], workspaceId: string): string {
  return workspaces.find((workspace) => workspace.workspace_id === workspaceId)?.name ?? workspaceId;
}

export function projectSelectOptions(
  projects: ProjectOption[],
  currentProjectId: string
): Array<{ value: string; label: string }> {
  const options = projects.map((project) => ({
    value: project.project_id,
    label: project.name,
  }));
  if (currentProjectId && !options.some((option) => option.value === currentProjectId)) {
    return [{ value: currentProjectId, label: currentProjectId }, ...options];
  }
  return options;
}

export function workspaceSelectOptions(
  workspaces: WorkspaceOption[],
  projectId: string,
  currentWorkspaceId: string
): Array<{ value: string; label: string }> {
  const options = workspaces
    .filter((workspace) => workspace.project_id === projectId)
    .map((workspace) => ({
      value: workspace.workspace_id,
      label: workspace.name,
    }));
  if (currentWorkspaceId && !options.some((option) => option.value === currentWorkspaceId)) {
    return [{ value: currentWorkspaceId, label: currentWorkspaceId }, ...options];
  }
  return options;
}

export function normalizeWorkspaceForProject(
  workspaces: WorkspaceOption[],
  projectId: string,
  currentWorkspaceId: string
): string {
  const scoped = workspaces.filter((workspace) => workspace.project_id === projectId);
  if (scoped.some((workspace) => workspace.workspace_id === currentWorkspaceId)) {
    return currentWorkspaceId;
  }
  return scoped[0]?.workspace_id ?? currentWorkspaceId;
}

export function formatTokenLabel(token: string): string {
  if (TOKEN_LABELS[token]) {
    return TOKEN_LABELS[token];
  }
  return token
    .replace(/[._-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function joinStudioList(values: string[]): string {
  return values.join("\n");
}

export function parseStudioList(value: string): string[] {
  return value
    .split(/[\n,]+/g)
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item, index, all) => all.indexOf(item) === index);
}

export function appendStudioListValue(current: string, value: string): string {
  return joinStudioList(uniqueStrings([...parseStudioList(current), value]));
}

function readStudioList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return uniqueStrings(value.map((item) => (typeof item === "string" ? item : "")));
}

function readCapabilitySelectionMetadata(
  metadata: Record<string, unknown>
): Record<string, unknown> {
  const preferred = toRecord(metadata.capability_provider_selection);
  if (Object.keys(preferred).length > 0) {
    return preferred;
  }
  return toRecord(metadata.skill_selection);
}

export function buildSkillSelectionPayload(
  items: SkillGovernanceItem[]
): Record<string, string[]> {
  const selected_item_ids: string[] = [];
  const disabled_item_ids: string[] = [];
  items.forEach((item) => {
    if (item.selected && !item.enabled_by_default) {
      selected_item_ids.push(item.item_id);
    }
    if (!item.selected && item.enabled_by_default) {
      disabled_item_ids.push(item.item_id);
    }
  });
  return {
    selected_item_ids,
    disabled_item_ids,
  };
}

export function buildCapabilityProviderEntries(
  snapshot: ControlPlaneSnapshot
): CapabilityProviderEntry[] {
  const governanceById = Object.fromEntries(
    snapshot.resources.skill_governance.items.map((item) => [item.item_id, item])
  ) as Record<string, SkillGovernanceItem>;
  const skillEntries = snapshot.resources.skill_provider_catalog.items.map((item) => {
    const governance = governanceById[item.selection_item_id];
    return {
      providerId: item.provider_id,
      label: item.label,
      description: item.description,
      selectionItemId: item.selection_item_id,
      kind: "skill" as const,
      defaultSelected: governance?.selected ?? false,
      enabled: item.enabled,
      availability: item.availability,
      editable: item.editable,
      tags: [item.worker_type, item.model_alias, item.tool_profile],
    };
  });
  const mcpEntries = snapshot.resources.mcp_provider_catalog.items.map((item) => {
    const governance = governanceById[item.selection_item_id];
    return {
      providerId: item.provider_id,
      label: item.label,
      description: item.description,
      selectionItemId: item.selection_item_id,
      kind: "mcp" as const,
      defaultSelected: governance?.selected ?? false,
      enabled: item.enabled,
      availability: item.status,
      editable: item.editable,
      tags: [`tools:${item.tool_count}`, item.enabled ? "enabled" : "disabled"],
    };
  });
  return [...skillEntries, ...mcpEntries];
}

export function buildCapabilitySelectionState(
  items: CapabilityProviderEntry[],
  metadata: Record<string, unknown>
): Record<string, boolean> {
  const rawSelection = readCapabilitySelectionMetadata(metadata);
  const selectedItemIds = new Set(readStudioList(rawSelection.selected_item_ids));
  const disabledItemIds = new Set(readStudioList(rawSelection.disabled_item_ids));
  return Object.fromEntries(
    items.map((item) => {
      if (selectedItemIds.has(item.selectionItemId)) {
        return [item.selectionItemId, true];
      }
      if (disabledItemIds.has(item.selectionItemId)) {
        return [item.selectionItemId, false];
      }
      return [item.selectionItemId, item.defaultSelected];
    })
  );
}

function buildCapabilitySelectionPayload(
  items: CapabilityProviderEntry[],
  state: Record<string, boolean>
): Record<string, string[]> {
  const selected_item_ids: string[] = [];
  const disabled_item_ids: string[] = [];
  items.forEach((item) => {
    const current = state[item.selectionItemId] ?? item.defaultSelected;
    if (current && !item.defaultSelected) {
      selected_item_ids.push(item.selectionItemId);
    }
    if (!current && item.defaultSelected) {
      disabled_item_ids.push(item.selectionItemId);
    }
  });
  return {
    selected_item_ids,
    disabled_item_ids,
  };
}

export function mergeCapabilitySelectionMetadata(
  metadata: Record<string, unknown>,
  items: CapabilityProviderEntry[],
  state: Record<string, boolean>
): Record<string, unknown> {
  const nextMetadata = { ...metadata };
  delete nextMetadata.skill_selection;
  delete nextMetadata.capability_provider_selection;
  const selection = buildCapabilitySelectionPayload(items, state);
  if (selection.selected_item_ids.length > 0 || selection.disabled_item_ids.length > 0) {
    nextMetadata.capability_provider_selection = selection;
  }
  return nextMetadata;
}

export function buildRootAgentStudioDraft(
  profile: WorkerProfileItem | null,
  selector: ControlPlaneSnapshot["resources"]["project_selector"],
  capabilityProviderEntries: CapabilityProviderEntry[],
  fallbackArchetype = "general"
): RootAgentStudioDraft {
  const metadata = profile?.static_config.metadata ?? {};
  return {
    profileId: profile?.profile_id ?? "",
    scope: profile?.scope ?? "project",
    projectId: profile?.project_id || selector.current_project_id,
    name: profile?.name ?? "",
    summary: profile?.summary ?? "",
    baseArchetype: profile?.static_config.base_archetype ?? fallbackArchetype,
    modelAlias: profile?.static_config.model_alias ?? "main",
    toolProfile: profile?.static_config.tool_profile ?? "minimal",
    defaultToolGroupsText: joinStudioList(profile?.static_config.default_tool_groups ?? []),
    selectedToolsText: joinStudioList(profile?.static_config.selected_tools ?? []),
    runtimeKindsText: joinStudioList(profile?.static_config.runtime_kinds ?? []),
    policyRefsText: joinStudioList(profile?.static_config.policy_refs ?? []),
    instructionOverlaysText: joinStudioList(profile?.static_config.instruction_overlays ?? []),
    tagsText: joinStudioList(profile?.static_config.tags ?? []),
    metadata,
    capabilitySelection: buildCapabilitySelectionState(capabilityProviderEntries, metadata),
  };
}

function buildRootAgentStudioDraftFromPayload(
  profile: Record<string, unknown>,
  selector: ControlPlaneSnapshot["resources"]["project_selector"],
  capabilityProviderEntries: CapabilityProviderEntry[],
  fallbackArchetype = "general"
): RootAgentStudioDraft {
  const scope =
    typeof profile.scope === "string" && profile.scope.trim() ? profile.scope : "project";
  const projectId =
    scope === "project" &&
    typeof profile.project_id === "string" &&
    profile.project_id.trim()
      ? profile.project_id
      : selector.current_project_id;
  return {
    profileId:
      typeof profile.profile_id === "string" && profile.profile_id.trim()
        ? profile.profile_id
        : "",
    scope,
    projectId: scope === "project" ? projectId : "",
    name: typeof profile.name === "string" ? profile.name : "",
    summary: typeof profile.summary === "string" ? profile.summary : "",
    baseArchetype:
      typeof profile.base_archetype === "string" && profile.base_archetype.trim()
        ? profile.base_archetype
        : fallbackArchetype,
    modelAlias:
      typeof profile.model_alias === "string" && profile.model_alias.trim()
        ? profile.model_alias
        : "main",
    toolProfile:
      typeof profile.tool_profile === "string" && profile.tool_profile.trim()
        ? profile.tool_profile
        : "minimal",
    defaultToolGroupsText: joinStudioList(readStudioList(profile.default_tool_groups)),
    selectedToolsText: joinStudioList(readStudioList(profile.selected_tools)),
    runtimeKindsText: joinStudioList(readStudioList(profile.runtime_kinds)),
    policyRefsText: joinStudioList(readStudioList(profile.policy_refs)),
    instructionOverlaysText: joinStudioList(readStudioList(profile.instruction_overlays)),
    tagsText: joinStudioList(readStudioList(profile.tags)),
    metadata: toRecord(profile.metadata),
    capabilitySelection: buildCapabilitySelectionState(
      capabilityProviderEntries,
      toRecord(profile.metadata)
    ),
  };
}

export function buildRootAgentStudioDraftFromReview(
  review: unknown,
  selector: ControlPlaneSnapshot["resources"]["project_selector"],
  capabilityProviderEntries: CapabilityProviderEntry[]
): RootAgentStudioDraft | null {
  const profile = toRecord(toRecord(review).profile);
  if (Object.keys(profile).length === 0) {
    return null;
  }
  return buildRootAgentStudioDraftFromPayload(profile, selector, capabilityProviderEntries);
}

export function buildRootAgentPayload(
  draft: RootAgentStudioDraft,
  capabilityProviderEntries: CapabilityProviderEntry[]
): Record<string, unknown> {
  return {
    profile_id: draft.profileId || undefined,
    scope: draft.scope,
    project_id: draft.scope === "project" ? draft.projectId : "",
    name: draft.name,
    summary: draft.summary,
    base_archetype: draft.baseArchetype,
    model_alias: draft.modelAlias,
    tool_profile: draft.toolProfile,
    default_tool_groups: parseStudioList(draft.defaultToolGroupsText),
    selected_tools: parseStudioList(draft.selectedToolsText),
    runtime_kinds: parseStudioList(draft.runtimeKindsText),
    policy_refs: parseStudioList(draft.policyRefsText),
    instruction_overlays: parseStudioList(draft.instructionOverlaysText),
    tags: parseStudioList(draft.tagsText),
    metadata: mergeCapabilitySelectionMetadata(
      draft.metadata,
      capabilityProviderEntries,
      draft.capabilitySelection
    ),
  };
}

export function formatWorkerProfileStatus(status: string): string {
  switch (status) {
    case "draft":
      return "草稿";
    case "active":
      return "已发布";
    case "archived":
      return "已归档";
    default:
      return formatTokenLabel(status);
  }
}

export function formatWorkerProfileOrigin(originKind: string): string {
  switch (originKind) {
    case "builtin":
      return "系统内置";
    case "custom":
      return "自定义";
    case "cloned":
      return "复制而来";
    case "extracted":
      return "运行态提炼";
    default:
      return formatTokenLabel(originKind);
  }
}
