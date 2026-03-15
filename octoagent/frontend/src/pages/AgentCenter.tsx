import { startTransition, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import AgentEditorSection from "../domains/agents/AgentEditorSection";
import AgentTemplatePicker from "../domains/agents/AgentTemplatePicker";
import {
  buildAgentEditorDraftFromProfile,
  buildAgentEditorDraftFromTemplate,
  buildAgentPayload,
  buildBlankAgentEditorDraft,
  buildCapabilityProviderEntries,
  buildModelAliasOptions,
  buildProjectOptions,
  buildToolGroupOptions,
  buildToolOptions,
  deriveAgentManagementView,
  formatAgentArchetype,
  formatProjectName,
  formatTokenLabel,
  parseAgentReview,
  type AgentCardViewModel,
  type AgentEditorDraft,
  type AgentEditorReview,
} from "../domains/agents/agentManagementData";
import type { AgentProfileItem } from "../types";
import { formatDateTime } from "../workbench/utils";

type EditorMode = "main" | "agent" | "create";

interface EditorState {
  mode: EditorMode;
  draft: AgentEditorDraft;
}

type BehaviorSystemSummary = NonNullable<AgentProfileItem["behavior_system"]>;
type BehaviorFileSummary = NonNullable<BehaviorSystemSummary["files"]>[number];
type BehaviorManifestFile = NonNullable<
  NonNullable<BehaviorSystemSummary["path_manifest"]>["effective_behavior_files"]
>[number];

interface BehaviorScopeGroup {
  scope: string;
  title: string;
  summary: string;
  files: Array<BehaviorManifestFile & { title: string; visibility: string; shareWithWorkers: boolean }>;
}

interface BootstrapTemplateGroup {
  key: string;
  title: string;
  summary: string;
  items: string[];
}

interface BootstrapQuestionRouteItem {
  stepId: string;
  route: string;
  target: string;
  summary: string;
}

interface BehaviorGovernanceCommand {
  key: string;
  title: string;
  summary: string;
  command: string;
}

const AGENT_PRIVATE_FILE_IDS = new Set(["IDENTITY.md", "SOUL.md", "HEARTBEAT.md"]);
const SHARED_FILE_IDS = new Set(["AGENTS.md", "USER.md", "TOOLS.md", "BOOTSTRAP.md"]);
const PROJECT_SHARED_FILE_IDS = new Set(["PROJECT.md", "KNOWLEDGE.md", "USER.md", "TOOLS.md"]);

function formatScopeTitle(scope: string): string {
  switch (scope) {
    case "system_shared":
      return "Shared Files";
    case "agent_private":
      return "Agent Private";
    case "project_shared":
      return "Project Shared";
    case "project_agent":
      return "Project-Agent Override";
    default:
      return formatTokenLabel(scope || "unknown");
  }
}

function formatScopeSummary(scope: string): string {
  switch (scope) {
    case "system_shared":
      return "所有 Agent 共享的全局规则和启动约束。";
    case "agent_private":
      return "这个 Agent 自己的身份、风格和内部节奏。";
    case "project_shared":
      return "当前 Project 下所有 Agent 共享的项目级说明。";
    case "project_agent":
      return "这个 Agent 在当前 Project 里的局部覆盖。";
    default:
      return "当前生效的行为文件。";
  }
}

function inferBehaviorScope(
  file: BehaviorManifestFile,
  fileSummary: BehaviorFileSummary | undefined
): string {
  const explicitScope = typeof file.scope === "string" ? file.scope.trim() : "";
  if (explicitScope) {
    return explicitScope;
  }
  const path = file.path || fileSummary?.path_hint || "";
  const sourceKind = file.source_kind || fileSummary?.source_kind || "";
  if (path.includes("/behavior/agents/") || sourceKind.includes("project_agent")) {
    return "project_agent";
  }
  if (path.includes("/projects/") || sourceKind.startsWith("project_")) {
    return "project_shared";
  }
  if (
    path.includes("/behavior/agents/") ||
    sourceKind.includes("agent_") ||
    AGENT_PRIVATE_FILE_IDS.has(file.file_id)
  ) {
    return "agent_private";
  }
  if (SHARED_FILE_IDS.has(file.file_id)) {
    return "system_shared";
  }
  if (PROJECT_SHARED_FILE_IDS.has(file.file_id)) {
    return "project_shared";
  }
  return "system_shared";
}

function describeReviewMode(editableMode?: string, reviewMode?: string): string {
  const editable = editableMode?.trim() || "proposal_required";
  const review = reviewMode?.trim() || "review_required";
  if (editable === "direct_edit" && review === "no_review") {
    return "可直接编辑";
  }
  if (editable === "direct_edit") {
    return "可直接编辑，建议 review";
  }
  if (review === "no_review") {
    return "提案后可直接应用";
  }
  return "先提案，再 review/apply";
}

function formatBootstrapTemplateLabel(templateId: string): string {
  return templateId.replace(/^behavior:(?:system|agent|project|project_agent):/, "");
}

function formatBehaviorScopeCli(scope: string): string {
  switch (scope) {
    case "system_shared":
      return "system";
    case "agent_private":
      return "agent";
    case "project_agent":
      return "project-agent";
    default:
      return "project";
  }
}

function inferBehaviorAgentSlug(
  profile: AgentProfileItem | null,
  summary: BehaviorSystemSummary | undefined
): string {
  const metadataValue = profile?.metadata?.behavior_agent_slug;
  if (typeof metadataValue === "string" && metadataValue.trim()) {
    return metadataValue.trim();
  }
  const agentRoot = summary?.path_manifest?.agent_behavior_root;
  if (typeof agentRoot === "string" && agentRoot.includes("/")) {
    const parts = agentRoot.split("/").filter(Boolean);
    const token = parts[parts.length - 1];
    if (token) {
      return token;
    }
  }
  const projectAgentRoot = summary?.path_manifest?.project_agent_behavior_root;
  if (typeof projectAgentRoot === "string" && projectAgentRoot.includes("/")) {
    const parts = projectAgentRoot.split("/").filter(Boolean);
    const token = parts[parts.length - 1];
    if (token) {
      return token;
    }
  }
  const profileParts = profile?.profile_id?.split(":").filter(Boolean) ?? [];
  const profileToken = profileParts[profileParts.length - 1];
  if (profileToken) {
    return profileToken;
  }
  return "butler";
}

function buildBehaviorGovernanceCommands(
  file: (BehaviorManifestFile & { title: string; visibility: string; shareWithWorkers: boolean }) | null,
  profile: AgentProfileItem | null,
  summary: BehaviorSystemSummary | undefined
): BehaviorGovernanceCommand[] {
  if (!file) {
    return [];
  }
  const scopeCli = formatBehaviorScopeCli(file.scope || "");
  const fileToken = file.file_id.replace(/\.md$/i, "");
  const agentSlug = inferBehaviorAgentSlug(profile, summary);
  const projectArg = profile?.project_id ? ` --project ${profile.project_id}` : "";
  const agentArg = ` --agent ${agentSlug}`;
  return [
    {
      key: "show",
      title: "查看 effective 内容",
      summary: "确认当前真正生效的内容和来源。",
      command: `octo behavior show ${fileToken}${projectArg}${agentArg}`,
    },
    {
      key: "edit",
      title: "准备编辑目标",
      summary: "materialize 对应 scope 的目标文件，再交给本机编辑器。",
      command: `octo behavior edit ${fileToken} --scope ${scopeCli}${projectArg}${agentArg}`,
    },
    {
      key: "diff",
      title: "比较 override 差异",
      summary: "查看这个 scope 相对下层来源到底改了什么。",
      command: `octo behavior diff ${fileToken} --scope ${scopeCli}${projectArg}${agentArg}`,
    },
    {
      key: "apply",
      title: "应用 reviewed proposal",
      summary: "把外部提案写回目标 behavior file。",
      command: `octo behavior apply ${fileToken} --scope ${scopeCli}${projectArg}${agentArg} --from /absolute/path/to/proposal.md`,
    },
  ];
}

function buildBootstrapTemplateGroups(
  summary: BehaviorSystemSummary | undefined,
  profile: AgentProfileItem | null
): BootstrapTemplateGroup[] {
  const explicitGroups = summary?.bootstrap_templates;
  const groups = explicitGroups ?? {
    shared: (summary?.bootstrap_template_ids ?? profile?.bootstrap_template_ids ?? []).filter((item) =>
      item.startsWith("behavior:system:")
    ),
    agent_private: (summary?.bootstrap_template_ids ?? profile?.bootstrap_template_ids ?? []).filter((item) =>
      item.startsWith("behavior:agent:")
    ),
    project_shared: (summary?.bootstrap_template_ids ?? profile?.bootstrap_template_ids ?? []).filter((item) =>
      item.startsWith("behavior:project:")
    ),
    project_agent: (summary?.bootstrap_template_ids ?? profile?.bootstrap_template_ids ?? []).filter((item) =>
      item.startsWith("behavior:project_agent:")
    ),
  };
  const normalized: Array<[string, string, string, string[] | undefined]> = [
    ["shared", "Shared Templates", "所有 Agent 共享的默认行为模板。", groups.shared],
    ["agent_private", "Agent Private Templates", "这个 Agent 自己的身份、性格和节奏模板。", groups.agent_private],
    ["project_shared", "Project Shared Templates", "当前 Project 的共享启动说明与约束模板。", groups.project_shared],
    ["project_agent", "Project-Agent Templates", "这个 Agent 在当前 Project 里的局部模板覆盖。", groups.project_agent],
  ];
  return normalized
    .map(([key, title, summaryText, items]) => ({
      key,
      title,
      summary: summaryText,
      items: (items ?? []).map(formatBootstrapTemplateLabel),
    }))
    .filter((group) => group.items.length > 0);
}

function readBootstrapSession(snapshot: ReturnType<typeof useWorkbench>["snapshot"]) {
  const raw = snapshot?.resources.bootstrap_session?.session;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  return raw as Record<string, unknown>;
}

function normalizeBootstrapRoute(
  routeValue: string,
  summary: BehaviorSystemSummary | undefined
): { route: string; target: string } {
  const normalizedRoute = routeValue.trim();
  if (!normalizedRoute) {
    return { route: "unknown", target: "" };
  }
  if (normalizedRoute.startsWith("behavior:")) {
    return {
      route: "behavior",
      target: normalizedRoute.replace(/^behavior:/, ""),
    };
  }
  if (normalizedRoute === "memory") {
    return {
      route: "memory",
      target: summary?.bootstrap_routes?.facts?.store || summary?.storage_boundary_hints?.facts_store || "MemoryService",
    };
  }
  if (normalizedRoute === "memory_policy") {
    return {
      route: "memory_policy",
      target: summary?.bootstrap_routes?.facts?.store || summary?.storage_boundary_hints?.facts_store || "MemoryService",
    };
  }
  if (normalizedRoute === "secrets") {
    return {
      route: "secrets",
      target:
        summary?.bootstrap_routes?.secrets?.path ||
        summary?.storage_boundary_hints?.secrets_store ||
        "SecretService",
    };
  }
  return { route: normalizedRoute, target: "" };
}

function buildBootstrapQuestionRoutes(
  session: Record<string, unknown> | null,
  summary: BehaviorSystemSummary | undefined
): BootstrapQuestionRouteItem[] {
  const metadata =
    session && typeof session.metadata === "object" && session.metadata && !Array.isArray(session.metadata)
      ? (session.metadata as Record<string, unknown>)
      : null;
  const questionnaire = metadata?.questionnaire;

  const normalizeItem = (
    raw: unknown,
    fallbackStepId: string
  ): BootstrapQuestionRouteItem | null => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      return null;
    }
    const item = raw as Record<string, unknown>;
    const stepId =
      (typeof item.step === "string" && item.step.trim()) ||
      (typeof fallbackStepId === "string" && fallbackStepId.trim()) ||
      "";
    if (!stepId) {
      return null;
    }
    const routeValue = typeof item.route === "string" ? item.route : "";
    const normalizedRoute = normalizeBootstrapRoute(routeValue, summary);
    return {
      stepId,
      route: normalizedRoute.route,
      target:
        (typeof item.target === "string" && item.target) ||
        normalizedRoute.target,
      summary:
        (typeof item.summary === "string" && item.summary) ||
        (typeof item.prompt === "string" ? item.prompt : ""),
    };
  };

  if (Array.isArray(questionnaire)) {
    return questionnaire
      .map((raw) => normalizeItem(raw, ""))
      .filter((item): item is BootstrapQuestionRouteItem => Boolean(item));
  }

  if (!questionnaire || typeof questionnaire !== "object") {
    return [];
  }

  return Object.entries(questionnaire as Record<string, unknown>)
    .map(([stepId, raw]) => normalizeItem(raw, stepId))
    .filter((item): item is BootstrapQuestionRouteItem => Boolean(item));
}

function buildBehaviorScopeGroups(summary: BehaviorSystemSummary | undefined): BehaviorScopeGroup[] {
  if (!summary?.path_manifest?.effective_behavior_files) {
    return [];
  }
  const fileSummaryById = new Map((summary.files ?? []).map((item) => [item.file_id, item]));
  const grouped = new Map<string, BehaviorScopeGroup>();

  summary.path_manifest.effective_behavior_files.forEach((file) => {
    const fileSummary = fileSummaryById.get(file.file_id);
    const scope = inferBehaviorScope(file, fileSummary);
    const current =
      grouped.get(scope) ??
      {
        scope,
        title: formatScopeTitle(scope),
        summary: formatScopeSummary(scope),
        files: [],
      };
    current.files.push({
      ...file,
      title: fileSummary?.title || file.file_id,
      visibility: fileSummary?.visibility || "private",
      shareWithWorkers: Boolean(fileSummary?.share_with_workers),
    });
    grouped.set(scope, current);
  });

  return ["system_shared", "agent_private", "project_shared", "project_agent"]
    .map((scope) => grouped.get(scope))
    .filter((item): item is BehaviorScopeGroup => Boolean(item))
    .map((group) => ({
      ...group,
      files: [...group.files].sort((left, right) => left.file_id.localeCompare(right.file_id, "zh-Hans-CN")),
    }));
}

function formatAgentScope(scope: string): string {
  switch (scope) {
    case "project":
      return "当前 Project";
    case "system":
      return "系统共享";
    default:
      return formatTokenLabel(scope || "unknown");
  }
}

function validateMetadataText(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return "";
  }
  try {
    const parsed = JSON.parse(trimmed);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return "附加配置需要是 JSON 对象。";
    }
    return "";
  } catch {
    return "附加配置不是合法的 JSON。";
  }
}

function toggleStringValue(values: string[], value: string): string[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

function describeTools(values: string[]): string {
  if (values.length === 0) {
    return "还没有固定工具";
  }
  if (values.length <= 3) {
    return values.map((value) => formatTokenLabel(value)).join("、");
  }
  return `${values.slice(0, 3).map((value) => formatTokenLabel(value)).join("、")} 等 ${values.length} 项`;
}

function renderAgentCard(
  agent: AgentCardViewModel,
  options: {
    onEdit: () => void;
    onStartSession?: () => void;
    onPromote?: () => void;
    onDelete?: () => void;
    primaryActionLabel: string;
    busyActionId: string | null;
  }
) {
  const canPromote = typeof options.onPromote === "function" && agent.profileStatus === "已发布";

  return (
    <article key={agent.profileId || agent.name} className={`wb-agent-card ${agent.isMainAgent ? "is-main" : ""}`}>
      <div className="wb-agent-card-topline">
        <span className={`wb-status-pill ${agent.status === "needs_setup" ? "is-warning" : "is-ready"}`}>
          {agent.isMainAgent ? "主 Agent" : agent.profileStatus}
        </span>
        <span className="wb-chip">{agent.sourceLabel}</span>
      </div>
      <div className="wb-agent-card-body">
        <strong>{agent.name}</strong>
        <p>{agent.summary}</p>
      </div>
      <div className="wb-agent-card-meta">
        <span>模型 {agent.modelAlias}</span>
        <span>默认工具组 {agent.defaultToolGroups.length}</span>
        <span>固定工具 {agent.selectedTools.length}</span>
        <span>进行中 {agent.activeWorkCount}</span>
      </div>
      <div className="wb-chip-row">
        {agent.defaultToolGroups.slice(0, 3).map((group) => (
          <span key={`${agent.profileId}:${group}`} className="wb-chip">
            {formatTokenLabel(group)}
          </span>
        ))}
      </div>
      <div className="wb-agent-card-actions">
        <button type="button" className="wb-button wb-button-primary" onClick={options.onEdit}>
          {options.primaryActionLabel}
        </button>
        {typeof options.onStartSession === "function" ? (
          <button
            type="button"
            className="wb-button wb-button-secondary"
            disabled={options.busyActionId === "session.new"}
            onClick={options.onStartSession}
          >
            直接开启会话
          </button>
        ) : null}
        {canPromote ? (
          <button
            type="button"
            className="wb-button wb-button-secondary"
            disabled={options.busyActionId === "worker_profile.bind_default"}
            onClick={options.onPromote}
          >
            设为主 Agent
          </button>
        ) : null}
        {typeof options.onDelete === "function" ? (
          <button
            type="button"
            className="wb-button wb-button-tertiary"
            disabled={options.busyActionId === "worker_profile.archive"}
            onClick={options.onDelete}
          >
            删除
          </button>
        ) : null}
      </div>
      <small className="wb-inline-note">
        {agent.updatedAt ? `最近更新于 ${formatDateTime(agent.updatedAt)}` : "还没有更新记录"} · 固定工具：{describeTools(agent.selectedTools)}
      </small>
    </article>
  );
}

export default function AgentCenter() {
  const { snapshot, submitAction, busyActionId, refreshSnapshot } = useWorkbench();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const behaviorCenterRef = useRef<HTMLElement | null>(null);
  const mainAgentRef = useRef<HTMLElement | null>(null);
  const agentProfilesDocument = snapshot!.resources.agent_profiles ?? {
    generated_at: "",
    profiles: [],
    active_project_id: "",
    active_workspace_id: "",
  };
  const workerProfilesDocument = snapshot!.resources.worker_profiles ?? {
    generated_at: "",
    profiles: [],
    summary: {},
  };

  const agentView = useMemo(() => deriveAgentManagementView(snapshot!), [snapshot]);
  const capabilityProviderEntries = useMemo(
    () => buildCapabilityProviderEntries(snapshot!),
    [
      snapshot!.resources.skill_provider_catalog.generated_at,
      snapshot!.resources.mcp_provider_catalog.generated_at,
      snapshot!.resources.skill_governance.generated_at,
    ]
  );
  const toolOptions = useMemo(() => buildToolOptions(snapshot!), [snapshot!.resources.capability_pack.generated_at]);
  const toolGroupOptions = useMemo(
    () => buildToolGroupOptions(snapshot!),
    [snapshot!.resources.capability_pack.generated_at]
  );
  const modelAliasOptions = useMemo(
    () => buildModelAliasOptions(snapshot!),
    [snapshot!.resources.config.generated_at]
  );
  const projectOptions = useMemo(() => {
    const all = buildProjectOptions(snapshot!.resources.project_selector);
    return all.filter((option) => option.value === agentView.currentProjectId);
  }, [agentView.currentProjectId, snapshot!.resources.project_selector.generated_at]);
  const toolProfileOptions = useMemo(
    () => [
      { value: "minimal", label: "仅保留基础工具" },
      { value: "standard", label: "常用工具" },
      { value: "privileged", label: "扩展工具" },
    ],
    []
  );
  const policyOptions = useMemo(
    () =>
      snapshot!.resources.policy_profiles.profiles.map((profile) => ({
        value: profile.profile_id,
        label: profile.label,
      })),
    [snapshot!.resources.policy_profiles.generated_at]
  );
  const skillEntries = capabilityProviderEntries.filter((entry) => entry.kind === "skill");
  const mcpEntries = capabilityProviderEntries.filter((entry) => entry.kind === "mcp");
  const builtinDirectAgents = useMemo(
    () =>
      workerProfilesDocument.profiles
        .filter(
          (profile) =>
            profile.scope === "system" &&
            profile.origin_kind === "builtin" &&
            profile.profile_id !== agentView.defaultProfileId
        )
        .map((profile) => ({
          profileId: profile.profile_id,
          name: profile.name,
          summary: profile.summary,
          status: "ready" as const,
          profileStatus: "内置",
          projectId: agentView.currentProjectId,
          projectName: agentView.currentProjectName,
          modelAlias: profile.static_config.model_alias,
          defaultToolGroups: profile.static_config.default_tool_groups,
          selectedTools: profile.static_config.selected_tools,
          activeWorkCount: profile.dynamic_context.active_work_count,
          waitingWorkCount: profile.dynamic_context.attention_work_count,
          attentionWorkCount: profile.dynamic_context.attention_work_count,
          updatedAt: profile.dynamic_context.updated_at,
          sourceLabel: "系统内置",
          isMainAgent: false,
          removable: false,
        })),
    [
      agentView.currentProjectId,
      agentView.currentProjectName,
      agentView.defaultProfileId,
      workerProfilesDocument.generated_at,
    ]
  );
  const sessionsDocument =
    (snapshot!.resources as {
      sessions?: {
        focused_session_id?: string;
        sessions?: Array<{
          session_id: string;
          project_id: string;
          workspace_id: string;
        }>;
      };
    }).sessions ?? null;
  const focusedSession =
    sessionsDocument?.focused_session_id && Array.isArray(sessionsDocument.sessions)
      ? sessionsDocument.sessions.find(
          (item) => item.session_id === sessionsDocument.focused_session_id
        ) ?? null
      : null;
  const focusedSessionProjectName = focusedSession?.project_id
    ? formatProjectName(snapshot!.resources.project_selector.available_projects, focusedSession.project_id)
    : "";
  const focusedSessionWorkspaceName = focusedSession?.workspace_id
    ? snapshot!.resources.project_selector.available_workspaces.find(
        (item) => item.workspace_id === focusedSession.workspace_id
      )?.name ?? focusedSession.workspace_id
    : "";
  const focusedSessionDiffersFromCurrentProject =
    Boolean(focusedSession?.project_id) && focusedSession?.project_id !== agentView.currentProjectId;
  const behaviorProfiles = useMemo(
    () =>
      agentProfilesDocument.profiles.filter(
        (profile) =>
          profile.scope === "system" || profile.project_id === "" || profile.project_id === agentView.currentProjectId
      ),
    [agentProfilesDocument.generated_at, agentView.currentProjectId]
  );
  const [selectedBehaviorProfileId, setSelectedBehaviorProfileId] = useState("");

  const [editorState, setEditorState] = useState<EditorState | null>(null);
  const [showTemplatePicker, setShowTemplatePicker] = useState(false);
  const [flashMessage, setFlashMessage] = useState(
    "先确定当前项目的主 Agent，再把其他 Agent 按职责分开。"
  );
  const [review, setReview] = useState<AgentEditorReview | null>(null);
  const [copiedGovernanceKey, setCopiedGovernanceKey] = useState("");

  const metadataError = editorState ? validateMetadataText(editorState.draft.metadataText) : "";

  useEffect(() => {
    setEditorState(null);
    setShowTemplatePicker(false);
    setReview(null);
    setFlashMessage(
      focusedSessionDiffersFromCurrentProject
        ? `当前聚焦会话属于「${focusedSessionProjectName} / ${focusedSessionWorkspaceName}」；这里改的是「${agentView.currentProjectName}」的默认 Agent 定义。`
        : `现在看到的是「${agentView.currentProjectName}」自己的默认 Agent 定义与已创建 Agent。`
    );
  }, [
    agentView.currentProjectId,
    agentView.currentProjectName,
    focusedSessionDiffersFromCurrentProject,
    focusedSessionProjectName,
    focusedSessionWorkspaceName,
  ]);

  useEffect(() => {
    setSelectedBehaviorProfileId((current) => {
      if (current && behaviorProfiles.some((profile) => profile.profile_id === current)) {
        return current;
      }
      const preferredProfileId =
        agentView.mainAgentProfile?.profile_id ||
        behaviorProfiles.find((profile) => profile.project_id === agentView.currentProjectId)?.profile_id ||
        behaviorProfiles[0]?.profile_id ||
        "";
      return preferredProfileId;
    });
  }, [agentView.currentProjectId, agentView.mainAgentProfile?.profile_id, behaviorProfiles]);

  const selectedBehaviorProfile =
    behaviorProfiles.find((profile) => profile.profile_id === selectedBehaviorProfileId) ??
    behaviorProfiles[0] ??
    null;
  const selectedBehaviorSystem = selectedBehaviorProfile?.behavior_system;
  const behaviorScopeGroups = buildBehaviorScopeGroups(selectedBehaviorSystem);
  const bootstrapSession = readBootstrapSession(snapshot);
  const bootstrapQuestionRoutes = buildBootstrapQuestionRoutes(
    bootstrapSession,
    selectedBehaviorSystem
  );
  const bootstrapTemplateGroups = buildBootstrapTemplateGroups(
    selectedBehaviorSystem,
    selectedBehaviorProfile
  );
  const effectiveBehaviorFiles = behaviorScopeGroups.flatMap((group) =>
    group.files.map((file) => ({
      ...file,
      scopeTitle: group.title,
      scopeSummary: group.summary,
    }))
  );
  const [selectedBehaviorFileKey, setSelectedBehaviorFileKey] = useState("");
  const bootstrapSessionStatus =
    bootstrapSession && typeof bootstrapSession.status === "string"
      ? bootstrapSession.status
      : "pending";
  const bootstrapCurrentStep =
    bootstrapSession && typeof bootstrapSession.current_step === "string"
      ? bootstrapSession.current_step
      : "";
  const bootstrapSessionAgentName =
    bootstrapSession && typeof bootstrapSession.agent_profile_id === "string"
      ? behaviorProfiles.find((profile) => profile.profile_id === bootstrapSession.agent_profile_id)?.name ??
        bootstrapSession.agent_profile_id
      : "";
  useEffect(() => {
    setSelectedBehaviorFileKey((current) => {
      if (
        current &&
        effectiveBehaviorFiles.some((file) => `${file.scope}:${file.path}:${file.file_id}` === current)
      ) {
        return current;
      }
      const preferred = effectiveBehaviorFiles[0];
      return preferred ? `${preferred.scope}:${preferred.path}:${preferred.file_id}` : "";
    });
  }, [effectiveBehaviorFiles]);
  const selectedBehaviorFile =
    effectiveBehaviorFiles.find(
      (file) => `${file.scope}:${file.path}:${file.file_id}` === selectedBehaviorFileKey
    ) ?? effectiveBehaviorFiles[0] ?? null;
  const governanceCommands = buildBehaviorGovernanceCommands(
    selectedBehaviorFile,
    selectedBehaviorProfile,
    selectedBehaviorSystem
  );

  useEffect(() => {
    const view = (searchParams.get("view") || "").trim().toLowerCase();
    if (view === "behavior") {
      behaviorCenterRef.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
      return;
    }
    if (view === "main") {
      mainAgentRef.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    }
  }, [searchParams]);

  async function handleCopyGovernanceCommand(command: BehaviorGovernanceCommand) {
    try {
      await navigator.clipboard.writeText(command.command);
      setCopiedGovernanceKey(command.key);
      setFlashMessage(`已复制「${command.title}」命令。`);
      window.setTimeout(() => {
        setCopiedGovernanceKey((current) => (current === command.key ? "" : current));
      }, 1500);
    } catch {
      setFlashMessage("复制失败了，请手动复制下面的命令。");
    }
  }
  function openMainEditor() {
    const draft =
      agentView.mainAgentProfile !== null
        ? buildAgentEditorDraftFromProfile(
            agentView.mainAgentProfile,
            agentView.currentProjectId,
            agentView.currentProjectName,
            capabilityProviderEntries
          )
        : buildAgentEditorDraftFromTemplate(
            agentView.mainAgentTemplate,
            agentView.currentProjectId,
            agentView.currentProjectName,
            capabilityProviderEntries,
            {
              asMainAgent: true,
              sourceName: `${agentView.currentProjectName} 主 Agent`,
            }
          );

    startTransition(() => {
      setShowTemplatePicker(false);
      setReview(null);
      setEditorState({
        mode: "main",
        draft,
      });
      setFlashMessage(
        agentView.mainAgentProfile
          ? "主 Agent 的修改会影响当前项目后续新会话的默认聊天入口。"
          : "先把当前项目自己的主 Agent 建起来，后面新的会话默认就会稳定指向它。"
      );
    });
  }

  function openAgentEditor(profileId: string) {
    const profile = snapshot!.resources.worker_profiles?.profiles.find(
      (item) => item.profile_id === profileId
    );
    if (!profile) {
      return;
    }
    startTransition(() => {
      setShowTemplatePicker(false);
      setReview(null);
      setEditorState({
        mode: "agent",
        draft: buildAgentEditorDraftFromProfile(
          profile,
          agentView.currentProjectId,
          agentView.currentProjectName,
          capabilityProviderEntries
        ),
      });
      setFlashMessage(`正在编辑「${profile.name}」。先看用途和工具，再决定要不要改成主 Agent。`);
    });
  }

  function openCreatePicker() {
    startTransition(() => {
      setEditorState(null);
      setReview(null);
      setShowTemplatePicker(true);
      setFlashMessage("先从模板或空白起点里选一个最接近的。");
    });
  }

  function openBlankCreate() {
    startTransition(() => {
      setShowTemplatePicker(false);
      setReview(null);
      setEditorState({
        mode: "create",
        draft: buildBlankAgentEditorDraft(
          agentView.currentProjectId,
          agentView.currentProjectName,
          capabilityProviderEntries
        ),
      });
      setFlashMessage("从空白开始时，先把用途、模型和工具组补清楚。");
    });
  }

  function openTemplateCreate(templateId: string) {
    const template = snapshot!.resources.worker_profiles?.profiles.find(
      (profile) => profile.profile_id === templateId
    );
    startTransition(() => {
      setShowTemplatePicker(false);
      setReview(null);
      setEditorState({
        mode: "create",
        draft: buildAgentEditorDraftFromTemplate(
          template ?? null,
          agentView.currentProjectId,
          agentView.currentProjectName,
          capabilityProviderEntries
        ),
      });
      setFlashMessage("模板只负责给你一个合理起点，后面的名称、用途和工具要按你的实际需要改。");
    });
  }

  function closeComposer() {
    setShowTemplatePicker(false);
    setEditorState(null);
    setReview(null);
    setFlashMessage("列表已经更新，你可以继续编辑其他 Agent 或新建一个。");
  }

  function updateDraft<Key extends keyof AgentEditorDraft>(key: Key, value: AgentEditorDraft[Key]) {
    setEditorState((current) =>
      current
        ? {
            ...current,
            draft: {
              ...current.draft,
              [key]: value,
            },
          }
        : current
    );
  }

  function updateDraftList(
    key: "defaultToolGroups" | "selectedTools" | "runtimeKinds" | "policyRefs",
    value: string
  ) {
    setEditorState((current) =>
      current
        ? {
            ...current,
            draft: {
              ...current.draft,
              [key]: toggleStringValue(current.draft[key], value),
            },
          }
        : current
    );
  }

  function updateCapability(selectionItemId: string, selected: boolean) {
    setEditorState((current) =>
      current
        ? {
            ...current,
            draft: {
              ...current.draft,
              capabilitySelection: {
                ...current.draft.capabilitySelection,
                [selectionItemId]: selected,
              },
            },
          }
        : current
    );
  }

  async function handleSave() {
    if (!editorState) {
      return;
    }
    if (metadataError) {
      setFlashMessage(metadataError);
      return;
    }

    const payload = buildAgentPayload(editorState.draft, capabilityProviderEntries);
    const reviewResult = await submitAction("worker_profile.review", { draft: payload });
    const parsedReview = parseAgentReview(reviewResult?.data.review);
    setReview(parsedReview);

    if (!parsedReview?.canSave || !parsedReview.ready) {
      setFlashMessage(
        parsedReview?.nextActions[0] ||
          parsedReview?.blockingReasons[0] ||
          parsedReview?.warnings[0] ||
          "当前配置还不能直接保存。"
      );
      return;
    }

    const result = await submitAction("worker_profile.apply", {
      draft: payload,
      publish: true,
      set_as_default: editorState.mode === "main",
      change_summary:
        editorState.mode === "main" ? "通过 Agents 页面更新主 Agent" : "通过 Agents 页面更新 Agent",
    });

    if (!result) {
      return;
    }

    closeComposer();
    setFlashMessage(
      editorState.mode === "main"
        ? "主 Agent 已保存，当前项目后面的聊天和任务会优先用它。"
        : editorState.draft.profileId
          ? `已更新「${editorState.draft.name}」。`
          : `已创建「${editorState.draft.name}」。`
    );
  }

  async function handleBindAsMain(profileId: string, name: string) {
    const result = await submitAction("worker_profile.bind_default", {
      profile_id: profileId,
    });
    if (result) {
      closeComposer();
      setFlashMessage(`已把「${name}」设为当前项目的主 Agent。`);
    }
  }

  async function handleDeleteAgent(agent: AgentCardViewModel) {
    const riskHint =
      agent.activeWorkCount > 0 || agent.attentionWorkCount > 0
        ? "它还有正在运行或待处理的工作。删除后，这些工作不会自动换人接手。"
        : "删除后，它会从当前项目列表里移走。";
    if (!window.confirm(`确认删除「${agent.name}」吗？\n\n${riskHint}`)) {
      return;
    }
    const result = await submitAction("worker_profile.archive", {
      profile_id: agent.profileId,
    });
    if (result) {
      if (editorState?.draft.profileId === agent.profileId) {
        closeComposer();
      }
      setFlashMessage(`已删除「${agent.name}」。`);
    }
  }

  const busySaving =
    busyActionId === "worker_profile.review" || busyActionId === "worker_profile.apply";

  async function handleStartAgentSession(profileId: string, agentName: string) {
    const result = await submitAction("session.new", {
      agent_profile_id: profileId,
    });
    if (!result) {
      return;
    }
    setFlashMessage(`下一条消息会直接开启「${agentName}」会话。`);
    navigate("/chat");
  }

  return (
    <div className="wb-page wb-agent-management-page">
      <section className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-kicker">Agents</p>
            <h1>当前项目的 Agent 管理</h1>
            <p className="wb-panel-copy">
              这里维护的是当前项目的默认 Agent 定义。先把主 Agent 定稳，再把其他 Agent 按职责拆开。
            </p>
          </div>
          <div className="wb-inline-actions wb-inline-actions-wrap">
            <button type="button" className="wb-button wb-button-primary" onClick={openCreatePicker}>
              新建 Agent
            </button>
            <Link className="wb-button wb-button-secondary" to="/agents/skills">
              管理 Skills
            </Link>
            <Link className="wb-button wb-button-tertiary" to="/agents/mcp">
              管理 MCP
            </Link>
          </div>
        </div>

        <div className="wb-agent-summary-grid">
          <div className="wb-note">
            <strong>{agentView.currentProjectName}</strong>
            <span>你现在看到的是这个项目自己的主 Agent 和已创建 Agent。</span>
          </div>
          <div className="wb-note">
            <strong>主 Agent</strong>
            <span>
              {agentView.mainAgent.status === "ready"
                ? "已经建立完成，会作为这个项目后续新会话的默认入口。"
                : "还在沿用系统模板，建议先建立项目自己的主 Agent。"}
            </span>
          </div>
          <div className="wb-note">
            <strong>已创建 Agent</strong>
            <span>{agentView.projectAgents.length} 个，按项目职责拆开管理。</span>
          </div>
          <div className="wb-note">
            <strong>已有会话</strong>
            <span>
              已经运行中的会话会继续沿用自己的 project / session 绑定，不会因为这里修改而被回写。
            </span>
          </div>
        </div>

        <div className="wb-inline-banner is-muted">
          <strong>{flashMessage}</strong>
          <span>
            这里影响的是当前项目的默认配置；如果你要管理别的项目，先切到对应 Project，再编辑它自己的 Agent。
          </span>
        </div>

        {focusedSessionDiffersFromCurrentProject ? (
          <div className="wb-inline-banner is-muted">
            <strong>当前聚焦会话仍在别的项目里运行</strong>
            <span>
              该会话属于 {focusedSessionProjectName} / {focusedSessionWorkspaceName}。你现在编辑的是
              {agentView.currentProjectName} 的默认 Agent 定义，不会反向改写那个会话。
            </span>
          </div>
        ) : null}

        {agentView.mainAgent.status !== "ready" ? (
          <div className="wb-inline-banner is-muted">
            <strong>当前项目还没有自己的主 Agent</strong>
            <span>
              Chat 这时仍会先回退到系统内建的 Butler / Research / Dev / Ops 运行时 lane。
              这些是系统 fallback 与专项委派能力，不等于你已经在当前项目里创建了同名 Agent。
            </span>
          </div>
        ) : null}
      </section>

      <section id="agents-behavior-center" ref={behaviorCenterRef} className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">Behavior Center</p>
            <h3>把共享规则、Agent 私有文件和 Project 覆盖放在一个地方看清楚</h3>
            <p className="wb-panel-copy">
              这里展示的是当前 Project 下的 effective behavior files、路径清单和存储边界。Settings
              不再维护这一块。
            </p>
          </div>
          <div className="wb-inline-actions wb-inline-actions-wrap">
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={() => void refreshSnapshot()}
            >
              刷新控制面
            </button>
            <Link className="wb-button wb-button-tertiary" to="/agents?view=main">
              定位到主 Agent
            </Link>
          </div>
        </div>

        {behaviorProfiles.length === 0 || selectedBehaviorProfile === null ? (
          <div className="wb-empty-state">
            <strong>当前作用域还没有可见的 Behavior Profile</strong>
            <span>先建立当前 Project 的主 Agent，或者刷新控制面，让当前有效的 behavior summary 出现在这里。</span>
            <div className="wb-inline-actions wb-inline-actions-wrap">
              <button
                type="button"
                className="wb-button wb-button-primary"
                onClick={openMainEditor}
              >
                建立主 Agent
              </button>
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={() => void refreshSnapshot()}
              >
                刷新控制面
              </button>
            </div>
          </div>
        ) : (
          <div className="wb-behavior-layout">
            <div className="wb-section-stack">
              <div className="wb-agent-tablist">
                {behaviorProfiles.map((profile) => {
                  const isActive = profile.profile_id === selectedBehaviorProfile.profile_id;
                  return (
                    <button
                      key={profile.profile_id}
                      type="button"
                      className={`wb-agent-tab ${isActive ? "is-active" : ""}`}
                      onClick={() => setSelectedBehaviorProfileId(profile.profile_id)}
                    >
                      <strong>{profile.name}</strong>
                      <span>{formatAgentScope(profile.scope)} · {profile.tool_profile}</span>
                    </button>
                  );
                })}
              </div>

              <div className="wb-card-grid wb-card-grid-4">
                <article className="wb-card">
                  <p className="wb-card-label">当前 Agent</p>
                  <strong>{selectedBehaviorProfile.name}</strong>
                  <span>{selectedBehaviorProfile.persona_summary || "还没有填写 persona 摘要。"}</span>
                </article>
                <article className="wb-card">
                  <p className="wb-card-label">有效文件</p>
                  <strong>{selectedBehaviorSystem?.files?.length ?? 0}</strong>
                  <span>{selectedBehaviorSystem?.source_chain?.length ?? 0} 条来源链</span>
                </article>
                <article className="wb-card">
                  <p className="wb-card-label">继承给 Worker</p>
                  <strong>{selectedBehaviorSystem?.worker_slice?.shared_file_ids?.length ?? 0}</strong>
                  <span>
                    {selectedBehaviorSystem?.worker_slice?.shared_file_ids?.slice(0, 3)?.join(" / ") || "当前没有共享切片"}
                  </span>
                </article>
                <article className="wb-card">
                  <p className="wb-card-label">运行时提示</p>
                  <strong>{selectedBehaviorSystem?.runtime_hint_fields?.length ?? 0}</strong>
                  <span>
                    {selectedBehaviorSystem?.runtime_hint_fields?.slice(0, 2)?.join(" / ") || "当前没有 runtime hint"}
                  </span>
                </article>
                <article className="wb-card">
                  <p className="wb-card-label">Bootstrap 模板</p>
                  <strong>{selectedBehaviorProfile.bootstrap_template_ids?.length ?? 0}</strong>
                  <span>
                    {bootstrapTemplateGroups.map((group) => group.title).join(" / ") || "当前没有 bootstrap 模板"}
                  </span>
                </article>
              </div>

              <div className="wb-inline-banner is-muted">
                <strong>当前 effective source chain</strong>
                <span>{selectedBehaviorSystem?.source_chain?.join(" -> ") || "default_behavior_templates"}</span>
              </div>

              <div className="wb-behavior-scope-grid">
                {behaviorScopeGroups.map((group) => (
                  <article key={group.scope} className="wb-note wb-behavior-scope-card">
                    <strong>{group.title}</strong>
                    <span>{group.summary}</span>
                    <div className="wb-chip-row">
                      <span className="wb-chip">{group.files.length} 个文件</span>
                    </div>
                    <div className="wb-note-stack">
                      {group.files.map((file) => (
                        <button
                          key={`${group.scope}:${file.file_id}`}
                          type="button"
                          className={`wb-note wb-behavior-file-row ${
                            selectedBehaviorFile &&
                            `${file.scope}:${file.path}:${file.file_id}` ===
                              `${selectedBehaviorFile.scope}:${selectedBehaviorFile.path}:${selectedBehaviorFile.file_id}`
                              ? "is-active"
                              : ""
                          }`}
                          onClick={() => setSelectedBehaviorFileKey(`${file.scope}:${file.path}:${file.file_id}`)}
                        >
                          <strong>{file.file_id}</strong>
                          <span>{file.title} · {file.visibility} · {describeReviewMode(file.editable_mode, file.review_mode)}</span>
                          <small className="wb-inline-note">
                            {file.path || "未物化"} · {file.exists_on_disk ? "已存在" : "按需 materialize"} ·
                            {file.shareWithWorkers ? " 会被 Worker 看到" : " 只影响当前 Agent"}
                          </small>
                        </button>
                      ))}
                    </div>
                  </article>
                ))}
              </div>
            </div>

            <div className="wb-section-stack">
              <article className="wb-note wb-behavior-scope-card">
                <strong>Bootstrap & Templates</strong>
                <span>默认会话 Agent 的初始化模板、当前 bootstrap 状态，以及问题答案会落去哪里。</span>
                <div className="wb-note-stack">
                  <div className="wb-note">
                    <strong>当前 bootstrap</strong>
                    <span>
                      {bootstrapSession
                        ? `${bootstrapSessionStatus}${bootstrapCurrentStep ? ` · 当前步骤 ${bootstrapCurrentStep}` : ""}`
                        : "当前 project 还没有 bootstrap session"}
                    </span>
                    {bootstrapSessionAgentName ? (
                      <small className="wb-inline-note">绑定 Agent：{bootstrapSessionAgentName}</small>
                    ) : null}
                  </div>
                  {bootstrapTemplateGroups.map((group) => (
                    <div key={group.key} className="wb-note">
                      <strong>{group.title}</strong>
                      <span>{group.summary}</span>
                      <small className="wb-inline-note">{group.items.join(" / ")}</small>
                    </div>
                  ))}
                </div>
              </article>

              <article className="wb-note wb-behavior-scope-card">
                <strong>Bootstrap 问卷与落点</strong>
                <span>首次进入 project 时默认会话 Agent 会按这条路由提问；答案不会随意落到 md。</span>
                <div className="wb-note-stack">
                  {bootstrapQuestionRoutes.length > 0 ? (
                    bootstrapQuestionRoutes.map((item) => (
                      <div key={item.stepId} className="wb-note">
                        <strong>{item.stepId}</strong>
                        <span>
                          {item.summary || "当前没有额外说明。"}
                        </span>
                        <small className="wb-inline-note">
                          路由：{item.route}
                          {item.target ? ` · 目标：${item.target}` : ""}
                        </small>
                      </div>
                    ))
                  ) : (
                    <div className="wb-note">
                      <strong>当前没有 bootstrap 问卷</strong>
                      <span>bootstrap 还没创建，或当前控制面尚未投影这轮问卷配置。</span>
                    </div>
                  )}
                </div>
              </article>

              <article className="wb-note wb-behavior-scope-card">
                <strong>Project Path Manifest</strong>
                <span>任何 Agent 要改 behavior、读 workspace 或找数据目录，都应该先看这份路径清单。</span>
                <div className="wb-note-stack">
                  <div className="wb-note">
                    <strong>project root</strong>
                    <span>{selectedBehaviorSystem?.path_manifest?.project_root || "未提供"}</span>
                  </div>
                  <div className="wb-note">
                    <strong>behavior roots</strong>
                    <span>
                      shared: {selectedBehaviorSystem?.path_manifest?.shared_behavior_root || "未提供"}
                      {" · "}
                      agent: {selectedBehaviorSystem?.path_manifest?.agent_behavior_root || "未提供"}
                    </span>
                  </div>
                  <div className="wb-note">
                    <strong>project roots</strong>
                    <span>
                      workspace: {selectedBehaviorSystem?.path_manifest?.project_workspace_root || "未提供"}
                      {" · "}
                      data: {selectedBehaviorSystem?.path_manifest?.project_data_root || "未提供"}
                    </span>
                  </div>
                  <div className="wb-note">
                    <strong>notes / artifacts / secrets</strong>
                    <span>
                      notes: {selectedBehaviorSystem?.path_manifest?.project_notes_root || "未提供"}
                      {" · "}
                      artifacts: {selectedBehaviorSystem?.path_manifest?.project_artifacts_root || "未提供"}
                      {" · "}
                      secrets: {selectedBehaviorSystem?.path_manifest?.secret_bindings_path || "未提供"}
                    </span>
                  </div>
                </div>
              </article>

              <article className="wb-note wb-behavior-scope-card">
                <strong>Storage Boundaries</strong>
                <span>把规则、事实、敏感值和工作材料分开，避免行为文件继续承担 Memory / Secrets 的角色。</span>
                <div className="wb-note-stack">
                  <div className="wb-note">
                    <strong>事实</strong>
                    <span>{selectedBehaviorSystem?.storage_boundary_hints?.facts_store || "MemoryService"}</span>
                  </div>
                  <div className="wb-note">
                    <strong>敏感值</strong>
                    <span>{selectedBehaviorSystem?.storage_boundary_hints?.secrets_store || "SecretService"}</span>
                  </div>
                  <div className="wb-note">
                    <strong>行为文件</strong>
                    <span>{selectedBehaviorSystem?.storage_boundary_hints?.behavior_store || "behavior_files"}</span>
                  </div>
                  <div className="wb-note">
                    <strong>工作材料</strong>
                    <span>
                      {(selectedBehaviorSystem?.storage_boundary_hints?.workspace_roots ?? []).join(" / ") || "workspace / data / notes / artifacts"}
                    </span>
                  </div>
                  <div className="wb-note">
                    <strong>Bootstrap 默认落点</strong>
                    <span>
                      facts → {selectedBehaviorSystem?.bootstrap_routes?.facts?.store || "MemoryService"}
                      {" · "}
                      secrets → {selectedBehaviorSystem?.bootstrap_routes?.secrets?.path || "project.secret-bindings.json"}
                    </span>
                    <small className="wb-inline-note">
                      身份 → {selectedBehaviorSystem?.bootstrap_routes?.assistant_identity?.target || "IDENTITY.md"}
                      {" · "}
                      性格 → {selectedBehaviorSystem?.bootstrap_routes?.assistant_personality?.target || "SOUL.md"}
                    </small>
                  </div>
                </div>
              </article>

              <article className="wb-note wb-behavior-scope-card">
                <strong>Effective View & Governance</strong>
                <span>选中一个具体文件后，看它真正影响谁、该怎么改；当前先在这里复制精确命令，再去终端 proposal/apply。</span>
                <div className="wb-settings-cli-grid">
                  {selectedBehaviorFile ? (
                    <>
                      <article className="wb-note wb-cli-snippet">
                        <strong>{selectedBehaviorFile.file_id}</strong>
                        <span>
                          {selectedBehaviorFile.scopeTitle} · {selectedBehaviorFile.title}
                        </span>
                        <small className="wb-inline-note">
                          {selectedBehaviorFile.path} · {describeReviewMode(selectedBehaviorFile.editable_mode, selectedBehaviorFile.review_mode)}
                        </small>
                        <small className="wb-inline-note">
                          {selectedBehaviorFile.shareWithWorkers ? "会进入 Worker 共享切片" : "只影响当前 Agent"} ·
                          {selectedBehaviorFile.exists_on_disk ? " 已物化" : " 尚未物化"}
                        </small>
                      </article>
                      {governanceCommands.map((snippet) => (
                        <article key={snippet.key} className="wb-note wb-cli-snippet">
                          <strong>{snippet.title}</strong>
                          <span>{snippet.summary}</span>
                          <pre className="wb-cli-snippet-code">{snippet.command}</pre>
                          <div className="wb-inline-actions wb-inline-actions-wrap">
                            <button
                              type="button"
                              className="wb-button wb-button-secondary wb-button-inline"
                              onClick={() => void handleCopyGovernanceCommand(snippet)}
                            >
                              {copiedGovernanceKey === snippet.key ? "已复制" : "复制命令"}
                            </button>
                          </div>
                        </article>
                      ))}
                    </>
                  ) : (
                    <article className="wb-note wb-cli-snippet">
                      <strong>当前没有可选文件</strong>
                      <span>先让当前 Agent 暴露出 effective behavior files，再选择具体文件查看治理动作。</span>
                    </article>
                  )}
                </div>
              </article>
            </div>
          </div>
        )}
      </section>

      <div className="wb-agent-management-layout">
        <div className="wb-section-stack">
          <section id="agents-main-agent" ref={mainAgentRef} className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">主 Agent</p>
                <h3>当前项目默认会先用这一个</h3>
              </div>
            </div>
            {renderAgentCard(agentView.mainAgent, {
              onEdit: openMainEditor,
              onStartSession: () =>
                void handleStartAgentSession(agentView.mainAgent.profileId, agentView.mainAgent.name),
              primaryActionLabel: agentView.mainAgent.status === "ready" ? "编辑主 Agent" : "建立主 Agent",
              busyActionId,
            })}
          </section>

          <section className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">已创建 Agent</p>
                <h3>按职责拆开的辅助 Agent</h3>
                <p className="wb-panel-copy">
                  这些 Agent 只属于当前项目默认定义。需要新的分工时再创建，不要把模板和正式 Agent 混在一起。
                </p>
              </div>
              <span className="wb-chip">{agentView.projectAgents.length} 个</span>
            </div>

            {agentView.projectAgents.length === 0 ? (
              <div className="wb-empty-state">
                <strong>当前项目还没有其他 Agent</strong>
                <span>如果你想把调研、开发或运行保障拆开，让不同 Agent 各司其职，可以先新建一个。</span>
                <div className="wb-inline-actions">
                  <button type="button" className="wb-button wb-button-primary" onClick={openCreatePicker}>
                    新建第一个 Agent
                  </button>
                </div>
              </div>
            ) : (
              <div className="wb-section-stack">
                {agentView.projectAgents.map((agent) =>
                  renderAgentCard(agent, {
                    onEdit: () => openAgentEditor(agent.profileId),
                    onStartSession: () => void handleStartAgentSession(agent.profileId, agent.name),
                    onPromote: () => void handleBindAsMain(agent.profileId, agent.name),
                    onDelete: () => void handleDeleteAgent(agent),
                    primaryActionLabel: "编辑",
                    busyActionId,
                  })
                )}
              </div>
            )}
          </section>

          {builtinDirectAgents.length > 0 ? (
            <section className="wb-panel">
              <div className="wb-panel-head">
                <div>
                  <p className="wb-card-label">直接会话</p>
                  <h3>需要时，单独和专长 Agent 开一条会话</h3>
                  <p className="wb-panel-copy">
                    这里不会再偷偷把当前聊天绑到别的 Agent；如果你要直接和 Researcher 之类的角色对话，就显式新开一条会话。
                    这些条目同时也是系统内建 runtime template，会被 Butler 在专项任务里当作 fallback lane 使用，
                    但它们不等于你已经在当前项目里创建了同名 Agent。
                  </p>
                </div>
                <span className="wb-chip">{builtinDirectAgents.length} 个</span>
              </div>
              <div className="wb-section-stack">
                {builtinDirectAgents.map((agent) =>
                  renderAgentCard(agent, {
                    onEdit: () => void handleStartAgentSession(agent.profileId, agent.name),
                    primaryActionLabel: "直接开启会话",
                    busyActionId,
                  })
                )}
              </div>
            </section>
          ) : null}
        </div>

        <div className="wb-section-stack">
          {showTemplatePicker ? (
            <AgentTemplatePicker
              currentProjectName={agentView.currentProjectName}
              templates={agentView.builtinTemplates}
              onPickTemplate={openTemplateCreate}
              onPickBlank={openBlankCreate}
              onCancel={closeComposer}
            />
          ) : editorState ? (
            <AgentEditorSection
              title={
                editorState.mode === "main"
                  ? "主 Agent"
                  : editorState.draft.profileId
                    ? editorState.draft.name
                    : "新建 Agent"
              }
              description={
                editorState.mode === "main"
                  ? "这里改的是当前项目默认会优先使用的 Agent。"
                  : "把名称、用途、模型和工具先讲清楚，后面的行为才会稳定。"
              }
              saveLabel={
                editorState.mode === "main"
                  ? "保存主 Agent"
                  : editorState.draft.profileId
                    ? "保存 Agent"
                    : "创建 Agent"
              }
              draft={editorState.draft}
              review={review}
              busy={busySaving}
              projectOptions={projectOptions}
              modelAliasOptions={modelAliasOptions}
              toolProfileOptions={toolProfileOptions}
              toolGroupOptions={toolGroupOptions}
              toolOptions={toolOptions}
              policyOptions={policyOptions}
              skillEntries={skillEntries}
              mcpEntries={mcpEntries}
              metadataError={metadataError}
              onChangeDraft={updateDraft}
              onToggleDefaultToolGroup={(value) => updateDraftList("defaultToolGroups", value)}
              onToggleSelectedTool={(value) => updateDraftList("selectedTools", value)}
              onToggleCapability={updateCapability}
              onToggleRuntimeKind={(value) => updateDraftList("runtimeKinds", value)}
              onTogglePolicyRef={(value) => updateDraftList("policyRefs", value)}
              onSave={() => void handleSave()}
              onCancel={closeComposer}
              formatTokenLabel={formatTokenLabel}
            />
          ) : (
            <section className="wb-panel wb-agent-editor-shell">
              <div className="wb-empty-state">
                <strong>先从左边选一个 Agent 或新建一个</strong>
                <span>
                  主 Agent 负责当前项目的默认聊天入口；其他 Agent 用来承接特定职责。
                </span>
                <div className="wb-inline-actions">
                  <button type="button" className="wb-button wb-button-primary" onClick={openMainEditor}>
                    {agentView.mainAgent.status === "ready" ? "编辑主 Agent" : "建立主 Agent"}
                  </button>
                  <button type="button" className="wb-button wb-button-secondary" onClick={openCreatePicker}>
                    新建 Agent
                  </button>
                </div>
                {agentView.mainAgentTemplate ? (
                  <small className="wb-inline-note">
                    当前系统默认起点：
                    {formatAgentArchetype(agentView.mainAgentTemplate.static_config.base_archetype)}
                    。如需看专项 fallback lane，左侧“系统内建运行时”会显示完整列表。
                  </small>
                ) : null}
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
