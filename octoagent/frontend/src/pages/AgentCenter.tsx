import { startTransition, useDeferredValue, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import type {
  AgentProfileItem,
  ControlPlaneSnapshot,
  PolicyProfileItem,
  ProjectOption,
  WorkerCapabilityProfile,
  WorkProjectionItem,
  WorkspaceOption,
} from "../types";
import { formatDateTime } from "../workbench/utils";

type WorkerCatalogView = "instances" | "templates";
type WorkUnitKind = "instance" | "template";
type WorkAgentStatus = "active" | "syncing" | "attention" | "paused" | "draft";
type WorkAgentSource = "runtime" | "capability" | "manual";

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
  general: "通用处理",
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
  general: "通用协作",
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
  "通用个人助手，优先帮助用户完成当前目标，并在必要时安排下一步。";

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
        name: "通用 Worker 模板",
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
  const initialPrimary = buildPrimaryAgentSeed(snapshot!);
  const initialAgents = buildWorkAgentSeeds(snapshot!);
  const initialSelection =
    initialAgents.find((agent) => agent.kind === "instance") ??
    initialAgents.find((agent) => agent.kind === "template") ??
    null;
  const [savedPrimary, setSavedPrimary] = useState(initialPrimary);
  const [primaryDraft, setPrimaryDraft] = useState(initialPrimary);
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
    "先分清三件事：主 Agent 决定默认上下文，Worker 实例反映当前分工，Worker 模板决定以后怎么新建。"
  );
  const deferredSearch = useDeferredValue(searchQuery);
  const syncKey = snapshot!.generated_at;

  useEffect(() => {
    const nextPrimary = buildPrimaryAgentSeed(snapshot!);
    const nextAgents = buildWorkAgentSeeds(snapshot!);
    const nextSelection =
      nextAgents.find((agent) => agent.kind === "instance") ??
      nextAgents.find((agent) => agent.kind === "template") ??
      null;
    setSavedPrimary(nextPrimary);
    setPrimaryDraft(nextPrimary);
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
  }, [selector.current_project_id, selector.current_workspace_id, snapshot, syncKey]);

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
  const templateDraftCount = workTemplates.filter((agent) => agent.source === "manual").length;
  const totalTasks = workInstances.reduce((sum, agent) => sum + agent.taskCount, 0);
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
  const policyLabel =
    policyProfiles.find((profile) => profile.profile_id === savedPrimary.policyProfileId)?.label ??
    savedPrimary.policyProfileId;
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

  function handleSavePrimary() {
    setSavedPrimary(primaryDraft);
    setFlashMessage("主 Agent 草案已暂存。真正写入底层配置时，请继续到设置页保存。");
  }

  function handleResetPrimary() {
    setPrimaryDraft(savedPrimary);
    setFlashMessage("主 Agent 草案已恢复到上一次同步状态。");
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
    <div className="wb-page">
      <section className="wb-hero wb-hero-agent">
        <div className="wb-hero-copy">
          <p className="wb-kicker">Agents</p>
          <h1>把总控、实例和模板分开看</h1>
          <p>
            主 Agent 决定默认项目、工作区和审批方式；Worker 实例反映当前谁在工作；Worker
            模板则负责以后新建时的默认起点。
          </p>
          <div className="wb-chip-row">
            <span className="wb-chip">当前项目 {currentProject?.name ?? selector.current_project_id}</span>
            <span className="wb-chip">当前工作区 {currentWorkspace?.name ?? selector.current_workspace_id}</span>
            <span className={`wb-chip ${pendingChanges > 0 ? "is-warning" : "is-success"}`}>
              {pendingChanges > 0 ? `待确认改动 ${pendingChanges}` : "当前无待确认改动"}
            </span>
          </div>
        </div>

        <div className="wb-hero-insights">
          <article className="wb-hero-metric">
            <p className="wb-card-label">主 Agent</p>
            <strong>{savedPrimary.name}</strong>
            <span>{findProjectName(availableProjects, savedPrimary.projectId)}</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">运行中的 Worker</p>
            <strong>{workInstances.length}</strong>
            <span>活跃 {activeWorkAgents} / 待处理 {attentionWorkAgents}</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">可复用模板</p>
            <strong>{workTemplates.length}</strong>
            <span>自定义 {templateDraftCount} / 总工作量 {totalTasks}</span>
          </article>
        </div>
      </section>

      <div className="wb-agent-rule-grid">
        <article className="wb-agent-rule-card">
          <p className="wb-card-label">主 Agent</p>
          <strong>决定默认上下文与审批强度</strong>
          <span>常改的是名称、默认 Project / Workspace、审批方式和 persona。</span>
        </article>
        <article className="wb-agent-rule-card">
          <p className="wb-card-label">Worker 实例</p>
          <strong>代表当前谁在负责什么</strong>
          <span>这里适合看负载、改归属、合并相近实例，或把一个实例拆小。</span>
        </article>
        <article className="wb-agent-rule-card">
          <p className="wb-card-label">Worker 模板</p>
          <strong>只影响以后怎么创建</strong>
          <span>模板是起点，不会直接改动已经在运行中的 Worker。</span>
        </article>
      </div>

      <div className="wb-inline-banner is-muted">
        <strong>当前提示</strong>
        <span>{flashMessage}</span>
      </div>

      <div className="wb-card-grid wb-card-grid-4">
        <article className="wb-card">
          <p className="wb-card-label">默认落点</p>
          <strong>{findProjectName(availableProjects, savedPrimary.projectId)}</strong>
          <span>{findWorkspaceName(availableWorkspaces, savedPrimary.workspaceId)}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">审批与工具</p>
          <strong>{policyLabel}</strong>
          <span>{formatToolProfile(savedPrimary.toolProfile)}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">当前视角</p>
          <strong>{currentProject?.name ?? selector.current_project_id}</strong>
          <span>{currentWorkspace?.name ?? selector.current_workspace_id}</span>
        </article>
        <article className={`wb-card ${pendingChanges > 0 ? "wb-card-accent is-warning" : ""}`}>
          <p className="wb-card-label">待确认改动</p>
          <strong>{pendingChanges > 0 ? "有" : "无"}</strong>
          <span>{primaryDirty ? "主 Agent 待确认" : "主 Agent 已同步"}</span>
          <span>{workDirty ? "Worker 编辑器待确认" : "Worker 编辑器已同步"}</span>
        </article>
      </div>

      <div className="wb-agent-layout">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">主 Agent</p>
              <h3>先确认默认项目、工作区和审批方式</h3>
              <p className="wb-panel-copy">
                日常只需要改这里最常用的几项；模型路由和工具范围收在高级配置里。
              </p>
            </div>
            <div className="wb-inline-actions wb-inline-actions-wrap">
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={handleResetPrimary}
                disabled={!primaryDirty}
              >
                撤回改动
              </button>
              <button
                type="button"
                className="wb-button wb-button-primary"
                onClick={handleSavePrimary}
                disabled={!primaryDirty}
              >
                暂存主 Agent 草案
              </button>
              <Link className="wb-button wb-button-tertiary" to="/settings">
                去设置页保存
              </Link>
            </div>
          </div>

          <div className="wb-stat-grid">
            <div className="wb-detail-block">
              <span className="wb-card-label">当前默认 Project</span>
              <strong>{findProjectName(availableProjects, savedPrimary.projectId)}</strong>
              <p>{findWorkspaceName(availableWorkspaces, savedPrimary.workspaceId)}</p>
            </div>
            <div className="wb-detail-block">
              <span className="wb-card-label">审批强度</span>
              <strong>{policyLabel}</strong>
              <p>{formatToolProfile(savedPrimary.toolProfile)}</p>
            </div>
          </div>

          <div className="wb-agent-form-grid">
            <label className="wb-field">
              <span>主 Agent 名称</span>
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
              <span>主 Agent 说明</span>
              <textarea
                rows={4}
                className="wb-textarea-prose"
                value={primaryDraft.personaSummary}
                onChange={(event) => updatePrimary("personaSummary", event.target.value)}
              />
            </label>
          </div>

          <div className="wb-agent-option-stack">
            <div>
              <p className="wb-card-label">审批方式</p>
              <div className="wb-agent-choice-grid">
                {policyProfiles.map(renderPolicyCard)}
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

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">当前视角</p>
              <h3>切换你现在正在查看的 Project / Workspace</h3>
              <p className="wb-panel-copy">
                这里控制页面“看哪里”，不会直接改主 Agent 和 Worker 的配置。
              </p>
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
              <Link className="wb-button wb-button-tertiary" to="/work">
                去看 Work
              </Link>
            </div>
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
