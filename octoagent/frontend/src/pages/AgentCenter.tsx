import { startTransition, useDeferredValue, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import type {
  AgentProfileItem,
  ControlPlaneSnapshot,
  ProjectOption,
  WorkerCapabilityProfile,
  WorkProjectionItem,
  WorkspaceOption,
} from "../types";
import { formatDateTime } from "../workbench/utils";

type WorkAgentStatus = "active" | "syncing" | "attention" | "paused" | "draft";
type WorkAgentSource = "runtime" | "template" | "manual";

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
  general: "通用 Worker",
  ops: "Ops Worker",
  research: "Research Worker",
  dev: "Dev Worker",
};

const WORK_AGENT_STATUS_LABELS: Record<WorkAgentStatus, string> = {
  active: "在线",
  syncing: "同步中",
  attention: "待处理",
  paused: "暂停",
  draft: "草案",
};

const WORK_AGENT_SOURCE_LABELS: Record<WorkAgentSource, string> = {
  runtime: "来自当前运行态",
  template: "来自能力模板",
  manual: "手动创建",
};

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

function findWorkspaceName(
  workspaces: WorkspaceOption[],
  workspaceId: string
): string {
  return (
    workspaces.find((workspace) => workspace.workspace_id === workspaceId)?.name ??
    workspaceId
  );
}

function formatWorkerType(workerType: string): string {
  return WORKER_TYPE_LABELS[workerType] ?? workerType;
}

function primaryProfileFromSnapshot(
  snapshot: ControlPlaneSnapshot
): AgentProfileItem | null {
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
      updated_at:
        typeof activeProfile.updated_at === "string" ? activeProfile.updated_at : null,
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
    snapshot.resources.policy_profiles.profiles.find((profile) => profile.is_active)
      ?.profile_id ?? "default";

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
  const capabilityTags = workerProfile?.capabilities?.slice(0, 3) ?? [];
  const routeTags = uniqueStrings(works.map((work) => work.route_reason)).slice(0, 2);
  const runtimeKinds = workerProfile?.runtime_kinds?.slice(0, 2) ?? [];
  const tags = uniqueStrings([
    ...capabilityTags,
    ...routeTags,
    ...runtimeKinds,
    ...tools.slice(0, 2),
  ]).slice(0, 6);
  const lastUpdated = works
    .map((work) => work.updated_at ?? "")
    .sort((left, right) => right.localeCompare(left))[0] || null;

  return {
    id: key,
    name: `${formatWorkerType(first.selected_worker_type)} · ${workspaceName}`,
    workerType: first.selected_worker_type,
    projectId: first.project_id,
    workspaceId: first.workspace_id,
    status: workAgentStatusForWorks(works),
    source: "runtime",
    toolProfile:
      requestedToolProfiles[0] ||
      workerProfile?.default_tool_profile ||
      primaryDraft.toolProfile,
    modelAlias:
      requestedModelAliases[0] ||
      workerProfile?.default_model_alias ||
      primaryDraft.modelAlias,
    autonomy: first.target_kind === "graph_agent" ? "pipeline" : "free-loop",
    summary: `当前汇总了 ${works.length} 条工作，等待处理 ${waitingCount} 条，可合并 ${mergeReadyCount} 条。`,
    tags,
    selectedTools: tools,
    taskCount: works.length,
    waitingCount,
    mergeReadyCount,
    lastUpdated,
  };
}

function buildWorkAgentSeeds(snapshot: ControlPlaneSnapshot): WorkAgentItem[] {
  const selector = snapshot.resources.project_selector;
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

  if (runtimeAgents.length > 0) {
    return runtimeAgents;
  }

  const fallbackProjectId = selector.current_project_id;
  const fallbackWorkspaceId = selector.current_workspace_id;
  const profileSeeds = snapshot.resources.capability_pack.pack.worker_profiles.slice(0, 4);
  if (profileSeeds.length > 0) {
    return profileSeeds.map((profile) => ({
      id: `template:${profile.worker_type}`,
      name: `${formatWorkerType(profile.worker_type)} 模板`,
      workerType: profile.worker_type,
      projectId: fallbackProjectId,
      workspaceId: fallbackWorkspaceId,
      status: "draft",
      source: "template",
      toolProfile: profile.default_tool_profile || primaryDraft.toolProfile,
      modelAlias: profile.default_model_alias || primaryDraft.modelAlias,
      autonomy: profile.runtime_kinds.includes("graph_agent") ? "pipeline" : "free-loop",
      summary: `当前还没有运行中的 ${formatWorkerType(profile.worker_type)}，你可以先从模板创建。`,
      tags: uniqueStrings([
        ...profile.capabilities.slice(0, 3),
        ...profile.default_tool_groups.slice(0, 2),
      ]),
      selectedTools: profile.default_tool_groups.slice(0, 4),
      taskCount: 0,
      waitingCount: 0,
      mergeReadyCount: 0,
      lastUpdated: null,
    }));
  }

  return [
    {
      id: "manual:default",
      name: "通用 Worker 草案",
      workerType: "general",
      projectId: fallbackProjectId,
      workspaceId: fallbackWorkspaceId,
      status: "draft",
      source: "manual",
      toolProfile: primaryDraft.toolProfile,
      modelAlias: primaryDraft.modelAlias,
      autonomy: "guided",
      summary: "当前还没有可展示的 Work Agent 运行态，你可以先创建一个草案。",
      tags: ["planner", "handoff"],
      selectedTools: [],
      taskCount: 0,
      waitingCount: 0,
      mergeReadyCount: 0,
      lastUpdated: null,
    },
  ];
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

function buildEmptyWorkDraft(primaryDraft: PrimaryAgentDraft): WorkAgentDraft {
  return {
    id: "",
    name: "",
    workerType: "general",
    projectId: primaryDraft.projectId,
    workspaceId: primaryDraft.workspaceId,
    status: "draft",
    source: "manual",
    toolProfile: primaryDraft.toolProfile,
    modelAlias: primaryDraft.modelAlias,
    autonomy: "guided",
    summary: "",
    tags: ["planner", "handoff"],
    selectedTools: [],
    taskCount: "0",
    waitingCount: "0",
    mergeReadyCount: "0",
  };
}

function hydrateWorkDraft(draft: WorkAgentDraft): WorkAgentItem {
  return {
    id: draft.id || `manual:${Date.now().toString(36)}`,
    name: draft.name.trim() || "未命名 Work Agent",
    workerType: draft.workerType.trim() || "general",
    projectId: draft.projectId.trim() || "default",
    workspaceId: draft.workspaceId.trim() || "primary",
    status: draft.status,
    source: draft.source,
    toolProfile: draft.toolProfile.trim() || "standard",
    modelAlias: draft.modelAlias.trim() || "main",
    autonomy: draft.autonomy.trim() || "guided",
    summary: draft.summary.trim() || "暂未补充说明。",
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

function workAgentStatusLabel(status: WorkAgentStatus): string {
  return WORK_AGENT_STATUS_LABELS[status];
}

export default function AgentCenter() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const selector = snapshot!.resources.project_selector;
  const policyProfiles = snapshot!.resources.policy_profiles.profiles;
  const configValue = toRecord(snapshot!.resources.config.current_value);
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
  const policyOptions = policyProfiles.map((profile) => ({
    id: profile.profile_id,
    label: profile.label,
    description: profile.description,
  }));
  const availableProjects = selector.available_projects;
  const [savedPrimary, setSavedPrimary] = useState(() => buildPrimaryAgentSeed(snapshot!));
  const [primaryDraft, setPrimaryDraft] = useState(savedPrimary);
  const [workAgents, setWorkAgents] = useState<WorkAgentItem[]>(() =>
    buildWorkAgentSeeds(snapshot!)
  );
  const [selectedWorkAgentId, setSelectedWorkAgentId] = useState("");
  const [selectedWorkAgentIds, setSelectedWorkAgentIds] = useState<string[]>([]);
  const [editorMode, setEditorMode] = useState<"edit" | "create">("edit");
  const [workDraft, setWorkDraft] = useState<WorkAgentDraft>(() =>
    buildEmptyWorkDraft(savedPrimary)
  );
  const [projectFilter, setProjectFilter] = useState("all");
  const [contextProjectId, setContextProjectId] = useState(selector.current_project_id);
  const [contextWorkspaceId, setContextWorkspaceId] = useState(selector.current_workspace_id);
  const [searchQuery, setSearchQuery] = useState("");
  const [flashMessage, setFlashMessage] = useState(
    "这个页面优先帮助你看清主 Agent、Work Agent 和 Project 的关系；主配置的最终保存仍通过设置页完成。"
  );
  const deferredSearch = useDeferredValue(searchQuery);
  const syncKey = snapshot!.generated_at;

  useEffect(() => {
    const nextPrimary = buildPrimaryAgentSeed(snapshot!);
    const nextAgents = buildWorkAgentSeeds(snapshot!);
    const firstAgent = nextAgents[0] ?? null;
    setSavedPrimary(nextPrimary);
    setPrimaryDraft(nextPrimary);
    setWorkAgents(nextAgents);
    setSelectedWorkAgentId(firstAgent?.id ?? "");
    setSelectedWorkAgentIds(firstAgent ? [firstAgent.id] : []);
    setWorkDraft(firstAgent ? toDraft(firstAgent) : buildEmptyWorkDraft(nextPrimary));
    setEditorMode(firstAgent ? "edit" : "create");
    setContextProjectId(selector.current_project_id);
    setContextWorkspaceId(selector.current_workspace_id);
  }, [syncKey, selector.current_project_id, selector.current_workspace_id, snapshot]);

  const currentProject =
    availableProjects.find((project) => project.project_id === selector.current_project_id) ??
    null;
  const availableContextWorkspaces = selector.available_workspaces.filter(
    (workspace) => workspace.project_id === contextProjectId
  );
  const selectedWorkAgent =
    editorMode === "edit"
      ? workAgents.find((agent) => agent.id === selectedWorkAgentId) ?? null
      : null;
  const primaryDirty = JSON.stringify(savedPrimary) !== JSON.stringify(primaryDraft);
  const workDirty =
    editorMode === "create" ||
    (selectedWorkAgent !== null &&
      JSON.stringify(toDraft(selectedWorkAgent)) !== JSON.stringify(workDraft));

  const normalizedSearch = deferredSearch.trim().toLowerCase();
  const visibleWorkAgents = workAgents.filter((agent) => {
    const matchesProject = projectFilter === "all" || agent.projectId === projectFilter;
    const matchesQuery =
      normalizedSearch.length === 0 ||
      [
        agent.name,
        formatWorkerType(agent.workerType),
        agent.projectId,
        agent.workspaceId,
        agent.summary,
        ...agent.tags,
        ...agent.selectedTools,
      ]
        .join(" ")
        .toLowerCase()
        .includes(normalizedSearch);
    return matchesProject && matchesQuery;
  });

  const activeWorkAgents = workAgents.filter((agent) => agent.status === "active").length;
  const attentionWorkAgents = workAgents.filter((agent) => agent.status === "attention").length;
  const draftWorkAgents = workAgents.filter((agent) => agent.status === "draft").length;
  const uniqueProjects = uniqueStrings(workAgents.map((agent) => agent.projectId));
  const totalTasks = workAgents.reduce((sum, agent) => sum + agent.taskCount, 0);
  const providerCount = Array.isArray(configValue.providers) ? configValue.providers.length : 0;

  function updatePrimary<Key extends keyof PrimaryAgentDraft>(
    key: Key,
    value: PrimaryAgentDraft[Key]
  ) {
    setPrimaryDraft((current) => ({ ...current, [key]: value }));
  }

  function updateWorkDraft<Key extends keyof WorkAgentDraft>(
    key: Key,
    value: WorkAgentDraft[Key]
  ) {
    setWorkDraft((current) => ({ ...current, [key]: value }));
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

  function selectWorkAgent(agent: WorkAgentItem) {
    startTransition(() => {
      setSelectedWorkAgentId(agent.id);
      setSelectedWorkAgentIds((current) =>
        current.length === 0 ? [agent.id] : current
      );
      setEditorMode("edit");
      setWorkDraft(toDraft(agent));
      setFlashMessage(`当前正在查看 ${agent.name}。`);
    });
  }

  function toggleWorkAgentSelection(agentId: string) {
    setSelectedWorkAgentIds((current) =>
      current.includes(agentId)
        ? current.filter((item) => item !== agentId)
        : [...current, agentId]
    );
  }

  function handleCreateDraft() {
    setEditorMode("create");
    setSelectedWorkAgentId("");
    setWorkDraft(buildEmptyWorkDraft(primaryDraft));
    setFlashMessage("已进入新建模式。先定角色、Project 和 Workspace，再补充摘要。");
  }

  function handleSavePrimary() {
    setSavedPrimary(primaryDraft);
    setFlashMessage("主 Agent 草案已暂存。若要落到底层配置，请继续在设置页保存。");
  }

  function handleResetPrimary() {
    setPrimaryDraft(savedPrimary);
    setFlashMessage("主 Agent 草案已恢复到上一次同步状态。");
  }

  function handleSaveWorkAgent() {
    const nextAgent = hydrateWorkDraft(workDraft);
    if (editorMode === "create") {
      setWorkAgents((current) => [nextAgent, ...current]);
      setSelectedWorkAgentId(nextAgent.id);
      setSelectedWorkAgentIds([nextAgent.id]);
      setEditorMode("edit");
      setWorkDraft(toDraft(nextAgent));
      setFlashMessage(`已创建 ${nextAgent.name} 草案。`);
      return;
    }

    setWorkAgents((current) =>
      current.map((agent) => (agent.id === nextAgent.id ? nextAgent : agent))
    );
    setWorkDraft(toDraft(nextAgent));
    setFlashMessage(`已更新 ${nextAgent.name}。`);
  }

  function handleResetWorkAgent() {
    if (editorMode === "create") {
      setWorkDraft(buildEmptyWorkDraft(primaryDraft));
      setFlashMessage("已重置新建表单。");
      return;
    }
    if (!selectedWorkAgent) {
      return;
    }
    setWorkDraft(toDraft(selectedWorkAgent));
    setFlashMessage(`已撤回 ${selectedWorkAgent.name} 的未保存改动。`);
  }

  function handleMergeWorkAgents() {
    if (selectedWorkAgentIds.length < 2) {
      return;
    }
    const mergeTargets = workAgents.filter((agent) => selectedWorkAgentIds.includes(agent.id));
    if (mergeTargets.length < 2) {
      return;
    }
    const mergedAgent: WorkAgentItem = {
      id: `manual:merge:${Date.now().toString(36)}`,
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
      summary: `由 ${mergeTargets.map((agent) => agent.name).join("、")} 合并而来，用于集中处理跨 Project 协作。`,
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
    setSelectedWorkAgentId(mergedAgent.id);
    setSelectedWorkAgentIds([mergedAgent.id]);
    setEditorMode("edit");
    setWorkDraft(toDraft(mergedAgent));
    setFlashMessage(`已合并 ${mergeTargets.length} 个 Work Agent。`);
  }

  function handleSplitWorkAgent() {
    if (!selectedWorkAgent) {
      return;
    }
    const [leftTags, rightTags] = splitList(selectedWorkAgent.tags);
    const [leftTools, rightTools] = splitList(selectedWorkAgent.selectedTools);
    const leftAgent: WorkAgentItem = {
      ...selectedWorkAgent,
      id: `manual:split-a:${Date.now().toString(36)}`,
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
      id: `manual:split-b:${Date.now().toString(36)}`,
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
    setSelectedWorkAgentId(leftAgent.id);
    setSelectedWorkAgentIds([leftAgent.id, rightAgent.id]);
    setEditorMode("edit");
    setWorkDraft(toDraft(leftAgent));
    setFlashMessage(`已把 ${selectedWorkAgent.name} 拆成两个 Work Agent 草案。`);
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
      setFlashMessage("已切换当前 Project 视角，Agent 页面会刷新为新的上下文。");
    }
  }

  return (
    <div className="wb-page">
      <section className="wb-hero wb-hero-agent">
        <div className="wb-hero-copy">
          <p className="wb-kicker">Agents</p>
          <h1>用一个页面看清主 Agent 与 Work Agent 的分工</h1>
          <p>
            这里把主 Agent 的配置、Project 上下文和 Work Agent 草案放在一起。你可以先看谁在负责什么，再决定是修改、创建、合并还是拆分。
          </p>
          <div className="wb-chip-row">
            <span className="wb-chip">当前 Project {currentProject?.name ?? selector.current_project_id}</span>
            <span className="wb-chip">主 Agent {savedPrimary.name}</span>
            <span className="wb-chip">Provider {savedPrimary.primaryProvider}</span>
          </div>
        </div>

        <div className="wb-hero-insights">
          <article className="wb-hero-metric">
            <p className="wb-card-label">主 Agent</p>
            <strong>{savedPrimary.name}</strong>
            <span>{savedPrimary.modelAlias} / {savedPrimary.toolProfile}</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">Work Agent</p>
            <strong>{workAgents.length}</strong>
            <span>在线 {activeWorkAgents} / 待处理 {attentionWorkAgents}</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">Project 覆盖</p>
            <strong>{uniqueProjects.length}</strong>
            <span>总工作量 {totalTasks} / 草案 {draftWorkAgents}</span>
          </article>
        </div>
      </section>

      <div className="wb-inline-banner is-muted">
        <strong>当前管理方式</strong>
        <span>{flashMessage}</span>
      </div>

      <div className="wb-card-grid wb-card-grid-4">
        <article className="wb-card">
          <p className="wb-card-label">模型运行</p>
          <strong>{savedPrimary.llmMode}</strong>
          <span>Proxy {savedPrimary.proxyUrl}</span>
          <span>Provider {providerCount}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">治理策略</p>
          <strong>
            {policyOptions.find((item) => item.id === savedPrimary.policyProfileId)?.label ??
              savedPrimary.policyProfileId}
          </strong>
          <span>Tool profile {savedPrimary.toolProfile}</span>
          <span>Scope {savedPrimary.scope}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">当前上下文</p>
          <strong>{selector.current_project_id}</strong>
          <span>Workspace {selector.current_workspace_id}</span>
          <span>切换后可直接重看 Agent 分布</span>
        </article>
        <article className={`wb-card ${primaryDirty || workDirty ? "wb-card-accent is-warning" : ""}`}>
          <p className="wb-card-label">未保存改动</p>
          <strong>{primaryDirty || workDirty ? "有" : "无"}</strong>
          <span>{primaryDirty ? "主 Agent 草案待确认" : "主 Agent 已同步"}</span>
          <span>{workDirty ? "Work Agent 编辑器待确认" : "Work Agent 编辑器已同步"}</span>
        </article>
      </div>

      <div className="wb-agent-layout">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">主 Agent</p>
              <h3>先确认总控配置</h3>
            </div>
            <div className="wb-inline-actions wb-inline-actions-wrap">
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={handleResetPrimary}
                disabled={!primaryDirty}
              >
                撤回草案
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

          <div className="wb-agent-form-grid">
            <label className="wb-field">
              <span>名称</span>
              <input
                type="text"
                value={primaryDraft.name}
                onChange={(event) => updatePrimary("name", event.target.value)}
              />
            </label>
            <label className="wb-field">
              <span>Scope</span>
              <select
                value={primaryDraft.scope}
                onChange={(event) => updatePrimary("scope", event.target.value)}
              >
                <option value="project">project</option>
                <option value="workspace">workspace</option>
                <option value="session">session</option>
              </select>
            </label>
            <label className="wb-field">
              <span>Project</span>
              <input
                list="agent-project-options"
                value={primaryDraft.projectId}
                onChange={(event) => updatePrimary("projectId", event.target.value)}
              />
            </label>
            <label className="wb-field">
              <span>Workspace</span>
              <input
                list="agent-workspace-options"
                value={primaryDraft.workspaceId}
                onChange={(event) => updatePrimary("workspaceId", event.target.value)}
              />
            </label>
            <label className="wb-field wb-field-span-2">
              <span>Persona 摘要</span>
              <textarea
                rows={4}
                value={primaryDraft.personaSummary}
                onChange={(event) => updatePrimary("personaSummary", event.target.value)}
              />
            </label>
          </div>

          <div className="wb-agent-option-stack">
            <div>
              <p className="wb-card-label">模型别名</p>
              <div className="wb-chip-row">
                {modelAliasOptions.map((alias) => (
                  <button
                    key={alias}
                    type="button"
                    className={`wb-chip-button ${
                      primaryDraft.modelAlias === alias ? "is-active" : ""
                    }`}
                    onClick={() => updatePrimary("modelAlias", alias)}
                  >
                    {alias}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <p className="wb-card-label">工具权限</p>
              <div className="wb-chip-row">
                {toolProfileOptions.map((profile) => (
                  <button
                    key={profile}
                    type="button"
                    className={`wb-chip-button ${
                      primaryDraft.toolProfile === profile ? "is-active" : ""
                    }`}
                    onClick={() => updatePrimary("toolProfile", profile)}
                  >
                    {profile}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <p className="wb-card-label">治理策略</p>
              <div className="wb-agent-choice-grid">
                {policyOptions.map((policy) => (
                  <button
                    key={policy.id}
                    type="button"
                    className={`wb-agent-choice-card ${
                      primaryDraft.policyProfileId === policy.id ? "is-active" : ""
                    }`}
                    onClick={() => updatePrimary("policyProfileId", policy.id)}
                  >
                    <strong>{policy.label}</strong>
                    <span>{policy.description}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">Project 视图</p>
              <h3>切换上下文和筛选范围</h3>
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
                切换当前 Project
              </button>
              <Link className="wb-button wb-button-tertiary" to="/work">
                去看 Work
              </Link>
            </div>
          </div>

          <div className="wb-inline-form">
            <label className="wb-field">
              <span>当前 Project</span>
              <select
                value={contextProjectId}
                onChange={(event) => {
                  setContextProjectId(event.target.value);
                  const fallbackWorkspace = selector.available_workspaces.find(
                    (workspace) => workspace.project_id === event.target.value
                  );
                  setContextWorkspaceId(
                    fallbackWorkspace?.workspace_id ?? selector.current_workspace_id
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
              <span>当前 Workspace</span>
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
              <span>查看所有 Work Agent</span>
            </button>
            {availableProjects.map((project) => (
              <button
                key={project.project_id}
                type="button"
                className={`wb-agent-project-card ${
                  projectFilter === project.project_id ? "is-active" : ""
                }`}
                onClick={() => setProjectFilter(project.project_id)}
              >
                <strong>{project.name}</strong>
                <span>{project.workspace_ids.length} 个 workspace</span>
              </button>
            ))}
          </div>
        </section>
      </div>

      <div className="wb-agent-layout">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">Work Agent</p>
              <h3>浏览 / 修改 / 创建 / 合并 / 拆分</h3>
            </div>
            <div className="wb-inline-actions wb-inline-actions-wrap">
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={handleMergeWorkAgents}
                disabled={selectedWorkAgentIds.length < 2}
              >
                合并选中
              </button>
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={handleSplitWorkAgent}
                disabled={!selectedWorkAgent}
              >
                拆分当前
              </button>
              <button
                type="button"
                className="wb-button wb-button-primary"
                onClick={handleCreateDraft}
              >
                新建 Work Agent 草案
              </button>
            </div>
          </div>

          <div className="wb-inline-form">
            <label className="wb-field">
              <span>搜索 Agent / Project / 标签</span>
              <input
                type="text"
                value={searchQuery}
                placeholder="例如：research、project-default、merge-ready"
                onChange={(event) => setSearchQuery(event.target.value)}
              />
            </label>
          </div>

          {visibleWorkAgents.length === 0 ? (
            <div className="wb-empty-state">
              <strong>当前没有匹配的 Work Agent</strong>
              <span>可以切换 Project 过滤，或者直接创建一个新的 Work Agent 草案。</span>
            </div>
          ) : (
            <div className="wb-agent-list">
              {visibleWorkAgents.map((agent) => (
                <article
                  key={agent.id}
                  className={`wb-agent-runtime-card ${
                    selectedWorkAgentId === agent.id && editorMode === "edit" ? "is-active" : ""
                  }`}
                >
                  <div className="wb-panel-head">
                    <label className="wb-agent-check">
                      <input
                        type="checkbox"
                        checked={selectedWorkAgentIds.includes(agent.id)}
                        onChange={() => toggleWorkAgentSelection(agent.id)}
                      />
                      <span>批量选择</span>
                    </label>
                    <span className={`wb-status-pill is-${agent.status}`}>
                      {workAgentStatusLabel(agent.status)}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="wb-agent-runtime-trigger"
                    onClick={() => selectWorkAgent(agent)}
                  >
                    <strong>{agent.name}</strong>
                    <p>{agent.summary}</p>
                    <div className="wb-chip-row">
                      <span className="wb-chip">{formatWorkerType(agent.workerType)}</span>
                      <span className="wb-chip">{agent.projectId}</span>
                      <span className="wb-chip">{agent.workspaceId}</span>
                      <span className="wb-chip">{WORK_AGENT_SOURCE_LABELS[agent.source]}</span>
                    </div>
                    <div className="wb-chip-row">
                      {agent.tags.map((tag) => (
                        <span key={tag} className="wb-chip is-warning">
                          {tag}
                        </span>
                      ))}
                    </div>
                    <div className="wb-agent-runtime-meta">
                      <span>任务 {agent.taskCount}</span>
                      <span>等待 {agent.waitingCount}</span>
                      <span>可合并 {agent.mergeReadyCount}</span>
                      <span>更新于 {formatDateTime(agent.lastUpdated)}</span>
                    </div>
                  </button>
                </article>
              ))}
            </div>
          )}
        </section>

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">编辑器</p>
              <h3>{editorMode === "create" ? "创建 Work Agent 草案" : "调整当前 Work Agent"}</h3>
            </div>
            <span className="wb-chip">
              {editorMode === "create" ? "新建模式" : selectedWorkAgent?.name ?? "未选择"}
            </span>
          </div>

          <div className="wb-agent-form-grid">
            <label className="wb-field">
              <span>名称</span>
              <input
                type="text"
                value={workDraft.name}
                onChange={(event) => updateWorkDraft("name", event.target.value)}
              />
            </label>
            <label className="wb-field">
              <span>Worker 类型</span>
              <select
                value={workDraft.workerType}
                onChange={(event) => updateWorkDraft("workerType", event.target.value)}
              >
                {["general", "research", "dev", "ops"].map((workerType) => (
                  <option key={workerType} value={workerType}>
                    {formatWorkerType(workerType)}
                  </option>
                ))}
              </select>
            </label>
            <label className="wb-field">
              <span>Project</span>
              <input
                list="agent-project-options"
                value={workDraft.projectId}
                onChange={(event) => updateWorkDraft("projectId", event.target.value)}
              />
            </label>
            <label className="wb-field">
              <span>Workspace</span>
              <input
                list="agent-workspace-options"
                value={workDraft.workspaceId}
                onChange={(event) => updateWorkDraft("workspaceId", event.target.value)}
              />
            </label>
            <label className="wb-field">
              <span>任务量</span>
              <input
                type="number"
                min="0"
                value={workDraft.taskCount}
                onChange={(event) => updateWorkDraft("taskCount", event.target.value)}
              />
            </label>
            <label className="wb-field">
              <span>等待数</span>
              <input
                type="number"
                min="0"
                value={workDraft.waitingCount}
                onChange={(event) => updateWorkDraft("waitingCount", event.target.value)}
              />
            </label>
            <label className="wb-field">
              <span>可合并</span>
              <input
                type="number"
                min="0"
                value={workDraft.mergeReadyCount}
                onChange={(event) => updateWorkDraft("mergeReadyCount", event.target.value)}
              />
            </label>
            <label className="wb-field">
              <span>自治方式</span>
              <select
                value={workDraft.autonomy}
                onChange={(event) => updateWorkDraft("autonomy", event.target.value)}
              >
                <option value="guided">guided</option>
                <option value="free-loop">free-loop</option>
                <option value="pipeline">pipeline</option>
              </select>
            </label>
            <label className="wb-field wb-field-span-2">
              <span>摘要</span>
              <textarea
                rows={4}
                value={workDraft.summary}
                onChange={(event) => updateWorkDraft("summary", event.target.value)}
              />
            </label>
          </div>

          <div className="wb-agent-option-stack">
            <div>
              <p className="wb-card-label">模型别名</p>
              <div className="wb-chip-row">
                {modelAliasOptions.map((alias) => (
                  <button
                    key={alias}
                    type="button"
                    className={`wb-chip-button ${
                      workDraft.modelAlias === alias ? "is-active" : ""
                    }`}
                    onClick={() => updateWorkDraft("modelAlias", alias)}
                  >
                    {alias}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <p className="wb-card-label">工具权限</p>
              <div className="wb-chip-row">
                {toolProfileOptions.map((profile) => (
                  <button
                    key={profile}
                    type="button"
                    className={`wb-chip-button ${
                      workDraft.toolProfile === profile ? "is-active" : ""
                    }`}
                    onClick={() => updateWorkDraft("toolProfile", profile)}
                  >
                    {profile}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <p className="wb-card-label">标签与工具</p>
              <div className="wb-chip-row">
                {uniqueStrings([
                  ...workDraft.tags,
                  ...workDraft.selectedTools,
                  "planner",
                  "handoff",
                  "memory",
                  "watchdog",
                  "frontend",
                  "research",
                ]).map((token) => (
                  <button
                    key={token}
                    type="button"
                    className={`wb-chip-button ${
                      workDraft.tags.includes(token) || workDraft.selectedTools.includes(token)
                        ? "is-active"
                        : ""
                    }`}
                    onClick={() => {
                      if (workDraft.selectedTools.includes(token)) {
                        toggleDraftToken("selectedTools", token);
                        return;
                      }
                      toggleDraftToken("tags", token);
                    }}
                  >
                    {token}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="wb-inline-actions wb-inline-actions-wrap">
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={handleResetWorkAgent}
            >
              撤回编辑
            </button>
            <button
              type="button"
              className="wb-button wb-button-primary"
              onClick={handleSaveWorkAgent}
            >
              {editorMode === "create" ? "创建草案" : "保存草案"}
            </button>
          </div>
        </section>
      </div>

      <datalist id="agent-project-options">
        {availableProjects.map((project: ProjectOption) => (
          <option key={project.project_id} value={project.project_id}>
            {project.name}
          </option>
        ))}
      </datalist>

      <datalist id="agent-workspace-options">
        {selector.available_workspaces.map((workspace) => (
          <option key={workspace.workspace_id} value={workspace.workspace_id}>
            {workspace.name}
          </option>
        ))}
      </datalist>
    </div>
  );
}
