import { startTransition, useDeferredValue, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import AgentHeroSection from "../domains/agents/AgentHeroSection";
import {
  buildCapabilityProviderEntries,
  buildCapabilitySelectionState,
  buildRootAgentPayload,
  buildRootAgentStudioDraftFromReview,
  buildSkillSelectionPayload,
  buildSkillSelectionState,
  buildSkillSelectionSyncKey,
  findProjectName,
  findWorkspaceName,
  formatTokenLabel,
  formatWorkerProfileOrigin,
  formatWorkerProfileStatus,
  joinStudioList,
  mergeCapabilitySelectionMetadata,
  normalizeWorkspaceForProject,
  parseStudioList,
  projectSelectOptions,
  readNestedString,
  toRecord,
  TOKEN_LABELS,
  type CapabilityProviderEntry,
  type RootAgentReviewResult,
  uniqueStrings,
  workspaceSelectOptions,
} from "../domains/agents/agentCenterData";
import ButlerWorkspaceSection from "../domains/agents/ButlerWorkspaceSection";
import ProviderWorkspaceSection from "../domains/agents/ProviderWorkspaceSection";
import TemplateWorkspaceSection from "../domains/agents/TemplateWorkspaceSection";
import TemplateRuntimeRail from "../domains/agents/TemplateRuntimeRail";
import { useRootAgentStudio } from "../domains/agents/useRootAgentStudio";
import WorkerWorkspaceSection from "../domains/agents/WorkerWorkspaceSection";
import type {
  AgentProfileItem,
  ControlPlaneSnapshot,
  PolicyProfileItem,
  ProjectOption,
  SetupReviewSummary,
  WorkerCapabilityProfile,
  WorkerProfileItem,
  WorkerProfilesDocument,
  WorkProjectionItem,
  WorkspaceOption,
} from "../types";
import {
  formatDateTime,
  formatWorkerTemplateName,
} from "../workbench/utils";

type WorkerCatalogView = "instances" | "templates";
type AgentWorkspaceView = "butler" | "templates" | "workers" | "providers";
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
  metadata: Record<string, unknown>;
  capabilitySelection: Record<string, boolean>;
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
  providers: {
    label: "Providers",
    title: "把能力目录和 Agent 绑定收回到同一个主路径",
    description: "在这里管理 Skill / MCP Provider，并设置这个 Project 的默认启用范围。",
  },
};

function parseWorkspaceView(rawValue: string | null): AgentWorkspaceView {
  if (rawValue === "butler" || rawValue === "templates" || rawValue === "workers") {
    return rawValue;
  }
  if (rawValue === "providers") {
    return "providers";
  }
  return "templates";
}

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

function buildPrimaryAgentPayload(
  primaryDraft: PrimaryAgentDraft,
  capabilityProviderEntries: CapabilityProviderEntry[]
): Record<string, unknown> {
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
    metadata: mergeCapabilitySelectionMetadata(
      primaryDraft.metadata,
      capabilityProviderEntries,
      primaryDraft.capabilitySelection
    ),
  };
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

function formatToolToken(toolName: string, toolLabelByName: Record<string, string>): string {
  return TOKEN_LABELS[toolName] ?? toolLabelByName[toolName] ?? formatTokenLabel(toolName);
}

function formatScope(scope: string): string {
  return SCOPE_OPTIONS.find((option) => option.value === scope)?.label ?? formatTokenLabel(scope);
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
      metadata:
        activeProfile.metadata &&
        typeof activeProfile.metadata === "object" &&
        !Array.isArray(activeProfile.metadata)
          ? (activeProfile.metadata as Record<string, unknown>)
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
  const capabilityProviderEntries = buildCapabilityProviderEntries(snapshot);
  const metadata = activeProfile?.metadata ?? {};

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
    metadata,
    capabilitySelection: buildCapabilitySelectionState(capabilityProviderEntries, metadata),
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
  const [searchParams, setSearchParams] = useSearchParams();
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
  const capabilityProviderEntries = useMemo(
    () => buildCapabilityProviderEntries(snapshot!),
    [snapshot!.resources.skill_provider_catalog.generated_at, snapshot!.resources.mcp_provider_catalog.generated_at, snapshot!.resources.skill_governance.generated_at]
  );
  const skillCapabilityEntries = capabilityProviderEntries.filter((item) => item.kind === "skill");
  const mcpCapabilityEntries = capabilityProviderEntries.filter((item) => item.kind === "mcp");
  const projectCapabilitySelectionSyncKey = buildSkillSelectionSyncKey(skillGovernance.items);
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
  const [activeWorkspaceView, setActiveWorkspaceView] = useState<AgentWorkspaceView>(() =>
    parseWorkspaceView(searchParams.get("view"))
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
  const [projectCapabilitySelection, setProjectCapabilitySelection] = useState<
    Record<string, boolean>
  >(() => buildSkillSelectionState(skillGovernance.items));
  const [flashMessage, setFlashMessage] = useState(
    "先确认默认配置，再从右侧启动任务或查看当前运行状态。"
  );
  const {
    selectedRootAgentId,
    setSelectedRootAgentId,
    selectedRootAgentProfile,
    rootAgentDraft,
    setRootAgentDraft,
    rootAgentDraftDirty,
    rootAgentReview,
    setRootAgentReview,
    rootAgentRevisions,
    rootAgentRevisionLoading,
    rootAgentRevisionError,
    rootAgentSpawnObjective,
    setRootAgentSpawnObjective,
    setRootAgentEditorMode,
    selectRootAgentProfile,
    updateRootAgentDraft,
    updateRootAgentCapabilitySelection,
    updateRootAgentProject,
    appendRootAgentDraftValue,
    resetToFreshRootAgent,
  } = useRootAgentStudio({
    rootAgentProfiles,
    rootAgentProfilesGeneratedAt: rootAgentProfilesDocument.generated_at,
    selector,
    capabilityProviderEntries,
  });
  const deferredSearch = useDeferredValue(searchQuery);

  useEffect(() => {
    const nextPrimary = buildPrimaryAgentSeed(snapshot!);
    setSavedPrimary(nextPrimary);
    setPrimaryDraft(nextPrimary);
  }, [primarySeedSyncKey]);

  useEffect(() => {
    setProjectCapabilitySelection(buildSkillSelectionState(skillGovernance.items));
  }, [projectCapabilitySelectionSyncKey, skillGovernance.items]);

  useEffect(() => {
    const requestedView = parseWorkspaceView(searchParams.get("view"));
    setActiveWorkspaceView((current) => (current === requestedView ? current : requestedView));
  }, [searchParams]);

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
  const selectedProjectCapabilityCount = skillGovernance.items.filter(
    (item) => projectCapabilitySelection[item.item_id] ?? item.selected
  ).length;
  const blockedProjectCapabilityCount = skillGovernance.items.filter((item) => item.blocking).length;
  const unavailableProjectCapabilityCount = skillGovernance.items.filter(
    (item) => item.availability !== "available"
  ).length;
  const projectCapabilityDirty =
    JSON.stringify(buildSkillSelectionPayload(skillGovernance.items, projectCapabilitySelection)) !==
    JSON.stringify(buildSkillSelectionPayload(skillGovernance.items));
  const pendingChanges =
    Number(primaryDirty) + Number(workDirty) + Number(projectCapabilityDirty);
  const butlerBusy =
    busyActionId === "setup.review" || busyActionId === "setup.apply";
  const providerWorkspaceBusy = busyActionId === "skills.selection.save";
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
  const selectedPrimaryCapabilityCount = capabilityProviderEntries.filter(
    (item) => primaryDraft.capabilitySelection[item.selectionItemId] ?? item.defaultSelected
  ).length;
  const selectedRootAgentCapabilityCount = capabilityProviderEntries.filter(
    (item) => rootAgentDraft.capabilitySelection[item.selectionItemId] ?? item.defaultSelected
  ).length;

  function updatePrimary<Key extends keyof PrimaryAgentDraft>(
    key: Key,
    value: PrimaryAgentDraft[Key]
  ) {
    setPrimaryDraft((current) => ({ ...current, [key]: value }));
  }

  function updatePrimaryCapabilitySelection(itemId: string, selected: boolean) {
    setPrimaryDraft((current) => ({
      ...current,
      capabilitySelection: {
        ...current.capabilitySelection,
        [itemId]: selected,
      },
    }));
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
      }, capabilityProviderEntries),
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

  function setWorkspaceView(view: AgentWorkspaceView) {
    setActiveWorkspaceView(view);
    const nextParams = new URLSearchParams(searchParams);
    if (view === "templates") {
      nextParams.delete("view");
    } else {
      nextParams.set("view", view);
    }
    setSearchParams(nextParams, { replace: true });
  }

  function updateProjectCapabilitySelection(itemId: string, selected: boolean) {
    setProjectCapabilitySelection((current) => ({
      ...current,
      [itemId]: selected,
    }));
  }

  async function handleSaveProjectCapabilitySelection() {
    const result = await submitAction("skills.selection.save", {
      selection: buildSkillSelectionPayload(skillGovernance.items, projectCapabilitySelection),
    });
    if (result) {
      setFlashMessage("当前项目默认范围已保存，Butler 和 Worker 绑定会继续以这里为基线。");
    }
  }

  function handleResetProjectCapabilitySelection() {
    setProjectCapabilitySelection(buildSkillSelectionState(skillGovernance.items));
    setFlashMessage("Project 默认启用范围已恢复到当前已保存状态。");
  }

  function openWorkspaceView(view: AgentWorkspaceView) {
    setWorkspaceView(view);
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
    setWorkspaceView(kind === "instance" ? "workers" : "templates");
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
    setWorkspaceView("workers");
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
    setWorkspaceView("workers");
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
    setWorkspaceView("workers");
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
    setWorkspaceView("workers");
    setActiveCatalog("instances");
    setEditorMode("create");
    setSelectedWorkAgentId("");
    setSelectedWorkAgentIds([]);
    setWorkDraft(createInstanceFromTemplate(selectedWorkAgent));
    setFlashMessage("已按当前草案生成一个新的 Worker 实例草案。");
  }

  function handleCreateInstanceFromAgent(agent: WorkAgentItem) {
    setWorkspaceView("workers");
    setActiveCatalog("instances");
    setEditorMode("create");
    setSelectedWorkAgentId("");
    setSelectedWorkAgentIds([]);
    setWorkDraft(createInstanceFromTemplate(agent));
    setFlashMessage("已按当前草案生成一个新的 Worker 实例草案。");
  }

  function handleCreateTemplateFromAgent(agent: WorkAgentItem) {
    setWorkspaceView("workers");
    setActiveCatalog("templates");
    setEditorMode("create");
    setSelectedWorkAgentId("");
    setSelectedWorkAgentIds([]);
    setWorkDraft(forkTemplateFromAgent(agent));
    setFlashMessage("已把当前实例复制成草案。清理掉只属于当前运行态的内容后再保存。");
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
    setWorkspaceView("templates");
    const name =
      profile.origin_kind === "builtin" ? `${profile.name} Copy` : `${profile.name} 副本`;
    const result = await submitAction("worker_profile.clone", {
      source_profile_id: profile.profile_id,
      name,
    });
    const payload = result?.data ?? {};
    const nextDraft = buildRootAgentStudioDraftFromReview(
      payload.review,
      selector,
      capabilityProviderEntries
    );
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
      draft: buildRootAgentPayload(rootAgentDraft, capabilityProviderEntries),
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
      draft: buildRootAgentPayload(rootAgentDraft, capabilityProviderEntries),
      publish,
      change_summary: publish ? "通过 AgentCenter 发布" : "通过 AgentCenter 更新草稿",
    });
    const payload = result?.data ?? {};
    const nextDraft = buildRootAgentStudioDraftFromReview(
      payload.review,
      selector,
      capabilityProviderEntries
    );
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
    const nextDraft = buildRootAgentStudioDraftFromReview(
      payload.review,
      selector,
      capabilityProviderEntries
    );
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
    setWorkspaceView("templates");
    resetToFreshRootAgent();
    setFlashMessage("开始一个新的 Worker 模板草稿。");
  }

  function renderCapabilityProviderSection(
    title: string,
    entries: CapabilityProviderEntry[],
    selection: Record<string, boolean>,
    onToggle: (itemId: string, selected: boolean) => void,
    manageTo: string
  ) {
    if (entries.length === 0) {
      return (
        <div className="wb-note">
          <strong>{title}</strong>
          <span>当前还没有可勾选的 Provider，先去对应 Provider 页安装，再回这里绑定。</span>
        </div>
      );
    }
    return (
      <div className="wb-note-stack">
        <div className="wb-root-agent-column-head">
          <strong>{title}</strong>
          <Link className="wb-button wb-button-tertiary" to={manageTo}>
            去管理
          </Link>
        </div>
        {entries.map((item) => {
          const selected = selection[item.selectionItemId] ?? item.defaultSelected;
          return (
            <label key={item.selectionItemId} className="wb-note wb-capability-toggle">
              <div>
                <strong>{item.label}</strong>
                <span>{item.description || item.providerId}</span>
                <small>
                  默认 {item.defaultSelected ? "开启" : "关闭"} · 当前 {item.availability}
                </small>
                <div className="wb-chip-row">
                  {item.tags.map((tag) => (
                    <span key={`${item.selectionItemId}:${tag}`} className="wb-chip">
                      {formatTokenLabel(tag)}
                    </span>
                  ))}
                </div>
              </div>
              <input
                type="checkbox"
                checked={selected}
                disabled={!item.enabled}
                onChange={(event) => onToggle(item.selectionItemId, event.target.checked)}
              />
            </label>
          );
        })}
      </div>
    );
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
      <AgentHeroSection
        currentProjectName={currentProject?.name ?? selector.current_project_id}
        currentWorkspaceName={currentWorkspace?.name ?? selector.current_workspace_id}
        reviewTone={reviewTone(primaryReview)}
        primaryReady={primaryReview.ready}
        primaryBlockingCount={primaryReview.blocking_reasons.length}
        primaryWarningCount={primaryReview.warnings.length}
        pendingChanges={pendingChanges}
        savedPrimaryName={savedPrimary.name}
        savedPrimaryToolProfileLabel={formatToolProfile(savedPrimary.toolProfile)}
        rootAgentProfilesCount={rootAgentProfiles.length}
        defaultRootAgentName={defaultRootAgentName}
        workInstancesCount={workInstances.length}
        activeWorkAgents={activeWorkAgents}
        attentionWorkAgents={attentionWorkAgents}
        savedPrimaryProjectName={findProjectName(availableProjects, savedPrimary.projectId)}
        savedPrimaryWorkspaceName={findWorkspaceName(
          availableWorkspaces,
          savedPrimary.workspaceId
        )}
        savedPrimaryModelAlias={savedPrimary.modelAlias}
        currentPolicyLabel={currentPolicy?.label ?? savedPrimary.policyProfileId}
        primaryDirty={primaryDirty}
        flashMessage={flashMessage}
        activeWorkspaceView={activeWorkspaceView}
        workspaceViews={(Object.keys(AGENT_WORKSPACE_COPY) as AgentWorkspaceView[]).map((view) => ({
          id: view,
          ...AGENT_WORKSPACE_COPY[view],
        }))}
        onOpenWorkspaceView={(viewId) => openWorkspaceView(viewId as AgentWorkspaceView)}
      />

      {activeWorkspaceView === "butler" ? (
        <ButlerWorkspaceSection
          primaryDirty={primaryDirty}
          butlerBusy={butlerBusy}
          draft={primaryDraft}
          scopeOptions={SCOPE_OPTIONS}
          primaryProjectOptions={primaryProjectOptions}
          primaryWorkspaceOptions={primaryWorkspaceOptions}
          review={{
            tone: reviewTone(primaryReview),
            headline: reviewHeadline(primaryReview),
            summary: reviewSummary(primaryReview),
            nextActions: primaryReview.next_actions,
          }}
          summary={{
            primaryProjectName: findProjectName(availableProjects, primaryDraft.projectId),
            primaryWorkspaceName: findWorkspaceName(availableWorkspaces, primaryDraft.workspaceId),
            currentPolicyLabel: currentPolicy?.label ?? primaryDraft.policyProfileId,
            primaryToolProfileLabel: formatToolProfile(primaryDraft.toolProfile),
            primaryModelAliasHint:
              MODEL_ALIAS_HINTS[primaryDraft.modelAlias] ?? "用于决定默认模型档位。",
            recallPresetLabel: recallPresetLabel ?? "自定义",
            recallPresetDescription:
              MEMORY_RECALL_PRESETS.find((preset) => preset.label === recallPresetLabel)
                ?.description ?? "已按当前项目做细化。",
            selectedPrimaryCapabilityCount,
          }}
          context={{
            contextProjectId,
            contextWorkspaceId,
            availableProjects,
            availableContextWorkspaces,
            canSwitchContext:
              busyActionId !== "project.select" &&
              !(
                contextProjectId === selector.current_project_id &&
                contextWorkspaceId === selector.current_workspace_id
              ),
          }}
          projectFilter={projectFilter}
          projectFilterStats={projectFilterStats}
          totalWorkInstances={workInstances.length}
          totalWorkTemplates={workTemplates.length}
          policyCards={policyProfiles.map(renderPolicyCard)}
          modelAliasButtons={modelAliasOptions.map((alias) => (
            <button
              key={alias}
              type="button"
              className={`wb-chip-button ${primaryDraft.modelAlias === alias ? "is-active" : ""}`}
              onClick={() => updatePrimary("modelAlias", alias)}
            >
              {alias}
            </button>
          ))}
          toolProfileButtons={toolProfileOptions.map((profile) => (
            <button
              key={profile}
              type="button"
              className={`wb-chip-button ${primaryDraft.toolProfile === profile ? "is-active" : ""}`}
              onClick={() => updatePrimary("toolProfile", profile)}
            >
              {formatToolProfile(profile)}
            </button>
          ))}
          recallPresetButtons={MEMORY_RECALL_PRESETS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              className={`wb-chip-button ${
                detectRecallPreset(primaryDraft.memoryRecall) === preset.label ? "is-active" : ""
              }`}
              onClick={() => applyPrimaryMemoryPreset(preset.id)}
            >
              {preset.label}
            </button>
          ))}
          skillCapabilitySection={renderCapabilityProviderSection(
            "Skills",
            skillCapabilityEntries,
            primaryDraft.capabilitySelection,
            updatePrimaryCapabilitySelection,
            "/agents/skills"
          )}
          mcpCapabilitySection={renderCapabilityProviderSection(
            "MCP",
            mcpCapabilityEntries,
            primaryDraft.capabilitySelection,
            updatePrimaryCapabilitySelection,
            "/agents/mcp"
          )}
          onResetPrimary={handleResetPrimary}
          onReviewPrimary={() => void handleReviewPrimary()}
          onApplyPrimary={() => void handleApplyPrimary()}
          onUpdatePrimaryField={updatePrimary}
          onUpdatePrimaryProject={updatePrimaryProject}
          onUpdatePrimaryMemoryAccess={updatePrimaryMemoryAccess}
          onUpdatePrimaryMemoryRecallField={(key, value) =>
            setPrimaryDraft((current) => ({
              ...current,
              memoryRecall: {
                ...current.memoryRecall,
                [key]: value,
              },
            }))
          }
          onContextProjectChange={(projectId) => {
            setContextProjectId(projectId);
            setContextWorkspaceId(
              normalizeWorkspaceForProject(
                availableWorkspaces,
                projectId,
                contextWorkspaceId
              )
            );
          }}
          onContextWorkspaceChange={setContextWorkspaceId}
          onSwitchProjectContext={() => void handleSwitchProjectContext()}
          onSetProjectFilter={setProjectFilter}
        />
      ) : null}

      {activeWorkspaceView === "templates" ? (
        <TemplateWorkspaceSection
          rootAgentProfilesCount={rootAgentProfiles.length}
          builtinRootAgentProfiles={builtinRootAgentProfiles}
          customRootAgentProfiles={customRootAgentProfiles}
          rootAgentActiveWorkCount={rootAgentActiveWorkCount}
          rootAgentRunningWorkCount={rootAgentRunningWorkCount}
          rootAgentAttentionWorkCount={rootAgentAttentionWorkCount}
          defaultRootAgentName={defaultRootAgentName}
          defaultRootAgentId={defaultRootAgentId}
          selectedRootAgentDisplayName={selectedRootAgentDisplayName}
          selectedRootAgentSummaryLabel={
            selectedRootAgentProfile
              ? `${formatScope(selectedRootAgentProfile.scope)} / ${findProjectName(
                  availableProjects,
                  selectedRootAgentProfile.project_id || selector.current_project_id
                )}`
              : "还没有保存"
          }
          latestRootAgentUpdateLabel={latestRootAgentUpdate ? formatDateTime(latestRootAgentUpdate) : "未记录"}
          latestRootAgentContextLabel={`上下文 ${
            selectedRootAgentDynamicContext?.active_project_id || selector.current_project_id
          } / ${selectedRootAgentDynamicContext?.active_workspace_id || selector.current_workspace_id}`}
          selectedRootAgentId={selectedRootAgentId}
          selectedRootAgentProfile={selectedRootAgentProfile}
          selectedRootAgentDisplayStatus={selectedRootAgentDisplayStatus}
          selectedRootAgentDisplayStatusLabel={formatWorkerProfileStatus(selectedRootAgentDisplayStatus)}
          selectedRootAgentIsDefault={selectedRootAgentIsDefault}
          rootAgentDraftDirty={rootAgentDraftDirty}
          selectedRootAgentOriginLabel={formatWorkerProfileOrigin(
            selectedRootAgentProfile?.origin_kind ?? "custom"
          )}
          selectedRootAgentScopeLabel={formatScope(rootAgentDraft.scope)}
          selectedRootAgentIsBuiltin={selectedRootAgentIsBuiltin}
          selectedRootAgentEditable={selectedRootAgentEditable}
          selectedRootAgentCapabilityCount={selectedRootAgentCapabilityCount}
          rootAgentDraft={rootAgentDraft}
          capabilityWorkerProfiles={capabilityWorkerProfiles}
          rootAgentProjectOptions={rootAgentProjectOptions}
          modelAliasOptions={modelAliasOptions}
          toolProfileOptions={toolProfileOptions}
          rootAgentSuggestedToolGroups={rootAgentSuggestedToolGroups}
          rootAgentSuggestedTools={rootAgentSuggestedTools}
          rootAgentSuggestedRuntimeKinds={rootAgentSuggestedRuntimeKinds}
          rootAgentSuggestedTags={rootAgentSuggestedTags}
          rootAgentReview={rootAgentReview}
          rootAgentReviewDiff={rootAgentReviewDiff}
          busyActionId={busyActionId}
          skillCapabilitySection={renderCapabilityProviderSection(
            "Skills",
            skillCapabilityEntries,
            rootAgentDraft.capabilitySelection,
            updateRootAgentCapabilitySelection,
            "/agents/skills"
          )}
          mcpCapabilitySection={renderCapabilityProviderSection(
            "MCP",
            mcpCapabilityEntries,
            rootAgentDraft.capabilitySelection,
            updateRootAgentCapabilitySelection,
            "/agents/mcp"
          )}
          runtimeRail={
            <TemplateRuntimeRail
              selectedRootAgentProfile={selectedRootAgentProfile}
              selectedRootAgentDynamicContext={selectedRootAgentDynamicContext}
              selectedRootAgentMountedTools={selectedRootAgentMountedTools}
              selectedRootAgentBlockedTools={selectedRootAgentBlockedTools}
              selectedRootAgentDiscoveryEntrypoints={selectedRootAgentDiscoveryEntrypoints}
              selectedRootAgentCapabilities={selectedRootAgentCapabilities}
              selectedRootAgentWarnings={selectedRootAgentWarnings}
              selectedRootAgentDisplayName={selectedRootAgentDisplayName}
              selectedRootAgentWorks={selectedRootAgentWorks}
              rootAgentSpawnObjective={rootAgentSpawnObjective}
              rootAgentRevisionLoading={rootAgentRevisionLoading}
              rootAgentRevisionError={rootAgentRevisionError}
              rootAgentRevisions={rootAgentRevisions}
              busyActionId={busyActionId}
              availableProjects={availableProjects}
              availableWorkspaces={availableWorkspaces}
              selectorProjectId={selector.current_project_id}
              selectorWorkspaceId={selector.current_workspace_id}
              onSpawnObjectiveChange={setRootAgentSpawnObjective}
              onSpawnFromRootAgent={(profileId) => void handleSpawnFromRootAgent(profileId)}
              onExtractRootAgentFromWork={(work) => void handleExtractRootAgentFromWork(work)}
              formatDateTime={formatDateTime}
              formatWorkerType={formatWorkerType}
              formatToolToken={formatToolToken}
              findProjectName={findProjectName}
              findWorkspaceName={findWorkspaceName}
              toolLabelByName={toolLabelByName}
            />
          }
          onCreateFreshRootAgent={handleCreateFreshRootAgent}
          onSelectRootAgentProfile={selectRootAgentProfile}
          onUpdateRootAgentDraft={updateRootAgentDraft}
          onUpdateRootAgentProject={updateRootAgentProject}
          onApplyRootAgentArchetypeDefaults={applyRootAgentArchetypeDefaults}
          onAppendRootAgentDraftValue={appendRootAgentDraftValue}
          onReviewRootAgentDraft={() => void handleReviewRootAgentDraft()}
          onSaveRootAgentDraft={(publish) => void handleSaveRootAgentDraft(publish)}
          onCreateRootAgentDraftFromTemplate={(profile) =>
            void handleCreateRootAgentDraftFromTemplate(profile)
          }
          onBindRootAgentDefault={() => void handleBindRootAgentDefault()}
          onSwitchToRootAgentContext={(profile) => void handleSwitchToRootAgentContext(profile)}
          onArchiveRootAgent={() => void handleArchiveRootAgent()}
          formatWorkerTemplateName={formatWorkerTemplateName}
          formatWorkerProfileStatus={formatWorkerProfileStatus}
          formatWorkerProfileOrigin={formatWorkerProfileOrigin}
          formatWorkerType={formatWorkerType}
          formatToolProfile={formatToolProfile}
          formatScope={formatScope}
          formatTokenLabel={formatTokenLabel}
          formatToolToken={formatToolToken}
          findProjectName={findProjectName}
          availableProjects={availableProjects}
          toolLabelByName={toolLabelByName}
        />
      ) : null}

      {activeWorkspaceView === "providers" ? (
        <ProviderWorkspaceSection
          skillProviderCount={snapshot!.resources.skill_provider_catalog.items.length}
          customSkillProviderCount={snapshot!.resources.skill_provider_catalog.items.filter(
            (item) => item.source_kind !== "builtin"
          ).length}
          builtinSkillProviderCount={snapshot!.resources.skill_provider_catalog.items.filter(
            (item) => item.source_kind === "builtin"
          ).length}
          mcpProviderCount={snapshot!.resources.mcp_provider_catalog.items.length}
          enabledMcpProviderCount={snapshot!.resources.mcp_provider_catalog.items.filter(
            (item) => item.enabled
          ).length}
          healthyMcpProviderCount={snapshot!.resources.mcp_provider_catalog.items.filter(
            (item) => item.status === "ready"
          ).length}
          selectedCapabilityCount={selectedProjectCapabilityCount}
          blockedCapabilityCount={blockedProjectCapabilityCount}
          unavailableCapabilityCount={unavailableProjectCapabilityCount}
          capabilitySelection={projectCapabilitySelection}
          capabilityItems={skillGovernance.items}
          capabilityDirty={projectCapabilityDirty}
          capabilitySaveBusy={providerWorkspaceBusy}
          onCapabilitySelectionChange={updateProjectCapabilitySelection}
          onSaveCapabilitySelection={() => void handleSaveProjectCapabilitySelection()}
          onResetCapabilitySelection={handleResetProjectCapabilitySelection}
          onOpenButler={() => openWorkspaceView("butler")}
          onOpenTemplates={() => openWorkspaceView("templates")}
        />
      ) : null}

      {activeWorkspaceView === "workers" ? (
        <WorkerWorkspaceSection
          activeCatalog={activeCatalog}
          catalogCopy={CATALOG_COPY}
          searchQuery={searchQuery}
          selectedWorkAgentId={selectedWorkAgentId}
          selectedWorkAgentIds={selectedWorkAgentIds}
          visibleCatalogItems={visibleCatalogItems}
          editorMode={editorMode}
          editingKind={editingKind}
          selectedWorkAgent={selectedWorkAgent}
          workDraft={workDraft}
          workInstances={workInstances}
          selectedTemplateUsageCount={selectedTemplateUsageCount}
          recommendedTags={recommendedTags}
          recommendedTools={recommendedTools}
          toolLabelByName={toolLabelByName}
          modelAliasOptions={modelAliasOptions}
          toolProfileOptions={toolProfileOptions}
          workProjectOptions={workProjectOptions}
          workWorkspaceOptions={workWorkspaceOptions}
          availableProjects={availableProjects}
          availableWorkspaces={availableWorkspaces}
          autonomyOptions={AUTONOMY_OPTIONS}
          modelAliasHints={MODEL_ALIAS_HINTS}
          workAgentStatusLabels={WORK_AGENT_STATUS_LABELS}
          workAgentSourceLabels={WORK_AGENT_SOURCE_LABELS}
          onOpenCatalog={openCatalog}
          onSearchQueryChange={setSearchQuery}
          onMergeWorkAgents={handleMergeWorkAgents}
          onSplitWorkAgent={handleSplitWorkAgent}
          onCreateDraft={handleCreateDraft}
          onCreateInstanceFromCurrentTemplate={handleCreateInstanceFromTemplate}
          onToggleWorkAgentSelection={toggleWorkAgentSelection}
          onSelectWorkAgent={selectWorkAgent}
          onCreateInstanceFromTemplate={handleCreateInstanceFromAgent}
          onSaveTemplateFromInstance={handleCreateTemplateFromAgent}
          onUpdateWorkField={updateWorkDraft}
          onUpdateWorkProject={updateWorkProject}
          onUpdateWorkerType={updateWorkerType}
          onToggleDraftToken={toggleDraftToken}
          onResetWorkAgent={handleResetWorkAgent}
          onForkTemplateFromInstance={handleForkTemplateFromInstance}
          onSaveWorkAgent={handleSaveWorkAgent}
          formatDateTime={formatDateTime}
          formatWorkerType={formatWorkerType}
          formatAutonomy={formatAutonomy}
          formatToolProfile={formatToolProfile}
          formatProjectWorkspace={formatProjectWorkspace}
          formatTokenLabel={formatTokenLabel}
          formatToolToken={formatToolToken}
          workAgentBadge={workAgentBadge}
        />
      ) : null}
    </div>
  );
}
