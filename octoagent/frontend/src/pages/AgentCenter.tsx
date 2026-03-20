import { startTransition, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useSearchParams } from "react-router-dom";
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
  deriveAgentManagementView,
  formatTokenLabel,
  parseAgentReview,
  type AgentCardViewModel,
  type AgentEditorDraft,
  type AgentEditorReview,
  type ApprovalOverrideDisplay,
  type BehaviorFileInfo,
} from "../domains/agents/agentManagementData";
import type { AgentProfileItem } from "../types";
import { formatDateTime } from "../workbench/utils";

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


function toggleStringValue(values: string[], value: string): string[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}


function renderAgentCard(
  agent: AgentCardViewModel,
  options: {
    onEdit: () => void;
    onDelete?: () => void;
    onOpenBehaviorFile?: (filePath: string, fileId: string) => void;
    primaryActionLabel: string;
    busyActionId: string | null;
    activeFilePath?: string;
  }
) {

  return (
    <article key={agent.profileId || agent.name} className={`wb-agent-card ${agent.isMainAgent ? "is-main" : ""}`}>
      {/* 标题行：名称 + badge + 操作按钮 */}
      <div className="wb-agent-card-topline">
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <strong style={{ fontSize: "1rem", color: "var(--cp-ink)" }}>{agent.name}</strong>
          <span className={`wb-status-pill ${agent.status === "needs_setup" ? "is-warning" : "is-ready"}`}>
            {agent.isMainAgent ? "主 Agent" : agent.profileStatus}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <button type="button" className="wb-button wb-button-secondary" style={{ padding: "4px 12px", fontSize: "0.82rem" }} onClick={options.onEdit}>
            {options.primaryActionLabel}
          </button>
          {typeof options.onDelete === "function" ? (
            <button
              type="button"
              className="wb-button wb-button-tertiary"
              style={{ padding: "4px 12px", fontSize: "0.82rem" }}
              disabled={options.busyActionId === "worker_profile.archive"}
              onClick={options.onDelete}
            >
              删除
            </button>
          ) : null}
        </div>
      </div>

      {/* 元信息：项目 + 模型 + 进行中 */}
      <div className="wb-agent-card-meta">
        {agent.projectName ? <span>{agent.projectName}</span> : null}
        <span>模型 {agent.modelAlias}</span>
        {agent.activeWorkCount > 0 && <span>进行中 {agent.activeWorkCount}</span>}
      </div>

      {/* 行为文件快捷按钮 */}
      {agent.behaviorFiles.length > 0 && options.onOpenBehaviorFile ? (
        <div className="wb-chip-row">
          {agent.behaviorFiles.map((file) => (
            <button
              key={file.file_id}
              type="button"
              className={`wb-chip ${options.activeFilePath === file.path ? "is-active" : ""}`}
              onClick={() => options.onOpenBehaviorFile!(file.path, file.file_id)}
              style={{ cursor: "pointer" }}
            >
              {file.file_id}
            </button>
          ))}
        </div>
      ) : null}

      <small className="wb-inline-note">
        {agent.updatedAt ? `最近更新于 ${formatDateTime(agent.updatedAt)}` : "还没有更新记录"}
      </small>
    </article>
  );
}

export default function AgentCenter() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
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
      snapshot!.resources.mcp_provider_catalog.generated_at,
      snapshot!.resources.skill_governance.generated_at,
    ]
  );
  const modelAliasOptions = useMemo(
    () => buildModelAliasOptions(snapshot!),
    [snapshot!.resources.config.generated_at]
  );
  const projectOptions = useMemo(() => {
    const all = buildProjectOptions(snapshot!.resources.project_selector);
    return all.filter((option) => option.value === agentView.currentProjectId);
  }, [agentView.currentProjectId, snapshot!.resources.project_selector.generated_at]);
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

  // 审批覆盖（全局，不区分 profile）
  const [approvalOverrides, setApprovalOverrides] = useState<ApprovalOverrideDisplay[]>([]);
  const [approvalOverridesLoading, setApprovalOverridesLoading] = useState(false);

  async function fetchApprovalOverrides() {
    setApprovalOverridesLoading(true);
    try {
      const response = await fetch("/api/approval-overrides");
      if (response.ok) {
        const data = await response.json();
        const items: ApprovalOverrideDisplay[] = (data.overrides ?? []).map(
          (o: Record<string, string>) => ({
            agentRuntimeId: o.agent_runtime_id ?? "",
            toolName: o.tool_name ?? "",
            decision: o.decision ?? "always",
            createdAt: o.created_at ?? "",
          })
        );
        setApprovalOverrides(items);
      }
    } catch {
      // 静默失败，列表保持空
    } finally {
      setApprovalOverridesLoading(false);
    }
  }

  async function handleRevokeOverride(agentRuntimeId: string, toolName: string) {
    try {
      const response = await fetch(
        `/api/approval-overrides/${encodeURIComponent(agentRuntimeId)}/${encodeURIComponent(toolName)}`,
        { method: "DELETE" }
      );
      if (response.ok) {
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
        behaviorFiles: agentView.mainAgent.behaviorFiles,
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

  function updateDraftList(
    key: "runtimeKinds",
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

  async function handleSave() {
    if (!editorState) {
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
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <p className="wb-card-label">Agents</p>
              <span className="wb-chip">{1 + agentView.projectAgents.length}</span>
            </div>
            <button type="button" className="wb-button wb-button-primary" onClick={openCreatePicker}>
              新建 Agent
            </button>
          </div>
          {renderAgentCard(agentView.mainAgent, {
            onEdit: openMainEditor,
            onOpenBehaviorFile: (filePath, fileId) => void handleOpenBehaviorFile(filePath, fileId),
            primaryActionLabel: agentView.mainAgent.status === "ready" ? "编辑" : "建立主 Agent",
            busyActionId,
            activeFilePath: viewingFilePath,
          })}
          {agentView.projectAgents.length > 0 ? (
            <div className="wb-section-stack">
              {agentView.projectAgents.map((agent) =>
                renderAgentCard(agent, {
                  onEdit: () => openAgentEditor(agent.profileId),
                  onDelete: () => void handleDeleteAgent(agent),
                  onOpenBehaviorFile: (filePath, fileId) => void handleOpenBehaviorFile(filePath, fileId),
                  primaryActionLabel: "编辑",
                  busyActionId,
                  activeFilePath: viewingFilePath,
                })
              )}
            </div>
          ) : null}
        </section>
      </div>

      {/* ── Modal: 编辑器 / 模板选择 / 行为文件查看 ── */}
      {(showTemplatePicker || editorState || viewingFilePath) && document.body ? createPortal(
        <div className="wb-modal-overlay" onClick={(e) => { if (e.target === e.currentTarget && e.detail > 0) closeComposer(); }}>
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
                behaviorFiles={editorState.behaviorFiles}
                approvalOverrides={approvalOverrides}
                approvalOverridesLoading={approvalOverridesLoading}
                onChangeDraft={updateDraft}
                onToggleRuntimeKind={(value) => updateDraftList("runtimeKinds", value)}
                onOpenBehaviorFile={(path, fileId) => void handleOpenBehaviorFile(path, fileId)}
                onRevokeOverride={(agentRuntimeId, toolName) => void handleRevokeOverride(agentRuntimeId, toolName)}
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
