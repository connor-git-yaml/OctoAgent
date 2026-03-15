import { startTransition, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
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
import { formatDateTime } from "../workbench/utils";

type EditorMode = "main" | "agent" | "create";

interface EditorState {
  mode: EditorMode;
  draft: AgentEditorDraft;
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
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const navigate = useNavigate();
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

  const [editorState, setEditorState] = useState<EditorState | null>(null);
  const [showTemplatePicker, setShowTemplatePicker] = useState(false);
  const [flashMessage, setFlashMessage] = useState(
    "先确定当前项目的主 Agent，再把其他 Agent 按职责分开。"
  );
  const [review, setReview] = useState<AgentEditorReview | null>(null);

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
    navigate("/");
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

      <div className="wb-agent-management-layout">
        <div className="wb-section-stack">
          <section className="wb-panel">
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
