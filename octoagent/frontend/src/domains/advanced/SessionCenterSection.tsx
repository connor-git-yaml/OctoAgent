import { Link } from "react-router-dom";
import type {
  A2AConversationItem,
  A2AMessageItem,
  RecallFrameItem,
  SessionProjectionItem,
  SessionProjectionSummary,
} from "../../types";

type SessionLaneFilter = "all" | "running" | "queue" | "history";

interface SessionCenterSectionProps {
  sessionFilter: string;
  onSessionFilterChange: (value: string) => void;
  sessionLane: SessionLaneFilter;
  onSessionLaneChange: (value: SessionLaneFilter) => void;
  sessionSummary: SessionProjectionSummary | null;
  contextA2AConversations: A2AConversationItem[];
  contextA2AMessages: A2AMessageItem[];
  contextRecallFrames: RecallFrameItem[];
  contextMemoryNamespaceCount: number;
  filteredSessions: SessionProjectionItem[];
  focusedSessionId: string;
  busyActionId: string | null;
  onNewSession: (session: SessionProjectionItem | null) => void;
  onFocusSession: (session: SessionProjectionItem) => void;
  onUnfocusSession: () => void;
  onResetSession: (session: SessionProjectionItem) => void;
  onExportSession: (session: SessionProjectionItem) => void;
  onInterruptSession: (session: SessionProjectionItem) => void;
  onResumeSession: (session: SessionProjectionItem) => void;
  formatDateTime: (value?: string | null) => string;
  formatA2ADirection: (value: string) => string;
  formatA2AMessageType: (value: string) => string;
  formatJson: (value: unknown) => string;
  statusTone: (status: string) => string;
}

export default function SessionCenterSection({
  sessionFilter,
  onSessionFilterChange,
  sessionLane,
  onSessionLaneChange,
  sessionSummary,
  contextA2AConversations,
  contextA2AMessages,
  contextRecallFrames,
  contextMemoryNamespaceCount,
  filteredSessions,
  focusedSessionId,
  busyActionId,
  onNewSession,
  onFocusSession,
  onUnfocusSession,
  onResetSession,
  onExportSession,
  onInterruptSession,
  onResumeSession,
  formatDateTime,
  formatA2ADirection,
  formatA2AMessageType,
  formatJson,
  statusTone,
}: SessionCenterSectionProps) {
  const focusedSession =
    filteredSessions.find((session) => session.session_id === focusedSessionId) ?? null;
  const laneOptions: Array<{
    value: SessionLaneFilter;
    label: string;
    count: number;
  }> = [
    {
      value: "all",
      label: "全部",
      count: sessionSummary?.total_sessions ?? filteredSessions.length,
    },
    {
      value: "running",
      label: "运行中",
      count: sessionSummary?.running_sessions ?? 0,
    },
    {
      value: "queue",
      label: "队列",
      count: sessionSummary?.queued_sessions ?? 0,
    },
    {
      value: "history",
      label: "历史",
      count: sessionSummary?.history_sessions ?? 0,
    },
  ];

  return (
    <section className="stack-section">
      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Session Center</p>
            <h3>会话与执行投影</h3>
          </div>
          <div className="action-row">
            <input
              className="search-input"
              value={sessionFilter}
              onChange={(event) => onSessionFilterChange(event.target.value)}
              placeholder="搜索 task / thread / requester"
            />
            <div className="action-row">
              {laneOptions.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={option.value === sessionLane ? "secondary-button" : "ghost-button"}
                  onClick={() => onSessionLaneChange(option.value)}
                >
                  {option.label} {option.count}
                </button>
              ))}
            </div>
            {focusedSessionId ? (
              <button
                type="button"
                className="ghost-button"
                onClick={onUnfocusSession}
                disabled={busyActionId === "session.unfocus"}
              >
                取消聚焦
              </button>
            ) : null}
            <button
              type="button"
              className="secondary-button"
              onClick={() => onNewSession(focusedSession)}
              disabled={busyActionId === "session.new"}
            >
              开始新对话
            </button>
          </div>
        </div>
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Butler{" -> "}Worker 内部会话</p>
            <h3>{contextA2AConversations.length}</h3>
          </div>
          <span className="tone-chip neutral">
            Recall {contextRecallFrames.length} / Namespace {contextMemoryNamespaceCount}
          </span>
        </div>
        {contextA2AConversations.length > 0 ? (
          <div className="event-list">
            {contextA2AConversations.slice(0, 3).map((conversation) => {
              const latestMessage =
                [...contextA2AMessages]
                  .filter(
                    (item) =>
                      item.a2a_conversation_id === conversation.a2a_conversation_id
                  )
                  .sort((left, right) => right.message_seq - left.message_seq)[0] ?? null;
              const workerRecall =
                [...contextRecallFrames]
                  .filter(
                    (item) =>
                      item.agent_session_id === conversation.target_agent_session_id
                  )
                  .sort((left, right) =>
                    String(right.created_at ?? "").localeCompare(
                      String(left.created_at ?? "")
                    )
                  )[0] ?? null;

              return (
                <div key={conversation.a2a_conversation_id} className="event-item">
                  <div>
                    <strong>
                      {conversation.source_agent || "Butler"}{" -> "}
                      {conversation.target_agent || "Worker"}
                    </strong>
                    <p>
                      {conversation.status} / {conversation.message_count} 条消息 / 最新{" "}
                      {formatA2AMessageType(conversation.latest_message_type)}
                    </p>
                    <p>
                      Butler Session {conversation.source_agent_session_id || "未记录"} /
                      Worker Session {conversation.target_agent_session_id || "未记录"}
                    </p>
                    <p>
                      {latestMessage
                        ? `${formatA2ADirection(latestMessage.direction)} / ${formatA2AMessageType(latestMessage.message_type)}`
                        : "当前还没有消息明细"}
                      {workerRecall ? ` / Recall hits ${workerRecall.memory_hit_count}` : ""}
                    </p>
                  </div>
                  <small>{formatDateTime(conversation.updated_at)}</small>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="muted">当前还没有 Butler{" -> "}Worker 的内部 A2A 会话。</p>
        )}
      </article>

      {filteredSessions.map((session) => (
        <article key={session.session_id} className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">{session.thread_id}</p>
              <h3>{session.title || session.task_id}</h3>
            </div>
            <div className="action-row">
              {session.session_id === focusedSessionId ? (
                <span className="tone-chip neutral">Focused</span>
              ) : null}
              <span className={`tone-chip ${statusTone(session.status)}`}>{session.status}</span>
            </div>
          </div>
          <p>{session.latest_message_summary || "暂无消息摘要"}</p>
          <div className="meta-grid">
            <span>Task: {session.task_id}</span>
            <span>Channel: {session.channel}</span>
            <span>Requester: {session.requester_id}</span>
            <span>Updated: {formatDateTime(session.latest_event_at)}</span>
            <span>Runtime: {session.runtime_kind || "-"}</span>
            <span>Parent Task: {session.parent_task_id || "-"}</span>
          </div>
            {session.execution_summary &&
            Object.keys(session.execution_summary).length > 0 ? (
            <pre className="json-preview">{formatJson(session.execution_summary)}</pre>
          ) : null}
          <div className="action-row">
            <button
              type="button"
              className="secondary-button"
              onClick={() => onFocusSession(session)}
              disabled={busyActionId === "session.focus"}
            >
              聚焦
            </button>
            <button
              type="button"
              className="ghost-button"
              onClick={() => onExportSession(session)}
              disabled={busyActionId === "session.export"}
            >
              导出
            </button>
            <button
              type="button"
              className="ghost-button"
              onClick={() => onResetSession(session)}
              disabled={busyActionId === "session.reset"}
            >
              重置 continuity
            </button>
            <button
              type="button"
              className="ghost-button"
              onClick={() => onInterruptSession(session)}
              disabled={busyActionId === "session.interrupt"}
            >
              取消
            </button>
            <button
              type="button"
              className="ghost-button"
              onClick={() => onResumeSession(session)}
              disabled={busyActionId === "session.resume"}
            >
              恢复
            </button>
            <Link className="inline-link" to={`/tasks/${session.task_id}`}>
              打开详情
            </Link>
            {session.detail_refs.execution_api ? (
              <a
                className="inline-link"
                href={session.detail_refs.execution_api}
                target="_blank"
                rel="noreferrer"
              >
                Execution API
              </a>
            ) : null}
          </div>
        </article>
      ))}
    </section>
  );
}
