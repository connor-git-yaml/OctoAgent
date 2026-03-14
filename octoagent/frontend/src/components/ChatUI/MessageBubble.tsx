/**
 * MessageBubble -- T048
 *
 * 消息气泡组件(用户/Agent 区分) + 流式渲染动画 + 审批提示样式。
 * 对齐 FR-023
 */

import { useState } from "react";
import type { ChatMessage } from "../../hooks/useChatStream";
import { HoverReveal } from "../../ui/primitives";
import { MarkdownContent } from "./MarkdownContent";

interface MessageBubbleTraceEntry {
  id: string;
  label: string;
  summary: string;
  stateLabel?: string;
  tone?: "success" | "warning" | "danger" | "running" | "draft";
}

interface MessageBubbleActivityItem {
  id: string;
  actor: string;
  stateLabel: string;
  tone: "success" | "warning" | "danger" | "running" | "draft";
  summary: string;
  traceTitle?: string;
  traceEntries?: MessageBubbleTraceEntry[];
}

interface MessageBubbleProps {
  message: ChatMessage;
  loadingLabel?: string;
  activityItems?: MessageBubbleActivityItem[];
}

export function MessageBubble({
  message,
  loadingLabel = "正在整理回复",
  activityItems = [],
}: MessageBubbleProps) {
  const [expandedTraceId, setExpandedTraceId] = useState<string | null>(null);
  const isUser = message.role === "user";
  const roleLabel = isUser ? "你" : "OctoAgent";
  const hasContent = Boolean(message.content?.trim());
  const showLoadingState = message.isStreaming && !isUser;
  const content = hasContent ? message.content : message.isStreaming ? "" : "暂无回复内容";

  return (
    <div className={`wb-message ${isUser ? "is-user" : "is-agent"}`}>
      <div className={`wb-message-card ${isUser ? "is-user" : "is-agent"}`}>
        <div className="wb-message-role">{roleLabel}</div>
        <div className="wb-message-content">
          {showLoadingState ? (
            <div className="wb-message-loading" aria-live="polite">
              <div className="wb-message-loading-head">
                <span>{loadingLabel}</span>
                <span className="wb-message-loading-dots" aria-hidden="true">
                  <span>.</span>
                  <span>.</span>
                  <span>.</span>
                </span>
              </div>
              {activityItems.length > 0 ? (
                <div className="wb-message-activity" aria-label="内部协作进度">
                  <div className="wb-message-activity-list">
                    {activityItems.map((item, index) => (
                      <div key={item.id} className="wb-message-activity-item">
                        <div
                          className="wb-message-activity-line"
                          aria-hidden={index === activityItems.length - 1}
                        />
                        <span
                          className={`wb-message-activity-dot is-${item.tone}`}
                          aria-hidden="true"
                        />
                        <div className="wb-message-activity-copy">
                          <div className="wb-message-activity-head">
                            <strong>{item.actor}</strong>
                            <span className={`wb-status-pill is-${item.tone}`}>{item.stateLabel}</span>
                          </div>
                          <span>{item.summary}</span>
                          {item.traceEntries && item.traceEntries.length > 0 ? (
                            <>
                              <div className="wb-message-trace-inline" aria-label={`${item.actor} 的处理阶段`}>
                                {item.traceEntries.slice(0, 4).map((entry, traceIndex) => (
                                  <div key={entry.id} className="wb-message-trace-inline-item">
                                    <div
                                      className="wb-message-trace-inline-line"
                                      aria-hidden={traceIndex === item.traceEntries!.slice(0, 4).length - 1}
                                    />
                                    <span
                                      className={`wb-message-trace-inline-dot is-${entry.tone ?? "draft"}`}
                                      aria-hidden="true"
                                    />
                                    <div className="wb-message-trace-inline-copy">
                                      <strong>{entry.label}</strong>
                                      {entry.stateLabel ? (
                                        <span className={`wb-status-pill is-${entry.tone ?? "draft"}`}>
                                          {entry.stateLabel}
                                        </span>
                                      ) : null}
                                    </div>
                                  </div>
                                ))}
                              </div>
                              <HoverReveal
                                label="查看细节"
                                expanded={expandedTraceId === item.id}
                                onToggle={(expanded) =>
                                  setExpandedTraceId(expanded ? item.id : null)
                                }
                                ariaLabel={`${item.actor} 的内部轨迹`}
                                triggerClassName="wb-button-inline wb-message-trace-trigger"
                              >
                                <div className="wb-message-trace">
                                  {item.traceTitle ? (
                                    <strong className="wb-message-trace-title">{item.traceTitle}</strong>
                                  ) : null}
                                  <div className="wb-message-trace-list">
                                    {item.traceEntries.map((entry, traceIndex) => (
                                      <div key={entry.id} className="wb-message-trace-item">
                                        <div
                                          className="wb-message-trace-line"
                                          aria-hidden={traceIndex === item.traceEntries!.length - 1}
                                        />
                                        <span
                                          className={`wb-message-trace-dot is-${entry.tone ?? "draft"}`}
                                          aria-hidden="true"
                                        />
                                        <div className="wb-message-trace-copy">
                                          <div className="wb-message-trace-head">
                                            <strong>{entry.label}</strong>
                                            {entry.stateLabel ? (
                                              <span className={`wb-status-pill is-${entry.tone ?? "draft"}`}>
                                                {entry.stateLabel}
                                              </span>
                                            ) : null}
                                          </div>
                                          <span>{entry.summary}</span>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              </HoverReveal>
                            </>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            <MarkdownContent content={content} />
          )}
        </div>
        {message.hasApproval ? (
          <div className={`wb-message-approval ${isUser ? "is-user" : "is-agent"}`}>
            这条回复需要确认，请到审批区处理。
          </div>
        ) : null}
      </div>
    </div>
  );
}
