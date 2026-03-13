import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { fetchTaskDetail } from "../api/client";
import { MessageBubble } from "../components/ChatUI/MessageBubble";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import {
  formatAgentRoleLabel,
  formatCollaborationDirectionLabel,
  formatDiscoveryEntrypointLabel,
  formatTaskStatusLabel,
  formatTaskStatusTone,
  formatToolBoundaryLabel,
} from "../domains/chat/presentation";
import { useChatStream } from "../hooks/useChatStream";
import {
  ActionBar,
  HoverReveal,
  InlineCallout,
  PageIntro,
  StatusBadge,
} from "../ui/primitives";
import type {
  SessionProjectionDocument,
  TaskDetailResponse,
} from "../types";
import { formatWorkerTemplateName } from "../workbench/utils";

function pushRestoreTaskId(taskIds: string[], taskId: string | undefined): void {
  if (!taskId || taskIds.includes(taskId)) {
    return;
  }
  taskIds.push(taskId);
}

function resolveRestorableTaskIds(sessions: SessionProjectionDocument): string[] {
  const webSessions = sessions.sessions.filter((item) => item.channel === "web");
  const candidates = webSessions.length > 0 ? webSessions : sessions.sessions;
  const taskIds: string[] = [];
  if (sessions.focused_session_id) {
    const focused = candidates.find((item) => item.session_id === sessions.focused_session_id);
    if (focused) {
      pushRestoreTaskId(taskIds, focused.task_id);
    }
  }
  if (sessions.focused_thread_id) {
    const focused = candidates.find((item) => item.thread_id === sessions.focused_thread_id);
    if (focused) {
      pushRestoreTaskId(taskIds, focused.task_id);
    }
  }

  for (const item of candidates) {
    pushRestoreTaskId(taskIds, item.task_id);
  }
  for (const item of sessions.sessions) {
    pushRestoreTaskId(taskIds, item.task_id);
  }

  return taskIds;
}

function readSummaryString(summary: Record<string, unknown>, key: string): string {
  const value = summary[key];
  return typeof value === "string" ? value : "";
}

function readSummaryNumber(summary: Record<string, unknown>, key: string): number {
  const value = summary[key];
  return typeof value === "number" ? value : 0;
}

function formatA2AMessageType(type: string): string {
  switch (type.trim().toUpperCase()) {
    case "TASK":
      return "任务下发";
    case "UPDATE":
      return "进度更新";
    case "RESULT":
      return "结果回传";
    case "ERROR":
      return "错误回传";
    case "HEARTBEAT":
      return "心跳";
    case "CANCEL":
      return "取消";
    default:
      return type || "未记录";
  }
}

function formatAgentUri(agentId: string, fallback: string): string {
  const normalized = agentId.trim();
  if (!normalized) {
    return fallback;
  }
  if (normalized.startsWith("agent://")) {
    return normalized;
  }
  return `agent://${normalized}`;
}

export default function ChatWorkbench() {
  const { snapshot, refreshResources } = useWorkbench();
  const sessions = snapshot!.resources.sessions.sessions;
  const workerProfilesDocument = snapshot!.resources.worker_profiles;
  const workerProfiles = workerProfilesDocument?.profiles ?? [];
  const restoreTaskIds = resolveRestorableTaskIds(snapshot!.resources.sessions);
  const { messages, sendMessage, streaming, restoring, error, taskId } = useChatStream(
    restoreTaskIds.length > 0 ? { taskIds: restoreTaskIds } : null
  );
  const [input, setInput] = useState("");
  const [showSessionInternalRefs, setShowSessionInternalRefs] = useState(false);
  const [showCollaborationTechRefs, setShowCollaborationTechRefs] = useState(false);
  const [taskDetail, setTaskDetail] = useState<TaskDetailResponse | null>(null);
  const context = snapshot!.resources.context_continuity;
  const memory = snapshot!.resources.memory;
  const activeSession = sessions.find((item) => item.task_id === taskId) ?? null;
  const activeWorkId =
    typeof activeSession?.execution_summary["work_id"] === "string"
      ? (activeSession.execution_summary["work_id"] as string)
      : "";
  const activeWork =
    snapshot!.resources.delegation.works.find((item) => item.work_id === activeWorkId) ?? null;
  const activeContextFrame =
    context.frames.find((item) => item.task_id === taskId) ??
    (activeSession
      ? context.frames.find((item) => item.session_id === activeSession.session_id) ?? null
      : null);
  const a2aConversations = context.a2a_conversations ?? [];
  const a2aMessages = context.a2a_messages ?? [];
  const recallFrames = context.recall_frames ?? [];
  const activeConversationId =
    readSummaryString(activeWork?.runtime_summary ?? {}, "research_a2a_conversation_id") ||
    activeWork?.a2a_conversation_id ||
    "";
  const activeA2AConversationRecord =
    (activeConversationId
      ? a2aConversations.find((item) => item.a2a_conversation_id === activeConversationId) ?? null
      : null) ??
    (activeWork?.work_id
      ? a2aConversations.find((item) => item.work_id === activeWork.work_id) ?? null
      : null) ??
    (taskId ? a2aConversations.find((item) => item.task_id === taskId) ?? null : null);
  const workDerivedButlerSessionId =
    readSummaryString(activeWork?.runtime_summary ?? {}, "research_butler_agent_session_id") ||
    activeWork?.butler_agent_session_id ||
    "";
  const workDerivedWorkerSessionId =
    readSummaryString(activeWork?.runtime_summary ?? {}, "research_worker_agent_session_id") ||
    activeWork?.worker_agent_session_id ||
    "";
  const workDerivedMessageCount =
    readSummaryNumber(activeWork?.runtime_summary ?? {}, "research_a2a_message_count") ||
    activeWork?.a2a_message_count ||
    0;
  const workDerivedLatestMessageType =
    activeWork != null && workDerivedMessageCount > 0 ? "RESULT" : "";
  const workDerivedWorkerAgent = formatAgentUri(
    readSummaryString(activeWork?.runtime_summary ?? {}, "research_worker_id") ||
      activeWork?.runtime_id ||
      activeWork?.selected_worker_type ||
      "",
    "Worker"
  );
  const hasInternalCollaboration =
    activeA2AConversationRecord != null || Boolean(activeConversationId || workDerivedWorkerSessionId);
  const activeConversationSourceAgent =
    activeA2AConversationRecord?.source_agent ||
    formatAgentUri(workDerivedButlerSessionId ? "butler.main" : "", "Butler");
  const activeConversationTargetAgent =
    activeA2AConversationRecord?.target_agent || workDerivedWorkerAgent;
  const activeConversationMessageCount =
    activeA2AConversationRecord?.message_count ?? workDerivedMessageCount;
  const activeConversationLatestType =
    activeA2AConversationRecord?.latest_message_type || workDerivedLatestMessageType;
  const activeConversationWorkerSessionId =
    activeA2AConversationRecord?.target_agent_session_id || workDerivedWorkerSessionId;
  const activeA2AMessages =
    activeA2AConversationRecord == null
      ? []
      : [...a2aMessages]
          .filter(
            (item) => item.a2a_conversation_id === activeA2AConversationRecord.a2a_conversation_id
          )
          .sort((left, right) => right.message_seq - left.message_seq)
          .slice(0, 3);
  const activeWorkerSessionId = activeConversationWorkerSessionId;
  const activeWorkerRecall =
    activeWorkerSessionId.length > 0
      ? [...recallFrames]
          .filter((item) => item.agent_session_id === activeWorkerSessionId)
          .sort((left, right) =>
            String(right.created_at ?? "").localeCompare(String(left.created_at ?? ""))
          )[0] ?? null
      : null;
  const isRestoringConversation = restoring && messages.length === 0;
  const isEmptyConversation = messages.length === 0 && !isRestoringConversation;
  const defaultRootAgentId = readSummaryString(
    workerProfilesDocument?.summary ?? {},
    "default_profile_id"
  );
  const defaultRootAgent = workerProfiles.find(
    (profile) => profile.profile_id === defaultRootAgentId
  );
  const activeAgentProfileId =
    activeWork?.requested_worker_profile_id ||
    activeWork?.agent_profile_id ||
    activeContextFrame?.agent_profile_id ||
    defaultRootAgent?.profile_id ||
    "";
  const activeRootAgent =
    workerProfiles.find((profile) => profile.profile_id === activeAgentProfileId) ??
    defaultRootAgent ??
    null;
  const activeWorkerTemplateName = activeRootAgent
    ? formatWorkerTemplateName(
        activeRootAgent.name,
        activeRootAgent.static_config?.base_archetype ?? null
      )
    : "";
  const defaultWorkerTemplateName = defaultRootAgent
    ? formatWorkerTemplateName(
        defaultRootAgent.name,
        defaultRootAgent.static_config?.base_archetype ?? null
      )
    : "";
  const activeToolResolutionMode =
    activeWork?.tool_resolution_mode ||
    activeRootAgent?.dynamic_context.current_tool_resolution_mode ||
    "";
  const activeMountedTools = activeWork?.mounted_tools ?? activeRootAgent?.dynamic_context.current_mounted_tools ?? [];
  const activeBlockedTools = activeWork?.blocked_tools ?? activeRootAgent?.dynamic_context.current_blocked_tools ?? [];
  const activeDiscoveryEntrypoints =
    activeRootAgent?.dynamic_context.current_discovery_entrypoints ?? [];
  const activeRootAgentBindingSource = activeWork?.requested_worker_profile_id
    ? "当前 Work"
    : activeContextFrame?.agent_profile_id
      ? "上下文帧"
      : defaultRootAgent?.profile_id
        ? "Project 默认"
        : "未绑定";
  const conversationRolePath = hasInternalCollaboration
    ? `${formatAgentRoleLabel(activeConversationSourceAgent)} -> ${formatAgentRoleLabel(activeConversationTargetAgent)}`
    : "";
  const collaborationTechRefs = [
    activeConversationId
      ? { label: "协作链路 ID", value: activeConversationId }
      : null,
    activeConversationWorkerSessionId
      ? { label: "专门角色会话", value: activeConversationWorkerSessionId }
      : null,
  ].filter((item): item is { label: string; value: string } => Boolean(item));
  const taskStatusLabel = formatTaskStatusLabel(taskDetail?.task.status ?? "");
  const taskStatusTone = formatTaskStatusTone(taskDetail?.task.status ?? "");
  const collaborationStatusTone = hasInternalCollaboration ? "success" : "draft";
  const internalRefs = [
    taskId ? { label: "任务 ID", value: taskId } : null,
    activeSession?.session_id ? { label: "会话 ID", value: activeSession.session_id } : null,
    activeWork?.work_id ? { label: "Work ID", value: activeWork.work_id } : null,
    activeContextFrame?.context_frame_id
      ? { label: "上下文帧 ID", value: activeContextFrame.context_frame_id }
      : null,
  ].filter((item): item is { label: string; value: string } => Boolean(item));

  useEffect(() => {
    let cancelled = false;
    async function loadDetail() {
      if (!taskId) {
        setTaskDetail(null);
        return;
      }
      try {
        const detail = await fetchTaskDetail(taskId);
        if (!cancelled) {
          setTaskDetail(detail);
        }
      } catch {
        if (!cancelled) {
          setTaskDetail(null);
        }
      }
    }
    void loadDetail();
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  useEffect(() => {
    if (!taskId) {
      return;
    }
    void refreshResources([
      {
        resource_type: snapshot!.resources.sessions.resource_type,
        resource_id: snapshot!.resources.sessions.resource_id,
        schema_version: snapshot!.resources.sessions.schema_version,
      },
      {
        resource_type: snapshot!.resources.delegation.resource_type,
        resource_id: snapshot!.resources.delegation.resource_id,
        schema_version: snapshot!.resources.delegation.schema_version,
      },
      {
        resource_type: snapshot!.resources.context_continuity.resource_type,
        resource_id: snapshot!.resources.context_continuity.resource_id,
        schema_version: snapshot!.resources.context_continuity.schema_version,
      },
    ]);
  }, [taskId, streaming]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!input.trim() || streaming) {
      return;
    }
    const text = input;
    setInput("");
    await sendMessage(text, {
      agentProfileId: activeRootAgent?.profile_id ?? defaultRootAgent?.profile_id ?? null,
    });
  }

  return (
    <div className="wb-page">
      <PageIntro
        kicker="Chat"
        title="在这里直接和 OctoAgent 对话"
        summary="发送消息后，你可以同时看到回复、任务状态和相关工作进度。"
        compact
        actions={
          <div className="wb-chip-row">
            <StatusBadge tone={taskStatusTone}>{taskStatusLabel}</StatusBadge>
            <StatusBadge tone={collaborationStatusTone}>
              {hasInternalCollaboration ? "已转交专门角色" : "主助手直接处理"}
            </StatusBadge>
          </div>
        }
      />

      <div className="wb-chat-layout">
        <section className={`wb-panel wb-chat-panel ${isEmptyConversation ? "is-empty" : ""}`}>
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">对话</p>
              <h3>{activeSession?.title ?? (taskId ? "对话进行中" : "还没有开始对话")}</h3>
            </div>
            {internalRefs.length > 0 ? (
              <HoverReveal
                label="技术详情"
                expanded={showSessionInternalRefs}
                onToggle={setShowSessionInternalRefs}
                ariaLabel="当前会话技术详情"
              >
                {internalRefs.map((item) => (
                  <div key={item.label} className="wb-hover-reveal-row">
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                  </div>
                ))}
              </HoverReveal>
            ) : null}
          </div>

          <InlineCallout
            title={`当前分工模板${activeRootAgent ? ` · ${activeWorkerTemplateName}` : " · 未显式绑定"}`}
          >
            来源 {activeRootAgentBindingSource}
            {activeToolResolutionMode
              ? ` / ${formatToolBoundaryLabel(activeToolResolutionMode)}`
              : ""}
          </InlineCallout>

          {isRestoringConversation ? (
            <div className="wb-chat-empty-stage is-restoring">
              <div className="wb-empty-state wb-chat-empty-card wb-chat-restore-card">
                <strong>正在恢复最近对话</strong>
                <span>稍等，我们正在读取历史消息、任务状态和相关上下文。</span>
              </div>
            </div>
          ) : isEmptyConversation ? (
            <div className="wb-chat-empty-stage">
              <div className="wb-empty-state wb-chat-empty-card">
                <strong>从这里发出第一条消息</strong>
                <span>比如告诉 OctoAgent 你要完成什么，它会开始创建任务并返回结果。</span>
              </div>
              {error ? (
                <InlineCallout title="刚才没有发送成功" tone="error">
                  {error}
                </InlineCallout>
              ) : null}
              <form className="wb-chat-form is-empty" onSubmit={handleSubmit}>
                <input
                  type="text"
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  placeholder="告诉 OctoAgent 你现在要做什么"
                  disabled={streaming}
                />
                <button
                  type="submit"
                  className="wb-button wb-button-primary"
                  disabled={streaming || !input.trim()}
                >
                  {streaming ? "发送中" : "发送"}
                </button>
              </form>
            </div>
          ) : (
            <>
              <div className="wb-chat-messages">
                {messages.map((message) => (
                  <MessageBubble key={message.id} message={message} />
                ))}
              </div>

              {error ? (
                <InlineCallout title="刚才没有发送成功" tone="error">
                  {error}
                </InlineCallout>
              ) : null}

              <form className="wb-chat-form" onSubmit={handleSubmit}>
                <input
                  type="text"
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  placeholder="告诉 OctoAgent 你现在要做什么"
                  disabled={streaming}
                />
                <button
                  type="submit"
                  className="wb-button wb-button-primary"
                  disabled={streaming || !input.trim()}
                >
                  {streaming ? "发送中" : "发送"}
                </button>
              </form>
            </>
          )}
        </section>

        <aside className="wb-chat-sidebar">
          <section className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">当前 Worker 模板</p>
                <h3>{activeWorkerTemplateName || "还没有绑定 Worker 模板"}</h3>
              </div>
            </div>
            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>绑定来源</strong>
                <span>{activeRootAgentBindingSource}</span>
              </div>
              <div className="wb-note">
                <strong>默认工具范围</strong>
                <span>{activeRootAgent?.static_config.tool_profile ?? "未记录"}</span>
              </div>
              <div className="wb-note">
                <strong>默认 Worker 模板</strong>
                <span>{defaultWorkerTemplateName || "还没有默认值"}</span>
              </div>
            </div>
            <ActionBar>
              <Link className="wb-button wb-button-tertiary" to="/agents">
                去 Agents 调整默认模板
              </Link>
            </ActionBar>
          </section>

          <section className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">当前任务</p>
                <h3>{taskDetail?.task.title ?? "等待开始"}</h3>
              </div>
              <StatusBadge tone={taskStatusTone}>{taskStatusLabel}</StatusBadge>
            </div>
            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>事件数</strong>
                <span>{taskDetail?.events.length ?? 0}</span>
              </div>
              <div className="wb-note">
                <strong>结果附件</strong>
                <span>{taskDetail?.artifacts.length ?? 0}</span>
              </div>
            </div>
            {taskId ? (
              <ActionBar>
                <Link className="wb-button wb-button-tertiary" to={`/tasks/${taskId}`}>
                  打开任务详情
                </Link>
              </ActionBar>
            ) : null}
          </section>

          <section className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">协作分工</p>
                <h3>
                  {hasInternalCollaboration
                    ? "OctoAgent 已拆给专门角色继续处理"
                    : "当前这轮先由主 Agent 直接处理"}
                </h3>
              </div>
              <StatusBadge tone={collaborationStatusTone}>
                {hasInternalCollaboration ? "内部协作中" : "直连处理"}
              </StatusBadge>
            </div>
            {hasInternalCollaboration ? (
              <>
                <div className="wb-note-stack">
                  <div className="wb-note">
                    <strong>当前负责角色</strong>
                    <span>{conversationRolePath}</span>
                  </div>
                  <div className="wb-note">
                    <strong>最近协作进展</strong>
                    <span>
                      {activeConversationMessageCount} 条 / 最新{" "}
                      {formatA2AMessageType(activeConversationLatestType)}
                    </span>
                  </div>
                  <div className="wb-note">
                    <strong>已参考的背景片段</strong>
                    <span>{activeWorkerRecall?.memory_hit_count ?? 0}</span>
                  </div>
                </div>
                {collaborationTechRefs.length > 0 ? (
                  <HoverReveal
                    label="协作技术详情"
                    expanded={showCollaborationTechRefs}
                    onToggle={setShowCollaborationTechRefs}
                    ariaLabel="当前协作技术详情"
                  >
                    {collaborationTechRefs.map((item) => (
                      <div key={item.label} className="wb-hover-reveal-row">
                        <span>{item.label}</span>
                        <strong>{item.value}</strong>
                      </div>
                    ))}
                  </HoverReveal>
                ) : null}
                {activeA2AMessages.length > 0 ? (
                  <div className="wb-note-stack">
                    {activeA2AMessages.map((message) => (
                      <div key={message.a2a_message_id} className="wb-note">
                        <strong>
                          {formatA2AMessageType(message.message_type)} · #{message.message_seq}
                        </strong>
                        <span>{formatCollaborationDirectionLabel(message.direction)}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
                {activeA2AMessages.length === 0 && activeA2AConversationRecord == null ? (
                  <InlineCallout title="当前只显示协作摘要">
                    当前快照还没有带回完整协作明细，需要时可去 Advanced 查看详细诊断。
                  </InlineCallout>
                ) : null}
                <ActionBar>
                  <Link className="wb-button wb-button-tertiary" to="/advanced">
                    打开 Advanced 诊断
                  </Link>
                </ActionBar>
              </>
            ) : (
              <InlineCallout title="当前先由主助手直接处理">
                如果这轮问题需要实时检索、运维或开发，系统会自动转给更适合的角色继续处理。
              </InlineCallout>
            )}
          </section>

          <section className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">当前可用工具</p>
                <h3>{activeWork?.title ?? "等待 work 生成"}</h3>
              </div>
            </div>
            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>当前可用</strong>
                <span>{activeMountedTools.length > 0 ? activeMountedTools.length : 0}</span>
              </div>
              <div className="wb-note">
                <strong>暂时不可用</strong>
                <span>{activeBlockedTools.length > 0 ? activeBlockedTools.length : 0}</span>
              </div>
              <div className="wb-note">
                <strong>补充资料入口</strong>
                <span>
                  {activeDiscoveryEntrypoints.length > 0
                    ? activeDiscoveryEntrypoints
                        .map((item) => formatDiscoveryEntrypointLabel(item))
                        .join(" / ")
                    : "当前没有额外资料入口"}
                </span>
              </div>
            </div>
            <div className="wb-chip-row">
              {activeMountedTools.slice(0, 6).map((tool) => (
                <span key={`mounted-${tool.tool_name}`} className="wb-chip">
                  {tool.tool_name}
                </span>
              ))}
              {activeMountedTools.length === 0 ? (
                <span className="wb-inline-note">当前还没有可用工具记录。</span>
              ) : null}
            </div>
            {activeBlockedTools.length > 0 ? (
              <div className="wb-note-stack">
                {activeBlockedTools.slice(0, 3).map((tool) => (
                  <div key={`blocked-${tool.tool_name}`} className="wb-note">
                    <strong>{tool.tool_name}</strong>
                    <span>{tool.summary || tool.reason_code || tool.status}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </section>

          <section className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">记忆与上下文</p>
                <h3>当前对话的相关背景</h3>
              </div>
            </div>
            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>当前记录</strong>
                <span>{memory.summary.sor_current_count}</span>
              </div>
              <div className="wb-note">
                <strong>上下文片段</strong>
                <span>{context.frames.length}</span>
              </div>
              <div className="wb-note">
                <strong>上下文状态</strong>
                <span>
                  {context.degraded.is_degraded
                    ? "当前只显示基础背景信息，但不影响继续对话。"
                    : "当前对话的背景摘要可以正常查看。"}
                </span>
              </div>
              <div className="wb-note">
                <strong>最近摘要</strong>
                <span>{activeContextFrame?.recent_summary ?? "当前还没有生成摘要。"}</span>
              </div>
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
