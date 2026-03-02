/**
 * ChatUI -- T049
 *
 * Chat UI 主组件。
 * 消息输入框 + 发送按钮 + 消息列表 + useChatStream 集成 + 审批提示引导。
 * 对齐 FR-023, FR-024, FR-025
 */

import { useRef, useState, type FormEvent } from "react";
import { useChatStream } from "../../hooks/useChatStream";
import { MessageBubble } from "./MessageBubble";

export function ChatUI() {
  const { messages, streaming, error, sendMessage } = useChatStream();
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim() || streaming) return;

    const text = input;
    setInput("");
    await sendMessage(text);

    // 滚动到底部
    setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, 100);
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        maxWidth: "800px",
        margin: "0 auto",
      }}
    >
      {/* 消息列表 */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "16px 0",
        }}
      >
        {messages.length === 0 ? (
          <div
            style={{
              textAlign: "center",
              color: "#999",
              padding: "60px 20px",
              fontSize: "14px",
            }}
          >
            Send a message to start a conversation.
          </div>
        ) : (
          messages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} />
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* 错误提示 */}
      {error && (
        <div
          style={{
            backgroundColor: "#ffebee",
            color: "#c62828",
            padding: "8px 16px",
            fontSize: "13px",
          }}
        >
          {error}
        </div>
      )}

      {/* 输入区域 */}
      <form
        onSubmit={handleSubmit}
        style={{
          display: "flex",
          gap: "8px",
          padding: "12px 16px",
          borderTop: "1px solid #e0e0e0",
          backgroundColor: "#fafafa",
        }}
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message..."
          disabled={streaming}
          style={{
            flex: 1,
            padding: "10px 14px",
            border: "1px solid #ddd",
            borderRadius: "20px",
            fontSize: "14px",
            outline: "none",
          }}
        />
        <button
          type="submit"
          disabled={streaming || !input.trim()}
          style={{
            padding: "10px 20px",
            backgroundColor:
              streaming || !input.trim() ? "#ccc" : "#1976d2",
            color: "white",
            border: "none",
            borderRadius: "20px",
            cursor:
              streaming || !input.trim() ? "not-allowed" : "pointer",
            fontSize: "14px",
          }}
        >
          {streaming ? "..." : "Send"}
        </button>
      </form>
    </div>
  );
}
