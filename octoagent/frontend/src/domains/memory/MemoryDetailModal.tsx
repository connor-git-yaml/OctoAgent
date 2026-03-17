import { useEffect } from "react";
import { createPortal } from "react-dom";
import { formatDateTime } from "../../workbench/utils";
import {
  type MemoryDisplayRecord,
  formatLayerLabel,
  formatPartitionLabel,
  metadataDetailEntries,
} from "./shared";

interface MemoryDetailModalProps {
  selectedRecord: MemoryDisplayRecord | null;
  open: boolean;
  onClose: () => void;
}

export default function MemoryDetailModal({
  selectedRecord,
  open,
  onClose,
}: MemoryDetailModalProps) {
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
                <strong>摘要</strong>
                <span>{selectedRecord.summary}</span>
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
                  创建于 {formatDateTime(rawRecord.created_at)}，更新于{" "}
                  {formatDateTime(rawRecord.updated_at ?? rawRecord.created_at)}
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
              <div className="wb-note">
                <strong>引用</strong>
                <span>
                  证据 {rawRecord.evidence_refs.length} · proposal {rawRecord.proposal_refs.length} ·
                  derived {rawRecord.derived_refs.length}
                </span>
              </div>
              <div className="wb-note">
                <strong>访问级别</strong>
                <span>
                  {rawRecord.requires_vault_authorization
                    ? "关联受控内容，读取原文需授权。"
                    : "可直接阅读。"}
                </span>
              </div>
              {rawRecord.evidence_refs.length > 0 ||
              rawRecord.proposal_refs.length > 0 ||
              rawRecord.derived_refs.length > 0 ? (
                <div className="wb-note">
                  <strong>关联标识</strong>
                  <span>
                    {[
                      ...rawRecord.evidence_refs
                        .map((item) =>
                          typeof item.id === "string"
                            ? item.id
                            : typeof item.ref_id === "string"
                              ? item.ref_id
                              : ""
                        )
                        .filter(Boolean),
                      ...rawRecord.proposal_refs,
                      ...rawRecord.derived_refs,
                    ].join(" · ") || "无"}
                  </span>
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
          </div>
        </div>,
        document.body
      )
    : null;
}
