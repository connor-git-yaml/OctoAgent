import { Link } from "react-router-dom";
import type { MemoryConsoleDocument } from "../../types";
import { formatDateTime } from "../../workbench/utils";
import {
  type MemoryDisplayRecord,
  formatLayerLabel,
  formatPartitionLabel,
} from "./shared";

interface MemoryResultsSectionProps {
  memory: MemoryConsoleDocument;
  records: MemoryDisplayRecord[];
  selectedRecordId: string;
  hasStoredRecords: boolean;
  busyActionId: string | null;
  onResetFilters: () => Promise<void>;
  onSelectRecord: (record: MemoryDisplayRecord) => void;
}

export default function MemoryResultsSection({
  memory,
  records,
  selectedRecordId,
  hasStoredRecords,
  busyActionId,
  onResetFilters,
  onSelectRecord,
}: MemoryResultsSectionProps) {
  return (
    <section className="wb-panel">
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">现在记住了什么</p>
          <h3>{records.length} 条可读记忆</h3>
        </div>
        <div className="wb-chip-row">
          <span className="wb-chip">需授权 {memory.summary.vault_ref_count}</span>
          <span className="wb-chip">待整理 {memory.summary.pending_replay_count}</span>
        </div>
      </div>

      {records.length === 0 ? (
        <div className="wb-empty-state">
          <strong>{hasStoredRecords ? "当前视图没有命中可读记忆" : "当前还没有可读记忆"}</strong>
          <span>
            {hasStoredRecords
              ? "先清空筛选，或者整理最新记忆后再看一遍。"
              : "先去 Chat 对话或导入历史内容，Memory 才会开始形成可读结论。"}
          </span>
          <div className="wb-inline-actions">
            <Link className="wb-button wb-button-primary" to="/chat">
              去 Chat
            </Link>
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={() => void onResetFilters()}
              disabled={busyActionId === "memory.query"}
            >
              清空筛选
            </button>
          </div>
        </div>
      ) : (
        <div className="wb-record-list">
          {records.map((displayRecord) => {
            const { record } = displayRecord;
            const isSelected = record.record_id === selectedRecordId;

            return (
              <article
                key={record.record_id}
                className={`wb-memory-card ${isSelected ? "is-selected" : ""}`}
              >
                <div className="wb-memory-head">
                  <div>
                    <div className="wb-chip-row">
                      <span className="wb-chip">{formatLayerLabel(record.layer)}</span>
                      <span className="wb-chip">{formatPartitionLabel(record.partition)}</span>
                      {record.requires_vault_authorization ? (
                        <span className="wb-chip is-warning">需授权</span>
                      ) : null}
                      {displayRecord.derivedTypeLabel ? (
                        <span className="wb-chip">{displayRecord.derivedTypeLabel}</span>
                      ) : null}
                    </div>
                    <strong>{displayRecord.title}</strong>
                    <p>{displayRecord.summary}</p>
                  </div>
                  <div className="wb-list-meta">
                    <span className={`wb-status-pill is-${record.status.toLowerCase()}`}>
                      {displayRecord.statusLabel}
                    </span>
                    <small>{formatDateTime(record.updated_at ?? record.created_at)}</small>
                  </div>
                </div>

                <div className="wb-chip-row">
                  <span className="wb-chip">证据 {record.evidence_refs.length}</span>
                  {record.version !== null ? (
                    <span className="wb-chip">版本 {record.version}</span>
                  ) : null}
                  {displayRecord.confidenceLabel ? (
                    <span className="wb-chip">置信度 {displayRecord.confidenceLabel}</span>
                  ) : null}
                </div>

                {displayRecord.metadataPreview.length > 0 ? (
                  <div className="wb-key-value-list">
                    {displayRecord.metadataPreview.map(([key, value]) => (
                      <div key={`${record.record_id}-${key}`} className="wb-key-value-item">
                        <span>{key}</span>
                        <strong>{value}</strong>
                      </div>
                    ))}
                  </div>
                ) : null}

                <div className="wb-inline-actions">
                  <button
                    type="button"
                    className="wb-button wb-button-tertiary wb-button-inline"
                    aria-label={`查看 ${displayRecord.title} 详情`}
                    onClick={() => onSelectRecord(displayRecord)}
                  >
                    {isSelected ? "正在查看" : "查看详情"}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
