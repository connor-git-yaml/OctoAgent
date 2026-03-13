import { startTransition, useDeferredValue, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchWorkerProfileRevisions } from "../api/client";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import type {
  AgentProfileItem,
  ControlPlaneSnapshot,
  PolicyProfileItem,
  ProjectOption,
  SetupReviewSummary,
  SkillGovernanceItem,
  WorkerCapabilityProfile,
  WorkerProfileItem,
  WorkerProfileRevisionItem,
  WorkerProfilesDocument,
  WorkProjectionItem,
  WorkspaceOption,
} from "../types";
import {
  formatDateTime,
  formatWorkerTemplateName,
} from "../workbench/utils";

type WorkerCatalogView = "instances" | "templates";
type AgentWorkspaceView = "butler" | "templates" | "workers";
type WorkUnitKind = "instance" | "template";
type WorkAgentStatus = "active" | "syncing" | "attention" | "paused" | "draft";
type WorkAgentSource = "runtime" | "capability" | "manual";

interface PrimaryMemoryAccessDraft {
  allowVault: boolean;
  includeHistory: boolean;
}

interface PrimaryMemoryRecallDraft {
  postFilterMode: string;
  rerankMode: string;
  minKeywordOverlap: string;
  scopeLimit: string;
  perScopeLimit: string;
  maxHits: string;
}

interface PrimaryAgentDraft {
  name: string;
  scope: string;
  projectId: string;
  workspaceId: string;
  personaSummary: string;
  modelAlias: string;
  toolProfile: string;
  llmMode: string;
  proxyUrl: string;
  primaryProvider: string;
  policyProfileId: string;
  memoryAccessPolicy: PrimaryMemoryAccessDraft;
  memoryRecall: PrimaryMemoryRecallDraft;
}

interface WorkAgentItem {
  id: string;
  kind: WorkUnitKind;
  name: string;
  workerType: string;
  projectId: string;
  workspaceId: string;
  status: WorkAgentStatus;
  source: WorkAgentSource;
  toolProfile: string;
  modelAlias: string;
  autonomy: string;
  summary: string;
  tags: string[];
  selectedTools: string[];
  taskCount: number;
  waitingCount: number;
  mergeReadyCount: number;
  lastUpdated: string | null;
}

interface WorkAgentDraft {
  id: string;
  kind: WorkUnitKind;
  name: string;
  workerType: string;
  projectId: string;
  workspaceId: string;
  status: WorkAgentStatus;
  source: WorkAgentSource;
  toolProfile: string;
  modelAlias: string;
  autonomy: string;
  summary: string;
  tags: string[];
  selectedTools: string[];
  taskCount: string;
  waitingCount: string;
  mergeReadyCount: string;
}

interface RootAgentStudioDraft {
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
}

interface RootAgentReviewResult {
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

const WORKER_TYPE_LABELS: Record<string, string> = {
  general: "Butler",
  ops: "运行保障",
  research: "调研整理",
  dev: "开发实现",
};

const WORK_AGENT_STATUS_LABELS: Record<WorkAgentStatus, string> = {
  active: "运行中",
  syncing: "待同步",
  attention: "需要处理",
  paused: "已暂停",
  draft: "草稿",
};

const WORK_AGENT_SOURCE_LABELS: Record<WorkAgentSource, string> = {
  runtime: "运行实例",
  capability: "系统模板",
  manual: "自定义草稿",
};

const AUTONOMY_OPTIONS = [
  {
    value: "guided",
    label: "需要关键确认",
    description: "适合日常任务，关键动作仍由你拍板。",
  },
  {
    value: "free-loop",
    label: "自主推进",
    description: "适合边界清晰的工作，Worker 可连续执行。",
  },
  {
    value: "pipeline",
    label: "按预设流程",
    description: "适合有固定步骤的处理链路。",
  },
] as const;

const SCOPE_OPTIONS = [
  {
    value: "project",
    label: "项目级默认",
    description: "同一个项目下默认沿用这套 Butler 设置。",
  },
  {
    value: "workspace",
    label: "工作区级默认",
    description: "不同 Workspace 可以拥有更细的默认行为。",
  },
  {
    value: "session",
    label: "会话级默认",
    description: "只对当前会话生效，适合临时实验。",
  },
] as const;

const TOOL_PROFILE_LABELS: Record<string, string> = {
  minimal: "仅基础工具",
  standard: "常用工具",
  privileged: "扩展工具",
};

const TOKEN_LABELS: Record<string, string> = {
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

const MODEL_ALIAS_HINTS: Record<string, string> = {
  main: "平衡质量与速度，适合默认值。",
  cheap: "优先节省成本，适合批量整理。",
  reasoning: "优先深度推理，适合复杂判断。",
};

const DEFAULT_MEMORY_SCOPE_LIMIT = "4";
const DEFAULT_MEMORY_PER_SCOPE_LIMIT = "3";
const DEFAULT_MEMORY_MAX_HITS = "4";

const MEMORY_RECALL_PRESETS: Array<{
  id: string;
  label: string;
  description: string;
  values: PrimaryMemoryRecallDraft;
}> = [
  {
    id: "conservative",
    label: "保守召回",
    description: "尽量少带无关记忆，适合刚起步或上下文很敏感的 Butler。",
    values: {
      postFilterMode: "keyword_overlap",
      rerankMode: "heuristic",
      minKeywordOverlap: "2",
      scopeLimit: "2",
      perScopeLimit: "2",
      maxHits: "3",
    },
  },
  {
    id: "balanced",
    label: "平衡默认",
    description: "适合大多数日常协作，先维持连续性，再避免上下文过载。",
    values: {
      postFilterMode: "keyword_overlap",
      rerankMode: "heuristic",
      minKeywordOverlap: "1",
      scopeLimit: DEFAULT_MEMORY_SCOPE_LIMIT,
      perScopeLimit: DEFAULT_MEMORY_PER_SCOPE_LIMIT,
      maxHits: DEFAULT_MEMORY_MAX_HITS,
    },
  },
  {
    id: "wide",
    label: "广覆盖",
    description: "更适合长链路任务或复盘，会带回更多记忆候选。",
    values: {
      postFilterMode: "none",
      rerankMode: "heuristic",
      minKeywordOverlap: "1",
      scopeLimit: "6",
      perScopeLimit: "4",
      maxHits: "8",
    },
  },
];

const CATALOG_COPY = {
  instances: {
    label: "运行中的 Worker",
    title: "实例负责当前正在推进的工作",
    description: "这里看现在谁在工作、谁卡住了、谁适合合并或拆分。",
  },
  templates: {
    label: "实例草案",
    title: "把当前实例沉淀成新的默认做法",
    description: "这里只有从实例复制出来的草案。要发布长期默认模板，请回到上面的 Worker 模板。",
  },
} as const;

const AGENT_WORKSPACE_COPY: Record<
  AgentWorkspaceView,
  { label: string; title: string; description: string }
> = {
  butler: {
    label: "Butler 设置",
    title: "先把默认行为定稳",
    description: "名称、默认位置、审批和记忆边界都在这里。",
  },
  templates: {
    label: "Worker 模板",
    title: "再选谁适合处理这类任务",
    description: "维护可复用模板，发布版本，并设为聊天默认。",
  },
  workers: {
    label: "运行中的 Worker",
    title: "最后看现在谁在做事",
    description: "查看实例、拆分合并，并处理当前运行中的工作。",
  },
};

const DEFAULT_PERSONA =
  "你是我的 Butler，也是长期协作的 Agent 管家。你要持续维护目标、上下文和节奏，先梳理事实与下一步，再安排合适的 Worker；遇到高风险、不可逆或越权动作时，先停下来向我确认。";

const EMPTY_ROOT_AGENT_PROFILES: WorkerProfilesDocument = {
  contract_version: "1.0.0",
  resource_type: "worker_profiles",
  resource_id: "worker-profiles:overview",
  schema_version: 1,
  generated_at: "",
  updated_at: "",
  status: "degraded",
  degraded: {
    is_degraded: true,
    reasons: ["worker_profiles_missing"],
    unavailable_sections: ["worker_profiles"],
  },
  warnings: ["worker profiles resource missing from snapshot"],
  capabilities: [],
  refs: {},
  active_project_id: "",
  active_workspace_id: "",
  profiles: [],
  summary: {},
};

function toRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function readNestedString(
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

function uniqueStrings(values: Array<string | null | undefined>): string[] {
  return values
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean)
    .filter((item, index, all) => all.indexOf(item) === index);
}

function parsePositiveInt(
  value: string,
  fallback: number,
  minimum: number,
  maximum: number
): number {
  const parsed = Number.parseInt(value.trim(), 10);
  if (Number.isNaN(parsed)) {
    return fallback;
  }
  return Math.max(minimum, Math.min(maximum, parsed));
}

function primaryMemoryAccessFromProfile(profile: AgentProfileItem | null): PrimaryMemoryAccessDraft {
  const raw =
    profile?.memory_access_policy &&
    typeof profile.memory_access_policy === "object" &&
    !Array.isArray(profile.memory_access_policy)
      ? profile.memory_access_policy
      : {};
  return {
    allowVault: Boolean(raw.allow_vault),
    includeHistory: Boolean(raw.include_history),
  };
}

function primaryMemoryRecallFromProfile(profile: AgentProfileItem | null): PrimaryMemoryRecallDraft {
  const budget =
    profile?.context_budget_policy &&
    typeof profile.context_budget_policy === "object" &&
    !Array.isArray(profile.context_budget_policy)
      ? profile.context_budget_policy
      : {};
  const raw =
    budget.memory_recall && typeof budget.memory_recall === "object" && !Array.isArray(budget.memory_recall)
      ? (budget.memory_recall as Record<string, unknown>)
      : {};
  return {
    postFilterMode: String(raw.post_filter_mode ?? "keyword_overlap") || "keyword_overlap",
    rerankMode: String(raw.rerank_mode ?? "heuristic") || "heuristic",
    minKeywordOverlap: String(raw.min_keyword_overlap ?? "1") || "1",
    scopeLimit: String(raw.scope_limit ?? DEFAULT_MEMORY_SCOPE_LIMIT) || DEFAULT_MEMORY_SCOPE_LIMIT,
    perScopeLimit:
      String(raw.per_scope_limit ?? DEFAULT_MEMORY_PER_SCOPE_LIMIT) || DEFAULT_MEMORY_PER_SCOPE_LIMIT,
    maxHits: String(raw.max_hits ?? DEFAULT_MEMORY_MAX_HITS) || DEFAULT_MEMORY_MAX_HITS,
  };
}

function buildSkillSelectionPayload(items: SkillGovernanceItem[]): Record<string, string[]> {
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

function reviewTone(review: SetupReviewSummary): "success" | "warning" | "danger" {
  if (review.blocking_reasons.length > 0) {
    return "danger";
  }
  if (review.warnings.length > 0) {
    return "warning";
  }
  return "success";
}

function reviewHeadline(review: SetupReviewSummary): string {
  if (review.blocking_reasons.length > 0) {
    return `还有 ${review.blocking_reasons.length} 个问题要先处理`;
  }
  if (review.warnings.length > 0) {
    return `配置可保存，但还有 ${review.warnings.length} 条提醒`;
  }
  return "Butler 配置已经可以直接保存";
}

function reviewSummary(review: SetupReviewSummary): string {
  if (review.next_actions.length > 0) {
    return review.next_actions[0];
  }
  if (review.warnings.length > 0) {
    return review.warnings[0];
  }
  return "当前没有额外的处理动作。";
}

function detectRecallPreset(recall: PrimaryMemoryRecallDraft): string | null {
  const matched = MEMORY_RECALL_PRESETS.find(
    (preset) =>
      preset.values.postFilterMode === recall.postFilterMode &&
      preset.values.rerankMode === recall.rerankMode &&
      preset.values.minKeywordOverlap === recall.minKeywordOverlap &&
      preset.values.scopeLimit === recall.scopeLimit &&
      preset.values.perScopeLimit === recall.perScopeLimit &&
      preset.values.maxHits === recall.maxHits
  );
  return matched?.label ?? null;
}

function buildPrimaryAgentPayload(primaryDraft: PrimaryAgentDraft): Record<string, unknown> {
  return {
    scope: primaryDraft.scope,
    name: primaryDraft.name,
    persona_summary: primaryDraft.personaSummary,
    model_alias: primaryDraft.modelAlias,
    tool_profile: primaryDraft.toolProfile,
    memory_access_policy: {
      allow_vault: primaryDraft.memoryAccessPolicy.allowVault,
      include_history: primaryDraft.memoryAccessPolicy.includeHistory,
    },
    context_budget_policy: {
      memory_recall: {
        post_filter_mode: primaryDraft.memoryRecall.postFilterMode || "keyword_overlap",
        rerank_mode: primaryDraft.memoryRecall.rerankMode || "heuristic",
        min_keyword_overlap: parsePositiveInt(primaryDraft.memoryRecall.minKeywordOverlap, 1, 1, 8),
        scope_limit: parsePositiveInt(primaryDraft.memoryRecall.scopeLimit, 4, 1, 8),
        per_scope_limit: parsePositiveInt(primaryDraft.memoryRecall.perScopeLimit, 3, 1, 12),
        max_hits: parsePositiveInt(primaryDraft.memoryRecall.maxHits, 4, 1, 20),
      },
    },
  };
}

function findProjectName(projects: ProjectOption[], projectId: string): string {
  return projects.find((project) => project.project_id === projectId)?.name ?? projectId;
}

function findWorkspaceName(workspaces: WorkspaceOption[], workspaceId: string): string {
  return workspaces.find((workspace) => workspace.workspace_id === workspaceId)?.name ?? workspaceId;
}

function projectSelectOptions(
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

function workspaceSelectOptions(
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

function normalizeWorkspaceForProject(
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

function formatWorkerType(workerType: string): string {
  return WORKER_TYPE_LABELS[workerType] ?? workerType;
}

function formatAutonomy(value: string): string {
  return AUTONOMY_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

function formatToolProfile(value: string): string {
  return TOOL_PROFILE_LABELS[value] ?? value;
}

function formatProjectWorkspace(
  projects: ProjectOption[],
  workspaces: WorkspaceOption[],
  projectId: string,
  workspaceId: string
): string {
  return `${findProjectName(projects, projectId)} / ${findWorkspaceName(workspaces, workspaceId)}`;
}

function workAgentBadge(agent: WorkAgentItem): { label: string; tone: string } {
  if (agent.kind === "instance") {
    return {
      label: WORK_AGENT_STATUS_LABELS[agent.status],
      tone: agent.status,
    };
  }
  return {
    label: WORK_AGENT_SOURCE_LABELS[agent.source],
    tone: agent.source === "capability" ? "ready" : "draft",
  };
}

function formatTokenLabel(token: string): string {
  if (TOKEN_LABELS[token]) {
    return TOKEN_LABELS[token];
  }
  return token
    .replace(/[._-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function formatToolToken(toolName: string, toolLabelByName: Record<string, string>): string {
  return TOKEN_LABELS[toolName] ?? toolLabelByName[toolName] ?? formatTokenLabel(toolName);
}

function formatScope(scope: string): string {
  return SCOPE_OPTIONS.find((option) => option.value === scope)?.label ?? formatTokenLabel(scope);
}

function joinStudioList(values: string[]): string {
  return values.join("\n");
}

function parseStudioList(value: string): string[] {
  return value
    .split(/[\n,]+/g)
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item, index, all) => all.indexOf(item) === index);
}

function appendStudioListValue(current: string, value: string): string {
  return joinStudioList(uniqueStrings([...parseStudioList(current), value]));
}

function readStudioList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return uniqueStrings(
    value.map((item) => (typeof item === "string" ? item : ""))
  );
}

function buildRootAgentStudioDraft(
  profile: WorkerProfileItem | null,
  selector: ControlPlaneSnapshot["resources"]["project_selector"],
  fallbackArchetype = "general"
): RootAgentStudioDraft {
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
  };
}

function buildRootAgentStudioDraftFromPayload(
  profile: Record<string, unknown>,
  selector: ControlPlaneSnapshot["resources"]["project_selector"],
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
  };
}

function buildRootAgentStudioDraftFromReview(
  review: unknown,
  selector: ControlPlaneSnapshot["resources"]["project_selector"]
): RootAgentStudioDraft | null {
  const profile = toRecord(toRecord(review).profile);
  if (Object.keys(profile).length === 0) {
    return null;
  }
  return buildRootAgentStudioDraftFromPayload(profile, selector);
}

function buildRootAgentPayload(draft: RootAgentStudioDraft): Record<string, unknown> {
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
  };
}

function formatWorkerProfileStatus(status: string): string {
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

function formatWorkerProfileOrigin(originKind: string): string {
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

function primaryProfileFromSnapshot(snapshot: ControlPlaneSnapshot): AgentProfileItem | null {
  const setupDetails = toRecord(snapshot.resources.setup_governance.agent_governance.details);
  const activeProfile = toRecord(setupDetails.active_agent_profile);
  if (typeof activeProfile.profile_id === "string" && activeProfile.profile_id.trim()) {
    return {
      profile_id: String(activeProfile.profile_id ?? ""),
      scope: String(activeProfile.scope ?? "project"),
      project_id: String(
        activeProfile.project_id ?? snapshot.resources.project_selector.current_project_id
      ),
      name: String(activeProfile.name ?? "OctoAgent"),
      persona_summary: String(activeProfile.persona_summary ?? DEFAULT_PERSONA),
      model_alias: String(activeProfile.model_alias ?? "main"),
      tool_profile: String(activeProfile.tool_profile ?? "standard"),
      memory_access_policy:
        activeProfile.memory_access_policy &&
        typeof activeProfile.memory_access_policy === "object" &&
        !Array.isArray(activeProfile.memory_access_policy)
          ? (activeProfile.memory_access_policy as Record<string, unknown>)
          : {},
      context_budget_policy:
        activeProfile.context_budget_policy &&
        typeof activeProfile.context_budget_policy === "object" &&
        !Array.isArray(activeProfile.context_budget_policy)
          ? (activeProfile.context_budget_policy as Record<string, unknown>)
          : {},
      updated_at: typeof activeProfile.updated_at === "string" ? activeProfile.updated_at : null,
    };
  }

  return snapshot.resources.agent_profiles.profiles[0] ?? null;
}

function buildPrimaryAgentSeed(snapshot: ControlPlaneSnapshot): PrimaryAgentDraft {
  const selector = snapshot.resources.project_selector;
  const configValue = toRecord(snapshot.resources.config.current_value);
  const runtime = toRecord(configValue.runtime);
  const providers = Array.isArray(configValue.providers)
    ? configValue.providers
        .map((item) => toRecord(item))
        .filter((item) => typeof item.id === "string")
    : [];
  const primaryProvider =
    providers.find((item) => item.enabled !== false)?.id ??
    providers[0]?.id ??
    "openrouter";
  const activeProfile = primaryProfileFromSnapshot(snapshot);
  const activePolicy =
    snapshot.resources.policy_profiles.profiles.find((profile) => profile.is_active)?.profile_id ??
    "default";

  return {
    name: activeProfile?.name || "OctoAgent",
    scope: activeProfile?.scope || "project",
    projectId: activeProfile?.project_id || selector.current_project_id,
    workspaceId: selector.current_workspace_id,
    personaSummary: activeProfile?.persona_summary || DEFAULT_PERSONA,
    modelAlias: activeProfile?.model_alias || "main",
    toolProfile: activeProfile?.tool_profile || "standard",
    llmMode: readNestedString(runtime, ["llm_mode"], "litellm"),
    proxyUrl: readNestedString(runtime, ["litellm_proxy_url"], "http://localhost:4000"),
    primaryProvider: String(primaryProvider),
    policyProfileId: activePolicy,
    memoryAccessPolicy: primaryMemoryAccessFromProfile(activeProfile),
    memoryRecall: primaryMemoryRecallFromProfile(activeProfile),
  };
}

function workAgentStatusForWorks(works: WorkProjectionItem[]): WorkAgentStatus {
  if (
    works.some((work) =>
      ["waiting_approval", "waiting_input", "escalated", "failed", "timed_out"].includes(
        work.status
      )
    )
  ) {
    return "attention";
  }
  if (works.some((work) => work.status === "running")) {
    return "active";
  }
  if (works.some((work) => ["created", "assigned"].includes(work.status))) {
    return "syncing";
  }
  return "paused";
}

function buildWorkAgentFromGroup(
  key: string,
  works: WorkProjectionItem[],
  workerProfiles: Record<string, WorkerCapabilityProfile>,
  workspaces: WorkspaceOption[],
  primaryDraft: PrimaryAgentDraft
): WorkAgentItem {
  const first = works[0];
  const workerProfile = workerProfiles[first.selected_worker_type];
  const tools = uniqueStrings(works.flatMap((work) => work.selected_tools));
  const requestedToolProfiles = uniqueStrings(
    works.map((work) =>
      readNestedString(toRecord(work.runtime_summary), ["requested_tool_profile"], "")
    )
  );
  const requestedModelAliases = uniqueStrings(
    works.map((work) =>
      readNestedString(toRecord(work.runtime_summary), ["requested_model_alias"], "")
    )
  );
  const waitingCount = works.filter((work) =>
    ["waiting_approval", "waiting_input", "paused"].includes(work.status)
  ).length;
  const mergeReadyCount = works.filter((work) => work.merge_ready).length;
  const workspaceName = findWorkspaceName(workspaces, first.workspace_id);
  const tags = uniqueStrings(workerProfile?.capabilities?.slice(0, 4) ?? []).slice(0, 4);
  const lastUpdated =
    works
      .map((work) => work.updated_at ?? "")
      .sort((left, right) => right.localeCompare(left))[0] || null;

  return {
    id: key,
    kind: "instance",
    name: `${formatWorkerType(first.selected_worker_type)} · ${workspaceName}`,
    workerType: first.selected_worker_type,
    projectId: first.project_id,
    workspaceId: first.workspace_id,
    status: workAgentStatusForWorks(works),
    source: "runtime",
    toolProfile:
      requestedToolProfiles[0] || workerProfile?.default_tool_profile || primaryDraft.toolProfile,
    modelAlias:
      requestedModelAliases[0] || workerProfile?.default_model_alias || primaryDraft.modelAlias,
    autonomy: first.target_kind === "graph_agent" ? "pipeline" : "free-loop",
    summary: `当前负责 ${works.length} 条工作，其中等待处理 ${waitingCount} 条，可直接合并 ${mergeReadyCount} 条。`,
    tags,
    selectedTools: tools,
    taskCount: works.length,
    waitingCount,
    mergeReadyCount,
    lastUpdated,
  };
}

function buildTemplateSeeds(
  snapshot: ControlPlaneSnapshot,
  primaryDraft: PrimaryAgentDraft
): WorkAgentItem[] {
  const selector = snapshot.resources.project_selector;
  const profiles = snapshot.resources.capability_pack.pack.worker_profiles;
  if (profiles.length === 0) {
    return [
      {
        id: "manual:template:default",
        kind: "template",
        name: "Butler 模板",
        workerType: "general",
        projectId: selector.current_project_id,
        workspaceId: selector.current_workspace_id,
        status: "draft",
        source: "manual",
        toolProfile: primaryDraft.toolProfile,
        modelAlias: primaryDraft.modelAlias,
        autonomy: "guided",
        summary: "为新建 Worker 准备一个更清晰的起点。",
        tags: ["planner", "handoff"],
        selectedTools: [],
        taskCount: 0,
        waitingCount: 0,
        mergeReadyCount: 0,
        lastUpdated: null,
      },
    ];
  }

  return profiles.map((profile) => {
    const capabilitySummary = uniqueStrings(profile.capabilities).slice(0, 2).join("、") || "通用任务";
    const toolSummary =
      uniqueStrings(profile.default_tool_groups).slice(0, 2).join("、") || "常用工具";
    return {
      id: `template:${profile.worker_type}`,
      kind: "template",
      name: `${formatWorkerType(profile.worker_type)} 模板`,
      workerType: profile.worker_type,
      projectId: selector.current_project_id,
      workspaceId: selector.current_workspace_id,
      status: "draft",
      source: "capability",
      toolProfile: profile.default_tool_profile || primaryDraft.toolProfile,
      modelAlias: profile.default_model_alias || primaryDraft.modelAlias,
      autonomy: profile.runtime_kinds.includes("graph_agent") ? "pipeline" : "guided",
      summary: `适合 ${capabilitySummary} 一类任务，默认带上 ${toolSummary}。`,
      tags: uniqueStrings(profile.capabilities.slice(0, 4)),
      selectedTools: uniqueStrings(profile.default_tool_groups.slice(0, 4)),
      taskCount: 0,
      waitingCount: 0,
      mergeReadyCount: 0,
      lastUpdated: null,
    };
  });
}

function buildWorkAgentSeeds(snapshot: ControlPlaneSnapshot): WorkAgentItem[] {
  const primaryDraft = buildPrimaryAgentSeed(snapshot);
  const workerProfiles = Object.fromEntries(
    snapshot.resources.capability_pack.pack.worker_profiles.map((profile) => [
      profile.worker_type,
      profile,
    ])
  ) as Record<string, WorkerCapabilityProfile>;
  const grouped = new Map<string, WorkProjectionItem[]>();

  for (const work of snapshot.resources.delegation.works) {
    const key = [
      work.selected_worker_type,
      work.project_id,
      work.workspace_id,
      work.owner_id || "owner",
    ].join("::");
    const current = grouped.get(key) ?? [];
    current.push(work);
    grouped.set(key, current);
  }

  const runtimeAgents = Array.from(grouped.entries()).map(([key, works]) =>
    buildWorkAgentFromGroup(
      key,
      works,
      workerProfiles,
      snapshot.resources.project_selector.available_workspaces,
      primaryDraft
    )
  );

  return [...runtimeAgents, ...buildTemplateSeeds(snapshot, primaryDraft)];
}

function toDraft(agent: WorkAgentItem): WorkAgentDraft {
  return {
    ...agent,
    tags: [...agent.tags],
    selectedTools: [...agent.selectedTools],
    taskCount: String(agent.taskCount),
    waitingCount: String(agent.waitingCount),
    mergeReadyCount: String(agent.mergeReadyCount),
  };
}

function buildEmptyWorkDraft(
  primaryDraft: PrimaryAgentDraft,
  kind: WorkUnitKind
): WorkAgentDraft {
  return {
    id: "",
    kind,
    name: "",
    workerType: "general",
    projectId: primaryDraft.projectId,
    workspaceId: primaryDraft.workspaceId,
    status: kind === "instance" ? "syncing" : "draft",
    source: "manual",
    toolProfile: primaryDraft.toolProfile,
    modelAlias: primaryDraft.modelAlias,
    autonomy: kind === "template" ? "guided" : "guided",
    summary: "",
    tags: ["planner", "handoff"],
    selectedTools: [],
    taskCount: "0",
    waitingCount: "0",
    mergeReadyCount: "0",
  };
}

function hydrateWorkDraft(draft: WorkAgentDraft): WorkAgentItem {
  const source =
    draft.kind === "template" && draft.source === "capability" ? "manual" : draft.source;
  const status =
    draft.kind === "template"
      ? "draft"
      : draft.status === "draft"
        ? "syncing"
        : draft.status;

  return {
    id:
      draft.id ||
      `${draft.kind === "instance" ? "manual:instance" : "manual:template"}:${Date.now().toString(36)}`,
    kind: draft.kind,
    name:
      draft.name.trim() ||
      (draft.kind === "template" ? "未命名 Worker 模板" : "未命名 Worker 实例"),
    workerType: draft.workerType.trim() || "general",
    projectId: draft.projectId.trim() || "default",
    workspaceId: draft.workspaceId.trim() || "primary",
    status,
    source,
    toolProfile: draft.toolProfile.trim() || "standard",
    modelAlias: draft.modelAlias.trim() || "main",
    autonomy: draft.autonomy.trim() || "guided",
    summary:
      draft.summary.trim() ||
      (draft.kind === "template" ? "暂未补充这个模板的使用场景。" : "暂未补充这个实例的职责说明。"),
    tags: uniqueStrings(draft.tags),
    selectedTools: uniqueStrings(draft.selectedTools),
    taskCount: Math.max(0, Number(draft.taskCount) || 0),
    waitingCount: Math.max(0, Number(draft.waitingCount) || 0),
    mergeReadyCount: Math.max(0, Number(draft.mergeReadyCount) || 0),
    lastUpdated: new Date().toISOString(),
  };
}

function splitList(values: string[]): [string[], string[]] {
  if (values.length < 2) {
    const left = values.length === 0 ? ["planner"] : values;
    return [left, ["handoff"]];
  }
  const middle = Math.ceil(values.length / 2);
  return [values.slice(0, middle), values.slice(middle)];
}

function forkTemplateFromAgent(agent: WorkAgentItem): WorkAgentDraft {
  return {
    ...toDraft(agent),
    id: "",
    kind: "template",
    name: agent.name.includes("模板") ? agent.name : `${agent.name} 模板`,
    source: "manual",
    status: "draft",
    summary: `基于「${agent.name}」整理出的模板，方便以后复用。`,
    taskCount: "0",
    waitingCount: "0",
    mergeReadyCount: "0",
  };
}

function createInstanceFromTemplate(agent: WorkAgentItem): WorkAgentDraft {
  return {
    ...toDraft(agent),
    id: "",
    kind: "instance",
    name: agent.name.replace(/\s*模板$/, "").trim() || `${formatWorkerType(agent.workerType)} 实例`,
    source: "manual",
    status: "syncing",
    summary: `基于模板「${agent.name}」创建的实例草案。`,
    taskCount: "0",
    waitingCount: "0",
    mergeReadyCount: "0",
  };
}

export default function AgentCenter() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const selector = snapshot!.resources.project_selector;
  const configValue = toRecord(snapshot!.resources.config.current_value);
  const setup = snapshot!.resources.setup_governance;
  const skillGovernance = snapshot!.resources.skill_governance;
  const policyProfiles = snapshot!.resources.policy_profiles.profiles;
  const capabilityWorkerProfiles = snapshot!.resources.capability_pack.pack.worker_profiles;
  const rootAgentProfilesDocument =
    snapshot!.resources.worker_profiles ?? EMPTY_ROOT_AGENT_PROFILES;
  const rootAgentProfiles = rootAgentProfilesDocument.profiles ?? [];
  const toolLabelByName = Object.fromEntries(
    snapshot!.resources.capability_pack.pack.tools.map((tool) => [tool.tool_name, tool.label])
  ) as Record<string, string>;
  const workerProfilesByType = Object.fromEntries(
    capabilityWorkerProfiles.map((profile) => [profile.worker_type, profile])
  ) as Record<string, WorkerCapabilityProfile>;
  const modelAliasOptions = uniqueStrings([
    ...Object.keys(toRecord(configValue.model_aliases)),
    "main",
    "cheap",
    "reasoning",
  ]);
  const toolProfileOptions = uniqueStrings([
    ...policyProfiles.map((profile) => profile.allowed_tool_profile),
    "minimal",
    "standard",
    "privileged",
  ]);
  const availableProjects = selector.available_projects;
  const availableWorkspaces = selector.available_workspaces;
  const primarySeed = buildPrimaryAgentSeed(snapshot!);
  const primarySeedSyncKey = JSON.stringify(primarySeed);
  const workAgentSeeds = buildWorkAgentSeeds(snapshot!);
  const workAgentSeedSyncKey = JSON.stringify(workAgentSeeds);
  const initialPrimary = primarySeed;
  const initialAgents = workAgentSeeds;
  const initialSelection =
    initialAgents.find((agent) => agent.kind === "instance") ??
    initialAgents.find((agent) => agent.kind === "template") ??
    null;
  const [savedPrimary, setSavedPrimary] = useState(initialPrimary);
  const [primaryDraft, setPrimaryDraft] = useState(initialPrimary);
  const [primaryReview, setPrimaryReview] = useState<SetupReviewSummary>(setup.review);
  const [workAgents, setWorkAgents] = useState<WorkAgentItem[]>(initialAgents);
  const [selectedWorkAgentId, setSelectedWorkAgentId] = useState(initialSelection?.id ?? "");
  const [selectedWorkAgentIds, setSelectedWorkAgentIds] = useState<string[]>(
    initialSelection?.kind === "instance" ? [initialSelection.id] : []
  );
  const [editorMode, setEditorMode] = useState<"edit" | "create">(
    initialSelection ? "edit" : "create"
  );
  const [activeWorkspaceView, setActiveWorkspaceView] =
    useState<AgentWorkspaceView>("templates");
  const [activeCatalog, setActiveCatalog] = useState<WorkerCatalogView>(
    initialSelection?.kind === "template" ? "templates" : "instances"
  );
  const [workDraft, setWorkDraft] = useState<WorkAgentDraft>(() =>
    initialSelection ? toDraft(initialSelection) : buildEmptyWorkDraft(initialPrimary, "instance")
  );
  const [projectFilter, setProjectFilter] = useState("all");
  const [contextProjectId, setContextProjectId] = useState(selector.current_project_id);
  const [contextWorkspaceId, setContextWorkspaceId] = useState(selector.current_workspace_id);
  const [searchQuery, setSearchQuery] = useState("");
  const [flashMessage, setFlashMessage] = useState(
    "先确认默认配置，再从右侧启动任务或查看当前运行状态。"
  );
  const [selectedRootAgentId, setSelectedRootAgentId] = useState(
    rootAgentProfiles[0]?.profile_id ?? ""
  );
  const [rootAgentDraft, setRootAgentDraft] = useState<RootAgentStudioDraft>(() =>
    buildRootAgentStudioDraft(rootAgentProfiles[0] ?? null, selector)
  );
  const [rootAgentReview, setRootAgentReview] = useState<RootAgentReviewResult | null>(null);
  const [rootAgentRevisions, setRootAgentRevisions] = useState<WorkerProfileRevisionItem[]>([]);
  const [rootAgentRevisionLoading, setRootAgentRevisionLoading] = useState(false);
  const [rootAgentRevisionError, setRootAgentRevisionError] = useState("");
  const [rootAgentSpawnObjective, setRootAgentSpawnObjective] = useState("");
  const [rootAgentEditorMode, setRootAgentEditorMode] = useState<"existing" | "create">(
    rootAgentProfiles[0] ? "existing" : "create"
  );
  const selectedRootAgentProfile =
    rootAgentProfiles.find((profile) => profile.profile_id === selectedRootAgentId) ?? null;
  const rootAgentDraftDirty =
    JSON.stringify(buildRootAgentPayload(rootAgentDraft)) !==
    JSON.stringify(
      buildRootAgentPayload(buildRootAgentStudioDraft(selectedRootAgentProfile, selector))
    );
  const rootAgentRevisionSyncKey = [
    selectedRootAgentId,
    selectedRootAgentProfile?.active_revision ?? 0,
    selectedRootAgentProfile?.draft_revision ?? 0,
  ].join(":");
  const deferredSearch = useDeferredValue(searchQuery);

  useEffect(() => {
    const nextPrimary = buildPrimaryAgentSeed(snapshot!);
    setSavedPrimary(nextPrimary);
    setPrimaryDraft(nextPrimary);
  }, [primarySeedSyncKey]);

  useEffect(() => {
    const nextAgents = buildWorkAgentSeeds(snapshot!);
    const nextPrimary = buildPrimaryAgentSeed(snapshot!);
    const nextSelection =
      nextAgents.find((agent) => agent.kind === "instance") ??
      nextAgents.find((agent) => agent.kind === "template") ??
      null;
    setWorkAgents(nextAgents);
    setSelectedWorkAgentId(nextSelection?.id ?? "");
    setSelectedWorkAgentIds(nextSelection?.kind === "instance" ? [nextSelection.id] : []);
    setEditorMode(nextSelection ? "edit" : "create");
    setActiveCatalog(nextSelection?.kind === "template" ? "templates" : "instances");
    setWorkDraft(
      nextSelection ? toDraft(nextSelection) : buildEmptyWorkDraft(nextPrimary, "instance")
    );
    setContextProjectId(selector.current_project_id);
    setContextWorkspaceId(selector.current_workspace_id);
  }, [primarySeedSyncKey, selector.current_project_id, selector.current_workspace_id, workAgentSeedSyncKey]);

  useEffect(() => {
    setPrimaryReview(setup.review);
  }, [setup.generated_at]);

  useEffect(() => {
    if (rootAgentEditorMode === "create") {
      return;
    }
    const nextSelectedId =
      rootAgentProfiles.find((profile) => profile.profile_id === selectedRootAgentId)?.profile_id ??
      rootAgentProfiles.find((profile) => profile.origin_kind !== "builtin")?.profile_id ??
      rootAgentProfiles[0]?.profile_id ??
      "";
    const nextSelectedProfile =
      rootAgentProfiles.find((profile) => profile.profile_id === nextSelectedId) ?? null;
    if (nextSelectedProfile === null) {
      setSelectedRootAgentId("");
      setRootAgentDraft(buildRootAgentStudioDraft(null, selector));
      setRootAgentReview(null);
      setRootAgentEditorMode("create");
      return;
    }
    if (nextSelectedId !== selectedRootAgentId) {
      setSelectedRootAgentId(nextSelectedId);
    }
    if (nextSelectedId !== selectedRootAgentId || !rootAgentDraftDirty) {
      setRootAgentDraft(buildRootAgentStudioDraft(nextSelectedProfile, selector));
      setRootAgentReview(null);
    }
  }, [
    rootAgentEditorMode,
    rootAgentProfilesDocument.generated_at,
    selector.current_project_id,
    selector.current_workspace_id,
    selectedRootAgentId,
    rootAgentDraftDirty,
  ]);

  useEffect(() => {
    if (!selectedRootAgentId) {
      setRootAgentRevisions([]);
      setRootAgentRevisionError("");
      setRootAgentRevisionLoading(false);
      return;
    }
    let cancelled = false;
    setRootAgentRevisionLoading(true);
    setRootAgentRevisionError("");
    void fetchWorkerProfileRevisions(selectedRootAgentId)
      .then((document) => {
        if (cancelled) {
          return;
        }
        setRootAgentRevisions(document.revisions ?? []);
      })
      .catch((error) => {
        if (cancelled) {
          return;
        }
        setRootAgentRevisionError(error instanceof Error ? error.message : "revision 加载失败");
        setRootAgentRevisions([]);
      })
      .finally(() => {
        if (!cancelled) {
          setRootAgentRevisionLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [rootAgentRevisionSyncKey]);

  const currentProject =
    availableProjects.find((project) => project.project_id === selector.current_project_id) ?? null;
  const currentWorkspace =
    availableWorkspaces.find((workspace) => workspace.workspace_id === selector.current_workspace_id) ??
    null;
  const primaryProjectOptions = projectSelectOptions(availableProjects, primaryDraft.projectId);
  const primaryWorkspaceOptions = workspaceSelectOptions(
    availableWorkspaces,
    primaryDraft.projectId,
    primaryDraft.workspaceId
  );
  const workProjectOptions = projectSelectOptions(availableProjects, workDraft.projectId);
  const workWorkspaceOptions = workspaceSelectOptions(
    availableWorkspaces,
    workDraft.projectId,
    workDraft.workspaceId
  );
  const availableContextWorkspaces = availableWorkspaces.filter(
    (workspace) => workspace.project_id === contextProjectId
  );
  const workInstances = workAgents.filter((agent) => agent.kind === "instance");
  const workTemplates = workAgents.filter((agent) => agent.kind === "template");
  const selectedWorkAgent =
    editorMode === "edit" ? workAgents.find((agent) => agent.id === selectedWorkAgentId) ?? null : null;
  const editingKind =
    editorMode === "create" ? workDraft.kind : selectedWorkAgent?.kind ?? workDraft.kind;
  const currentWorkerProfile = workerProfilesByType[workDraft.workerType];
  const normalizedSearch = deferredSearch.trim().toLowerCase();

  const visibleInstances = workInstances.filter((agent) => {
    const matchesProject = projectFilter === "all" || agent.projectId === projectFilter;
    const matchesQuery =
      normalizedSearch.length === 0 ||
      [
        agent.name,
        formatWorkerType(agent.workerType),
        agent.summary,
        agent.projectId,
        agent.workspaceId,
        ...agent.tags,
        ...agent.selectedTools,
      ]
        .join(" ")
        .toLowerCase()
        .includes(normalizedSearch);
    return matchesProject && matchesQuery;
  });

  const visibleTemplates = workTemplates.filter((agent) => {
    const matchesProject = projectFilter === "all" || agent.projectId === projectFilter;
    const matchesQuery =
      normalizedSearch.length === 0 ||
      [
        agent.name,
        formatWorkerType(agent.workerType),
        agent.summary,
        agent.projectId,
        agent.workspaceId,
        ...agent.tags,
        ...agent.selectedTools,
      ]
        .join(" ")
        .toLowerCase()
        .includes(normalizedSearch);
    return matchesProject && matchesQuery;
  });

  const visibleCatalogItems = activeCatalog === "instances" ? visibleInstances : visibleTemplates;
  const activeWorkAgents = workInstances.filter((agent) => agent.status === "active").length;
  const attentionWorkAgents = workInstances.filter((agent) => agent.status === "attention").length;
  const primaryDirty = JSON.stringify(savedPrimary) !== JSON.stringify(primaryDraft);
  const workDirty =
    editorMode === "create" ||
    (selectedWorkAgent !== null && JSON.stringify(toDraft(selectedWorkAgent)) !== JSON.stringify(workDraft));
  const pendingChanges = Number(primaryDirty) + Number(workDirty);
  const projectFilterIds = uniqueStrings([
    ...availableProjects.map((project) => project.project_id),
    ...workAgents.map((agent) => agent.projectId),
  ]);
  const projectFilterStats = projectFilterIds.map((projectId) => ({
    projectId,
    name: findProjectName(availableProjects, projectId),
    instanceCount: workInstances.filter((agent) => agent.projectId === projectId).length,
    templateCount: workTemplates.filter((agent) => agent.projectId === projectId).length,
  }));
  const currentPolicy =
    policyProfiles.find((profile) => profile.profile_id === primaryDraft.policyProfileId) ?? null;
  const butlerBusy =
    busyActionId === "setup.review" || busyActionId === "setup.apply";
  const recallPresetLabel = detectRecallPreset(primaryDraft.memoryRecall);
  const selectedTemplateUsageCount =
    selectedWorkAgent?.kind === "template"
      ? workInstances.filter((agent) => agent.workerType === selectedWorkAgent.workerType).length
      : 0;
  const recommendedTags = uniqueStrings([
    ...(currentWorkerProfile?.capabilities ?? []),
    ...workDraft.tags,
    "planner",
    "handoff",
    "memory",
    "frontend",
    "research",
    "watchdog",
  ]).slice(0, 10);
  const recommendedTools = uniqueStrings([
    ...(currentWorkerProfile?.default_tool_groups ?? []),
    ...snapshot!.resources.capability_pack.pack.fallback_toolset,
    ...workDraft.selectedTools,
  ]).slice(0, 10);
  const rootAgentActiveWorkCount = rootAgentProfiles.reduce(
    (sum, profile) => sum + Math.max(profile.dynamic_context.active_work_count || 0, 0),
    0
  );
  const rootAgentRunningWorkCount = rootAgentProfiles.reduce(
    (sum, profile) => sum + Math.max(profile.dynamic_context.running_work_count || 0, 0),
    0
  );
  const rootAgentAttentionWorkCount = rootAgentProfiles.reduce(
    (sum, profile) => sum + Math.max(profile.dynamic_context.attention_work_count || 0, 0),
    0
  );
  const builtinRootAgentProfiles = rootAgentProfiles.filter(
    (profile) => profile.origin_kind === "builtin"
  );
  const customRootAgentProfiles = rootAgentProfiles.filter(
    (profile) => profile.origin_kind !== "builtin"
  );
  const latestRootAgentUpdate =
    rootAgentProfiles
      .map((profile) => profile.dynamic_context.updated_at ?? "")
      .sort((left, right) => right.localeCompare(left))[0] || null;
  const selectedRootAgentWorks = snapshot!.resources.delegation.works.filter((work) => {
    if (!selectedRootAgentProfile) {
      return false;
    }
    if (work.requested_worker_profile_id === selectedRootAgentProfile.profile_id) {
      return true;
    }
    return (
      selectedRootAgentProfile.origin_kind === "builtin" &&
      !work.requested_worker_profile_id &&
      work.selected_worker_type === selectedRootAgentProfile.static_config.base_archetype
    );
  });
  const rootAgentProjectOptions = projectSelectOptions(availableProjects, rootAgentDraft.projectId);
  const rootAgentArchetypeProfile = workerProfilesByType[rootAgentDraft.baseArchetype] ?? null;
  const rootAgentSuggestedToolGroups = uniqueStrings([
    ...(rootAgentArchetypeProfile?.default_tool_groups ?? []),
    ...(selectedRootAgentProfile?.static_config.default_tool_groups ?? []),
  ]).slice(0, 8);
  const rootAgentSuggestedTools = uniqueStrings([
    ...(selectedRootAgentProfile?.dynamic_context.current_selected_tools ?? []),
    ...(selectedRootAgentProfile?.static_config.selected_tools ?? []),
    ...snapshot!.resources.capability_pack.pack.fallback_toolset,
  ]).slice(0, 10);
  const rootAgentSuggestedRuntimeKinds = uniqueStrings([
    ...(rootAgentArchetypeProfile?.runtime_kinds ?? []),
    ...parseStudioList(rootAgentDraft.runtimeKindsText),
    "worker",
    "subagent",
    "acp_runtime",
    "graph_agent",
  ]);
  const rootAgentSuggestedTags = uniqueStrings([
    ...(rootAgentArchetypeProfile?.capabilities ?? []),
    ...parseStudioList(rootAgentDraft.tagsText),
    "singleton",
    "router",
    "workspace",
  ]).slice(0, 10);
  const rootAgentReviewDiff = rootAgentReview?.diff.changed_fields ?? [];
  const selectedRootAgentDynamicContext = selectedRootAgentProfile?.dynamic_context ?? null;
  const selectedRootAgentCapabilities = selectedRootAgentProfile?.capabilities ?? [];
  const selectedRootAgentWarnings = selectedRootAgentProfile?.warnings ?? [];
  const selectedRootAgentEditable = selectedRootAgentProfile?.editable ?? true;
  const selectedRootAgentIsBuiltin = selectedRootAgentProfile?.origin_kind === "builtin";
  const selectedRootAgentDisplayStatus = selectedRootAgentProfile?.status ?? "draft";
  const rootAgentSummary = toRecord(rootAgentProfilesDocument.summary);
  const defaultRootAgentId = readNestedString(rootAgentSummary, ["default_profile_id"]);
  const defaultRootAgentName = formatWorkerTemplateName(
    readNestedString(rootAgentSummary, ["default_profile_name"])
  );
  const selectedRootAgentDisplayName = formatWorkerTemplateName(
    selectedRootAgentProfile?.name ?? rootAgentDraft.name,
    selectedRootAgentProfile?.static_config.base_archetype ?? rootAgentDraft.baseArchetype
  );
  const selectedRootAgentIsDefault =
    Boolean(selectedRootAgentProfile?.profile_id) &&
    selectedRootAgentProfile?.profile_id === defaultRootAgentId;
  const selectedRootAgentMountedTools =
    selectedRootAgentDynamicContext?.current_mounted_tools ?? [];
  const selectedRootAgentBlockedTools =
    selectedRootAgentDynamicContext?.current_blocked_tools ?? [];
  const selectedRootAgentDiscoveryEntrypoints =
    selectedRootAgentDynamicContext?.current_discovery_entrypoints ?? [];

  function updatePrimary<Key extends keyof PrimaryAgentDraft>(
    key: Key,
    value: PrimaryAgentDraft[Key]
  ) {
    setPrimaryDraft((current) => ({ ...current, [key]: value }));
  }

  function updatePrimaryProject(projectId: string) {
    setPrimaryDraft((current) => ({
      ...current,
      projectId,
      workspaceId: normalizeWorkspaceForProject(
        availableWorkspaces,
        projectId,
        current.workspaceId
      ),
    }));
  }

  function updatePrimaryMemoryAccess(
    key: keyof PrimaryMemoryAccessDraft,
    value: boolean
  ) {
    setPrimaryDraft((current) => ({
      ...current,
      memoryAccessPolicy: {
        ...current.memoryAccessPolicy,
        [key]: value,
      },
    }));
  }

  function applyPrimaryMemoryPreset(presetId: string) {
    const preset = MEMORY_RECALL_PRESETS.find((item) => item.id === presetId);
    if (!preset) {
      return;
    }
    setPrimaryDraft((current) => ({
      ...current,
      memoryRecall: { ...preset.values },
    }));
  }

  function buildPrimarySetupDraft() {
    return {
      config: snapshot!.resources.config.current_value,
      policy_profile_id: primaryDraft.policyProfileId,
      agent_profile: buildPrimaryAgentPayload({
        ...primaryDraft,
        scope: primaryDraft.scope || "project",
      }),
      skill_selection: buildSkillSelectionPayload(skillGovernance.items),
      secret_values: {},
    };
  }

  async function handleReviewPrimary() {
    const result = await submitAction("setup.review", {
      draft: buildPrimarySetupDraft(),
    });
    const nextReview = result?.data.review;
    if (nextReview && typeof nextReview === "object" && !Array.isArray(nextReview)) {
      const parsedReview = nextReview as SetupReviewSummary;
      setPrimaryReview(parsedReview);
      setFlashMessage(
        parsedReview.ready
          ? "Butler 配置检查通过，可以直接保存。"
          : `Butler 配置还需要处理 ${parsedReview.blocking_reasons.length} 个问题。`
      );
    }
  }

  async function handleApplyPrimary() {
    const draft = buildPrimarySetupDraft();
    const reviewResult = await submitAction("setup.review", { draft });
    const nextReview = reviewResult?.data.review;
    if (nextReview && typeof nextReview === "object" && !Array.isArray(nextReview)) {
      const parsedReview = nextReview as SetupReviewSummary;
      setPrimaryReview(parsedReview);
      if (!parsedReview.ready) {
        setFlashMessage("Butler 配置还没准备好，先处理提示里的阻塞项。");
        return;
      }
    } else if (!primaryReview.ready) {
      setFlashMessage("Butler 配置还没准备好，先执行一次检查。");
      return;
    }

    const result = await submitAction("setup.apply", { draft });
    const appliedReview = result?.data.review;
    if (appliedReview && typeof appliedReview === "object" && !Array.isArray(appliedReview)) {
      setPrimaryReview(appliedReview as SetupReviewSummary);
    }
    if (result) {
      setFlashMessage("Butler 配置已保存，当前实例会按新的默认行为继续工作。");
    }
  }

  function updateWorkDraft<Key extends keyof WorkAgentDraft>(
    key: Key,
    value: WorkAgentDraft[Key]
  ) {
    setWorkDraft((current) => ({ ...current, [key]: value }));
  }

  function updateWorkProject(projectId: string) {
    setWorkDraft((current) => ({
      ...current,
      projectId,
      workspaceId: normalizeWorkspaceForProject(
        availableWorkspaces,
        projectId,
        current.workspaceId
      ),
    }));
  }

  function updateWorkerType(workerType: string) {
    setWorkDraft((current) => {
      const profile = workerProfilesByType[workerType];
      return {
        ...current,
        workerType,
        modelAlias:
          current.modelAlias || profile?.default_model_alias || savedPrimary.modelAlias,
        toolProfile:
          current.toolProfile || profile?.default_tool_profile || savedPrimary.toolProfile,
        tags: current.tags.length > 0 ? current.tags : profile?.capabilities.slice(0, 3) ?? current.tags,
        selectedTools:
          current.selectedTools.length > 0
            ? current.selectedTools
            : profile?.default_tool_groups.slice(0, 4) ?? current.selectedTools,
      };
    });
  }

  function toggleDraftToken(key: "tags" | "selectedTools", value: string) {
    setWorkDraft((current) => {
      const exists = current[key].includes(value);
      return {
        ...current,
        [key]: exists
          ? current[key].filter((item) => item !== value)
          : [...current[key], value],
      };
    });
  }

  function openWorkspaceView(view: AgentWorkspaceView) {
    setActiveWorkspaceView(view);
    if (view === "workers" && activeCatalog !== "instances") {
      openCatalog("instances");
    }
  }

  function openCatalog(nextCatalog: WorkerCatalogView) {
    const nextKind = nextCatalog === "instances" ? "instance" : "template";
    setActiveCatalog(nextCatalog);
    if (editorMode === "create" && workDraft.kind === nextKind) {
      return;
    }
    const nextSelection = workAgents.find((agent) => agent.kind === nextKind) ?? null;
    if (!nextSelection) {
      setEditorMode("create");
      setSelectedWorkAgentId("");
      setSelectedWorkAgentIds([]);
      setWorkDraft(buildEmptyWorkDraft(primaryDraft, nextKind));
      return;
    }
    setSelectedWorkAgentId(nextSelection.id);
    setSelectedWorkAgentIds(nextSelection.kind === "instance" ? [nextSelection.id] : []);
    setEditorMode("edit");
    setWorkDraft(toDraft(nextSelection));
  }

  function selectWorkAgent(agent: WorkAgentItem) {
    startTransition(() => {
      setActiveCatalog(agent.kind === "instance" ? "instances" : "templates");
      setSelectedWorkAgentId(agent.id);
      if (agent.kind === "instance") {
        setSelectedWorkAgentIds((current) => (current.length === 0 ? [agent.id] : current));
      } else {
        setSelectedWorkAgentIds([]);
      }
      setEditorMode("edit");
      setWorkDraft(toDraft(agent));
      setFlashMessage(
        agent.kind === "instance"
          ? `正在查看实例「${agent.name}」，这里改的是当前分工与落点。`
          : `正在查看模板「${agent.name}」，这里改的是以后新建时的默认起点。`
      );
    });
  }

  function toggleWorkAgentSelection(agentId: string) {
    setSelectedWorkAgentIds((current) =>
      current.includes(agentId)
        ? current.filter((item) => item !== agentId)
        : [...current, agentId]
    );
  }

  function handleCreateDraft(kind: WorkUnitKind) {
    setActiveWorkspaceView(kind === "instance" ? "workers" : "templates");
    setActiveCatalog(kind === "instance" ? "instances" : "templates");
    setEditorMode("create");
    setSelectedWorkAgentId("");
    setSelectedWorkAgentIds([]);
    setWorkDraft(buildEmptyWorkDraft(primaryDraft, kind));
    setFlashMessage(
      kind === "instance"
        ? "先说明这个 Worker 实例要负责什么，再决定它落在哪个 Project 和 Workspace。"
        : "草案只用来沉淀新的默认做法，不会直接影响已经在运行的实例。"
    );
  }

  function handleResetPrimary() {
    setPrimaryDraft(savedPrimary);
    setFlashMessage("Butler 草案已恢复到上一次保存状态。");
  }

  function handleSaveWorkAgent() {
    const nextAgent = hydrateWorkDraft(workDraft);
    const isForkingSystemTemplate =
      editorMode === "edit" &&
      selectedWorkAgent?.kind === "template" &&
      selectedWorkAgent.source === "capability";

    if (editorMode === "create" || isForkingSystemTemplate) {
      const createdAgent = {
        ...nextAgent,
        id: nextAgent.id || `manual:${nextAgent.kind}:${Date.now().toString(36)}`,
        source: "manual" as const,
      };
      setWorkAgents((current) => [createdAgent, ...current]);
      setSelectedWorkAgentId(createdAgent.id);
      setSelectedWorkAgentIds(createdAgent.kind === "instance" ? [createdAgent.id] : []);
      setEditorMode("edit");
      setActiveCatalog(createdAgent.kind === "instance" ? "instances" : "templates");
      setWorkDraft(toDraft(createdAgent));
      setFlashMessage(
        createdAgent.kind === "instance"
          ? `已创建实例草案「${createdAgent.name}」。`
          : isForkingSystemTemplate
            ? `已基于系统模板另存一份实例草案「${createdAgent.name}」。`
            : `已创建实例草案「${createdAgent.name}」。`
      );
      return;
    }

    setWorkAgents((current) =>
      current.map((agent) => (agent.id === nextAgent.id ? nextAgent : agent))
    );
    setActiveCatalog(nextAgent.kind === "instance" ? "instances" : "templates");
    setWorkDraft(toDraft(nextAgent));
    setFlashMessage(
      nextAgent.kind === "instance"
        ? `已更新实例「${nextAgent.name}」。`
        : `已更新实例草案「${nextAgent.name}」。`
    );
  }

  function handleResetWorkAgent() {
    if (editorMode === "create") {
      setWorkDraft(buildEmptyWorkDraft(primaryDraft, workDraft.kind));
      setFlashMessage(workDraft.kind === "instance" ? "已重置实例草案。" : "已重置草案内容。");
      return;
    }
    if (!selectedWorkAgent) {
      return;
    }
    setWorkDraft(toDraft(selectedWorkAgent));
    setFlashMessage(
      selectedWorkAgent.kind === "instance"
        ? `已撤回实例「${selectedWorkAgent.name}」的未保存改动。`
        : `已撤回草案「${selectedWorkAgent.name}」的未保存改动。`
    );
  }

  function handleMergeWorkAgents() {
    if (selectedWorkAgentIds.length < 2) {
      return;
    }
    const mergeTargets = workInstances.filter((agent) => selectedWorkAgentIds.includes(agent.id));
    if (mergeTargets.length < 2) {
      return;
    }
    const mergedAgent: WorkAgentItem = {
      id: `manual:instance:merge:${Date.now().toString(36)}`,
      kind: "instance",
      name: `${mergeTargets[0].name} + ${mergeTargets.length - 1}`,
      workerType: mergeTargets[0].workerType,
      projectId:
        uniqueStrings(mergeTargets.map((agent) => agent.projectId)).length === 1
          ? mergeTargets[0].projectId
          : "cross-project",
      workspaceId:
        uniqueStrings(mergeTargets.map((agent) => agent.workspaceId)).length === 1
          ? mergeTargets[0].workspaceId
          : "shared/merge-lane",
      status: "syncing",
      source: "manual",
      toolProfile: mergeTargets.some((agent) => agent.toolProfile === "privileged")
        ? "privileged"
        : mergeTargets[0].toolProfile,
      modelAlias: mergeTargets.some((agent) => agent.modelAlias === "reasoning")
        ? "reasoning"
        : mergeTargets[0].modelAlias,
      autonomy: mergeTargets.some((agent) => agent.autonomy === "free-loop")
        ? "free-loop"
        : "guided",
      summary: `由 ${mergeTargets.map((agent) => agent.name).join("、")} 合并而来，方便收口同类工作。`,
      tags: uniqueStrings(mergeTargets.flatMap((agent) => agent.tags)),
      selectedTools: uniqueStrings(mergeTargets.flatMap((agent) => agent.selectedTools)),
      taskCount: mergeTargets.reduce((sum, agent) => sum + agent.taskCount, 0),
      waitingCount: mergeTargets.reduce((sum, agent) => sum + agent.waitingCount, 0),
      mergeReadyCount: mergeTargets.reduce((sum, agent) => sum + agent.mergeReadyCount, 0),
      lastUpdated: new Date().toISOString(),
    };
    setWorkAgents((current) => [
      mergedAgent,
      ...current.filter((agent) => !selectedWorkAgentIds.includes(agent.id)),
    ]);
    setActiveCatalog("instances");
    setActiveWorkspaceView("workers");
    setSelectedWorkAgentId(mergedAgent.id);
    setSelectedWorkAgentIds([mergedAgent.id]);
    setEditorMode("edit");
    setWorkDraft(toDraft(mergedAgent));
    setFlashMessage(`已合并 ${mergeTargets.length} 个 Worker 实例。`);
  }

  function handleSplitWorkAgent() {
    if (!selectedWorkAgent || selectedWorkAgent.kind !== "instance") {
      return;
    }
    const [leftTags, rightTags] = splitList(selectedWorkAgent.tags);
    const [leftTools, rightTools] = splitList(selectedWorkAgent.selectedTools);
    const leftAgent: WorkAgentItem = {
      ...selectedWorkAgent,
      id: `manual:instance:split-a:${Date.now().toString(36)}`,
      name: `${selectedWorkAgent.name} · A`,
      workspaceId: `${selectedWorkAgent.workspaceId}/a`,
      status: "syncing",
      source: "manual",
      summary: `${selectedWorkAgent.summary} 拆分后负责前半段任务流。`,
      tags: leftTags,
      selectedTools: leftTools,
      taskCount: Math.max(1, Math.ceil(selectedWorkAgent.taskCount / 2)),
      waitingCount: Math.ceil(selectedWorkAgent.waitingCount / 2),
      mergeReadyCount: Math.ceil(selectedWorkAgent.mergeReadyCount / 2),
      lastUpdated: new Date().toISOString(),
    };
    const rightAgent: WorkAgentItem = {
      ...selectedWorkAgent,
      id: `manual:instance:split-b:${Date.now().toString(36)}`,
      name: `${selectedWorkAgent.name} · B`,
      workspaceId: `${selectedWorkAgent.workspaceId}/b`,
      status: "syncing",
      source: "manual",
      summary: `${selectedWorkAgent.summary} 拆分后负责后半段任务流。`,
      tags: rightTags,
      selectedTools: rightTools,
      taskCount: Math.max(0, Math.floor(selectedWorkAgent.taskCount / 2)),
      waitingCount: Math.floor(selectedWorkAgent.waitingCount / 2),
      mergeReadyCount: Math.floor(selectedWorkAgent.mergeReadyCount / 2),
      lastUpdated: new Date().toISOString(),
    };
    setWorkAgents((current) => [
      leftAgent,
      rightAgent,
      ...current.filter((agent) => agent.id !== selectedWorkAgent.id),
    ]);
    setActiveCatalog("instances");
    setActiveWorkspaceView("workers");
    setSelectedWorkAgentId(leftAgent.id);
    setSelectedWorkAgentIds([leftAgent.id, rightAgent.id]);
    setEditorMode("edit");
    setWorkDraft(toDraft(leftAgent));
    setFlashMessage(`已把实例「${selectedWorkAgent.name}」拆成两个更小的执行单元。`);
  }

  function handleForkTemplateFromInstance() {
    if (!selectedWorkAgent || selectedWorkAgent.kind !== "instance") {
      return;
    }
    setActiveWorkspaceView("workers");
    setActiveCatalog("templates");
    setEditorMode("create");
    setSelectedWorkAgentId("");
    setSelectedWorkAgentIds([]);
    setWorkDraft(forkTemplateFromAgent(selectedWorkAgent));
    setFlashMessage("已把当前实例复制成草案。清理掉只属于当前运行态的内容后再保存。");
  }

  function handleCreateInstanceFromTemplate() {
    if (!selectedWorkAgent || selectedWorkAgent.kind !== "template") {
      return;
    }
    setActiveWorkspaceView("workers");
    setActiveCatalog("instances");
    setEditorMode("create");
    setSelectedWorkAgentId("");
    setSelectedWorkAgentIds([]);
    setWorkDraft(createInstanceFromTemplate(selectedWorkAgent));
    setFlashMessage("已按当前草案生成一个新的 Worker 实例草案。");
  }

  async function handleSwitchProjectContext() {
    const selectedWorkspace =
      availableContextWorkspaces.find(
        (workspace) => workspace.workspace_id === contextWorkspaceId
      )?.workspace_id ?? availableContextWorkspaces[0]?.workspace_id;
    if (!selectedWorkspace) {
      return;
    }
    const result = await submitAction("project.select", {
      project_id: contextProjectId,
      workspace_id: selectedWorkspace,
    });
    if (result) {
      setFlashMessage("当前视角已切到新的 Project / Workspace，列表会随之刷新。");
    }
  }

  async function handleSwitchToRootAgentContext(profile: WorkerProfileItem) {
    const projectId =
      profile.dynamic_context.active_project_id || profile.project_id || selector.current_project_id;
    const workspaceId =
      profile.dynamic_context.active_workspace_id ||
      normalizeWorkspaceForProject(
        availableWorkspaces,
        projectId,
        selector.current_workspace_id
      );
    if (!workspaceId) {
      return;
    }
    const result = await submitAction("project.select", {
      project_id: projectId,
      workspace_id: workspaceId,
    });
    if (result) {
      startTransition(() => {
        setContextProjectId(projectId);
        setContextWorkspaceId(workspaceId);
      });
      setFlashMessage(
        `当前视角已切到模板「${formatWorkerTemplateName(
          profile.name,
          profile.static_config.base_archetype
        )}」所在上下文。`
      );
    }
  }

  function selectRootAgentProfile(profile: WorkerProfileItem | null) {
    startTransition(() => {
      setRootAgentEditorMode(profile ? "existing" : "create");
      setSelectedRootAgentId(profile?.profile_id ?? "");
      setRootAgentDraft(buildRootAgentStudioDraft(profile, selector));
      setRootAgentReview(null);
    });
  }

  function updateRootAgentDraft<Key extends keyof RootAgentStudioDraft>(
    key: Key,
    value: RootAgentStudioDraft[Key]
  ) {
    setRootAgentDraft((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function updateRootAgentProject(projectId: string) {
    setRootAgentDraft((current) => ({
      ...current,
      projectId,
    }));
  }

  function appendRootAgentDraftValue(
    key:
      | "defaultToolGroupsText"
      | "selectedToolsText"
      | "runtimeKindsText"
      | "policyRefsText"
      | "instructionOverlaysText"
      | "tagsText",
    value: string
  ) {
    setRootAgentDraft((current) => ({
      ...current,
      [key]: appendStudioListValue(current[key], value),
    }));
  }

  function applyRootAgentArchetypeDefaults() {
    const archetype = workerProfilesByType[rootAgentDraft.baseArchetype];
    if (!archetype) {
      return;
    }
    setRootAgentDraft((current) => ({
      ...current,
      modelAlias: current.modelAlias || archetype.default_model_alias,
      toolProfile: current.toolProfile || archetype.default_tool_profile,
      defaultToolGroupsText:
        parseStudioList(current.defaultToolGroupsText).length > 0
          ? current.defaultToolGroupsText
          : joinStudioList(archetype.default_tool_groups),
      runtimeKindsText:
        parseStudioList(current.runtimeKindsText).length > 0
          ? current.runtimeKindsText
          : joinStudioList(archetype.runtime_kinds),
      tagsText:
        parseStudioList(current.tagsText).length > 0
          ? current.tagsText
          : joinStudioList(archetype.capabilities),
    }));
    setFlashMessage(`已套用 ${formatWorkerType(rootAgentDraft.baseArchetype)} 模板的默认配置。`);
  }

  async function handleCreateRootAgentDraftFromTemplate(profile: WorkerProfileItem) {
    setActiveWorkspaceView("templates");
    const name =
      profile.origin_kind === "builtin" ? `${profile.name} Copy` : `${profile.name} 副本`;
    const result = await submitAction("worker_profile.clone", {
      source_profile_id: profile.profile_id,
      name,
    });
    const payload = result?.data ?? {};
    const nextDraft = buildRootAgentStudioDraftFromReview(payload.review, selector);
    const nextProfileId =
      typeof payload.profile_id === "string" ? payload.profile_id : "";
    if (nextProfileId) {
      setRootAgentEditorMode("existing");
      setSelectedRootAgentId(nextProfileId);
      if (nextDraft) {
        setRootAgentDraft(nextDraft);
      }
      setFlashMessage(
        `已基于「${formatWorkerTemplateName(
          profile.name,
          profile.static_config.base_archetype
        )}」创建新的 Worker 模板草稿。`
      );
    }
    if (payload.review && typeof payload.review === "object") {
      setRootAgentReview(payload.review as RootAgentReviewResult);
    }
  }

  async function handleReviewRootAgentDraft() {
    const result = await submitAction("worker_profile.review", {
      draft: buildRootAgentPayload(rootAgentDraft),
    });
    const review = result?.data.review;
    if (!review || typeof review !== "object") {
      return;
    }
    setRootAgentReview(review as RootAgentReviewResult);
    setFlashMessage(
      (review as RootAgentReviewResult).ready
        ? "模板检查通过，可以保存或发布。"
        : "模板检查已返回，请先处理阻塞项。"
    );
  }

  async function handleSaveRootAgentDraft(publish = false) {
    const result = await submitAction("worker_profile.apply", {
      draft: buildRootAgentPayload(rootAgentDraft),
      publish,
      change_summary: publish ? "通过 AgentCenter 发布" : "通过 AgentCenter 更新草稿",
    });
    const payload = result?.data ?? {};
    const nextDraft = buildRootAgentStudioDraftFromReview(payload.review, selector);
    const nextProfileId =
      typeof payload.profile_id === "string" ? payload.profile_id : rootAgentDraft.profileId;
    if (nextProfileId) {
      setRootAgentEditorMode("existing");
      setSelectedRootAgentId(nextProfileId);
      if (nextDraft) {
        setRootAgentDraft(nextDraft);
      } else {
        setRootAgentDraft((current) => ({
          ...current,
          profileId: nextProfileId,
        }));
      }
    }
    const review = payload.review;
    if (review && typeof review === "object") {
      setRootAgentReview(review as RootAgentReviewResult);
    }
    setFlashMessage(publish ? "Worker 模板已发布。" : "Worker 模板草稿已保存。");
  }

  async function handleArchiveRootAgent() {
    if (!rootAgentDraft.profileId) {
      return;
    }
    const result = await submitAction("worker_profile.archive", {
      profile_id: rootAgentDraft.profileId,
    });
    if (result) {
      setFlashMessage("Worker 模板已归档。");
    }
  }

  async function handleBindRootAgentDefault() {
    if (!selectedRootAgentProfile) {
      return;
    }
    const result = await submitAction("worker_profile.bind_default", {
      profile_id: selectedRootAgentProfile.profile_id,
    });
    if (result) {
      setFlashMessage(
        `已把「${formatWorkerTemplateName(
          selectedRootAgentProfile.name,
          selectedRootAgentProfile.static_config.base_archetype
        )}」设为当前聊天默认 Worker 模板。`
      );
    }
  }

  async function handleSpawnFromRootAgent(profileId: string) {
    if (!rootAgentSpawnObjective.trim()) {
      setFlashMessage("先写清楚这次要执行什么，再用模板启动。");
      return;
    }
    const result = await submitAction("worker.spawn_from_profile", {
      profile_id: profileId,
      objective: rootAgentSpawnObjective.trim(),
    });
    if (result) {
      setRootAgentSpawnObjective("");
      setFlashMessage("已按当前模板创建新任务，稍后去 Work 或 Chat 看执行结果。");
    }
  }

  async function handleExtractRootAgentFromWork(work: WorkProjectionItem) {
    const result = await submitAction("worker.extract_profile_from_runtime", {
      work_id: work.work_id,
      name: `${work.title || formatWorkerType(work.selected_worker_type)} Worker 模板`,
    });
    const payload = result?.data ?? {};
    const nextDraft = buildRootAgentStudioDraftFromReview(payload.review, selector);
    const nextProfileId =
      typeof payload.profile_id === "string" ? payload.profile_id : "";
    if (nextProfileId) {
      setRootAgentEditorMode("existing");
      setSelectedRootAgentId(nextProfileId);
      if (nextDraft) {
        setRootAgentDraft(nextDraft);
      }
      setFlashMessage("已从当前运行结果提炼出新的 Worker 模板草稿。");
    }
    if (payload.review && typeof payload.review === "object") {
      setRootAgentReview(payload.review as RootAgentReviewResult);
    }
  }

  function handleCreateFreshRootAgent() {
    setActiveWorkspaceView("templates");
    setRootAgentEditorMode("create");
    setSelectedRootAgentId("");
    setRootAgentDraft(buildRootAgentStudioDraft(null, selector));
    setRootAgentReview(null);
    setRootAgentSpawnObjective("");
    setFlashMessage("开始一个新的 Worker 模板草稿。");
  }

  function renderPolicyCard(profile: PolicyProfileItem) {
    const active = primaryDraft.policyProfileId === profile.profile_id;
    return (
      <button
        key={profile.profile_id}
        type="button"
        className={`wb-agent-choice-card ${active ? "is-active" : ""}`}
        onClick={() => updatePrimary("policyProfileId", profile.profile_id)}
      >
        <strong>{profile.label}</strong>
        <span>{profile.description}</span>
      </button>
    );
  }

  return (
    <div className="wb-page wb-butler-page">
      <section className="wb-hero wb-hero-agent wb-butler-hero">
        <div className="wb-hero-copy">
          <p className="wb-kicker">Agents</p>
          <h1>Butler 与 Worker</h1>
          <p>在这里配置 Butler、维护 Worker 模板，并查看当前运行中的 Worker。</p>
          <div className="wb-chip-row">
            <span className="wb-chip">当前项目 {currentProject?.name ?? selector.current_project_id}</span>
            <span className="wb-chip">当前工作区 {currentWorkspace?.name ?? selector.current_workspace_id}</span>
            <span className={`wb-chip ${reviewTone(primaryReview) === "danger" ? "is-warning" : "is-success"}`}>
              {reviewTone(primaryReview) === "danger"
                ? `阻塞 ${primaryReview.blocking_reasons.length}`
                : primaryReview.ready
                  ? "配置检查通过"
                  : `提醒 ${primaryReview.warnings.length}`}
            </span>
            <span className={`wb-chip ${pendingChanges > 0 ? "is-warning" : "is-success"}`}>
              {pendingChanges > 0 ? `待确认改动 ${pendingChanges}` : "当前已同步"}
            </span>
          </div>
        </div>

        <div className="wb-hero-insights">
        <article className="wb-hero-metric">
          <p className="wb-card-label">Butler 配置</p>
          <strong>{savedPrimary.name}</strong>
          <span>{formatToolProfile(savedPrimary.toolProfile)}</span>
        </article>
        <article className="wb-hero-metric">
          <p className="wb-card-label">Worker 模板</p>
          <strong>{rootAgentProfiles.length}</strong>
          <span>默认模板 {defaultRootAgentName || "未设置"}</span>
        </article>
        <article className="wb-hero-metric">
          <p className="wb-card-label">运行中的 Worker</p>
          <strong>{workInstances.length}</strong>
          <span>活跃 {activeWorkAgents} / 待处理 {attentionWorkAgents}</span>
        </article>
      </div>
      </section>

      <div className="wb-butler-summary-grid">
        <article className="wb-butler-summary-card is-accent">
          <p className="wb-card-label">Butler 配置</p>
          <strong>名称、默认位置、审批和记忆边界都在这里</strong>
          <span>改这里会影响新会话默认怎么工作，不会直接改已经在运行的任务。</span>
        </article>
        <article className="wb-butler-summary-card">
          <p className="wb-card-label">默认位置</p>
          <strong>{findProjectName(availableProjects, savedPrimary.projectId)}</strong>
          <span>{findWorkspaceName(availableWorkspaces, savedPrimary.workspaceId)}</span>
        </article>
        <article className="wb-butler-summary-card">
          <p className="wb-card-label">模型与工具</p>
          <strong>
            {savedPrimary.modelAlias} · {formatToolProfile(savedPrimary.toolProfile)}
          </strong>
          <span>{currentPolicy?.label ?? savedPrimary.policyProfileId}</span>
        </article>
        <article className={`wb-butler-summary-card ${pendingChanges > 0 ? "is-warning" : ""}`}>
          <p className="wb-card-label">待保存改动</p>
          <strong>{pendingChanges > 0 ? `${pendingChanges} 处` : "已同步"}</strong>
          <span>{primaryDirty ? "Butler 草案未保存" : "Butler 已同步到底层配置"}</span>
        </article>
      </div>

      <div className="wb-inline-banner is-muted">
        <strong>当前提示</strong>
        <span>{flashMessage}</span>
      </div>

      <div className="wb-agent-workflow-nav" role="tablist" aria-label="Agents 工作流">
        {(Object.keys(AGENT_WORKSPACE_COPY) as AgentWorkspaceView[]).map((view) => (
          <button
            key={view}
            type="button"
            role="tab"
            aria-selected={activeWorkspaceView === view}
            className={`wb-agent-workflow-button ${
              activeWorkspaceView === view ? "is-active" : ""
            }`}
            onClick={() => openWorkspaceView(view)}
          >
            <strong>{AGENT_WORKSPACE_COPY[view].label}</strong>
            <span>{AGENT_WORKSPACE_COPY[view].description}</span>
          </button>
        ))}
      </div>

      <div className="wb-inline-banner is-muted">
        <strong>{AGENT_WORKSPACE_COPY[activeWorkspaceView].title}</strong>
        <span>{AGENT_WORKSPACE_COPY[activeWorkspaceView].description}</span>
      </div>

      {activeWorkspaceView === "butler" ? (
      <div className="wb-agent-layout">
        <section className="wb-panel wb-butler-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">Butler</p>
              <h3>Butler 设置</h3>
              <p className="wb-panel-copy">这里决定 Butler 的名称、默认项目、审批方式和记忆边界。</p>
            </div>
            <div className="wb-inline-actions wb-inline-actions-wrap">
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={handleResetPrimary}
                disabled={!primaryDirty || butlerBusy}
              >
                撤回改动
              </button>
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={() => void handleReviewPrimary()}
                disabled={butlerBusy}
              >
                检查 Butler 变更
              </button>
              <button
                type="button"
                className="wb-button wb-button-primary"
                onClick={() => void handleApplyPrimary()}
                disabled={!primaryDirty || butlerBusy}
              >
                保存 Butler 配置
              </button>
              <Link className="wb-button wb-button-tertiary" to="/settings">
                去 Settings 调连接
              </Link>
            </div>
          </div>

          <div
            className={`wb-inline-banner ${
              reviewTone(primaryReview) === "danger" ? "is-error" : "is-muted"
            }`}
          >
            <strong>{reviewHeadline(primaryReview)}</strong>
            <span>{reviewSummary(primaryReview)}</span>
          </div>

          <div className="wb-stat-grid">
            <div className="wb-detail-block">
              <span className="wb-card-label">当前默认 Project</span>
              <strong>{findProjectName(availableProjects, primaryDraft.projectId)}</strong>
              <p>{findWorkspaceName(availableWorkspaces, primaryDraft.workspaceId)}</p>
            </div>
            <div className="wb-detail-block">
              <span className="wb-card-label">审批强度</span>
              <strong>{currentPolicy?.label ?? primaryDraft.policyProfileId}</strong>
              <p>{formatToolProfile(primaryDraft.toolProfile)}</p>
            </div>
            <div className="wb-detail-block">
              <span className="wb-card-label">默认模型</span>
              <strong>{primaryDraft.modelAlias}</strong>
              <p>{MODEL_ALIAS_HINTS[primaryDraft.modelAlias] ?? "控制默认思考档位。"}</p>
            </div>
            <div className="wb-detail-block">
              <span className="wb-card-label">记忆策略</span>
              <strong>{recallPresetLabel ?? "自定义"}</strong>
              <p>
                Vault {primaryDraft.memoryAccessPolicy.allowVault ? "可引用" : "关闭"} / 历史
                {primaryDraft.memoryAccessPolicy.includeHistory ? "已纳入" : "未纳入"}
              </p>
            </div>
          </div>

          <div className="wb-agent-form-grid">
            <label className="wb-field">
              <span>Butler 名称</span>
              <input
                type="text"
                value={primaryDraft.name}
                onChange={(event) => updatePrimary("name", event.target.value)}
              />
            </label>
            <label className="wb-field">
              <span>默认生效范围</span>
              <select
                value={primaryDraft.scope}
                onChange={(event) => updatePrimary("scope", event.target.value)}
              >
                {SCOPE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="wb-field">
              <span>默认 Project</span>
              <select
                value={primaryDraft.projectId}
                onChange={(event) => updatePrimaryProject(event.target.value)}
              >
                {primaryProjectOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="wb-field">
              <span>默认 Workspace</span>
              <select
                value={primaryDraft.workspaceId}
                onChange={(event) => updatePrimary("workspaceId", event.target.value)}
              >
                {primaryWorkspaceOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="wb-field wb-field-span-2">
              <span>Persona（角色说明）</span>
              <small>这会影响 Butler 的语气、处理顺序和默认工作方式。</small>
              <textarea
                rows={4}
                className="wb-textarea-prose"
                value={primaryDraft.personaSummary}
                placeholder="例如：你负责先整理现状、提醒风险，再把任务交给合适的 Worker。"
                onChange={(event) => updatePrimary("personaSummary", event.target.value)}
              />
            </label>
          </div>

          <div className="wb-butler-stack">
            <div>
              <p className="wb-card-label">审批与治理</p>
              <div className="wb-agent-choice-grid">
                {policyProfiles.map(renderPolicyCard)}
              </div>
            </div>

            <div>
              <p className="wb-card-label">记忆边界</p>
              <div className="wb-butler-memory-grid">
                <label className="wb-butler-toggle-card">
                  <div>
                    <strong>允许带回 Vault 引用</strong>
                    <span>Butler 在 recall 时可以把受控 Vault 引用纳入候选，但仍受权限与审批约束。</span>
                  </div>
                  <input
                    type="checkbox"
                    checked={primaryDraft.memoryAccessPolicy.allowVault}
                    onChange={(event) =>
                      updatePrimaryMemoryAccess("allowVault", event.target.checked)
                    }
                  />
                </label>

                <label className="wb-butler-toggle-card">
                  <div>
                    <strong>默认包含历史版本</strong>
                    <span>适合需要看演变过程的项目；如果你更看重简洁上下文，可以先关闭。</span>
                  </div>
                  <input
                    type="checkbox"
                    checked={primaryDraft.memoryAccessPolicy.includeHistory}
                    onChange={(event) =>
                      updatePrimaryMemoryAccess("includeHistory", event.target.checked)
                    }
                  />
                </label>
              </div>
            </div>

            <details className="wb-agent-details">
              <summary>展开高级配置</summary>
              <div className="wb-agent-option-stack">
                <div>
                  <p className="wb-card-label">默认模型档位</p>
                  <div className="wb-chip-row">
                    {modelAliasOptions.map((alias) => (
                      <button
                        key={alias}
                        type="button"
                        className={`wb-chip-button ${primaryDraft.modelAlias === alias ? "is-active" : ""}`}
                        onClick={() => updatePrimary("modelAlias", alias)}
                      >
                        {alias}
                      </button>
                    ))}
                  </div>
                  <p className="wb-inline-note">
                    {MODEL_ALIAS_HINTS[primaryDraft.modelAlias] ?? "用于决定默认模型档位。"}
                  </p>
                </div>

                <div>
                  <p className="wb-card-label">工具范围</p>
                  <div className="wb-chip-row">
                    {toolProfileOptions.map((profile) => (
                      <button
                        key={profile}
                        type="button"
                        className={`wb-chip-button ${primaryDraft.toolProfile === profile ? "is-active" : ""}`}
                        onClick={() => updatePrimary("toolProfile", profile)}
                      >
                        {formatToolProfile(profile)}
                      </button>
                    ))}
                  </div>
                  <p className="wb-inline-note">
                    `standard` 适合大多数场景；只有明确需要更宽的工具面时再切 `privileged`。
                  </p>
                </div>

                <div>
                  <p className="wb-card-label">记忆召回预设</p>
                  <div className="wb-chip-row">
                    {MEMORY_RECALL_PRESETS.map((preset) => (
                      <button
                        key={preset.id}
                        type="button"
                        className={`wb-chip-button ${
                          detectRecallPreset(primaryDraft.memoryRecall) === preset.label
                            ? "is-active"
                            : ""
                        }`}
                        onClick={() => applyPrimaryMemoryPreset(preset.id)}
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                  <p className="wb-inline-note">
                    当前 {recallPresetLabel ?? "自定义"}：{MEMORY_RECALL_PRESETS.find((preset) => preset.label === recallPresetLabel)?.description ?? "已按当前项目做细化。"}
                  </p>
                </div>

                <div className="wb-agent-form-grid">
                  <label className="wb-field">
                    <span>后过滤策略</span>
                    <select
                      value={primaryDraft.memoryRecall.postFilterMode}
                      onChange={(event) =>
                        setPrimaryDraft((current) => ({
                          ...current,
                          memoryRecall: {
                            ...current.memoryRecall,
                            postFilterMode: event.target.value,
                          },
                        }))
                      }
                    >
                      <option value="keyword_overlap">keyword_overlap · 保守过滤</option>
                      <option value="none">none · 不额外过滤</option>
                    </select>
                  </label>
                  <label className="wb-field">
                    <span>重排策略</span>
                    <select
                      value={primaryDraft.memoryRecall.rerankMode}
                      onChange={(event) =>
                        setPrimaryDraft((current) => ({
                          ...current,
                          memoryRecall: {
                            ...current.memoryRecall,
                            rerankMode: event.target.value,
                          },
                        }))
                      }
                    >
                      <option value="heuristic">heuristic · 主题优先</option>
                      <option value="none">none · 保留原始顺序</option>
                    </select>
                  </label>
                  <label className="wb-field">
                    <span>最低关键词重叠</span>
                    <input
                      type="text"
                      value={primaryDraft.memoryRecall.minKeywordOverlap}
                      onChange={(event) =>
                        setPrimaryDraft((current) => ({
                          ...current,
                          memoryRecall: {
                            ...current.memoryRecall,
                            minKeywordOverlap: event.target.value,
                          },
                        }))
                      }
                    />
                  </label>
                  <label className="wb-field">
                    <span>最多查几个 Scope</span>
                    <input
                      type="text"
                      value={primaryDraft.memoryRecall.scopeLimit}
                      onChange={(event) =>
                        setPrimaryDraft((current) => ({
                          ...current,
                          memoryRecall: {
                            ...current.memoryRecall,
                            scopeLimit: event.target.value,
                          },
                        }))
                      }
                    />
                  </label>
                  <label className="wb-field">
                    <span>每个 Scope 最多带回几条</span>
                    <input
                      type="text"
                      value={primaryDraft.memoryRecall.perScopeLimit}
                      onChange={(event) =>
                        setPrimaryDraft((current) => ({
                          ...current,
                          memoryRecall: {
                            ...current.memoryRecall,
                            perScopeLimit: event.target.value,
                          },
                        }))
                      }
                    />
                  </label>
                  <label className="wb-field">
                    <span>总命中上限</span>
                    <input
                      type="text"
                      value={primaryDraft.memoryRecall.maxHits}
                      onChange={(event) =>
                        setPrimaryDraft((current) => ({
                          ...current,
                          memoryRecall: {
                            ...current.memoryRecall,
                            maxHits: event.target.value,
                          },
                        }))
                      }
                    />
                  </label>
                </div>

                <div className="wb-agent-advanced-grid">
                  <div className="wb-detail-block">
                    <span className="wb-card-label">模型运行方式</span>
                    <strong>{primaryDraft.llmMode}</strong>
                    <p>{primaryDraft.primaryProvider}</p>
                  </div>
                  <div className="wb-detail-block">
                    <span className="wb-card-label">接入地址</span>
                    <strong>{primaryDraft.proxyUrl}</strong>
                    <p>只在排查连接问题时需要关注</p>
                  </div>
                </div>
              </div>
            </details>
          </div>
        </section>

        <section className="wb-panel wb-butler-side">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">当前状态</p>
              <h3>切换查看范围并处理常见提醒</h3>
              <p className="wb-panel-copy">这里只影响你现在看到的 Project 和 Workspace，不会改默认配置。</p>
            </div>
            <div className="wb-inline-actions wb-inline-actions-wrap">
              <Link className="wb-button wb-button-secondary" to="/settings">
                去 Settings
              </Link>
              <Link className="wb-button wb-button-tertiary" to="/work">
                去看 Work
              </Link>
            </div>
          </div>

          <div className="wb-butler-side-stack">
            <article className="wb-butler-brief-card">
              <div className="wb-butler-brief-head">
                <div>
                  <p className="wb-card-label">当前视角</p>
                  <strong>切换你正在观察的 Project / Workspace</strong>
                </div>
                <span className="wb-chip">不会改 Butler 默认配置</span>
              </div>
              <div className="wb-inline-form">
                <label className="wb-field">
                  <span>查看哪个 Project</span>
                  <select
                    value={contextProjectId}
                    onChange={(event) => {
                      setContextProjectId(event.target.value);
                      setContextWorkspaceId(
                        normalizeWorkspaceForProject(
                          availableWorkspaces,
                          event.target.value,
                          contextWorkspaceId
                        )
                      );
                    }}
                  >
                    {availableProjects.map((project) => (
                      <option key={project.project_id} value={project.project_id}>
                        {project.name}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="wb-field">
                  <span>查看哪个 Workspace</span>
                  <select
                    value={contextWorkspaceId}
                    onChange={(event) => setContextWorkspaceId(event.target.value)}
                  >
                    {availableContextWorkspaces.map((workspace) => (
                      <option key={workspace.workspace_id} value={workspace.workspace_id}>
                        {workspace.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="wb-inline-actions wb-inline-actions-wrap">
                <button
                  type="button"
                  className="wb-button wb-button-secondary"
                  disabled={
                    busyActionId === "project.select" ||
                    (contextProjectId === selector.current_project_id &&
                      contextWorkspaceId === selector.current_workspace_id)
                  }
                  onClick={() => void handleSwitchProjectContext()}
                >
                  切到这个视角
                </button>
              </div>
            </article>

            <article className="wb-butler-brief-card">
              <div className="wb-butler-brief-head">
                <div>
                  <p className="wb-card-label">使用提示</p>
                  <strong>先说目标，再看执行细节</strong>
                </div>
              </div>
              <div className="wb-note-stack">
                <div className="wb-note">
                  <strong>先说明结果</strong>
                  <span>直接告诉 Butler 你要什么结果，比解释内部流程更有效。</span>
                </div>
                <div className="wb-note">
                  <strong>高风险会先确认</strong>
                  <span>涉及高风险动作时，界面会先停下来让你确认。</span>
                </div>
                <div className="wb-note">
                  <strong>执行细节去 Work 看</strong>
                  <span>想看谁在执行、卡在哪一步，直接去 Work 页面最清楚。</span>
                </div>
              </div>
            </article>

            <article className="wb-butler-brief-card">
              <div className="wb-butler-brief-head">
                <div>
                  <p className="wb-card-label">保存前提醒</p>
                  <strong>{reviewHeadline(primaryReview)}</strong>
                </div>
                <span className={`wb-status-pill is-${reviewTone(primaryReview)}`}>
                  {reviewTone(primaryReview)}
                </span>
              </div>
              <div className="wb-note-stack">
                {primaryReview.next_actions.slice(0, 3).map((item) => (
                  <div key={item} className="wb-note">
                    <strong>下一步</strong>
                    <span>{item}</span>
                  </div>
                ))}
                {primaryReview.next_actions.length === 0 ? (
                  <div className="wb-note">
                    <strong>当前状态</strong>
                    <span>没有额外提示，可以继续维护 Worker，或去 Settings 调整平台连接。</span>
                  </div>
                ) : null}
              </div>
            </article>

            <div className="wb-agent-project-grid">
              <button
                type="button"
                className={`wb-agent-project-card ${projectFilter === "all" ? "is-active" : ""}`}
                onClick={() => setProjectFilter("all")}
              >
                <strong>全部 Project</strong>
                <span>
                  实例 {workInstances.length} / 模板 {workTemplates.length}
                </span>
              </button>
              {projectFilterStats.map((project) => (
                <button
                  key={project.projectId}
                  type="button"
                  className={`wb-agent-project-card ${projectFilter === project.projectId ? "is-active" : ""}`}
                  onClick={() => setProjectFilter(project.projectId)}
                >
                  <strong>{project.name}</strong>
                  <span>
                    实例 {project.instanceCount} / 模板 {project.templateCount}
                  </span>
                </button>
              ))}
            </div>
          </div>
        </section>
      </div>
      ) : null}

      {activeWorkspaceView === "templates" ? (
      <section className="wb-panel wb-root-agent-hub">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">Worker 模板</p>
            <h3>在这里维护 Butler 会调用的 Worker 模板，并查看它们最近做了什么</h3>
            <p className="wb-panel-copy">
              左侧选模板，中间改默认配置，右侧看当前运行状态和最近任务。需要追内部链路时，再去
              Advanced。
            </p>
          </div>
          <div className="wb-inline-actions wb-inline-actions-wrap">
            <button
              type="button"
              className="wb-button wb-button-primary"
              onClick={handleCreateFreshRootAgent}
            >
              新建 Worker 模板
            </button>
            <Link className="wb-button wb-button-secondary" to="/advanced">
              去 Control Plane
            </Link>
            <Link className="wb-button wb-button-tertiary" to="/work">
              去看 Work
            </Link>
          </div>
        </div>

        <div className="wb-inline-banner is-muted">
          <strong>当前按单模板模式工作</strong>
          <span>
            一个 Worker 模板通常对应你现在看到的默认工作方式。这里会同时展示静态配置、当前
            Project / Workspace、运行负载和版本记录，方便你决定何时保存、发布或提炼新模板。
          </span>
        </div>

        <div className="wb-root-agent-summary-grid">
          <div className="wb-detail-block">
            <span className="wb-card-label">模板总数</span>
            <strong>{rootAgentProfiles.length}</strong>
            <p>内置 {builtinRootAgentProfiles.length} / 自定义 {customRootAgentProfiles.length}</p>
          </div>
          <div className="wb-detail-block">
            <span className="wb-card-label">激活中的 Work</span>
            <strong>{rootAgentActiveWorkCount}</strong>
            <p>运行中 {rootAgentRunningWorkCount} / 需关注 {rootAgentAttentionWorkCount}</p>
          </div>
          <div className="wb-detail-block">
            <span className="wb-card-label">默认 Worker 模板</span>
            <strong>{defaultRootAgentName || "还没有默认值"}</strong>
            <p>{defaultRootAgentId || "发布并绑定后会显示在这里"}</p>
          </div>
          <div className="wb-detail-block">
            <span className="wb-card-label">当前选中</span>
            <strong>{selectedRootAgentDisplayName || "新建草稿"}</strong>
            <p>
              {selectedRootAgentProfile
                ? `${formatScope(selectedRootAgentProfile.scope)} / ${findProjectName(
                    availableProjects,
                    selectedRootAgentProfile.project_id || selector.current_project_id
                  )}`
                : "还没有保存"}
            </p>
          </div>
          <div className="wb-detail-block">
            <span className="wb-card-label">最近更新时间</span>
            <strong>{latestRootAgentUpdate ? formatDateTime(latestRootAgentUpdate) : "未记录"}</strong>
            <p>
              上下文 {selectedRootAgentDynamicContext?.active_project_id || selector.current_project_id} /{" "}
              {selectedRootAgentDynamicContext?.active_workspace_id || selector.current_workspace_id}
            </p>
          </div>
        </div>

        <div className="wb-root-agent-layout">
          <aside className="wb-root-agent-browser">
            <article className="wb-root-agent-browser-panel">
              <div className="wb-root-agent-browser-head">
                <div>
                  <p className="wb-card-label">内置模板</p>
                  <strong>先选一个起点，再决定是否另存为自己的 Worker 模板</strong>
                </div>
                <span className="wb-status-pill is-ready">{builtinRootAgentProfiles.length}</span>
              </div>
              <div className="wb-root-agent-library-section">
                {builtinRootAgentProfiles.map((profile) => (
                  <button
                    key={profile.profile_id}
                    type="button"
                    className={`wb-root-agent-library-item ${
                      selectedRootAgentId === profile.profile_id ? "is-active" : ""
                    }`}
                    onClick={() => selectRootAgentProfile(profile)}
                  >
                    <div className="wb-root-agent-library-head">
                      <div>
                        <strong>
                          {formatWorkerTemplateName(
                            profile.name,
                            profile.static_config.base_archetype
                          )}
                        </strong>
                        <span>{profile.summary || "系统 archetype 默认配置。"}</span>
                      </div>
                      <span className={`wb-status-pill is-${profile.status}`}>
                        {formatWorkerProfileStatus(profile.status)}
                      </span>
                    </div>
                    <div className="wb-chip-row">
                      <span className="wb-chip">{formatWorkerProfileOrigin(profile.origin_kind)}</span>
                      <span className="wb-chip">{formatWorkerType(profile.static_config.base_archetype)}</span>
                      <span className="wb-chip">{formatToolProfile(profile.static_config.tool_profile)}</span>
                    </div>
                  </button>
                ))}
              </div>
            </article>

            <article className="wb-root-agent-browser-panel">
              <div className="wb-root-agent-browser-head">
                <div>
                  <p className="wb-card-label">已保存模板</p>
                  <strong>你已经保存过的 Worker 模板</strong>
                </div>
                <span className="wb-status-pill is-active">{customRootAgentProfiles.length}</span>
              </div>
              {customRootAgentProfiles.length === 0 ? (
                <div className="wb-empty-state">
                  <strong>还没有自定义 Worker 模板</strong>
                  <span>从左侧选一个内置模板，或直接点“新建 Worker 模板”。</span>
                </div>
              ) : (
                <div className="wb-root-agent-library-section">
                  {customRootAgentProfiles.map((profile) => {
                    const isSelected = selectedRootAgentId === profile.profile_id;
                    const hasAttention = profile.dynamic_context.attention_work_count > 0;
                    return (
                      <button
                        key={profile.profile_id}
                        type="button"
                        className={`wb-root-agent-library-item ${isSelected ? "is-active" : ""}`}
                        onClick={() => selectRootAgentProfile(profile)}
                      >
                        <div className="wb-root-agent-library-head">
                          <div>
                            <strong>
                              {formatWorkerTemplateName(
                                profile.name,
                                profile.static_config.base_archetype
                              )}
                            </strong>
                            <span>{profile.summary || "当前 profile 没有额外摘要。"}</span>
                          </div>
                          <span
                            className={`wb-status-pill is-${
                              hasAttention ? "warning" : profile.status
                            }`}
                          >
                            {hasAttention
                              ? `提醒 ${profile.dynamic_context.attention_work_count}`
                              : formatWorkerProfileStatus(profile.status)}
                          </span>
                        </div>
                        <div className="wb-root-agent-library-meta">
                          <span>{findProjectName(availableProjects, profile.project_id || selector.current_project_id)}</span>
                          <span>
                            版本 {profile.active_revision || 0}
                            {profile.draft_revision > profile.active_revision
                              ? ` / 草稿 ${profile.draft_revision}`
                              : ""}
                          </span>
                        </div>
                        <div className="wb-chip-row">
                          <span className="wb-chip">{formatWorkerProfileOrigin(profile.origin_kind)}</span>
                          <span className="wb-chip">{formatScope(profile.scope)}</span>
                          <span className="wb-chip">
                            {formatWorkerType(profile.static_config.base_archetype)}
                          </span>
                          {profile.profile_id === defaultRootAgentId ? (
                            <span className="wb-chip is-success">聊天默认</span>
                          ) : null}
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </article>
          </aside>

          <section className="wb-root-agent-studio">
            <article className="wb-root-agent-studio-panel">
              <div className="wb-root-agent-card-head">
                <div>
                  <p className="wb-card-label">模板编辑</p>
                  <h3>{selectedRootAgentDisplayName || "新的 Worker 模板草稿"}</h3>
                  <p className="wb-inline-note">
                    这里改的是默认配置。右侧会同步显示当前运行状态、版本记录和最近任务。
                  </p>
                </div>
                <div className="wb-chip-row">
                  <span className={`wb-status-pill is-${selectedRootAgentDisplayStatus}`}>
                    {formatWorkerProfileStatus(selectedRootAgentDisplayStatus)}
                  </span>
                  {selectedRootAgentIsDefault ? (
                    <span className="wb-chip is-success">当前聊天默认</span>
                  ) : null}
                  {rootAgentDraftDirty ? <span className="wb-chip is-warning">未保存变更</span> : null}
                  <span className="wb-chip">
                    {formatWorkerProfileOrigin(selectedRootAgentProfile?.origin_kind ?? "custom")}
                  </span>
                  <span className="wb-chip">{formatScope(rootAgentDraft.scope)}</span>
                </div>
              </div>

              {selectedRootAgentIsBuiltin ? (
                <div className="wb-inline-banner is-muted">
                  <strong>当前选中的是内置模板</strong>
                  <span>
                    你可以直接修改并保存，系统会自动生成新的 Worker 模板；也可以先点击“复制成新模
                    板”保留原模板不动。
                  </span>
                </div>
              ) : null}

              <div className="wb-root-agent-studio-form">
                <label className="wb-field">
                  <span>名称</span>
                  <input
                    type="text"
                    value={rootAgentDraft.name}
                    onChange={(event) => updateRootAgentDraft("name", event.target.value)}
                    placeholder="例如：家庭 NAS 管家"
                  />
                </label>
                <label className="wb-field">
                  <span>作用范围</span>
                  <select
                    value={rootAgentDraft.scope}
                    onChange={(event) => updateRootAgentDraft("scope", event.target.value)}
                  >
                    <option value="project">项目级默认</option>
                    <option value="system">系统级默认</option>
                  </select>
                </label>
                <label className="wb-field">
                  <span>所属项目</span>
                  <select
                    value={rootAgentDraft.projectId}
                    disabled={rootAgentDraft.scope !== "project"}
                    onChange={(event) => updateRootAgentProject(event.target.value)}
                  >
                    {rootAgentProjectOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="wb-field">
                  <span>模板起点</span>
                  <select
                    value={rootAgentDraft.baseArchetype}
                    onChange={(event) => updateRootAgentDraft("baseArchetype", event.target.value)}
                  >
                    {capabilityWorkerProfiles.map((profile) => (
                      <option key={profile.worker_type} value={profile.worker_type}>
                        {formatWorkerType(profile.worker_type)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="wb-field">
                  <span>模型别名</span>
                  <select
                    value={rootAgentDraft.modelAlias}
                    onChange={(event) => updateRootAgentDraft("modelAlias", event.target.value)}
                  >
                    {modelAliasOptions.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="wb-field">
                  <span>工具边界</span>
                  <select
                    value={rootAgentDraft.toolProfile}
                    onChange={(event) => updateRootAgentDraft("toolProfile", event.target.value)}
                  >
                    {toolProfileOptions.map((option) => (
                      <option key={option} value={option}>
                        {formatToolProfile(option)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="wb-field wb-field-span-2">
                  <span>摘要</span>
                  <textarea
                    className="wb-textarea-prose"
                    value={rootAgentDraft.summary}
                    onChange={(event) => updateRootAgentDraft("summary", event.target.value)}
                    placeholder="说明它长期负责什么、边界在哪里、什么时候应该叫它出场。"
                  />
                </label>
                <label className="wb-field">
                  <span>默认工具组</span>
                  <textarea
                    value={rootAgentDraft.defaultToolGroupsText}
                    onChange={(event) =>
                      updateRootAgentDraft("defaultToolGroupsText", event.target.value)
                    }
                    placeholder="每行一个 tool_group，例如 web、memory、project"
                  />
                  <small>这里写 tool group，不是具体 tool name。</small>
                </label>
                <label className="wb-field">
                  <span>固定工具</span>
                  <textarea
                    value={rootAgentDraft.selectedToolsText}
                    onChange={(event) =>
                      updateRootAgentDraft("selectedToolsText", event.target.value)
                    }
                    placeholder="每行一个 tool，例如 web.search"
                  />
                  <small>pin 住 1-3 个关键工具，运行行为会稳定很多。</small>
                </label>
                <label className="wb-field">
                  <span>运行形态</span>
                  <textarea
                    value={rootAgentDraft.runtimeKindsText}
                    onChange={(event) =>
                      updateRootAgentDraft("runtimeKindsText", event.target.value)
                    }
                    placeholder="例如 worker、subagent"
                  />
                </label>
                <label className="wb-field">
                  <span>策略引用</span>
                  <textarea
                    value={rootAgentDraft.policyRefsText}
                    onChange={(event) =>
                      updateRootAgentDraft("policyRefsText", event.target.value)
                    }
                    placeholder="例如 default"
                  />
                </label>
                <label className="wb-field">
                  <span>补充指令</span>
                  <textarea
                    value={rootAgentDraft.instructionOverlaysText}
                    onChange={(event) =>
                      updateRootAgentDraft("instructionOverlaysText", event.target.value)
                    }
                    placeholder="每行一句补充指令，例如：优先解释风险，不直接执行高危操作。"
                  />
                </label>
                <label className="wb-field">
                  <span>Tags</span>
                  <textarea
                    value={rootAgentDraft.tagsText}
                    onChange={(event) => updateRootAgentDraft("tagsText", event.target.value)}
                    placeholder="每行一个标签，例如 nas、router、finance"
                  />
                </label>
              </div>

              <div className="wb-root-agent-token-grid">
                <div className="wb-root-agent-token-card">
                  <div className="wb-root-agent-column-head">
                    <strong>推荐工具组</strong>
                    <button
                      type="button"
                      className="wb-button wb-button-tertiary"
                      onClick={applyRootAgentArchetypeDefaults}
                    >
                      套用 archetype 默认值
                    </button>
                  </div>
                  <div className="wb-chip-row">
                    {rootAgentSuggestedToolGroups.map((toolGroup) => (
                      <button
                        key={toolGroup}
                        type="button"
                        className="wb-chip-button"
                        onClick={() => appendRootAgentDraftValue("defaultToolGroupsText", toolGroup)}
                      >
                        {formatTokenLabel(toolGroup)}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="wb-root-agent-token-card">
                  <div className="wb-root-agent-column-head">
                    <strong>推荐固定工具</strong>
                    <span>优先 pin 关键能力</span>
                  </div>
                  <div className="wb-chip-row">
                    {rootAgentSuggestedTools.map((tool) => (
                      <button
                        key={tool}
                        type="button"
                        className="wb-chip-button"
                        onClick={() => appendRootAgentDraftValue("selectedToolsText", tool)}
                      >
                        {formatToolToken(tool, toolLabelByName)}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="wb-root-agent-token-card">
                  <div className="wb-root-agent-column-head">
                    <strong>运行形态</strong>
                    <span>和 Agent Zero / OpenClaw 一样，先把运行边界说清楚</span>
                  </div>
                  <div className="wb-chip-row">
                    {rootAgentSuggestedRuntimeKinds.map((kind) => (
                      <button
                        key={kind}
                        type="button"
                        className="wb-chip-button"
                        onClick={() => appendRootAgentDraftValue("runtimeKindsText", kind)}
                      >
                        {formatTokenLabel(kind)}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="wb-root-agent-token-card">
                  <div className="wb-root-agent-column-head">
                    <strong>标签建议</strong>
                    <span>标签帮助 Butler 更快找到合适的 Worker 模板</span>
                  </div>
                  <div className="wb-chip-row">
                    {rootAgentSuggestedTags.map((tag) => (
                      <button
                        key={tag}
                        type="button"
                        className="wb-chip-button"
                        onClick={() => appendRootAgentDraftValue("tagsText", tag)}
                      >
                        {formatTokenLabel(tag)}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {rootAgentReview ? (
                <div className="wb-root-agent-review-panel">
                  <div className="wb-root-agent-card-head">
                    <div>
                      <p className="wb-card-label">检查结果</p>
                      <strong>
                        {rootAgentReview.ready
                          ? "当前草稿可以保存或发布"
                          : "先处理阻塞项，再继续发布"}
                      </strong>
                    </div>
                    <span
                      className={`wb-status-pill is-${
                        rootAgentReview.ready
                          ? "success"
                          : rootAgentReview.save_errors.length > 0
                            ? "danger"
                            : "warning"
                      }`}
                    >
                      {rootAgentReview.ready ? "通过" : "待处理"}
                    </span>
                  </div>
                  <div className="wb-root-agent-review-grid">
                    <div className="wb-note-stack">
                      {rootAgentReview.save_errors.map((item) => (
                        <div key={item} className="wb-note">
                          <strong>保存失败</strong>
                          <span>{item}</span>
                        </div>
                      ))}
                      {rootAgentReview.blocking_reasons.map((item) => (
                        <div key={item} className="wb-note">
                          <strong>阻塞项</strong>
                          <span>{item}</span>
                        </div>
                      ))}
                      {rootAgentReview.warnings.map((item) => (
                        <div key={item} className="wb-note">
                          <strong>提醒</strong>
                          <span>{item}</span>
                        </div>
                      ))}
                    </div>
                    <div className="wb-note-stack">
                      {rootAgentReview.next_actions.map((item) => (
                        <div key={item} className="wb-note">
                          <strong>下一步</strong>
                          <span>{item}</span>
                        </div>
                      ))}
                      {rootAgentReviewDiff.length > 0 ? (
                        <div className="wb-note">
                          <strong>变更字段</strong>
                          <span>
                            {rootAgentReviewDiff.map((item) => formatTokenLabel(item.field)).join("、")}
                          </span>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </div>
              ) : null}

              <div className="wb-inline-actions wb-inline-actions-wrap">
                <button
                  type="button"
                  className="wb-button wb-button-secondary"
                  onClick={() => void handleReviewRootAgentDraft()}
                  disabled={busyActionId === "worker_profile.review"}
                >
                  检查草稿
                </button>
                <button
                  type="button"
                  className="wb-button wb-button-primary"
                  onClick={() => void handleSaveRootAgentDraft(false)}
                  disabled={busyActionId === "worker_profile.apply"}
                >
                  {selectedRootAgentIsBuiltin ? "另存草稿" : "保存草稿"}
                </button>
                <button
                  type="button"
                  className="wb-button wb-button-primary"
                  onClick={() => void handleSaveRootAgentDraft(true)}
                  disabled={busyActionId === "worker_profile.apply"}
                >
                  {selectedRootAgentIsBuiltin ? "另存并发布" : "发布版本"}
                </button>
                <button
                  type="button"
                  className="wb-button wb-button-tertiary"
                  onClick={() =>
                    selectedRootAgentProfile
                      ? void handleCreateRootAgentDraftFromTemplate(selectedRootAgentProfile)
                      : undefined
                  }
                  disabled={!selectedRootAgentProfile || busyActionId === "worker_profile.clone"}
                >
                  复制成新模板
                </button>
                <button
                  type="button"
                  className="wb-button wb-button-tertiary"
                  onClick={() => void handleBindRootAgentDefault()}
                  disabled={
                    !selectedRootAgentProfile ||
                    selectedRootAgentIsBuiltin ||
                    selectedRootAgentDisplayStatus !== "active" ||
                    busyActionId === "worker_profile.bind_default"
                  }
                >
                  {selectedRootAgentIsDefault ? "已是聊天默认" : "设为聊天默认"}
                </button>
                <button
                  type="button"
                  className="wb-button wb-button-tertiary"
                  onClick={() =>
                    selectedRootAgentProfile
                      ? void handleSwitchToRootAgentContext(selectedRootAgentProfile)
                      : undefined
                  }
                  disabled={!selectedRootAgentProfile || busyActionId === "project.select"}
                >
                  切到这个模板的上下文
                </button>
                <button
                  type="button"
                  className="wb-button wb-button-tertiary"
                  onClick={() => void handleArchiveRootAgent()}
                  disabled={
                    !rootAgentDraft.profileId ||
                    !selectedRootAgentEditable ||
                    busyActionId === "worker_profile.archive"
                  }
                >
                  归档当前模板
                </button>
              </div>
            </article>
          </section>

          <aside className="wb-root-agent-runtime-rail">
            <article className="wb-root-agent-runtime-panel">
              <div className="wb-root-agent-card-head">
                <div>
                  <p className="wb-card-label">当前运行状态</p>
                  <strong>
                    {selectedRootAgentProfile
                      ? "这个模板最近是怎么工作的"
                      : "先从左侧选一个模板，或先保存当前草稿"}
                  </strong>
                </div>
                <span className="wb-chip">
                  {selectedRootAgentDynamicContext?.updated_at
                    ? formatDateTime(selectedRootAgentDynamicContext.updated_at)
                    : "尚未刷新"}
                </span>
              </div>
              {selectedRootAgentProfile ? (
                <>
                    <div className="wb-root-agent-context-grid">
                      <div className="wb-detail-block">
                        <span className="wb-card-label">活跃任务</span>
                        <strong>{selectedRootAgentDynamicContext?.active_work_count ?? 0}</strong>
                        <p>运行中 {selectedRootAgentDynamicContext?.running_work_count ?? 0}</p>
                      </div>
                      <div className="wb-detail-block">
                        <span className="wb-card-label">需要处理</span>
                        <strong>{selectedRootAgentDynamicContext?.attention_work_count ?? 0}</strong>
                        <p>Target {selectedRootAgentDynamicContext?.latest_target_kind || "-"}</p>
                      </div>
                      <div className="wb-detail-block">
                        <span className="wb-card-label">工具分配</span>
                        <strong>
                          {selectedRootAgentDynamicContext?.current_tool_resolution_mode || "legacy"}
                        </strong>
                      <p>
                        mounted {selectedRootAgentMountedTools.length} / blocked{" "}
                        {selectedRootAgentBlockedTools.length}
                      </p>
                    </div>
                  </div>
                  <div className="wb-key-value-list">
                    <span>项目 / 工作区</span>
                    <strong>
                      {findProjectName(
                        availableProjects,
                        selectedRootAgentDynamicContext?.active_project_id ||
                          selectedRootAgentProfile.project_id ||
                          selector.current_project_id
                      )}{" "}
                      /{" "}
                      {findWorkspaceName(
                        availableWorkspaces,
                        selectedRootAgentDynamicContext?.active_workspace_id ||
                          selector.current_workspace_id
                      )}
                    </strong>
                    <span>快照</span>
                    <strong>{selectedRootAgentProfile.effective_snapshot_id || "-"}</strong>
                    <span>最近任务</span>
                    <strong>
                      {selectedRootAgentDynamicContext?.latest_work_title ||
                        selectedRootAgentDynamicContext?.latest_work_id ||
                        "-"}
                    </strong>
                    <span>最近 Task</span>
                    <strong>{selectedRootAgentDynamicContext?.latest_task_id || "-"}</strong>
                  </div>
                  <div className="wb-root-agent-token-stack">
                    <div>
                      <p className="wb-card-label">当前工具宇宙</p>
                      <div className="wb-chip-row">
                        {(selectedRootAgentDynamicContext?.current_selected_tools ?? []).length > 0 ? (
                          selectedRootAgentDynamicContext!.current_selected_tools.map((tool) => (
                            <span key={tool} className="wb-chip">
                              {formatToolToken(tool, toolLabelByName)}
                            </span>
                          ))
                        ) : (
                          <span className="wb-inline-note">还没有记录 selected tools。</span>
                        )}
                      </div>
                    </div>
                    <div>
                      <p className="wb-card-label">工具发现入口</p>
                      <div className="wb-chip-row">
                        {selectedRootAgentDiscoveryEntrypoints.length > 0 ? (
                          selectedRootAgentDiscoveryEntrypoints.map((tool) => (
                            <span key={tool} className="wb-chip">
                              {formatToolToken(tool, toolLabelByName)}
                            </span>
                          ))
                        ) : (
                          <span className="wb-inline-note">当前没有额外入口提示。</span>
                        )}
                      </div>
                    </div>
                    {selectedRootAgentMountedTools.length > 0 ? (
                      <div>
                        <p className="wb-card-label">已挂载工具</p>
                        <div className="wb-note-stack">
                          {selectedRootAgentMountedTools.slice(0, 4).map((tool) => (
                            <div key={`mounted-${tool.tool_name}`} className="wb-note">
                              <strong>{formatToolToken(tool.tool_name, toolLabelByName)}</strong>
                              <span>{tool.summary || tool.source_kind}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}
                    {selectedRootAgentBlockedTools.length > 0 ? (
                      <div>
                        <p className="wb-card-label">当前被阻塞的工具</p>
                        <div className="wb-note-stack">
                          {selectedRootAgentBlockedTools.slice(0, 4).map((tool) => (
                            <div key={`blocked-${tool.tool_name}`} className="wb-note">
                              <strong>{formatToolToken(tool.tool_name, toolLabelByName)}</strong>
                              <span>{tool.summary || tool.reason_code || tool.status}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}
                    {selectedRootAgentCapabilities.length > 0 ? (
                      <div>
                        <p className="wb-card-label">控制面能力</p>
                        <div className="wb-chip-row">
                          {selectedRootAgentCapabilities.map((capability) => (
                            <span key={capability.capability_id} className="wb-chip">
                              {capability.label}
                            </span>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>
                  {selectedRootAgentWarnings.length > 0 ? (
                    <div className="wb-note-stack">
                      {selectedRootAgentWarnings.map((warning) => (
                        <div key={warning} className="wb-note">
                          <strong>提醒</strong>
                          <span>{warning}</span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </>
              ) : (
                <div className="wb-empty-state">
                  <strong>还没有运行态视图</strong>
                  <span>先选一个已有模板，或者保存现在的草稿，再回来观察运行状态。</span>
                </div>
              )}
            </article>

            <article className="wb-root-agent-runtime-panel">
              <div className="wb-root-agent-card-head">
                <div>
                  <p className="wb-card-label">新建任务</p>
                  <strong>按这个模板启动一次新的工作</strong>
                </div>
                <Link className="wb-button wb-button-tertiary" to="/chat">
                  去 Chat 观察执行
                </Link>
              </div>
              <label className="wb-field">
                <span>任务目标</span>
                <textarea
                  className="wb-textarea-prose"
                  value={rootAgentSpawnObjective}
                  onChange={(event) => setRootAgentSpawnObjective(event.target.value)}
                  placeholder="例如：检查家庭 NAS 备份是否异常，并给出今天的处理建议。"
                />
              </label>
              <div className="wb-inline-actions wb-inline-actions-wrap">
                <button
                  type="button"
                  className="wb-button wb-button-primary"
                  disabled={!selectedRootAgentProfile || busyActionId === "worker.spawn_from_profile"}
                  onClick={() =>
                    selectedRootAgentProfile
                      ? void handleSpawnFromRootAgent(selectedRootAgentProfile.profile_id)
                      : undefined
                  }
                >
                  用这个模板启动
                </button>
                <Link className="wb-button wb-button-secondary" to="/work">
                  去看 Runtime Work
                </Link>
              </div>
            </article>

            <article className="wb-root-agent-runtime-panel">
              <div className="wb-root-agent-card-head">
                <div>
                  <p className="wb-card-label">版本记录</p>
                  <strong>这里看每次发布后的版本和快照</strong>
                </div>
                <span className="wb-chip">{selectedRootAgentDisplayName || "未选中模板"}</span>
              </div>
              {rootAgentRevisionLoading ? (
                <div className="wb-empty-state">
                  <strong>正在加载版本记录</strong>
                  <span>稍等一下，马上就好。</span>
                </div>
              ) : rootAgentRevisionError ? (
                <div className="wb-inline-banner is-error">
                  <strong>版本记录加载失败</strong>
                  <span>{rootAgentRevisionError}</span>
                </div>
              ) : rootAgentRevisions.length === 0 ? (
                <div className="wb-empty-state">
                  <strong>还没有版本记录</strong>
                  <span>保存草稿后点击“发布版本”，这里就会出现可追踪版本。</span>
                </div>
              ) : (
                <div className="wb-root-agent-revision-list">
                  {rootAgentRevisions.map((revision) => (
                    <div key={revision.revision_id} className="wb-root-agent-runtime-item">
                      <div className="wb-root-agent-library-head">
                        <div>
                          <strong>版本 {revision.revision}</strong>
                          <span>{revision.change_summary || "未填写变更摘要"}</span>
                        </div>
                        <span className="wb-chip">{revision.created_by || "system"}</span>
                      </div>
                      <div className="wb-root-agent-library-meta">
                        <span>{revision.created_at ? formatDateTime(revision.created_at) : "未记录时间"}</span>
                        <span>{String(revision.snapshot_payload.profile_id || revision.revision_id)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </article>

            <article className="wb-root-agent-runtime-panel">
              <div className="wb-root-agent-card-head">
                <div>
                  <p className="wb-card-label">最近任务</p>
                  <strong>这里显示最近哪些任务使用了这个模板</strong>
                </div>
                <span className="wb-chip">{selectedRootAgentWorks.length} 个 Work</span>
              </div>
              {selectedRootAgentWorks.length === 0 ? (
                <div className="wb-empty-state">
                  <strong>当前还没有关联 Work</strong>
                  <span>发布后从上面的“新建任务”区域启动一次，这里就会显示最近任务。</span>
                </div>
              ) : (
                <div className="wb-root-agent-work-list">
                  {selectedRootAgentWorks.map((work) => (
                    <div key={work.work_id} className="wb-root-agent-runtime-item">
                      <div className="wb-root-agent-library-head">
                        <div>
                          <strong>{work.title || work.work_id}</strong>
                          <span>{work.route_reason || formatWorkerType(work.selected_worker_type)}</span>
                        </div>
                        <span className={`wb-status-pill is-${work.status}`}>{work.status}</span>
                      </div>
                      <div className="wb-key-value-list">
                        <span>Agent / Profile</span>
                        <strong>
                          {work.agent_profile_id || "-"} /{" "}
                          {work.requested_worker_profile_id || "回退到 archetype"}
                        </strong>
                        <span>使用的模板</span>
                        <strong>{work.requested_worker_profile_id || "回退到 archetype"}</strong>
                        <span>版本 / 快照</span>
                        <strong>
                          {work.requested_worker_profile_version || "-"} /{" "}
                          {work.effective_worker_snapshot_id || "-"}
                        </strong>
                        <span>Worker / Target</span>
                        <strong>
                          {formatWorkerType(work.selected_worker_type)} / {work.target_kind || "-"}
                        </strong>
                        <span>工具分配</span>
                        <strong>{work.tool_resolution_mode || "legacy"}</strong>
                      </div>
                      <div className="wb-chip-row">
                        {work.selected_tools.length > 0 ? (
                          work.selected_tools.map((tool) => (
                            <span key={tool} className="wb-chip">
                              {formatToolToken(tool, toolLabelByName)}
                            </span>
                          ))
                        ) : (
                          <span className="wb-inline-note">当前 work 没有 selected tools 记录。</span>
                        )}
                      </div>
                      {work.blocked_tools && work.blocked_tools.length > 0 ? (
                        <div className="wb-note-stack">
                          {work.blocked_tools.slice(0, 2).map((tool) => (
                            <div
                              key={`lineage-blocked-${work.work_id}-${tool.tool_name}`}
                              className="wb-note"
                            >
                              <strong>{formatToolToken(tool.tool_name, toolLabelByName)}</strong>
                              <span>{tool.summary || tool.reason_code || tool.status}</span>
                            </div>
                          ))}
                        </div>
                      ) : null}
                      <div className="wb-inline-actions wb-inline-actions-wrap">
                        <button
                          type="button"
                          className="wb-button wb-button-secondary"
                          onClick={() => void handleExtractRootAgentFromWork(work)}
                          disabled={busyActionId === "worker.extract_profile_from_runtime"}
                        >
                          从这个运行结果提炼模板
                        </button>
                        <Link className="wb-button wb-button-tertiary" to="/work">
                          去 Work 看详情
                        </Link>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </article>
          </aside>
        </div>
      </section>
      ) : null}

      {activeWorkspaceView === "workers" ? (
      <section className="wb-panel wb-worker-hub">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">Worker 管理</p>
            <h3>先看实例，再决定是否沉淀成草案</h3>
            <p className="wb-panel-copy">
              这里优先处理当前正在运行的 Worker。只有当某个实例值得长期复用时，再把它沉淀成草案。
            </p>
          </div>
        </div>

        <div className="wb-agent-tablist" role="tablist" aria-label="Worker 管理视图">
          <button
            type="button"
            role="tab"
            aria-selected={activeCatalog === "instances"}
            className={`wb-agent-tab ${activeCatalog === "instances" ? "is-active" : ""}`}
            onClick={() => openCatalog("instances")}
          >
            <strong>{CATALOG_COPY.instances.label}</strong>
            <span>{CATALOG_COPY.instances.description}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeCatalog === "templates"}
            className={`wb-agent-tab ${activeCatalog === "templates" ? "is-active" : ""}`}
            onClick={() => openCatalog("templates")}
          >
            <strong>{CATALOG_COPY.templates.label}</strong>
            <span>{CATALOG_COPY.templates.description}</span>
          </button>
        </div>

        <div className="wb-inline-banner is-muted">
          <strong>{CATALOG_COPY[activeCatalog].title}</strong>
          <span>
            {activeCatalog === "instances"
              ? "实例适合看当前负载、归属和合并拆分；需要沉淀经验时，再切到实例草案。"
              : "这里的草案来自当前实例。要发布长期默认模板，请回到上面的 Worker 模板工作台。"}
          </span>
        </div>

        <div className="wb-worker-toolbar">
          <label className="wb-field">
            <span>{activeCatalog === "instances" ? "搜索实例" : "搜索草案"}</span>
            <input
              type="text"
              value={searchQuery}
              placeholder={
                activeCatalog === "instances"
                  ? "例如：开发、待处理、Primary Workspace"
                  : "例如：巡检、默认工具、handoff"
              }
              onChange={(event) => setSearchQuery(event.target.value)}
            />
          </label>

          <div className="wb-inline-actions wb-inline-actions-wrap">
            {activeCatalog === "instances" ? (
              <>
                <button
                  type="button"
                  className="wb-button wb-button-secondary"
                  onClick={handleMergeWorkAgents}
                  disabled={selectedWorkAgentIds.length < 2}
                >
                  合并选中实例
                </button>
                <button
                  type="button"
                  className="wb-button wb-button-secondary"
                  onClick={handleSplitWorkAgent}
                  disabled={!selectedWorkAgent || selectedWorkAgent.kind !== "instance"}
                >
                  拆分当前实例
                </button>
                <button
                  type="button"
                  className="wb-button wb-button-primary"
                  onClick={() => handleCreateDraft("instance")}
                >
                  新建 Worker 实例
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="wb-button wb-button-secondary"
                  onClick={handleCreateInstanceFromTemplate}
                  disabled={!selectedWorkAgent || selectedWorkAgent.kind !== "template"}
                >
                  按当前草案新建实例
                </button>
                <button
                  type="button"
                  className="wb-button wb-button-primary"
                  onClick={() => handleCreateDraft("template")}
                >
                  新建实例草案
                </button>
              </>
            )}
          </div>
        </div>

        <div className="wb-worker-hub-layout">
          <div className="wb-worker-browser">
            {visibleCatalogItems.length === 0 ? (
              <div className="wb-empty-state">
                <strong>当前没有匹配内容</strong>
                <span>
                  试着切换 Project 过滤，或者直接创建一个新的
                  {activeCatalog === "instances" ? "实例" : "草案"}。
                </span>
              </div>
            ) : (
              <div className="wb-agent-list">
                {visibleCatalogItems.map((agent) => {
                  const badge = workAgentBadge(agent);
                  const isActive = selectedWorkAgentId === agent.id && editorMode === "edit";
                  return (
                    <article
                      key={agent.id}
                      className={`wb-agent-runtime-card ${isActive ? "is-active" : ""}`}
                    >
                      <div className="wb-worker-card-head">
                        <div className="wb-worker-card-leading">
                          {agent.kind === "instance" ? (
                            <label className="wb-agent-check">
                              <input
                                type="checkbox"
                                checked={selectedWorkAgentIds.includes(agent.id)}
                                onChange={() => toggleWorkAgentSelection(agent.id)}
                              />
                              <span>批量选择</span>
                            </label>
                          ) : (
                            <span className="wb-card-label">草案</span>
                          )}
                          <span className={`wb-status-pill is-${badge.tone}`}>{badge.label}</span>
                        </div>
                        <span className="wb-card-label">{formatWorkerType(agent.workerType)}</span>
                      </div>

                      <div className="wb-worker-card-body">
                        <strong>{agent.name}</strong>
                        <p>{agent.summary}</p>
                        <div className="wb-chip-row">
                          <span className="wb-chip">
                            {formatProjectWorkspace(
                              availableProjects,
                              availableWorkspaces,
                              agent.projectId,
                              agent.workspaceId
                            )}
                          </span>
                          <span className="wb-chip">{formatAutonomy(agent.autonomy)}</span>
                          <span className="wb-chip">{formatToolProfile(agent.toolProfile)}</span>
                        </div>
                        <div className="wb-chip-row">
                          {agent.tags.map((tag) => (
                            <span key={tag} className="wb-chip is-warning">
                              {formatTokenLabel(tag)}
                            </span>
                          ))}
                        </div>
                        <div className="wb-agent-runtime-meta">
                          {agent.kind === "instance" ? (
                            <>
                              <span>当前任务 {agent.taskCount}</span>
                              <span>等待 {agent.waitingCount}</span>
                              <span>适合合并 {agent.mergeReadyCount}</span>
                            </>
                          ) : (
                            <>
                              <span>默认工具 {agent.selectedTools.length}</span>
                              <span>
                                同类型实例{" "}
                                {workInstances.filter((item) => item.workerType === agent.workerType).length}
                              </span>
                              <span>{WORK_AGENT_SOURCE_LABELS[agent.source]}</span>
                            </>
                          )}
                          <span>
                            {agent.lastUpdated ? `更新于 ${formatDateTime(agent.lastUpdated)}` : "尚未保存"}
                          </span>
                        </div>
                      </div>

                      <div className="wb-worker-card-actions">
                        <button
                          type="button"
                          className="wb-button wb-button-secondary wb-button-inline"
                          onClick={() => selectWorkAgent(agent)}
                        >
                          {agent.kind === "instance" ? "查看与修改实例" : "查看与修改模板"}
                        </button>
                        {agent.kind === "template" ? (
                          <button
                            type="button"
                            className="wb-button wb-button-tertiary wb-button-inline"
                          onClick={() => {
                              setActiveCatalog("instances");
                              setEditorMode("create");
                              setSelectedWorkAgentId("");
                              setSelectedWorkAgentIds([]);
                              setWorkDraft(createInstanceFromTemplate(agent));
                              setFlashMessage("已按当前草案生成一个新的 Worker 实例草案。");
                            }}
                          >
                            用它新建实例
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="wb-button wb-button-tertiary wb-button-inline"
                            onClick={() => {
                              setActiveCatalog("templates");
                              setEditorMode("create");
                              setSelectedWorkAgentId("");
                              setSelectedWorkAgentIds([]);
                              setWorkDraft(forkTemplateFromAgent(agent));
                              setFlashMessage(
                                "已把当前实例复制成草案。清理掉只属于当前运行态的内容后再保存。"
                              );
                            }}
                          >
                            另存为草案
                          </button>
                        )}
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </div>

          <aside className="wb-worker-editor">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">{editingKind === "instance" ? "实例编辑器" : "草案编辑器"}</p>
                <h3>
                  {editingKind === "instance"
                    ? editorMode === "create"
                      ? "创建新的 Worker 实例"
                      : "调整当前 Worker 实例"
                    : editorMode === "create"
                      ? "创建新的实例草案"
                      : "调整当前实例草案"}
                </h3>
              </div>
              <span className="wb-chip">
                {editorMode === "create"
                  ? editingKind === "instance"
                    ? "新实例草案"
                    : "新草案"
                  : selectedWorkAgent?.name ?? "未选择"}
              </span>
            </div>

            <div className="wb-inline-banner is-muted">
              <strong>{editingKind === "instance" ? "你正在编辑实例" : "你正在编辑草案"}</strong>
              <span>
                {editingKind === "instance"
                  ? "实例对应当前分工。这里改的是归属、角色和默认做法，运行指标仅作参考。"
                  : "草案来自当前实例，用来沉淀新的默认做法。要发布长期默认模板，请回到上面的 Worker 模板。"}
              </span>
            </div>

            <div className="wb-stat-grid">
              <div className="wb-detail-block">
                <span className="wb-card-label">归属位置</span>
                <strong>
                  {formatProjectWorkspace(
                    availableProjects,
                    availableWorkspaces,
                    workDraft.projectId,
                    workDraft.workspaceId
                  )}
                </strong>
                <p>{formatWorkerType(workDraft.workerType)}</p>
              </div>
              <div className="wb-detail-block">
                <span className="wb-card-label">
                  {editingKind === "instance" ? "当前状态" : "草案用途"}
                </span>
                <strong>
                  {editingKind === "instance"
                    ? WORK_AGENT_STATUS_LABELS[workDraft.status]
                    : WORK_AGENT_SOURCE_LABELS[workDraft.source]}
                </strong>
                <p>{formatAutonomy(workDraft.autonomy)}</p>
              </div>
              {editingKind === "instance" ? (
                <>
                  <div className="wb-detail-block">
                    <span className="wb-card-label">当前任务量</span>
                    <strong>{workDraft.taskCount}</strong>
                    <p>等待中 {workDraft.waitingCount}</p>
                  </div>
                  <div className="wb-detail-block">
                    <span className="wb-card-label">适合合并</span>
                    <strong>{workDraft.mergeReadyCount}</strong>
                    <p>用于判断是否需要收口同类实例</p>
                  </div>
                </>
              ) : (
                <>
                  <div className="wb-detail-block">
                    <span className="wb-card-label">同类型实例</span>
                    <strong>{selectedTemplateUsageCount}</strong>
                    <p>当前正在使用相近角色的实例数量</p>
                  </div>
                  <div className="wb-detail-block">
                    <span className="wb-card-label">默认工具</span>
                    <strong>{workDraft.selectedTools.length}</strong>
                    <p>保存后会作为新建时的默认勾选</p>
                  </div>
                </>
              )}
            </div>

            <div className="wb-agent-form-grid">
              <label className="wb-field">
                <span>{editingKind === "instance" ? "实例名称" : "草案名称"}</span>
                <input
                  type="text"
                  value={workDraft.name}
                  onChange={(event) => updateWorkDraft("name", event.target.value)}
                />
              </label>
              <label className="wb-field">
                <span>负责角色</span>
                <select
                  value={workDraft.workerType}
                  onChange={(event) => updateWorkerType(event.target.value)}
                >
                  {["general", "research", "dev", "ops"].map((workerType) => (
                    <option key={workerType} value={workerType}>
                      {formatWorkerType(workerType)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="wb-field">
                <span>归属 Project</span>
                <select
                  value={workDraft.projectId}
                  onChange={(event) => updateWorkProject(event.target.value)}
                >
                  {workProjectOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="wb-field">
                <span>归属 Workspace</span>
                <select
                  value={workDraft.workspaceId}
                  onChange={(event) => updateWorkDraft("workspaceId", event.target.value)}
                >
                  {workWorkspaceOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="wb-field wb-field-span-2">
                <span>{editingKind === "instance" ? "当前职责说明" : "这个草案适合什么场景"}</span>
                <textarea
                  rows={4}
                  className="wb-textarea-prose"
                  value={workDraft.summary}
                  onChange={(event) => updateWorkDraft("summary", event.target.value)}
                />
              </label>
            </div>

            <div className="wb-agent-option-stack">
              <div>
                <p className="wb-card-label">处理方式</p>
                <div className="wb-agent-choice-grid">
                  {AUTONOMY_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      className={`wb-agent-choice-card ${workDraft.autonomy === option.value ? "is-active" : ""}`}
                      onClick={() => updateWorkDraft("autonomy", option.value)}
                    >
                      <strong>{option.label}</strong>
                      <span>{option.description}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <p className="wb-card-label">职责标签</p>
                <div className="wb-chip-row">
                  {recommendedTags.map((tag) => (
                    <button
                      key={tag}
                      type="button"
                      className={`wb-chip-button ${workDraft.tags.includes(tag) ? "is-active" : ""}`}
                      onClick={() => toggleDraftToken("tags", tag)}
                    >
                      {formatTokenLabel(tag)}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <p className="wb-card-label">默认工具</p>
                <div className="wb-chip-row">
                  {recommendedTools.map((tool) => (
                    <button
                      key={tool}
                      type="button"
                      className={`wb-chip-button ${workDraft.selectedTools.includes(tool) ? "is-active" : ""}`}
                      onClick={() => toggleDraftToken("selectedTools", tool)}
                    >
                      {formatToolToken(tool, toolLabelByName)}
                    </button>
                  ))}
                </div>
              </div>

              <details className="wb-agent-details">
                <summary>展开高级配置</summary>
                <div className="wb-agent-option-stack">
                  <div>
                    <p className="wb-card-label">思考档位</p>
                    <div className="wb-chip-row">
                      {modelAliasOptions.map((alias) => (
                        <button
                          key={alias}
                          type="button"
                          className={`wb-chip-button ${workDraft.modelAlias === alias ? "is-active" : ""}`}
                          onClick={() => updateWorkDraft("modelAlias", alias)}
                        >
                          {alias}
                        </button>
                      ))}
                    </div>
                    <p className="wb-inline-note">
                      {MODEL_ALIAS_HINTS[workDraft.modelAlias] ?? "用于决定默认模型档位。"}
                    </p>
                  </div>

                  <div>
                    <p className="wb-card-label">工具范围</p>
                    <div className="wb-chip-row">
                      {toolProfileOptions.map((profile) => (
                        <button
                          key={profile}
                          type="button"
                          className={`wb-chip-button ${workDraft.toolProfile === profile ? "is-active" : ""}`}
                          onClick={() => updateWorkDraft("toolProfile", profile)}
                        >
                          {formatToolProfile(profile)}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </details>
            </div>

            <div className="wb-inline-actions wb-inline-actions-wrap">
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={handleResetWorkAgent}
              >
                撤回编辑
              </button>
              {editingKind === "instance" ? (
                <button
                  type="button"
                  className="wb-button wb-button-tertiary"
                  onClick={handleForkTemplateFromInstance}
                  disabled={!selectedWorkAgent || selectedWorkAgent.kind !== "instance"}
                >
                  另存为草案
                </button>
              ) : (
                <button
                  type="button"
                  className="wb-button wb-button-tertiary"
                  onClick={handleCreateInstanceFromTemplate}
                  disabled={!selectedWorkAgent || selectedWorkAgent.kind !== "template"}
                >
                  按草案新建实例
                </button>
              )}
              <button
                type="button"
                className="wb-button wb-button-primary"
                onClick={handleSaveWorkAgent}
              >
                {editingKind === "instance"
                  ? editorMode === "create"
                    ? "保存实例草案"
                    : "保存实例调整"
                  : editorMode === "create"
                    ? "保存草案"
                    : "保存草案调整"}
              </button>
            </div>
          </aside>
        </div>
      </section>
      ) : null}
    </div>
  );
}
