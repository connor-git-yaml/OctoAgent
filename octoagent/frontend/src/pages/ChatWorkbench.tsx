import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { fetchTaskDetail } from "../api/client";
import { MessageBubble } from "../components/ChatUI/MessageBubble";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import { useChatStream } from "../hooks/useChatStream";
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
  const [showInternalRefs, setShowInternalRefs] = useState(false);
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
      <section className="wb-hero wb-hero-compact">
        <div>
          <p className="wb-kicker">Chat</p>
          <h1>在这里直接和 OctoAgent 对话</h1>
          <p>发送消息后，你可以同时看到回复、任务状态和相关工作进度。</p>
        </div>
      </section>

      <div className="wb-chat-layout">
        <section className={`wb-panel wb-chat-panel ${isEmptyConversation ? "is-empty" : ""}`}>
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">对话</p>
              <h3>{activeSession?.title ?? (taskId ? "对话进行中" : "还没有开始对话")}</h3>
            </div>
            {internalRefs.length > 0 ? (
              <div
                className="wb-hover-reveal"
                onMouseEnter={() => setShowInternalRefs(true)}
                onMouseLeave={() => setShowInternalRefs(false)}
              >
                <button
                  type="button"
                  className="wb-hover-reveal-trigger"
                  aria-expanded={showInternalRefs}
                  onClick={() => setShowInternalRefs((current) => !current)}
                  onFocus={() => setShowInternalRefs(true)}
                  onBlur={() => setShowInternalRefs(false)}
                >
                  内部标识
                </button>
                {showInternalRefs ? (
                  <div className="wb-hover-reveal-card" role="note" aria-label="当前会话内部标识">
                    {internalRefs.map((item) => (
                      <div key={item.label} className="wb-hover-reveal-row">
                        <span>{item.label}</span>
                        <strong>{item.value}</strong>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>

          <div className="wb-inline-banner is-muted">
            <strong>
              当前 Worker 模板 {activeRootAgent ? `· ${activeWorkerTemplateName}` : "· 未显式绑定"}
            </strong>
            <span>
              绑定来源 {activeRootAgentBindingSource}
              {activeToolResolutionMode ? ` / 工具分配 ${activeToolResolutionMode}` : ""}
            </span>
          </div>

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
              {error ? <div className="wb-inline-banner is-error">{error}</div> : null}
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

              {error ? <div className="wb-inline-banner is-error">{error}</div> : null}

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
                <strong>静态工具边界</strong>
                <span>{activeRootAgent?.static_config.tool_profile ?? "未记录"}</span>
              </div>
              <div className="wb-note">
                <strong>默认 Worker 模板</strong>
                <span>{defaultWorkerTemplateName || "还没有默认值"}</span>
              </div>
              <Link className="wb-button wb-button-tertiary" to="/agents">
                去 Agents 调整默认模板
              </Link>
            </div>
          </section>

          <section className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">当前任务</p>
                <h3>{taskDetail?.task.title ?? "等待开始"}</h3>
              </div>
            </div>
            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>状态</strong>
                <span>{taskDetail?.task.status ?? "尚未创建"}</span>
              </div>
              <div className="wb-note">
                <strong>事件数</strong>
                <span>{taskDetail?.events.length ?? 0}</span>
              </div>
              <div className="wb-note">
                <strong>结果附件</strong>
                <span>{taskDetail?.artifacts.length ?? 0}</span>
              </div>
              {taskId ? (
                <Link className="wb-button wb-button-tertiary" to={`/tasks/${taskId}`}>
                  打开任务详情
                </Link>
              ) : null}
            </div>
          </section>

          <section className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">内部协作</p>
                <h3>
                  {hasInternalCollaboration
                    ? "Butler 正在和 Worker 协作"
                    : "当前这轮还没有内部委派"}
                </h3>
              </div>
            </div>
            {hasInternalCollaboration ? (
              <>
                <div className="wb-note-stack">
                  <div className="wb-note">
                    <strong>当前链路</strong>
                    <span>
                      {activeConversationSourceAgent}{" -> "}
                      {activeConversationTargetAgent}
                    </span>
                  </div>
                  <div className="wb-note">
                    <strong>内部消息</strong>
                    <span>
                      {activeConversationMessageCount} 条 / 最新{" "}
                      {formatA2AMessageType(activeConversationLatestType)}
                    </span>
                  </div>
                  <div className="wb-note">
                    <strong>Worker Session</strong>
                    <span>{activeConversationWorkerSessionId || "未记录"}</span>
                  </div>
                  <div className="wb-note">
                    <strong>Recall 命中</strong>
                    <span>{activeWorkerRecall?.memory_hit_count ?? 0}</span>
                  </div>
                </div>
                {activeA2AMessages.length > 0 ? (
                  <div className="wb-note-stack">
                    {activeA2AMessages.map((message) => (
                      <div key={message.a2a_message_id} className="wb-note">
                        <strong>
                          {formatA2AMessageType(message.message_type)} · #{message.message_seq}
                        </strong>
                        <span>
                          {message.direction === "inbound"
                            ? "Worker -> Butler"
                            : "Butler -> Worker"}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : null}
                {activeA2AMessages.length === 0 && activeA2AConversationRecord == null ? (
                  <div className="wb-note-stack">
                    <div className="wb-note">
                      <strong>消息明细</strong>
                      <span>当前快照未带回完整 A2A 明细，可去 Advanced 查看完整 runtime truth。</span>
                    </div>
                  </div>
                ) : null}
                <Link className="wb-button wb-button-tertiary" to="/advanced">
                  去 Advanced 看完整 runtime truth
                </Link>
              </>
            ) : (
              <div className="wb-note-stack">
                <div className="wb-note">
                  <strong>当前状态</strong>
                  <span>如果这轮问题需要 Research / Ops / Dev，Butler 会在内部建立独立 A2A 会话。</span>
                </div>
              </div>
            )}
          </section>

          <section className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">工具解释</p>
                <h3>{activeWork?.title ?? "等待 work 生成"}</h3>
              </div>
            </div>
            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>挂载中的工具</strong>
                <span>{activeMountedTools.length > 0 ? activeMountedTools.length : 0}</span>
              </div>
              <div className="wb-note">
                <strong>被阻塞的工具</strong>
                <span>{activeBlockedTools.length > 0 ? activeBlockedTools.length : 0}</span>
              </div>
              <div className="wb-note">
                <strong>发现入口</strong>
                <span>
                  {activeDiscoveryEntrypoints.length > 0
                    ? activeDiscoveryEntrypoints.join(" / ")
                    : "当前没有额外 discovery 入口"}
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
                <span className="wb-inline-note">当前还没有 mounted tools 记录。</span>
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
