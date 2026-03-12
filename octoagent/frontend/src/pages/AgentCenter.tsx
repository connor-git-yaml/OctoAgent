import { startTransition, useDeferredValue, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import type {
  AgentProfileItem,
  ControlPlaneSnapshot,
  PolicyProfileItem,
  ProjectOption,
  SetupReviewSummary,
  SkillGovernanceItem,
  WorkerCapabilityProfile,
  WorkProjectionItem,
  WorkspaceOption,
} from "../types";
import { formatDateTime } from "../workbench/utils";

type WorkerCatalogView = "instances" | "templates";
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
    description: "同一个 Project 下默认沿用这套主 Agent 设置。",
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
    label: "Worker 模板",
    title: "模板决定以后新建 Worker 时的起点",
    description: "模板不会直接改动当前运行中的实例，只影响以后怎么创建。",
  },
} as const;

const DEFAULT_PERSONA =
  "你是我的 Butler，也是长期协作的 Agent 管家。你要持续维护目标、上下文和节奏，先梳理事实与下一步，再安排合适的 Worker；遇到高风险、不可逆或越权动作时，先停下来向我确认。";

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
  const workerProfiles = snapshot!.resources.capability_pack.pack.worker_profiles;
  const toolLabelByName = Object.fromEntries(
    snapshot!.resources.capability_pack.pack.tools.map((tool) => [tool.tool_name, tool.label])
  ) as Record<string, string>;
  const workerProfilesByType = Object.fromEntries(
    workerProfiles.map((profile) => [profile.worker_type, profile])
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
    "先把 Butler 的身份、边界和默认落点定稳，再把具体任务交给合适的 Worker。"
  );
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
    setActiveCatalog(kind === "instance" ? "instances" : "templates");
    setEditorMode("create");
    setSelectedWorkAgentId("");
    setSelectedWorkAgentIds([]);
    setWorkDraft(buildEmptyWorkDraft(primaryDraft, kind));
    setFlashMessage(
      kind === "instance"
        ? "先说明这个 Worker 实例要负责什么，再决定它落在哪个 Project 和 Workspace。"
        : "模板只定义以后新建时的默认做法，不会直接影响已经在运行的实例。"
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
            ? `已基于系统模板另存一份自定义模板「${createdAgent.name}」。`
            : `已创建模板「${createdAgent.name}」。`
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
        : `已更新模板「${nextAgent.name}」。`
    );
  }

  function handleResetWorkAgent() {
    if (editorMode === "create") {
      setWorkDraft(buildEmptyWorkDraft(primaryDraft, workDraft.kind));
      setFlashMessage(workDraft.kind === "instance" ? "已重置实例草案。" : "已重置模板草案。");
      return;
    }
    if (!selectedWorkAgent) {
      return;
    }
    setWorkDraft(toDraft(selectedWorkAgent));
    setFlashMessage(
      selectedWorkAgent.kind === "instance"
        ? `已撤回实例「${selectedWorkAgent.name}」的未保存改动。`
        : `已撤回模板「${selectedWorkAgent.name}」的未保存改动。`
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
    setActiveCatalog("templates");
    setEditorMode("create");
    setSelectedWorkAgentId("");
    setSelectedWorkAgentIds([]);
    setWorkDraft(forkTemplateFromAgent(selectedWorkAgent));
    setFlashMessage("已把当前实例复制成模板草案。清理掉只属于当前运行态的内容后再保存。");
  }

  function handleCreateInstanceFromTemplate() {
    if (!selectedWorkAgent || selectedWorkAgent.kind !== "template") {
      return;
    }
    setActiveCatalog("instances");
    setEditorMode("create");
    setSelectedWorkAgentId("");
    setSelectedWorkAgentIds([]);
    setWorkDraft(createInstanceFromTemplate(selectedWorkAgent));
    setFlashMessage("已按当前模板生成一个新的 Worker 实例草案。");
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
          <h1>让 Butler 管全局，把 Worker 留给具体工作</h1>
          <p>
            Butler 负责理解你的目标、维护上下文连续性、安排合适的 Worker，并在关键风险点先请你确认；
            Worker 实例只处理具体执行，模板只定义以后怎么创建。
          </p>
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
            <p className="wb-card-label">Butler</p>
            <strong>{savedPrimary.name}</strong>
            <span>{formatToolProfile(savedPrimary.toolProfile)}</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">运行中的 Worker</p>
            <strong>{workInstances.length}</strong>
            <span>活跃 {activeWorkAgents} / 待处理 {attentionWorkAgents}</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">记忆边界</p>
            <strong>{recallPresetLabel ?? "自定义"}</strong>
            <span>
              Vault {primaryDraft.memoryAccessPolicy.allowVault ? "已允许" : "关闭"} / 历史
              {primaryDraft.memoryAccessPolicy.includeHistory ? "已纳入" : "未纳入"}
            </span>
          </article>
        </div>
      </section>

      <div className="wb-butler-summary-grid">
        <article className="wb-butler-summary-card is-accent">
          <p className="wb-card-label">Butler 定位</p>
          <strong>总控、调度、护栏都在这里</strong>
          <span>这里定义 Butler 的身份、默认落点、审批边界和默认记忆边界。</span>
        </article>
        <article className="wb-butler-summary-card">
          <p className="wb-card-label">默认落点</p>
          <strong>{findProjectName(availableProjects, savedPrimary.projectId)}</strong>
          <span>{findWorkspaceName(availableWorkspaces, savedPrimary.workspaceId)}</span>
        </article>
        <article className="wb-butler-summary-card">
          <p className="wb-card-label">模型与治理</p>
          <strong>
            {savedPrimary.modelAlias} · {formatToolProfile(savedPrimary.toolProfile)}
          </strong>
          <span>{currentPolicy?.label ?? savedPrimary.policyProfileId}</span>
        </article>
        <article className={`wb-butler-summary-card ${pendingChanges > 0 ? "is-warning" : ""}`}>
          <p className="wb-card-label">待确认改动</p>
          <strong>{pendingChanges > 0 ? `${pendingChanges} 处` : "已同步"}</strong>
          <span>{primaryDirty ? "Butler 草案未保存" : "Butler 已同步到底层配置"}</span>
        </article>
      </div>

      <div className="wb-inline-banner is-muted">
        <strong>当前提示</strong>
        <span>{flashMessage}</span>
      </div>

      <div className="wb-agent-layout">
        <section className="wb-panel wb-butler-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">Butler（主 Agent）</p>
              <h3>先定义 Butler 的身份、边界和默认落点</h3>
              <p className="wb-panel-copy">
                `Agents` 现在就是 Butler 的唯一入口。名称、Persona、审批强度、默认模型和工具权限都在这里统一保存。
              </p>
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
              <p>{MODEL_ALIAS_HINTS[primaryDraft.modelAlias] ?? "控制 Butler 默认思考档位。"}</p>
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
              <small>
                这会决定 Butler 对外的语气、优先级和调度方式。建议强调“统筹、护栏、分派 Worker”。
              </small>
              <textarea
                rows={4}
                className="wb-textarea-prose"
                value={primaryDraft.personaSummary}
                placeholder="例如：你是我的 Butler，也是负责长期协作节奏的 Agent 管家。请先梳理现状与下一步，再安排合适的 Worker。"
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
              <summary>展开 Butler 高级配置</summary>
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
                    `standard` 适合大多数 Butler；只有明确需要更宽的工具面时再切 `privileged`。
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
              <p className="wb-card-label">Butler 视角</p>
              <h3>把当前观察视角、运行提示和工作原则分开管理</h3>
              <p className="wb-panel-copy">
                `Agents` 负责 Butler 与 Worker；模型连接、渠道接入和平台级设置继续放在 `Settings`。
              </p>
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
                  <p className="wb-card-label">Butler 守则</p>
                  <strong>把目标澄清、分派、护栏和收口分开做</strong>
                </div>
              </div>
              <div className="wb-note-stack">
                <div className="wb-note">
                  <strong>先澄清目标</strong>
                  <span>Butler 先判断目标、上下文和风险，再决定要不要分派 Worker。</span>
                </div>
                <div className="wb-note">
                  <strong>再委派 Worker</strong>
                  <span>Research / Dev / Ops 只负责具体执行，Butler 负责拆分、合并、删除和交接。</span>
                </div>
                <div className="wb-note">
                  <strong>关键动作先确认</strong>
                  <span>高风险动作由 Butler 先拦住，再请你确认，不让具体 Worker 越过护栏。</span>
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

      <section className="wb-panel wb-worker-hub">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">Worker 管理</p>
            <h3>先看实例，再维护模板</h3>
            <p className="wb-panel-copy">
              把“谁在工作”与“以后怎么创建”拆开以后，页面会更容易维护，也更不容易误操作。
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
              ? "实例适合看当前负载、归属和合并拆分；模板适合整理以后新建时的默认做法。"
              : "模板只会作为新建起点。要处理当前工作，请切回运行中的 Worker。"}
          </span>
        </div>

        <div className="wb-worker-toolbar">
          <label className="wb-field">
            <span>{activeCatalog === "instances" ? "搜索实例" : "搜索模板"}</span>
            <input
              type="text"
              value={searchQuery}
              placeholder={
                activeCatalog === "instances"
                  ? "例如：开发、待处理、Primary Workspace"
                  : "例如：调研、默认工具、handoff"
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
                  按当前模板新建实例
                </button>
                <button
                  type="button"
                  className="wb-button wb-button-primary"
                  onClick={() => handleCreateDraft("template")}
                >
                  新建 Worker 模板
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
                  {activeCatalog === "instances" ? "实例" : "模板"}。
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
                            <span className="wb-card-label">模板</span>
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
                              setFlashMessage("已按当前模板生成一个新的 Worker 实例草案。");
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
                                "已把当前实例复制成模板草案。清理掉只属于当前运行态的内容后再保存。"
                              );
                            }}
                          >
                            另存为模板
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
                <p className="wb-card-label">{editingKind === "instance" ? "实例编辑器" : "模板编辑器"}</p>
                <h3>
                  {editingKind === "instance"
                    ? editorMode === "create"
                      ? "创建新的 Worker 实例"
                      : "调整当前 Worker 实例"
                    : editorMode === "create"
                      ? "创建新的 Worker 模板"
                      : "调整当前 Worker 模板"}
                </h3>
              </div>
              <span className="wb-chip">
                {editorMode === "create"
                  ? editingKind === "instance"
                    ? "新实例草案"
                    : "新模板草案"
                  : selectedWorkAgent?.name ?? "未选择"}
              </span>
            </div>

            <div className="wb-inline-banner is-muted">
              <strong>{editingKind === "instance" ? "你正在编辑实例" : "你正在编辑模板"}</strong>
              <span>
                {editingKind === "instance"
                  ? "实例对应当前分工。这里改的是归属、角色和默认做法，运行指标仅作参考。"
                  : "模板只影响以后怎么新建。系统模板不会被直接覆盖，保存时会生成你的自定义模板。"}
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
                  {editingKind === "instance" ? "当前状态" : "模板用途"}
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
                <span>{editingKind === "instance" ? "实例名称" : "模板名称"}</span>
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
                <span>{editingKind === "instance" ? "当前职责说明" : "这个模板适合什么场景"}</span>
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
                  另存为模板
                </button>
              ) : (
                <button
                  type="button"
                  className="wb-button wb-button-tertiary"
                  onClick={handleCreateInstanceFromTemplate}
                  disabled={!selectedWorkAgent || selectedWorkAgent.kind !== "template"}
                >
                  按模板新建实例
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
                    ? "保存模板"
                    : "保存模板调整"}
              </button>
            </div>
          </aside>
        </div>
      </section>
    </div>
  );
}
