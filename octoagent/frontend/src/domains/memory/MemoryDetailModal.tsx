import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { formatDateTime } from "../../workbench/utils";
import {
  type MemoryDisplayRecord,
  formatLayerLabel,
  formatPartitionLabel,
  metadataDetailEntries,
} from "./shared";
import MemoryEditDialog from "./MemoryEditDialog";

interface MemoryDetailModalProps {
  selectedRecord: MemoryDisplayRecord | null;
  open: boolean;
  onClose: () => void;
  onSubmitAction?: (actionId: string, params: Record<string, unknown>) => Promise<unknown>;
  busyActionId?: string;
}

export default function MemoryDetailModal({
  selectedRecord,
  open,
  onClose,
  onSubmitAction,
  busyActionId,
}: MemoryDetailModalProps) {
  const [editOpen, setEditOpen] = useState(false);
  const [archiveConfirm, setArchiveConfirm] = useState(false);

  // 打开或切换记录时重置编辑/归档状态
  useEffect(() => {
    if (open) {
      setEditOpen(false);
      setArchiveConfirm(false);
    }
  }, [open, selectedRecord?.record.record_id]);

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open || !selectedRecord) return null;

  const rawRecord = selectedRecord.record;
  const metadataEntries = metadataDetailEntries(rawRecord);
  const isSor = rawRecord.layer === "sor";
  const isCurrent = rawRecord.status === "current";
  const isArchived = rawRecord.status === "archived";
  const canEdit = isSor && isCurrent && onSubmitAction;
  const canArchive = isSor && isCurrent && onSubmitAction;
  const canRestore = isSor && isArchived && onSubmitAction;

  async function handleArchive() {
    if (!onSubmitAction || !rawRecord.record_id) return;
    await onSubmitAction("memory.sor.archive", {
      scope_id: rawRecord.scope_id || "",
      memory_id: rawRecord.record_id,
      expected_version: rawRecord.version ?? 1,
    });
    setArchiveConfirm(false);
    onClose();
  }

  async function handleRestore() {
    if (!onSubmitAction || !rawRecord.record_id) return;
    await onSubmitAction("memory.sor.restore", {
      scope_id: rawRecord.scope_id || "",
      memory_id: rawRecord.record_id,
    });
    onClose();
  }

  return document.body
    ? createPortal(
        <div
          className="wb-modal-overlay"
          onClick={(e) => {
            if (e.target === e.currentTarget && e.detail > 0) onClose();
          }}
        >
          <div className="wb-modal-body">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">记忆详情</p>
                <h3>{selectedRecord.title}</h3>
              </div>
              <button
                type="button"
                className="wb-button wb-button-tertiary wb-button-inline"
                onClick={onClose}
              >
                关闭
              </button>
            </div>

            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>内容</strong>
                <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {rawRecord.summary || selectedRecord.summary}
                </span>
              </div>
              <div className="wb-note">
                <strong>状态</strong>
                <span>
                  {selectedRecord.statusLabel} · {formatLayerLabel(rawRecord.layer)} ·{" "}
                  {formatPartitionLabel(rawRecord.partition)}
                </span>
              </div>
              <div className="wb-note">
                <strong>时间</strong>
                <span>
                  创建于 {formatDateTime(rawRecord.created_at)}
                  {rawRecord.updated_at && rawRecord.updated_at !== rawRecord.created_at
                    ? `，更新于 ${formatDateTime(rawRecord.updated_at)}`
                    : ""}
                </span>
              </div>
              {selectedRecord.derivedTypeLabel || selectedRecord.confidenceLabel ? (
                <div className="wb-note">
                  <strong>派生信息</strong>
                  <span>
                    {selectedRecord.derivedTypeLabel || "未标注类型"}
                    {selectedRecord.confidenceLabel
                      ? ` · 置信度 ${selectedRecord.confidenceLabel}`
                      : ""}
                  </span>
                </div>
              ) : null}
              {rawRecord.requires_vault_authorization ? (
                <div className="wb-note">
                  <strong>访问级别</strong>
                  <span>关联受控内容，读取原文需授权。</span>
                </div>
              ) : null}
              {metadataEntries.length > 0 ? (
                <div className="wb-note">
                  <strong>补充信息</strong>
                  <div className="wb-key-value-list">
                    {metadataEntries.map(([key, value]) => (
                      <div key={`${rawRecord.record_id}-${key}`} className="wb-key-value-item">
                        <span>{key}</span>
                        <strong>{value}</strong>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>

            {/* T027 + T037: 编辑 / 归档 / 恢复 操作按钮 */}
            {(canEdit || canArchive || canRestore) && (
              <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem", paddingTop: "0.75rem", borderTop: "1px solid var(--color-border)" }}>
                {canEdit && (
                  <button
                    type="button"
                    className="wb-button wb-button-secondary"
                    onClick={() => setEditOpen(true)}
                    disabled={!!busyActionId}
                  >
                    编辑
                  </button>
                )}
                {canArchive && !archiveConfirm && (
                  <button
                    type="button"
                    className="wb-button wb-button-tertiary"
                    onClick={() => setArchiveConfirm(true)}
                    disabled={!!busyActionId}
                  >
                    归档
                  </button>
                )}
                {canArchive && archiveConfirm && (
                  <span style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                    <span style={{ fontSize: "0.85em", color: "var(--color-text-secondary)" }}>
                      确认归档？归档后将从默认列表隐藏。
                    </span>
                    <button
                      type="button"
                      className="wb-button wb-button-danger"
                      onClick={handleArchive}
                      disabled={!!busyActionId}
                    >
                      确认归档
                    </button>
                    <button
                      type="button"
                      className="wb-button wb-button-tertiary"
                      onClick={() => setArchiveConfirm(false)}
                    >
                      取消
                    </button>
                  </span>
                )}
                {canRestore && (
                  <button
                    type="button"
                    className="wb-button wb-button-secondary"
                    onClick={handleRestore}
                    disabled={!!busyActionId}
                  >
                    恢复
                  </button>
                )}
              </div>
            )}
          </div>

          {/* T028-T030: 编辑对话框 */}
          {editOpen && onSubmitAction && (
            <MemoryEditDialog
              record={rawRecord}
              onClose={() => setEditOpen(false)}
              onSave={async (content, subjectKey) => {
                await onSubmitAction("memory.sor.edit", {
                  scope_id: rawRecord.scope_id || "",
                  subject_key: rawRecord.subject_key || "",
                  content,
                  new_subject_key: subjectKey !== rawRecord.subject_key ? subjectKey : "",
                  expected_version: rawRecord.version ?? 1,
                  edit_summary: "",
                });
                setEditOpen(false);
                onClose();
              }}
              busyActionId={busyActionId}
            />
          )}
        </div>,
        document.body
      )
    : null;
}
