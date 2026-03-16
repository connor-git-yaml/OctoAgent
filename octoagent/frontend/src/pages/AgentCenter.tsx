import { startTransition, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate, useSearchParams } from "react-router-dom";
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
  const [flashMessage, setFlashMessage] = useState("");
  const [review, setReview] = useState<AgentEditorReview | null>(null);

  // 行为文件查看/编辑状态
  const [viewingFilePath, setViewingFilePath] = useState("");
  const [viewingFileId, setViewingFileId] = useState("");
  const [fileContent, setFileContent] = useState("");
  const [fileContentLoading, setFileContentLoading] = useState(false);
  const [editingFile, setEditingFile] = useState(false);
  const [editFileContent, setEditFileContent] = useState("");

  const metadataError = editorState ? validateMetadataText(editorState.draft.metadataText) : "";

  useEffect(() => {
    setEditorState(null);
    setShowTemplatePicker(false);
    setReview(null);
    setFlashMessage("");
    setViewingFilePath("");
  }, [agentView.currentProjectId]);

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
      const result = await submitAction("behavior.read_file", { file_path: filePath });
      if (result?.data?.exists) {
        setFileContent(String(result.data.content ?? ""));
      } else {
        setFileContent("");
        setFlashMessage("文件尚未创建，保存后将自动 materialize。");
      }
    } catch {
      setFileContent("");
      setFlashMessage("读取文件失败。");
    } finally {
      setFileContentLoading(false);
    }
  }

  async function handleSaveBehaviorFile() {
    if (!viewingFilePath) {
      return;
    }
    try {
      await submitAction("behavior.write_file", {
        file_path: viewingFilePath,
        content: editFileContent,
      });
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
      });
      setFlashMessage("");
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
      setViewingFilePath("");
      setEditorState({
        mode: "agent",
        draft: buildAgentEditorDraftFromProfile(
          profile,
          agentView.currentProjectId,
          agentView.currentProjectName,
          capabilityProviderEntries
        ),
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
      });
      setFlashMessage("");
    });
  }

  function openTemplateCreate(templateId: string) {
    const template = snapshot!.resources.worker_profiles?.profiles.find(
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
    navigate("/");
  }

  return (
    <div className="wb-page wb-agent-management-page">
      {flashMessage ? (
        <div className="wb-inline-banner is-muted">
          <strong>{flashMessage}</strong>
        </div>
      ) : null}

      <section id="agents-behavior-center" ref={behaviorCenterRef} className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <h3>行为文件</h3>
          </div>
          <div className="wb-inline-actions">
            <button type="button" className="wb-button wb-button-primary" onClick={openCreatePicker}>
              新建 Agent
            </button>
            <button
              type="button"
              className="wb-button wb-button-tertiary"
              onClick={() => void refreshSnapshot()}
            >
              刷新
            </button>
          </div>
        </div>

        {behaviorProfiles.length === 0 || selectedBehaviorProfile === null ? (
          <div className="wb-empty-state">
            <strong>暂无行为文件</strong>
            <div className="wb-inline-actions">
              <button type="button" className="wb-button wb-button-primary" onClick={openMainEditor}>
                建立主 Agent
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="wb-behavior-scope-grid">
              {behaviorScopeGroups.filter((group) => group.scope !== "agent_private").map((group) => (
                <article key={group.scope} className="wb-note wb-behavior-scope-card">
                  <strong>{group.title}</strong>
                  <div className="wb-note-stack">
                    {group.files.map((file) => (
                      <button
                        key={`${group.scope}:${file.file_id}`}
                        type="button"
                        className={`wb-note wb-behavior-file-row ${
                          viewingFilePath === file.path ? "is-active" : ""
                        }`}
                        onClick={() => void handleOpenBehaviorFile(file.path, file.file_id)}
                      >
                        <strong>{file.file_id}</strong>
                        <span>{file.title}</span>
                        <small className="wb-inline-note">
                          {file.exists_on_disk ? "已创建" : "待创建"}
                        </small>
                      </button>
                    ))}
                  </div>
                </article>
              ))}
            </div>
          </>
        )}
      </section>

      <div className="wb-agent-management-layout">
        <section id="agents-main-agent" ref={mainAgentRef} className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">主 Agent</p>
            </div>
          </div>
          {renderAgentCard(agentView.mainAgent, {
            onEdit: openMainEditor,
            onStartSession: () =>
              void handleStartAgentSession(agentView.mainAgent.profileId, agentView.mainAgent.name),
            primaryActionLabel: agentView.mainAgent.status === "ready" ? "编辑" : "建立主 Agent",
            busyActionId,
          })}
          {/* 主 Agent 的行为文件 */}
          {behaviorScopeGroups
            .filter((g) => g.scope === "agent_private")
            .flatMap((g) => g.files)
            .length > 0 ? (
            <div className="wb-chip-row" style={{ marginTop: "0.5rem" }}>
              {behaviorScopeGroups
                .filter((g) => g.scope === "agent_private")
                .flatMap((g) => g.files)
                .map((file) => (
                  <button
                    key={file.file_id}
                    type="button"
                    className={`wb-chip ${viewingFilePath === file.path ? "is-active" : ""}`}
                    onClick={() => void handleOpenBehaviorFile(file.path, file.file_id)}
                    style={{ cursor: "pointer" }}
                  >
                    {file.file_id}
                  </button>
                ))}
            </div>
          ) : null}
        </section>

        {agentView.projectAgents.length > 0 ? (
          <section className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">其他 Agent</p>
              </div>
              <span className="wb-chip">{agentView.projectAgents.length}</span>
            </div>
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
          </section>
        ) : null}
      </div>

      {/* ── Modal: 编辑器 / 模板选择 / 行为文件查看 ── */}
      {(showTemplatePicker || editorState || viewingFilePath) && document.body ? createPortal(
        <div className="wb-modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) closeComposer(); }}>
          <div className="wb-modal-body">
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
                    ? "当前项目默认 Agent。"
                    : editorState.draft.profileId
                      ? "编辑 Agent 配置。"
                      : "创建新 Agent。"
                }
                saveLabel={
                  editorState.mode === "main"
                    ? "保存"
                    : editorState.draft.profileId
                      ? "保存"
                      : "创建"
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
            ) : viewingFilePath ? (
              <section className="wb-panel wb-agent-editor-shell">
                <div className="wb-panel-head">
                  <div>
                    <p className="wb-card-label">{viewingFileId}</p>
                    <h3>{viewingFilePath.split("/").pop()}</h3>
                  </div>
                  <div className="wb-inline-actions">
                    {editingFile ? (
                      <>
                        <button
                          type="button"
                          className="wb-button wb-button-primary"
                          disabled={busyActionId === "behavior.write_file"}
                          onClick={() => void handleSaveBehaviorFile()}
                        >
                          保存
                        </button>
                        <button
                          type="button"
                          className="wb-button wb-button-tertiary"
                          onClick={() => setEditingFile(false)}
                        >
                          取消
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        className="wb-button wb-button-secondary"
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
                      className="wb-button wb-button-tertiary"
                      onClick={() => setViewingFilePath("")}
                    >
                      关闭
                    </button>
                  </div>
                </div>
                {fileContentLoading ? (
                  <div className="wb-empty-state">
                    <span>加载中…</span>
                  </div>
                ) : editingFile ? (
                  <textarea
                    className="wb-textarea-prose wb-behavior-file-editor"
                    value={editFileContent}
                    onChange={(e) => setEditFileContent(e.target.value)}
                    style={{ minHeight: "400px", fontFamily: "monospace", fontSize: "0.85rem" }}
                  />
                ) : (
                  <pre className="wb-behavior-file-content" style={{ whiteSpace: "pre-wrap", padding: "1rem", fontSize: "0.85rem", lineHeight: "1.6", maxHeight: "600px", overflow: "auto" }}>
                    {fileContent || "（文件为空或尚未创建）"}
                  </pre>
                )}
              </section>
            ) : null}
          </div>
        </div>,
        document.body
      ) : null}
    </div>
  );
}
