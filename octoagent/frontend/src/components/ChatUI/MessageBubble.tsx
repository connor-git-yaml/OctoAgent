/**
 * MessageBubble -- T048
 *
 * 消息气泡组件(用户/Agent 区分) + 流式渲染动画 + 审批提示样式。
 * 对齐 FR-023
 */

import type { ChatMessage } from "../../hooks/useChatStream";

interface MessageBubbleProps {
  message: ChatMessage;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const roleLabel = isUser ? "你" : "OctoAgent";
  const content = message.content || (message.isStreaming ? "正在思考..." : "暂无回复内容");

  return (
    <div className={`wb-message ${isUser ? "is-user" : "is-agent"}`}>
      <div className={`wb-message-card ${isUser ? "is-user" : "is-agent"}`}>
        <div className="wb-message-role">{roleLabel}</div>
        <div className="wb-message-content">
          {content}
          {message.isStreaming ? <span className="wb-message-cursor" aria-hidden="true" /> : null}
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
