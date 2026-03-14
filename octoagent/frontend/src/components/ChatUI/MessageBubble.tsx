/**
 * MessageBubble -- T048
 *
 * 消息气泡组件(用户/Agent 区分) + 流式渲染动画 + 审批提示样式。
 * 对齐 FR-023
 */

import type { ChatMessage } from "../../hooks/useChatStream";
import { MarkdownContent } from "./MarkdownContent";

interface MessageBubbleProps {
  message: ChatMessage;
}

export function MessageBubble({ message }: MessageBubbleProps) {
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
              <span>正在整理回复</span>
              <span className="wb-message-loading-dots" aria-hidden="true">
                <span>.</span>
                <span>.</span>
                <span>.</span>
              </span>
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
