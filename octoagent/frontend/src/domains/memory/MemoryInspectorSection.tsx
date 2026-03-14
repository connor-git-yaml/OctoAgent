import { Link } from "react-router-dom";
import type { MemoryConsoleDocument } from "../../types";
import { formatDateTime } from "../../workbench/utils";
import {
  type MemoryDisplayRecord,
  formatLayerLabel,
  formatPartitionLabel,
  metadataDetailEntries,
} from "./shared";

interface MemoryInspectorSectionProps {
  memory: MemoryConsoleDocument;
  selectedRecord: MemoryDisplayRecord | null;
  layerOptions: string[];
  partitionOptions: string[];
  retrievalLabel: string;
}

export default function MemoryInspectorSection({
  memory,
  selectedRecord,
  layerOptions,
  partitionOptions,
  retrievalLabel,
}: MemoryInspectorSectionProps) {
  const rawRecord = selectedRecord?.record ?? null;
  const metadataEntries = rawRecord ? metadataDetailEntries(rawRecord) : [];

  return (
    <div className="wb-section-stack">
      <section className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">记忆详情</p>
            <h3>{selectedRecord ? selectedRecord.title : "选择一条记录后再查看详情"}</h3>
          </div>
        </div>

        {selectedRecord && rawRecord ? (
          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>摘要</strong>
              <span>{selectedRecord.summary}</span>
            </div>
            <div className="wb-note">
              <strong>当前状态</strong>
              <span>
                {selectedRecord.statusLabel} · {formatLayerLabel(rawRecord.layer)} ·{" "}
                {formatPartitionLabel(rawRecord.partition)}
              </span>
            </div>
            <div className="wb-note">
              <strong>时间线</strong>
              <span>
                创建于 {formatDateTime(rawRecord.created_at)}，最近更新{" "}
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
              <strong>证据与引用</strong>
              <span>
                证据 {rawRecord.evidence_refs.length} · proposal {rawRecord.proposal_refs.length} ·
                derived {rawRecord.derived_refs.length}
              </span>
            </div>
            <div className="wb-note">
              <strong>访问级别</strong>
              <span>
                {rawRecord.requires_vault_authorization
                  ? "这条记录关联受控内容，进一步读取原文仍需授权。"
                  : "这条记录当前可以直接阅读。"}
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
                  ].join(" · ") || "当前没有可展示的关联标识。"}
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
        ) : (
          <div className="wb-empty-state">
            <strong>还没有选中具体记录</strong>
            <span>从左侧结果列表点开一条记录后，这里会显示更完整的上下文说明。</span>
          </div>
        )}
      </section>

      <section className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">当前视图</p>
            <h3>这次筛选包含哪些内容</h3>
          </div>
        </div>

        <div className="wb-note-stack">
          <div className="wb-note">
            <strong>覆盖范围</strong>
            <span>
              {memory.available_scopes.length > 0 || memory.summary.scope_count > 0
                ? `当前命中了 ${memory.available_scopes.length || memory.summary.scope_count} 个上下文范围。`
                : "当前还没有命中任何上下文范围。"}
            </span>
          </div>
          <div className="wb-note">
            <strong>记忆类型</strong>
            <div className="wb-chip-row">
              {layerOptions.filter(Boolean).map((layer) => (
                <span key={layer} className="wb-chip">
                  {formatLayerLabel(layer)}
                </span>
              ))}
            </div>
          </div>
          <div className="wb-note">
            <strong>主题分区</strong>
            <div className="wb-chip-row">
              {partitionOptions.filter(Boolean).length > 0 ? (
                partitionOptions
                  .filter(Boolean)
                  .map((partition) => (
                    <span key={partition} className="wb-chip">
                      {formatPartitionLabel(partition)}
                    </span>
                  ))
              ) : (
                <span>当前记录里还没有可枚举的主题分区。</span>
              )}
            </div>
          </div>
          <div className="wb-note">
            <strong>当前检索路径</strong>
            <span>{retrievalLabel}</span>
          </div>
        </div>
      </section>

      <section className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">更多入口</p>
            <h3>需要更深入时，再打开这些页面</h3>
          </div>
        </div>

        <div className="wb-note-stack">
          <div className="wb-note">
            <strong>Memory 设置</strong>
            <span>
              切换本地或增强模式、补最小配置、调整召回策略，都在 Settings 的 Memory 分区。
            </span>
            <div className="wb-inline-actions">
              <Link
                className="wb-button wb-button-tertiary wb-button-inline"
                to="/settings#settings-group-memory"
              >
                打开 Settings &gt; Memory
              </Link>
            </div>
          </div>
          <div className="wb-note">
            <strong>Advanced 诊断</strong>
            <span>
              如果连接持续异常、需要看恢复状态，或者想做更细的排查，再进入 Advanced 页面。
            </span>
            <div className="wb-inline-actions">
              <Link className="wb-button wb-button-tertiary wb-button-inline" to="/advanced">
                打开 Advanced
              </Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
