import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { fetchTaskDetail } from "../api/client";
import { MessageBubble } from "../components/ChatUI/MessageBubble";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import { useChatStream } from "../hooks/useChatStream";
import type { TaskDetailResponse } from "../types";
import { formatDateTime } from "../workbench/utils";

export default function ChatWorkbench() {
  const { snapshot, refreshResources } = useWorkbench();
  const { messages, sendMessage, streaming, error, taskId } = useChatStream();
  const [input, setInput] = useState("");
  const [taskDetail, setTaskDetail] = useState<TaskDetailResponse | null>(null);
  const sessions = snapshot!.resources.sessions.sessions;
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
          <h1>在同一工作台里看对话、任务和工作</h1>
          <p>这一步已经不再把聊天单独当成一个 demo 组件，而是开始接上 task/work 摘要。</p>
        </div>
      </section>

      <div className="wb-chat-layout">
        <section className="wb-panel wb-chat-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">对话</p>
              <h3>{taskId ? `当前 task: ${taskId}` : "还没有活动中的任务"}</h3>
            </div>
          </div>

          <div className="wb-chat-messages">
            {messages.length === 0 ? (
              <div className="wb-empty-state">
                <strong>从这里开始第一条消息</strong>
                <span>后续这里会接入 033 的 context provenance 和 034 的压缩状态。</span>
              </div>
            ) : (
              messages.map((message) => <MessageBubble key={message.id} message={message} />)
            )}
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
                <strong>Artifacts</strong>
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
                <strong>runtime</strong>
                <span>{activeSession?.runtime_kind ?? "未决"}</span>
              </div>
              <div className="wb-note">
                <strong>child works</strong>
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
                <h3>用真实 context frame 替代硬编码占位</h3>
              </div>
            </div>
            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>current records</strong>
                <span>{memory.summary.sor_current_count}</span>
              </div>
              <div className="wb-note">
                <strong>Context frames</strong>
                <span>{context.frames.length}</span>
              </div>
              <div className="wb-note">
                <strong>Context continuity</strong>
                <span>
                  {context.degraded.is_degraded
                    ? "033 仍有降级项，当前只显示基础 provenance。"
                    : "当前作用域的 context continuity 已可查看。"}
                </span>
              </div>
              <div className="wb-note">
                <strong>Recent summary</strong>
                <span>{activeContextFrame?.recent_summary ?? "当前还没有 recent summary。"}</span>
              </div>
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
