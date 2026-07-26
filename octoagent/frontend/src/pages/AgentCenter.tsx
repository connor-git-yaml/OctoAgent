import { startTransition, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useSearchParams } from "react-router-dom";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import AgentEditorSection from "../domains/agents/AgentEditorSection";
import AgentCard from "../domains/agents/AgentOverview";
import AgentTemplatePicker from "../domains/agents/AgentTemplatePicker";
import BehaviorVersionHistory from "../domains/agents/BehaviorVersionHistory";
import {
  buildAgentEditorDraftFromProfile,
  buildAgentEditorDraftFromTemplate,
  buildAgentPayload,
  buildBlankAgentEditorDraft,
  buildCapabilityProviderEntries,
  buildModelAliasOptions,
  deriveAgentManagementView,
  formatTokenLabel,
  parseAgentReview,
  type AgentCardViewModel,
  type AgentEditorDraft,
  type AgentEditorReview,
  type ApprovalOverrideDisplay,
  type BehaviorFileInfo,
} from "../domains/agents/agentManagementData";
import { resolveResourcePageState } from "../domains/shared/resourcePageState";
import {
  fetchAgentApprovalOverrides,
  revokeAgentApprovalOverride,
} from "../api/f149/adapters";
import { executeF149Action } from "../platform/actions/f149Actions";
import type { AgentProfileItem } from "../types";
import "./AgentCenter.css";

type EditorMode = "main" | "agent" | "create";

interface EditorState {
  mode: EditorMode;
  draft: AgentEditorDraft;
  behaviorFiles: BehaviorFileInfo[];
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


const AGENT_PRIVATE_FILE_IDS = new Set(["IDENTITY.md", "SOUL.md", "HEARTBEAT.md"]);
const SHARED_FILE_IDS = new Set(["AGENTS.md", "USER.md", "TOOLS.md", "BOOTSTRAP.md"]);
const PROJECT_SHARED_FILE_IDS = new Set(["PROJECT.md", "KNOWLEDGE.md", "USER.md", "TOOLS.md"]);

function formatScopeTitle(scope: string): string {
  switch (scope) {
    case "system_shared":
      return "全局共享规则";
    case "agent_private":
      return "Agent 私有配置";
    case "project_shared":
      return "项目共享配置";
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


export default function AgentCenter() {
  const {
    snapshot,
    loading = false,
    error = null,
    authError = null,
    refreshSnapshot,
    submitAction,
    busyActionId,
  } = useWorkbench();
  const [searchParams] = useSearchParams();
  const behaviorCenterRef = useRef<HTMLElement | null>(null);
  const mainAgentRef = useRef<HTMLElement | null>(null);
  const agentProfilesDocument = snapshot!.resources.agent_profiles ?? {
    generated_at: "",
    profiles: [],
    active_project_id: "",
  };

  const agentView = useMemo(() => deriveAgentManagementView(snapshot!), [snapshot]);
  const capabilityProviderEntries = useMemo(
    () => buildCapabilityProviderEntries(snapshot!),
    [
      snapshot!.resources.mcp_provider_catalog.generated_at,
      snapshot!.resources.skill_governance.generated_at,
    ]
  );
  const modelAliasOptions = useMemo(
    () => buildModelAliasOptions(snapshot!),
    [snapshot!.resources.config.generated_at]
  );
  // 全局管理视图——行为文件区展示所有 agent profile，不按当前项目过滤
  const behaviorProfiles = useMemo(
    () => agentProfilesDocument?.profiles ?? [],
    [agentProfilesDocument?.generated_at]
  );
  const [selectedBehaviorProfileId, setSelectedBehaviorProfileId] = useState("");

  const [editorState, setEditorState] = useState<EditorState | null>(null);
  const [showTemplatePicker, setShowTemplatePicker] = useState(false);
  const [flashMessage, setFlashMessage] = useState("");
  const [review, setReview] = useState<AgentEditorReview | null>(null);

  // 行为文件查看/编辑状态
  const [viewingFilePath, setViewingFilePath] = useState("");
  const [viewingFileId, setViewingFileId] = useState("");
  const [fileContent, setFileContent] = useState("");
  const [fileContentLoading, setFileContentLoading] = useState(false);
  const [editingFile, setEditingFile] = useState(false);
  const [editFileContent, setEditFileContent] = useState("");

  // 审批覆盖（全局，不区分 profile）
  const [approvalOverrides, setApprovalOverrides] = useState<ApprovalOverrideDisplay[]>([]);
  const [approvalOverridesLoading, setApprovalOverridesLoading] = useState(false);
  const [approvalOverridesError, setApprovalOverridesError] = useState("");
  const [historyFile, setHistoryFile] = useState<{
    file: BehaviorFileInfo;
    trigger: HTMLButtonElement;
  } | null>(null);

  async function fetchApprovalOverrides() {
    setApprovalOverridesLoading(true);
    setApprovalOverridesError("");
    try {
      const items: ApprovalOverrideDisplay[] =
        await fetchAgentApprovalOverrides();
      setApprovalOverrides(items);
    } catch {
      setApprovalOverrides([]);
      setApprovalOverridesError("临时授权加载失败，请重试。");
    } finally {
      setApprovalOverridesLoading(false);
    }
  }

  async function handleRevokeOverride(agentRuntimeId: string, toolName: string) {
    try {
      const revoked = await revokeAgentApprovalOverride(agentRuntimeId, toolName);
      if (revoked) {
        setApprovalOverrides((current) =>
          current.filter(
            (o) => !(o.agentRuntimeId === agentRuntimeId && o.toolName === toolName)
          )
        );
        setFlashMessage("已撤销授权。下次使用该工具时需要重新确认。");
      } else {
        setFlashMessage("撤销失败，请重试。");
      }
    } catch {
      setFlashMessage("撤销失败，请重试。");
    }
  }

  useEffect(() => {
    setEditorState(null);
    setShowTemplatePicker(false);
    setReview(null);
    setFlashMessage("");
    setViewingFilePath("");
    setHistoryFile(null);
  }, [agentView.currentProjectId]);

  useEffect(() => {
    setSelectedBehaviorProfileId((current) => {
      if (current && behaviorProfiles.some((profile) => profile.profile_id === current)) {
        return current;
      }
      const preferredProfileId =
        agentView.mainAgentProfile?.profile_id ||
        behaviorProfiles[0]?.profile_id ||
        "";
      return preferredProfileId;
    });
  }, [agentView.mainAgentProfile?.profile_id, behaviorProfiles]);

  const selectedBehaviorProfile =
    behaviorProfiles.find((profile) => profile.profile_id === selectedBehaviorProfileId) ??
    behaviorProfiles[0] ??
    null;
  const selectedBehaviorSystem = selectedBehaviorProfile?.behavior_system;
  const behaviorScopeGroups = buildBehaviorScopeGroups(selectedBehaviorSystem);

  // 编辑器打开时加载审批覆盖列表
  useEffect(() => {
    if (editorState) {
      void fetchApprovalOverrides();
    }
  }, [editorState !== null]);

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

  function closeHistory() {
    const trigger = historyFile?.trigger ?? null;
    setHistoryFile(null);
    window.requestAnimationFrame(() => trigger?.focus());
  }

  async function handleOpenBehaviorFile(filePath: string, fileId: string) {
    if (!filePath) {
      return;
    }
    setEditorState(null);
    setShowTemplatePicker(false);
    setViewingFilePath(filePath);
    setViewingFileId(fileId);
    setEditingFile(false);
    setFileContentLoading(true);
    try {
      const outcome = await executeF149Action(
        {
          actionId: "behavior.read_file",
          params: { file_path: filePath },
        },
        submitAction,
      );
      if (outcome.ok && outcome.data.exists) {
        setFileContent(outcome.data.content);
      } else {
        setFileContent("");
        setFlashMessage(
          outcome.ok
            ? "文件尚未创建，保存后会自动建立。"
            : "读取行为文件失败，请重试。",
        );
      }
    } catch {
      setFileContent("");
      setFlashMessage("读取行为文件失败，请重试。");
    } finally {
      setFileContentLoading(false);
    }
  }

  async function handleSaveBehaviorFile() {
    if (!viewingFilePath) {
      return;
    }
    try {
      const agentSlug =
        viewingFilePath.match(/behavior\/agents\/([^/]+)\//)?.[1] ?? "";
      const projectSlug =
        viewingFilePath.match(/projects\/([^/]+)\//)?.[1] ?? "";
      const outcome = await executeF149Action(
        {
          actionId: "behavior.write_file",
          params: {
            agent_slug: agentSlug,
            content: editFileContent,
            file_id: viewingFileId,
            project_slug: projectSlug,
          },
        },
        submitAction,
      );
      if (!outcome.ok) {
        setFlashMessage("保存失败，请重新加载后再试。");
        return;
      }
      setFileContent(editFileContent);
      setEditingFile(false);
      setFlashMessage("已保存。");
    } catch {
      setFlashMessage("保存失败。");
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
      setViewingFilePath("");
      setEditorState({
        mode: "main",
        draft,
        behaviorFiles: agentView.mainAgent.behaviorFiles,
      });
      setFlashMessage("");
    });
  }

  function openAgentEditor(profileId: string) {
    const profile = snapshot!.resources.worker_profiles?.profiles?.find(
      (item) => item.profile_id === profileId
    );
    if (!profile) {
      return;
    }
    const agentCard = agentView.projectAgents.find((a) => a.profileId === profileId);
    startTransition(() => {
      setShowTemplatePicker(false);
      setReview(null);
      setViewingFilePath("");
      setEditorState({
        mode: "agent",
        draft: buildAgentEditorDraftFromProfile(
          profile,
          agentView.currentProjectId,
          agentView.currentProjectName,
          capabilityProviderEntries
        ),
        behaviorFiles: agentCard?.behaviorFiles ?? [],
      });
      setFlashMessage("");
    });
  }

  function openCreatePicker() {
    startTransition(() => {
      setEditorState(null);
      setReview(null);
      setViewingFilePath("");
      setShowTemplatePicker(true);
      setFlashMessage("");
    });
  }

  function openBlankCreate() {
    startTransition(() => {
      setShowTemplatePicker(false);
      setReview(null);
      setViewingFilePath("");
      setEditorState({
        mode: "create",
        draft: buildBlankAgentEditorDraft(
          agentView.currentProjectId,
          agentView.currentProjectName,
          capabilityProviderEntries
        ),
        behaviorFiles: [],
      });
      setFlashMessage("");
    });
  }

  function openTemplateCreate(templateId: string) {
    const template = snapshot!.resources.worker_profiles?.profiles?.find(
      (profile) => profile.profile_id === templateId
    );
    startTransition(() => {
      setShowTemplatePicker(false);
      setReview(null);
      setViewingFilePath("");
      setEditorState({
        mode: "create",
        draft: buildAgentEditorDraftFromTemplate(
          template ?? null,
          agentView.currentProjectId,
          agentView.currentProjectName,
          capabilityProviderEntries
        ),
        behaviorFiles: [],
      });
      setFlashMessage("");
    });
  }

  function closeComposer() {
    setShowTemplatePicker(false);
    setEditorState(null);
    setReview(null);
    setViewingFilePath("");
    setFlashMessage("");
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

  async function handleSave() {
    if (!editorState) {
      return;
    }

    const payload = buildAgentPayload(editorState.draft, capabilityProviderEntries);
    if (!editorState.draft.profileId && editorState.mode !== "main") {
      setReview(null);
      const result = await submitAction("agent.create_worker_with_project", {
        worker_name: editorState.draft.name,
        project_name: editorState.draft.name,
        model_alias: editorState.draft.modelAlias,
        tool_profile: editorState.draft.toolProfile,
        permission_preset: editorState.draft.permissionPreset,
        role_card: editorState.draft.roleCard,
      });
      if (!result) {
        return;
      }
      const createdProfileId = String(result.data?.worker_profile_id ?? "");
      const createdProjectId = String(result.data?.project_id ?? "");
      if (createdProfileId && createdProjectId) {
        const postCreatePayload = buildAgentPayload(
          {
            ...editorState.draft,
            profileId: createdProfileId,
            projectId: createdProjectId,
          },
          capabilityProviderEntries
        );
        await submitAction("worker_profile.apply", {
          draft: postCreatePayload,
          publish: true,
          set_as_default: false,
          change_summary: "创建 Agent 后同步配置",
        });
      }
      closeComposer();
      setFlashMessage(`已创建「${editorState.draft.name}」，并生成项目与会话。`);
      return;
    }

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
        editorState.mode === "main" ? "通过智能体页面更新主 Agent" : "通过智能体页面更新 Agent",
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
    busyActionId === "worker_profile.review" ||
    busyActionId === "worker_profile.apply" ||
    busyActionId === "agent.create_worker_with_project";
  const agents = [
    ...(agentView.mainAgent.status === "ready" || agentView.projectAgents.length > 0
      ? [agentView.mainAgent]
      : []),
    ...agentView.projectAgents,
  ];
  const pageResolution = resolveResourcePageState({
    loading,
    hasContent: agents.length > 0,
    connected: true,
    error: authError ?? (error ? new Error(error) : null),
  });
  const pageState =
    pageResolution.owner === "surface"
      ? pageResolution.state.kind
      : "recoverable-error";

  return (
    <div className="f149-agent-page">
      <section className="f149-agent-hero">
        <div>
          <p className="f149-agent-kicker">AGENTS</p>
          <h1>智能体</h1>
          <p>管理主助手和分工，让不同角色各自负责最合适的工作。</p>
        </div>
        <div className="f149-agent-hero-actions">
          <span aria-label={`共 ${agents.length} 个智能体`}>
            <strong>{agents.length}</strong>
            个
          </span>
          <button type="button" onClick={openCreatePicker}>
            新建 Agent
          </button>
        </div>
      </section>

      {flashMessage ? (
        <div className="f149-agent-banner" role="status">
          {flashMessage}
        </div>
      ) : null}

      {pageState === "loading" ? (
        <div className="f149-agent-state" aria-live="polite">
          <span className="f149-agent-state-mark" aria-hidden="true" />
          <strong>正在整理智能体</strong>
          <p>正在准备角色与行为文件。</p>
        </div>
      ) : null}

      {pageState === "empty" ? (
        <div className="f149-agent-state">
          <span className="f149-agent-state-mark is-empty" aria-hidden="true" />
          <strong>还没有智能体</strong>
          <p>新建一个开始分工，先从主助手或空白角色起步。</p>
        </div>
      ) : null}

      {pageState === "permission-denied" ? (
        <div className="f149-agent-state is-error" role="alert">
          <strong>当前账号没有权限管理智能体</strong>
          <p>请联系管理员确认这项资源的访问权限。</p>
        </div>
      ) : null}

      {[
        "recoverable-error",
        "conflict",
        "not-found",
        "disconnected",
      ].includes(pageState) ? (
        <div className="f149-agent-state is-error" role="alert">
          <strong>智能体列表加载失败</strong>
          <p>连接可能暂时不稳定，请稍后再试。</p>
          <button type="button" onClick={() => void refreshSnapshot?.()}>
            重试
          </button>
        </div>
      ) : null}

      {pageState === "ready" ? (
        <div className="f149-agent-workspace">
          <section
            id="agents-main-agent"
            ref={mainAgentRef}
            className="f149-agent-list"
            aria-label="智能体列表"
          >
            {agents.map((agent) => (
              <AgentCard
                key={agent.profileId}
                agent={agent}
                busyActionId={busyActionId}
                onEdit={
                  agent.isMainAgent
                    ? openMainEditor
                    : () => openAgentEditor(agent.profileId)
                }
                onDelete={
                  agent.removable
                    ? () => void handleDeleteAgent(agent)
                    : undefined
                }
                onOpenBehaviorFile={(file) =>
                  void handleOpenBehaviorFile(file.path, file.file_id)
                }
                onOpenHistory={(file, trigger) =>
                  setHistoryFile({ file, trigger })
                }
              />
            ))}
          </section>

          <aside
            id="agents-behavior-center"
            ref={behaviorCenterRef}
            className={`f149-agent-side-panel ${
              historyFile ? "is-history-open" : ""
            }`}
          >
            {historyFile ? (
              <BehaviorVersionHistory
                fileId={historyFile.file.file_id}
                scope={historyFile.file.scope || "agent_private"}
                agentSlug={
                  historyFile.file.path.match(
                    /behavior\/agents\/([^/]+)\//,
                  )?.[1] ?? ""
                }
                projectSlug={
                  historyFile.file.path.match(/projects\/([^/]+)\//)?.[1] ?? ""
                }
                onClose={closeHistory}
              />
            ) : (
              <>
                <header className="f149-agent-side-header">
                  <div>
                    <p>BEHAVIOR</p>
                    <h2>行为文件</h2>
                  </div>
                </header>
                {behaviorProfiles.length === 0 ||
                selectedBehaviorProfile === null ? (
                  <div className="f149-agent-side-empty">
                    <strong>暂无行为文件</strong>
                    <span>创建智能体后会在这里显示。</span>
                  </div>
                ) : (
                  <div className="f149-agent-behavior-groups">
                    {behaviorScopeGroups
                      .filter((group) => group.scope !== "agent_private")
                      .map((group) => (
                        <article key={group.scope}>
                          <header>
                            <strong>{group.title}</strong>
                            <span>{group.files.length}</span>
                          </header>
                          {group.files.map((file) => (
                            <button
                              key={`${group.scope}:${file.file_id}`}
                              type="button"
                              className={
                                viewingFilePath === file.path
                                  ? "is-active"
                                  : ""
                              }
                              onClick={() =>
                                void handleOpenBehaviorFile(
                                  file.path,
                                  file.file_id,
                                )
                              }
                            >
                              <span>
                                <strong>{file.file_id}</strong>
                                <small>{file.title}</small>
                              </span>
                              <small>
                                {file.exists_on_disk ? "已创建" : "待创建"}
                              </small>
                            </button>
                          ))}
                        </article>
                      ))}
                  </div>
                )}
              </>
            )}
          </aside>
        </div>
      ) : null}

      {(showTemplatePicker || editorState || viewingFilePath) && document.body
        ? createPortal(
            <div
              className="f149-agent-modal-backdrop"
              onMouseDown={(event) => {
                if (event.target === event.currentTarget) {
                  closeComposer();
                }
              }}
            >
              <div className="f149-agent-modal">
                {showTemplatePicker ? (
                  <AgentTemplatePicker
                    currentProjectName={agentView.currentProjectName}
                    templates={agentView.builtinTemplates}
                    onPickTemplate={openTemplateCreate}
                    onPickBlank={openBlankCreate}
                    onCancel={closeComposer}
                  />
                ) : editorState ? (
                  <>
                    {approvalOverridesError ? (
                      <div className="f149-agent-modal-error" role="alert">
                        {approvalOverridesError}
                      </div>
                    ) : null}
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
                          ? "当前项目默认 Agent。"
                          : editorState.draft.profileId
                            ? "编辑 Agent 配置。"
                            : "创建新 Agent。"
                      }
                      isCreate={
                        editorState.mode === "create" &&
                        !editorState.draft.profileId
                      }
                      saveLabel={
                        editorState.draft.profileId ||
                        editorState.mode === "main"
                          ? "保存"
                          : "创建"
                      }
                      draft={editorState.draft}
                      review={review}
                      busy={busySaving}
                      modelAliasOptions={modelAliasOptions}
                      behaviorFiles={editorState.behaviorFiles}
                      approvalOverrides={approvalOverrides}
                      approvalOverridesLoading={approvalOverridesLoading}
                      onChangeDraft={updateDraft}
                      onOpenBehaviorFile={(path, fileId) =>
                        void handleOpenBehaviorFile(path, fileId)
                      }
                      onRevokeOverride={(agentRuntimeId, toolName) =>
                        void handleRevokeOverride(agentRuntimeId, toolName)
                      }
                      onSave={() => void handleSave()}
                      onCancel={closeComposer}
                      formatTokenLabel={formatTokenLabel}
                    />
                  </>
                ) : viewingFilePath ? (
                  <section className="f149-agent-file-editor">
                    <header>
                      <div>
                        <p>{viewingFileId}</p>
                        <h2>{viewingFilePath.split("/").pop()}</h2>
                      </div>
                      <div>
                        {editingFile ? (
                          <>
                            <button
                              type="button"
                              disabled={
                                busyActionId === "behavior.write_file"
                              }
                              onClick={() => void handleSaveBehaviorFile()}
                            >
                              保存
                            </button>
                            <button
                              type="button"
                              onClick={() => setEditingFile(false)}
                            >
                              取消
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            onClick={() => {
                              setEditFileContent(fileContent);
                              setEditingFile(true);
                            }}
                          >
                            编辑
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => setViewingFilePath("")}
                        >
                          关闭
                        </button>
                      </div>
                    </header>
                    {fileContentLoading ? (
                      <div className="f149-agent-file-state">加载中…</div>
                    ) : editingFile ? (
                      <textarea
                        value={editFileContent}
                        onChange={(event) =>
                          setEditFileContent(event.target.value)
                        }
                      />
                    ) : (
                      <pre>{fileContent || "（文件为空或尚未创建）"}</pre>
                    )}
                  </section>
                ) : null}
              </div>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
