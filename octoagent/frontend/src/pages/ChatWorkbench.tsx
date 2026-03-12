import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { fetchTaskDetail } from "../api/client";
import { MessageBubble } from "../components/ChatUI/MessageBubble";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import { useChatStream } from "../hooks/useChatStream";
import type { SessionProjectionDocument, TaskDetailResponse } from "../types";
import { formatDateTime } from "../workbench/utils";

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

export default function ChatWorkbench() {
  const { snapshot, refreshResources } = useWorkbench();
  const sessions = snapshot!.resources.sessions.sessions;
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
  const isRestoringConversation = restoring && messages.length === 0;
  const isEmptyConversation = messages.length === 0 && !isRestoringConversation;
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
    await sendMessage(text);
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
                <p className="wb-card-label">当前工作</p>
                <h3>{activeWork?.title ?? "等待 work 生成"}</h3>
              </div>
            </div>
            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>运行方式</strong>
                <span>{activeSession?.runtime_kind ?? "未决"}</span>
              </div>
              <div className="wb-note">
                <strong>子任务</strong>
                <span>{activeWork?.child_work_count ?? 0}</span>
              </div>
              <div className="wb-note">
                <strong>最新更新时间</strong>
                <span>{formatDateTime(activeWork?.updated_at)}</span>
              </div>
            </div>
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
