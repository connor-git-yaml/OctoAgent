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

  return (
    <div
      style={{
        display: "flex",
        justifyContent: isUser ? "flex-end" : "flex-start",
        marginBottom: "12px",
        padding: "0 16px",
      }}
    >
      <div
        style={{
          maxWidth: "70%",
          padding: "10px 14px",
          borderRadius: isUser ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
          backgroundColor: isUser ? "#1976d2" : "#f5f5f5",
          color: isUser ? "white" : "#333",
          fontSize: "14px",
          lineHeight: "1.5",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {/* 角色标签 */}
        <div
          style={{
            fontSize: "11px",
            color: isUser ? "rgba(255,255,255,0.7)" : "#999",
            marginBottom: "4px",
          }}
        >
          {isUser ? "You" : "Agent"}
        </div>

        {/* 消息内容 */}
        <div>
          {message.content || (message.isStreaming ? "" : "(empty)")}
          {message.isStreaming && (
            <span
              style={{
                display: "inline-block",
                width: "8px",
                height: "16px",
                backgroundColor: isUser ? "rgba(255,255,255,0.5)" : "#999",
                marginLeft: "2px",
                animation: "blink 1s infinite",
                verticalAlign: "text-bottom",
              }}
            />
          )}
        </div>

        {/* 审批提示 -- FR-025 */}
        {message.hasApproval && (
          <div
            style={{
              marginTop: "8px",
              padding: "6px 10px",
              backgroundColor: isUser
                ? "rgba(255,255,255,0.15)"
                : "#fff3e0",
              borderRadius: "4px",
              fontSize: "12px",
              color: isUser ? "rgba(255,255,255,0.9)" : "#e65100",
            }}
          >
            Waiting for approval... Check the Approvals panel.
          </div>
        )}
      </div>
    </div>
  );
}
