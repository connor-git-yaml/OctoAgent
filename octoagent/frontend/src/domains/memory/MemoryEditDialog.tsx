import { useState } from "react";
import type { MemoryRecordProjection } from "../../types";

interface MemoryEditDialogProps {
  record: MemoryRecordProjection;
  onClose: () => void;
  onSave: (content: string, subjectKey: string) => Promise<void>;
  busyActionId?: string;
}

/**
 * T028-T030: SoR 记忆编辑对话框。
 * 支持 inline 编辑 content 和 subject_key，保存时携带乐观锁 expected_version。
 * 收到 VERSION_CONFLICT 时显示刷新提示。
 */
export default function MemoryEditDialog({
  record,
  onClose,
  onSave,
  busyActionId,
}: MemoryEditDialogProps) {
  // 优先使用完整 summary（由后端 projection 截取 content[:240]），避免截断丢失
  const [content, setContent] = useState(record.summary || "");
  const [subjectKey, setSubjectKey] = useState(record.subject_key || "");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    if (!content.trim()) {
      setError("内容不能为空");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave(content.trim(), subjectKey.trim());
    } catch (e: unknown) {
      // T030: 乐观锁冲突处理
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("VERSION_CONFLICT") || msg.includes("版本冲突")) {
        setError("版本冲突，请刷新后重试。其他用户或系统可能已更新了此记忆。");
      } else {
        setError(`保存失败: ${msg}`);
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="wb-modal-overlay"
      style={{ zIndex: 1001 }}
      onClick={(e) => {
        if (e.target === e.currentTarget && e.detail > 0) onClose();
      }}
    >
      <div className="wb-modal-body" style={{ maxWidth: "600px" }}>
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">编辑记忆</p>
            <h3>{record.subject_key || "未命名"}</h3>
          </div>
          <button
            type="button"
            className="wb-button wb-button-tertiary wb-button-inline"
            onClick={onClose}
          >
            取消
          </button>
        </div>

        <div className="wb-note-stack">
          <div className="wb-note">
            <strong>主题标识</strong>
            <input
              type="text"
              className="wb-input"
              value={subjectKey}
              onChange={(e) => setSubjectKey(e.target.value)}
              placeholder="subject_key"
              style={{ width: "100%", marginTop: "0.25rem" }}
            />
          </div>
          <div className="wb-note">
            <strong>内容</strong>
            <textarea
              className="wb-input"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={8}
              style={{ width: "100%", marginTop: "0.25rem", resize: "vertical" }}
              placeholder="记忆内容..."
            />
          </div>
          {error && (
            <div className="wb-note" style={{ color: "var(--color-danger)" }}>
              {error}
            </div>
          )}
        </div>

        <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem", justifyContent: "flex-end" }}>
          <button
            type="button"
            className="wb-button wb-button-tertiary"
            onClick={onClose}
          >
            取消
          </button>
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={handleSave}
            disabled={saving || !!busyActionId}
          >
            {saving ? "保存中..." : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
