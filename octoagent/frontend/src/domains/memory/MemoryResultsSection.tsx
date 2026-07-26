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
  onSelectRecord: (record: MemoryDisplayRecord, trigger: HTMLElement) => void;
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
    <section className="wb-panel f149-memory-results">
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
          <strong>{hasStoredRecords ? "没有匹配的记忆" : "还没有记忆"}</strong>
          <span>
            {hasStoredRecords
              ? "换个关键词，或者清除筛选后再看看。"
              : "先和助手聊聊，值得记住的背景会出现在这里。"}
          </span>
          <div className="wb-inline-actions">
            <Link className="wb-button wb-button-primary" to="/">
              开始对话
            </Link>
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={() => void onResetFilters()}
              disabled={busyActionId === "memory.query"}
            >
              清除筛选
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
                className={`wb-memory-card f149-memory-record${
                  record.status === "archived" ? " is-archived" : ""
                }`}
                onClick={(event) =>
                  onSelectRecord(displayRecord, event.currentTarget)
                }
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelectRecord(displayRecord, event.currentTarget);
                  }
                }}
                role="button"
                tabIndex={0}
                aria-label={`查看 ${displayRecord.title}`}
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

                <span className="f149-memory-record-action">
                  {record.status === "archived" ? "查看并恢复" : "查看与编辑"}
                </span>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
