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
  hasStoredRecords: boolean;
  busyActionId: string | null;
  consolidateMessage: string;
  consolidateIsError: boolean;
  onResetFilters: () => Promise<void>;
  onSelectRecord: (record: MemoryDisplayRecord) => void;
  onConsolidate: () => Promise<void>;
}

export default function MemoryResultsSection({
  memory,
  records,
  hasStoredRecords,
  busyActionId,
  consolidateMessage,
  consolidateIsError,
  onResetFilters,
  onSelectRecord,
  onConsolidate,
}: MemoryResultsSectionProps) {
  const isConsolidating = busyActionId === "memory.consolidate";

  return (
    <section className="wb-panel">
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">记忆列表</p>
          <h3>{records.length} 条记忆</h3>
        </div>
        <div className="wb-inline-actions">
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={() => void onConsolidate()}
            disabled={isConsolidating || records.length === 0}
          >
            {isConsolidating ? "整理中…" : "整理记忆"}
          </button>
          <div className="wb-chip-row">
            <span className="wb-chip">需授权 {memory.summary.vault_ref_count}</span>
            <span className="wb-chip">待整理 {memory.summary.pending_consolidation_count}</span>
          </div>
        </div>
      </div>

      {consolidateMessage ? (
        <div className={`wb-inline-banner ${consolidateIsError ? "is-warning" : "is-success"}`}>
          <span>{consolidateMessage}</span>
        </div>
      ) : null}

      {records.length === 0 ? (
        <div className="wb-empty-state">
          <strong>{hasStoredRecords ? "当前筛选没有命中记忆" : "还没有记忆"}</strong>
          <span>
            {hasStoredRecords
              ? "试试清空筛选条件。"
              : "去 Chat 对话或导入历史内容后，这里会出现记忆。"}
          </span>
          <div className="wb-inline-actions">
            <Link className="wb-button wb-button-primary" to="/">
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

            return (
              <article
                key={record.record_id}
                className="wb-memory-card"
                onClick={() => onSelectRecord(displayRecord)}
                style={{
                  cursor: "pointer",
                  ...(record.status === "archived" ? { opacity: 0.65 } : {}),
                }}
              >
                <div className="wb-memory-head">
                  <div>
                    <div className="wb-chip-row">
                      <span className={`wb-chip is-layer-${record.layer}`}>{formatLayerLabel(record.layer)}</span>
                      <span className="wb-chip">{formatPartitionLabel(record.partition)}</span>
                      {record.status === "archived" ? (
                        <span className="wb-chip is-muted">已归档</span>
                      ) : null}
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
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
