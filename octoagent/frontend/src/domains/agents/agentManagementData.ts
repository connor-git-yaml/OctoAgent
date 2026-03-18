import type {
  ControlPlaneSnapshot,
  ProjectOption,
  ToolAvailabilityExplanation,
  WorkerProfileItem,
} from "../../types";

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

export interface BehaviorFileInfo {
  file_id: string;
  path: string;
  scope?: string;
  exists_on_disk?: boolean;
}

export interface AgentCardViewModel {
  profileId: string;
  name: string;
  summary: string;
  status: "ready" | "needs_setup";
  profileStatus: string;
  projectId: string;
  projectName: string;
  modelAlias: string;
  /** Feature 061: 权限 Preset 显示标签 */
  permissionPreset: string;
  defaultToolGroups: string[];
  selectedTools: string[];
  activeWorkCount: number;
  waitingWorkCount: number;
  attentionWorkCount: number;
  updatedAt: string | null;
  sourceLabel: string;
  isMainAgent: boolean;
  removable: boolean;
  behaviorFiles: BehaviorFileInfo[];
}

export interface BuiltinAgentTemplateViewModel {
  templateId: string;
  name: string;
  summary: string;
  baseArchetype: string;
  modelAlias: string;
  defaultToolGroups: string[];
  selectedTools: string[];
}

export interface AgentManagementViewModel {
  currentProjectId: string;
  currentProjectName: string;
  mainAgent: AgentCardViewModel;
  projectAgents: AgentCardViewModel[];
  builtinTemplates: BuiltinAgentTemplateViewModel[];
  defaultProfileId: string;
  defaultProfileName: string;
  mainAgentProfile: WorkerProfileItem | null;
  mainAgentTemplate: WorkerProfileItem | null;
}

export interface AgentEditorDraft {
  profileId: string;
  projectId: string;
  name: string;
  baseArchetype: string;
  modelAlias: string;
  toolProfile: string;
  /** Feature 061: 权限 Preset（minimal/normal/full），取代 toolProfile */
  permissionPreset: string;
  /** Feature 061: 角色卡片（简短角色描述） */
  roleCard: string;
  defaultToolGroups: string[];
  selectedTools: string[];
  capabilitySelection: Record<string, boolean>;
  runtimeKinds: string[];
  policyRefs: string[];
  instructionOverlaysText: string;
  tagsText: string;
  metadataText: string;
  originKind: "custom" | "cloned" | "extracted";
}

export interface AgentEditorReview {
  canSave: boolean;
  ready: boolean;
  warnings: string[];
  blockingReasons: string[];
  nextActions: string[];
}

export interface ToolOption {
  toolName: string;
  label: string;
  toolGroup: string;
  availability: string;
}

const ARCHETYPE_LABELS: Record<string, string> = {
  general: "通用协作",
  ops: "运行保障",
  research: "资料调研",
  dev: "开发实现",
};

const DEFAULT_TOOL_PROFILE = "standard";
/** Feature 061: 默认权限 Preset */
const DEFAULT_PERMISSION_PRESET = "normal";
const DEFAULT_MODEL_ALIAS = "main";
const DEFAULT_RUNTIME_KINDS = ["worker"];

function toRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function readString(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return uniqueStrings(value.map((item) => (typeof item === "string" ? item : "")));
}

export function uniqueStrings(values: Array<string | null | undefined>): string[] {
  return values
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean)
    .filter((item, index, all) => all.indexOf(item) === index);
}

export function formatTokenLabel(token: string): string {
  return token
    .replace(/[._-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function formatAgentStatus(status: string): string {
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

export function formatAgentArchetype(archetype: string): string {
  return ARCHETYPE_LABELS[archetype] ?? formatTokenLabel(archetype);
}

/** Feature 061: 权限 Preset 用户友好标签 */
const PRESET_LABELS: Record<string, string> = {
  minimal: "保守模式",
  normal: "标准模式",
  full: "完全信任",
};

export function formatPermissionPreset(preset: string): string {
  return PRESET_LABELS[preset] ?? formatTokenLabel(preset);
}

export function formatProjectName(projects: ProjectOption[], projectId: string): string {
  return projects.find((project) => project.project_id === projectId)?.name ?? projectId;
}

function joinLines(values: string[]): string {
  return values.join("\n");
}

function parseLineList(value: string): string[] {
  return uniqueStrings(value.split(/\n+/g));
}

function parseMetadataText(value: string): Record<string, unknown> {
  const trimmed = value.trim();
  if (!trimmed) {
    return {};
  }
  try {
    const parsed = JSON.parse(trimmed);
    return toRecord(parsed);
  } catch {
    return {};
  }
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

export function buildCapabilityProviderEntries(
  snapshot: ControlPlaneSnapshot
): CapabilityProviderEntry[] {
  const governanceById = Object.fromEntries(
    snapshot.resources.skill_governance.items.map((item) => [item.item_id, item])
  ) as Record<string, (typeof snapshot.resources.skill_governance.items)[number]>;

  // Feature 057: skill entries 直接从 skill_governance 获取
  const skillEntries = snapshot.resources.skill_governance.items
    .filter((item) => item.source_kind !== "mcp")
    .map((item) => ({
      providerId: item.item_id.replace(/^skill:/, ""),
      label: item.label,
      description: "",
      selectionItemId: item.item_id,
      kind: "skill" as const,
      defaultSelected: item.selected,
      enabled: true,
      availability: item.availability,
      editable: false,
      tags: [item.source_kind],
    }));

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
      tags: [`tools:${item.tool_count}`],
    };
  });

  return [...skillEntries, ...mcpEntries];
}

export function buildCapabilitySelectionState(
  items: CapabilityProviderEntry[],
  metadata: Record<string, unknown>
): Record<string, boolean> {
  const selection = readCapabilitySelectionMetadata(metadata);
  const selectedItemIds = new Set(readStringArray(selection.selected_item_ids));
  const disabledItemIds = new Set(readStringArray(selection.disabled_item_ids));

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

export function mergeCapabilitySelectionMetadata(
  metadata: Record<string, unknown>,
  items: CapabilityProviderEntry[],
  state: Record<string, boolean>
): Record<string, unknown> {
  const nextMetadata = { ...metadata };
  delete nextMetadata.skill_selection;
  delete nextMetadata.capability_provider_selection;

  const selected_item_ids: string[] = [];
  const disabled_item_ids: string[] = [];
  items.forEach((item) => {
    const selected = state[item.selectionItemId] ?? item.defaultSelected;
    if (selected && !item.defaultSelected) {
      selected_item_ids.push(item.selectionItemId);
    }
    if (!selected && item.defaultSelected) {
      disabled_item_ids.push(item.selectionItemId);
    }
  });

  if (selected_item_ids.length > 0 || disabled_item_ids.length > 0) {
    nextMetadata.capability_provider_selection = {
      selected_item_ids,
      disabled_item_ids,
    };
  }
  return nextMetadata;
}

function formatSourceLabel(profile: WorkerProfileItem): string {
  switch (profile.origin_kind) {
    case "builtin":
      return `${formatAgentArchetype(profile.static_config.base_archetype)} 模板`;
    case "cloned":
      return "从模板创建";
    case "extracted":
      return "从历史工作整理";
    default:
      return "当前项目 Agent";
  }
}

const AGENT_PRIVATE_FILE_IDS = new Set(["IDENTITY.md", "SOUL.md", "HEARTBEAT.md"]);

function extractAgentPrivateBehaviorFiles(profile: WorkerProfileItem): BehaviorFileInfo[] {
  const files = profile.behavior_system?.path_manifest?.effective_behavior_files;
  if (!Array.isArray(files)) {
    return [];
  }
  return files.filter(
    (f) => f.file_id && f.path && AGENT_PRIVATE_FILE_IDS.has(f.file_id)
  );
}

function mapProfileToCard(
  profile: WorkerProfileItem,
  projects: ProjectOption[],
  isMainAgent: boolean
): AgentCardViewModel {
  return {
    profileId: profile.profile_id,
    name: profile.name,
    summary: profile.summary || "还没有填写用途说明。",
    status: "ready",
    profileStatus: formatAgentStatus(profile.status),
    projectId: profile.project_id,
    projectName: formatProjectName(projects, profile.project_id),
    modelAlias: profile.static_config.model_alias || DEFAULT_MODEL_ALIAS,
    permissionPreset: profile.static_config.permission_preset || DEFAULT_PERMISSION_PRESET,
    defaultToolGroups: profile.static_config.default_tool_groups ?? [],
    selectedTools: profile.static_config.selected_tools ?? [],
    activeWorkCount: Math.max(profile.dynamic_context.active_work_count || 0, 0),
    waitingWorkCount:
      Math.max(profile.dynamic_context.active_work_count || 0, 0) -
      Math.max(profile.dynamic_context.running_work_count || 0, 0),
    attentionWorkCount: Math.max(profile.dynamic_context.attention_work_count || 0, 0),
    updatedAt: profile.dynamic_context.updated_at ?? null,
    sourceLabel: formatSourceLabel(profile),
    isMainAgent,
    removable: !isMainAgent,
    behaviorFiles: extractAgentPrivateBehaviorFiles(profile),
  };
}

export function deriveAgentManagementView(
  snapshot: ControlPlaneSnapshot
): AgentManagementViewModel {
  const selector = snapshot.resources.project_selector;
  const currentProjectId = selector.current_project_id;
  const currentProjectName = formatProjectName(selector.available_projects, currentProjectId);
  const workerProfilesDocument = snapshot.resources.worker_profiles;
  const profiles = workerProfilesDocument?.profiles ?? [];
  const summary = toRecord(workerProfilesDocument?.summary);
  const defaultProfileId = readString(summary.default_profile_id);
  const defaultProfileName = readString(summary.default_profile_name);

  const builtinProfiles = profiles.filter((profile) => profile.origin_kind === "builtin");
  const builtinProfileById = Object.fromEntries(
    builtinProfiles.map((profile) => [profile.profile_id, profile])
  ) as Record<string, WorkerProfileItem>;
  const currentProjectProfiles = profiles.filter(
    (profile) =>
      profile.origin_kind !== "builtin" &&
      profile.scope === "project" &&
      profile.project_id === currentProjectId &&
      profile.status !== "archived"
  );

  const mainProfile =
    currentProjectProfiles.find((profile) => profile.profile_id === defaultProfileId) ?? null;
  const fallbackTemplate =
    builtinProfileById[defaultProfileId] ??
    builtinProfiles.find((profile) => profile.static_config.base_archetype === "general") ??
    builtinProfiles[0] ??
    null;

  const mainAgent =
    mainProfile !== null
      ? mapProfileToCard(mainProfile, selector.available_projects, true)
      : {
          profileId: fallbackTemplate?.profile_id ?? "",
          name: `${currentProjectName} 主 Agent`,
          summary:
            fallbackTemplate?.summary ||
            "先建立当前项目自己的主 Agent，后面聊天和工作分派会更稳定。",
          status: "needs_setup" as const,
          profileStatus: "待建立",
          projectId: currentProjectId,
          projectName: currentProjectName,
          modelAlias: fallbackTemplate?.static_config.model_alias || DEFAULT_MODEL_ALIAS,
          permissionPreset: fallbackTemplate?.static_config.permission_preset || DEFAULT_PERMISSION_PRESET,
          defaultToolGroups: fallbackTemplate?.static_config.default_tool_groups ?? [],
          selectedTools: fallbackTemplate?.static_config.selected_tools ?? [],
          activeWorkCount: 0,
          waitingWorkCount: 0,
          attentionWorkCount: 0,
          updatedAt: fallbackTemplate?.dynamic_context.updated_at ?? null,
          sourceLabel: fallbackTemplate
            ? `当前还在使用 ${formatAgentArchetype(fallbackTemplate.static_config.base_archetype)} 模板`
            : "当前还没有主 Agent",
          isMainAgent: true,
          removable: false,
          behaviorFiles: fallbackTemplate ? extractAgentPrivateBehaviorFiles(fallbackTemplate) : [],
        };

  const projectAgents = currentProjectProfiles
    .filter((profile) => profile.profile_id !== mainProfile?.profile_id)
    .map((profile) => mapProfileToCard(profile, selector.available_projects, false))
    .sort((left, right) => left.name.localeCompare(right.name, "zh-Hans-CN"));

  const builtinTemplates = builtinProfiles.map((profile) => ({
    templateId: profile.profile_id,
    name: `${formatAgentArchetype(profile.static_config.base_archetype)} 模板`,
    summary: profile.summary || "从这个起点开始，会自动带上对应的常用工具和能力。",
    baseArchetype: profile.static_config.base_archetype,
    modelAlias: profile.static_config.model_alias || DEFAULT_MODEL_ALIAS,
    defaultToolGroups: profile.static_config.default_tool_groups ?? [],
    selectedTools: profile.static_config.selected_tools ?? [],
  }));

  return {
    currentProjectId,
    currentProjectName,
    mainAgent,
    projectAgents,
    builtinTemplates,
    defaultProfileId,
    defaultProfileName,
    mainAgentProfile: mainProfile,
    mainAgentTemplate: fallbackTemplate,
  };
}

function buildDraftFromProfileLike(
  profile: Pick<
    WorkerProfileItem,
    "profile_id" | "name" | "summary" | "project_id" | "origin_kind" | "static_config"
  > | null,
  currentProjectId: string,
  currentProjectName: string,
  capabilityProviderEntries: CapabilityProviderEntry[],
  overrides?: Partial<AgentEditorDraft>
): AgentEditorDraft {
  const metadata = profile?.static_config.metadata ?? {};
  return {
    profileId: profile?.profile_id ?? "",
    projectId: profile?.project_id || currentProjectId,
    name: profile?.name || `${currentProjectName} Agent`,
    baseArchetype: profile?.static_config.base_archetype || "general",
    modelAlias: profile?.static_config.model_alias || DEFAULT_MODEL_ALIAS,
    toolProfile: profile?.static_config.tool_profile || DEFAULT_TOOL_PROFILE,
    permissionPreset: profile?.static_config.permission_preset || DEFAULT_PERMISSION_PRESET,
    roleCard: profile?.static_config.role_card || "",
    defaultToolGroups: profile?.static_config.default_tool_groups ?? [],
    selectedTools: profile?.static_config.selected_tools ?? [],
    capabilitySelection: buildCapabilitySelectionState(capabilityProviderEntries, metadata),
    runtimeKinds:
      profile?.static_config.runtime_kinds?.length ? profile.static_config.runtime_kinds : DEFAULT_RUNTIME_KINDS,
    policyRefs: profile?.static_config.policy_refs ?? [],
    instructionOverlaysText: joinLines(profile?.static_config.instruction_overlays ?? []),
    tagsText: joinLines(profile?.static_config.tags ?? []),
    metadataText: Object.keys(metadata).length > 0 ? JSON.stringify(metadata, null, 2) : "",
    originKind:
      profile?.origin_kind === "cloned" || profile?.origin_kind === "extracted"
        ? profile.origin_kind
        : "custom",
    ...overrides,
  };
}

export function buildAgentEditorDraftFromProfile(
  profile: WorkerProfileItem,
  currentProjectId: string,
  currentProjectName: string,
  capabilityProviderEntries: CapabilityProviderEntry[]
): AgentEditorDraft {
  return buildDraftFromProfileLike(
    profile,
    currentProjectId,
    currentProjectName,
    capabilityProviderEntries
  );
}

export function buildAgentEditorDraftFromTemplate(
  template: WorkerProfileItem | null,
  currentProjectId: string,
  currentProjectName: string,
  capabilityProviderEntries: CapabilityProviderEntry[],
  options?: {
    asMainAgent?: boolean;
    sourceName?: string;
  }
): AgentEditorDraft {
  return buildDraftFromProfileLike(
    template,
    currentProjectId,
    currentProjectName,
    capabilityProviderEntries,
    {
      profileId: "",
      projectId: currentProjectId,
      name:
        options?.sourceName ||
        (options?.asMainAgent ? `${currentProjectName} 主 Agent` : `${currentProjectName} 新 Agent`),
      originKind: template ? "cloned" : "custom",
    }
  );
}

export function buildBlankAgentEditorDraft(
  currentProjectId: string,
  currentProjectName: string,
  capabilityProviderEntries: CapabilityProviderEntry[]
): AgentEditorDraft {
  return buildDraftFromProfileLike(null, currentProjectId, currentProjectName, capabilityProviderEntries, {
    name: `${currentProjectName} 新 Agent`,
    originKind: "custom",
  });
}

export function buildAgentPayload(
  draft: AgentEditorDraft,
  capabilityProviderEntries: CapabilityProviderEntry[]
): Record<string, unknown> {
  const metadata = parseMetadataText(draft.metadataText);
  return {
    profile_id: draft.profileId || undefined,
    scope: "project",
    project_id: draft.projectId,
    name: draft.name,
    base_archetype: draft.baseArchetype,
    model_alias: draft.modelAlias,
    tool_profile: draft.toolProfile,
    permission_preset: draft.permissionPreset,
    role_card: draft.roleCard,
    default_tool_groups: uniqueStrings(draft.defaultToolGroups),
    selected_tools: uniqueStrings(draft.selectedTools),
    runtime_kinds: uniqueStrings(draft.runtimeKinds),
    policy_refs: uniqueStrings(draft.policyRefs),
    instruction_overlays: parseLineList(draft.instructionOverlaysText),
    tags: parseLineList(draft.tagsText),
    metadata: mergeCapabilitySelectionMetadata(metadata, capabilityProviderEntries, draft.capabilitySelection),
    origin_kind: draft.originKind,
  };
}

export function buildToolOptions(snapshot: ControlPlaneSnapshot): ToolOption[] {
  return snapshot.resources.capability_pack.pack.tools.map((tool) => ({
    toolName: tool.tool_name,
    label: tool.label || formatTokenLabel(tool.tool_name),
    toolGroup: tool.tool_group || "other",
    availability: tool.availability || "available",
  }));
}

export function buildToolGroupOptions(snapshot: ControlPlaneSnapshot): string[] {
  return uniqueStrings(
    snapshot.resources.capability_pack.pack.tools.map((tool) => tool.tool_group)
  );
}

export function buildModelAliasOptions(snapshot: ControlPlaneSnapshot): string[] {
  return uniqueStrings([
    ...Object.keys(toRecord(snapshot.resources.config.current_value.model_aliases)),
    DEFAULT_MODEL_ALIAS,
    "cheap",
    "reasoning",
  ]);
}

export function buildProjectOptions(
  selector: ControlPlaneSnapshot["resources"]["project_selector"]
): Array<{ value: string; label: string }> {
  return selector.available_projects.map((project) => ({
    value: project.project_id,
    label: project.name,
  }));
}

export function buildToolUsageSummary(
  tools: ToolAvailabilityExplanation[] | undefined,
  fallback: string[]
): string[] {
  const resolved = (tools ?? [])
    .map((item) => item.tool_name)
    .filter(Boolean);
  return uniqueStrings([...resolved, ...fallback]);
}

export function parseAgentReview(review: unknown): AgentEditorReview | null {
  const raw = toRecord(review);
  if (Object.keys(raw).length === 0) {
    return null;
  }
  return {
    canSave: Boolean(raw.can_save),
    ready: Boolean(raw.ready),
    warnings: readStringArray(raw.warnings),
    blockingReasons: readStringArray(raw.blocking_reasons),
    nextActions: readStringArray(raw.next_actions),
  };
}
