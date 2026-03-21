/**
 * 删除对话确认弹框 -- 二次确认后级联删除 session 所有数据。
 */

import { createPortal } from "react-dom";

interface DeleteSessionModalProps {
  sessionTitle: string;
  taskCount: number;
  busy: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export default function DeleteSessionModal({
  sessionTitle,
  taskCount,
  busy,
  onConfirm,
  onClose,
}: DeleteSessionModalProps) {
  return createPortal(
    <div
      className="wb-modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div className="wb-modal-body" style={{ maxWidth: 420, padding: 24 }}>
        <h2 style={{ margin: "0 0 12px", fontSize: 17 }}>删除对话</h2>
        <p style={{ margin: "0 0 8px", color: "var(--cp-ink)" }}>
          确定要删除「{sessionTitle}」吗？
        </p>
        <p
          style={{
            margin: "0 0 20px",
            color: "var(--cp-muted)",
            fontSize: 13,
            lineHeight: 1.5,
          }}
        >
          此对话中的{taskCount > 0 ? ` ${taskCount} 个任务、` : ""}
          所有消息记录和工作产物将被永久删除，且无法恢复。
        </p>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button
            type="button"
            className="wb-button wb-button--secondary"
            onClick={onClose}
            disabled={busy}
          >
            取消
          </button>
          <button
            type="button"
            className="wb-button wb-button--danger"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "删除中..." : "确认删除"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
