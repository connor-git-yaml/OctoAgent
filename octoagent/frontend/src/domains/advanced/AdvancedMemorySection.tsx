import type {
  ActionResultEnvelope,
  MemoryConsoleDocument,
  MemoryProposalAuditDocument,
  MemoryRecordProjection,
  MemorySubjectHistoryDocument,
  VaultAuthorizationDocument,
} from "../../types";

interface MemoryQueryDraft {
  scopeId: string;
  partition: string;
  layer: string;
  query: string;
  includeHistory: boolean;
  includeVaultRefs: boolean;
  limit: number;
}

interface MemoryAccessDraft {
  scopeId: string;
  partition: string;
  subjectKey: string;
  reason: string;
}

interface MemoryRetrieveDraft {
  scopeId: string;
  partition: string;
  subjectKey: string;
  query: string;
  grantId: string;
}

interface MemoryExportDraft {
  scopeIds: string;
  includeHistory: boolean;
  includeVaultRefs: boolean;
}

interface MemoryRestoreDraft {
  snapshotRef: string;
  targetScopeMode: string;
  scopeIds: string;
}

interface AdvancedMemorySectionProps {
  memory: MemoryConsoleDocument;
  memoryBusy: boolean;
  busyActionId: string | null;
  memoryQueryDraft: MemoryQueryDraft;
  memoryAccessDraft: MemoryAccessDraft;
  memoryRetrieveDraft: MemoryRetrieveDraft;
  memoryExportDraft: MemoryExportDraft;
  memoryRestoreDraft: MemoryRestoreDraft;
  memoryScopeOptions: string[];
  memoryPartitionOptions: string[];
  memoryLayerOptions: string[];
  selectedMemoryRecord: MemoryRecordProjection | null;
  selectedSubjectHistory: MemorySubjectHistoryDocument | null;
  memoryProposals: MemoryProposalAuditDocument | null;
  vaultAuthorization: VaultAuthorizationDocument | null;
  lastMemoryAction: ActionResultEnvelope | null;
  onUpdateMemoryQueryDraft: (
    key: keyof MemoryQueryDraft,
    value: string | number | boolean
  ) => void;
  onRefreshMemoryQuery: () => void;
  onRefreshMemoryDetails: () => void;
  onFocusMemoryRecord: (record: MemoryRecordProjection) => void;
  onResolveVaultRequest: (requestId: string, decision: "approve" | "reject") => void;
  onUpdateMemoryAccessDraft: (
    key: keyof MemoryAccessDraft,
    value: string
  ) => void;
  onRequestVaultAccess: () => void;
  onUpdateMemoryRetrieveDraft: (
    key: keyof MemoryRetrieveDraft,
    value: string
  ) => void;
  onRetrieveVault: () => void;
  onUpdateMemoryExportDraft: (
    key: keyof MemoryExportDraft,
    value: string | boolean
  ) => void;
  onUpdateMemoryRestoreDraft: (
    key: keyof MemoryRestoreDraft,
    value: string
  ) => void;
  onInspectMemoryExport: () => void;
  onVerifyMemoryRestore: () => void;
  formatMemoryPartition: (value: string) => string;
  formatMemoryLayer: (value: string) => string;
  formatDateTime: (value?: string | null) => string;
  formatJson: (value: unknown) => string;
  statusTone: (status: string) => string;
}

export default function AdvancedMemorySection({
  memory,
  memoryBusy,
  busyActionId,
  memoryQueryDraft,
  memoryAccessDraft,
  memoryRetrieveDraft,
  memoryExportDraft,
  memoryRestoreDraft,
  memoryScopeOptions,
  memoryPartitionOptions,
  memoryLayerOptions,
  selectedMemoryRecord,
  selectedSubjectHistory,
  memoryProposals,
  vaultAuthorization,
  lastMemoryAction,
  onUpdateMemoryQueryDraft,
  onRefreshMemoryQuery,
  onRefreshMemoryDetails,
  onFocusMemoryRecord,
  onResolveVaultRequest,
  onUpdateMemoryAccessDraft,
  onRequestVaultAccess,
  onUpdateMemoryRetrieveDraft,
  onRetrieveVault,
  onUpdateMemoryExportDraft,
  onUpdateMemoryRestoreDraft,
  onInspectMemoryExport,
  onVerifyMemoryRestore,
  formatMemoryPartition,
  formatMemoryLayer,
  formatDateTime,
  formatJson,
  statusTone,
}: AdvancedMemorySectionProps) {
  return (
    <section className="stack-section">
      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">记忆与敏感信息 / Memory Console</p>
            <h3>{memory.active_project_id || "未绑定 Project"}</h3>
          </div>
          <span className={`tone-chip ${statusTone(memory.status)}`}>{memory.status}</span>
        </div>
        <p className="muted">
          先用“内容类型 + 关键词”缩小范围，再从结果里点“查看历史”。如果需要查看敏感内容，
          下方授权表单会自动带入你选中的条目。
        </p>
        <div className="meta-grid">
          <span>Workspace {memory.active_workspace_id || "-"}</span>
          <span>范围 {memory.summary.scope_count}</span>
          <span>片段 {memory.summary.fragment_count}</span>
          <span>当前结论 {memory.summary.sor_current_count}</span>
          <span>历史版本 {memory.summary.sor_history_count}</span>
          <span>敏感引用 {memory.summary.vault_ref_count}</span>
          <span>写入提议 {memory.summary.proposal_count}</span>
        </div>
        <div className="form-grid">
          <label>
            关键词
            <input
              value={memoryQueryDraft.query}
              onChange={(event) => onUpdateMemoryQueryDraft("query", event.target.value)}
              placeholder="例如 Alice / credential / 健康检查"
            />
          </label>
          <label>
            想看哪类内容
            <select
              value={memoryQueryDraft.partition}
              onChange={(event) => onUpdateMemoryQueryDraft("partition", event.target.value)}
            >
              <option value="">全部内容</option>
              {memoryPartitionOptions.map((item) => (
                <option key={item} value={item}>
                  {formatMemoryPartition(item)}
                </option>
              ))}
            </select>
          </label>
          <label>
            来自哪一层
            <select
              value={memoryQueryDraft.layer}
              onChange={(event) => onUpdateMemoryQueryDraft("layer", event.target.value)}
            >
              <option value="">全部来源</option>
              {memoryLayerOptions.map((item) => (
                <option key={item} value={item}>
                  {formatMemoryLayer(item)}
                </option>
              ))}
            </select>
          </label>
          <label>
            展示数量
            <select
              value={memoryQueryDraft.limit}
              onChange={(event) =>
                onUpdateMemoryQueryDraft("limit", Number(event.target.value) || 50)
              }
            >
              {[20, 50, 100, 200].map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
        </div>
        <details className="disclosure-card">
          <summary>高级过滤</summary>
          <div className="form-grid">
            <label>
              限定 Scope
              <select
                value={memoryQueryDraft.scopeId}
                onChange={(event) => onUpdateMemoryQueryDraft("scopeId", event.target.value)}
              >
                <option value="">当前项目全部范围</option>
                {memoryScopeOptions.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <label className="checkbox-line">
              <input
                type="checkbox"
                checked={memoryQueryDraft.includeHistory}
                onChange={(event) =>
                  onUpdateMemoryQueryDraft("includeHistory", event.target.checked)
                }
              />
              包含历史版本
            </label>
            <label className="checkbox-line">
              <input
                type="checkbox"
                checked={memoryQueryDraft.includeVaultRefs}
                onChange={(event) =>
                  onUpdateMemoryQueryDraft("includeVaultRefs", event.target.checked)
                }
              />
              包含 Vault 引用
            </label>
          </div>
        </details>
        <div className="action-row">
          <button
            type="button"
            className="primary-button"
            onClick={onRefreshMemoryQuery}
            disabled={busyActionId === "memory.query"}
          >
            刷新 Memory 视图
          </button>
          <button
            type="button"
            className="ghost-button"
            onClick={onRefreshMemoryDetails}
            disabled={memoryBusy}
          >
            刷新授权与提议
          </button>
        </div>
        {memory.available_scopes.length > 0 ? (
          <p className="muted">当前可用范围: {memory.available_scopes.join(", ")}</p>
        ) : null}
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">搜索结果 / Memory Records</p>
            <h3>{memory.records.length}</h3>
          </div>
          <span className="tone-chip neutral">
            {memoryLayerOptions.map((item) => formatMemoryLayer(item)).join(" / ") || "全部来源"}
          </span>
        </div>
        {selectedMemoryRecord ? (
          <p className="muted">
            当前已选目标: {selectedMemoryRecord.summary || selectedMemoryRecord.subject_key}
          </p>
        ) : (
          <p className="muted">还没有选中条目。点任意一条记录即可查看历史并带入授权表单。</p>
        )}
        <div className="event-list">
          {memory.records.map((record) => (
            <div key={record.record_id} className="event-item">
              <div>
                <strong>{record.summary || record.subject_key || record.record_id}</strong>
                <p>
                  {formatMemoryLayer(record.layer)} / {formatMemoryPartition(record.partition)} /{" "}
                  {record.scope_id}
                </p>
                <small>
                  subject={record.subject_key || "-"} | 状态={record.status} | 版本=
                  {record.version ?? "-"}
                </small>
              </div>
              <div className="action-row">
                <span
                  className={`tone-chip ${
                    record.requires_vault_authorization ? "warning" : "neutral"
                  }`}
                >
                  {record.requires_vault_authorization ? "需授权" : "普通记录"}
                </span>
                {record.subject_key ? (
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() => onFocusMemoryRecord(record)}
                    disabled={memoryBusy}
                  >
                    查看历史
                  </button>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </article>

      <div className="section-grid">
        <article className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">这条内容的变化 / Subject History</p>
              <h3>{selectedSubjectHistory?.subject_key || "未选择条目"}</h3>
            </div>
            <span className="tone-chip neutral">
              {selectedSubjectHistory?.history.length ?? 0} history
            </span>
          </div>
          {selectedSubjectHistory ? (
            <>
              <p>
                当前版本:{" "}
                <strong>
                  {selectedSubjectHistory.current_record?.summary ||
                    selectedSubjectHistory.current_record?.record_id ||
                    "无 current"}
                </strong>
              </p>
              <div className="event-list">
                {selectedSubjectHistory.history.map((record) => (
                  <div key={record.record_id} className="event-item">
                    <div>
                      <strong>{record.summary || record.record_id}</strong>
                      <p>
                        {record.status} / v{record.version ?? "-"} /{" "}
                        {formatDateTime(record.updated_at || record.created_at)}
                      </p>
                    </div>
                    <small>{formatMemoryPartition(record.partition)}</small>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="muted">从上方结果里选一条记录，系统会自动带入历史和授权信息。</p>
          )}
        </article>

        <article className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">写入建议 / WriteProposal Audit</p>
              <h3>{memoryProposals?.items.length ?? 0}</h3>
            </div>
            <span className="tone-chip neutral">
              Pending {memoryProposals?.summary.pending ?? 0}
            </span>
          </div>
          <div className="meta-grid">
            <span>已校验 {memoryProposals?.summary.validated ?? 0}</span>
            <span>已拒绝 {memoryProposals?.summary.rejected ?? 0}</span>
            <span>已提交 {memoryProposals?.summary.committed ?? 0}</span>
          </div>
          <div className="event-list">
            {(memoryProposals?.items ?? []).map((item) => (
              <div key={item.proposal_id} className="event-item">
                <div>
                  <strong>{item.subject_key || item.proposal_id}</strong>
                  <p>
                    {item.action} / {formatMemoryPartition(item.partition)} / {item.scope_id}
                  </p>
                  <small>{item.rationale || "没有额外说明"}</small>
                </div>
                <small>{item.status}</small>
              </div>
            ))}
          </div>
        </article>

        <article className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">授权申请 / Vault Authorization</p>
              <h3>{vaultAuthorization?.active_requests.length ?? 0}</h3>
            </div>
            <span className="tone-chip neutral">
              Grants {vaultAuthorization?.active_grants.length ?? 0}
            </span>
          </div>
          <div className="event-list">
            {(vaultAuthorization?.active_requests ?? []).map((item) => (
              <div key={item.request_id} className="event-item">
                <div>
                  <strong>{item.subject_key || item.request_id}</strong>
                  <p>
                    {item.scope_id} / {formatMemoryPartition(item.partition || "")} / {item.status}
                  </p>
                  <small>{item.reason || "未填写理由"}</small>
                </div>
                <div className="action-row">
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => onResolveVaultRequest(item.request_id, "approve")}
                    disabled={busyActionId === "vault.access.resolve"}
                  >
                    批准授权
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() => onResolveVaultRequest(item.request_id, "reject")}
                    disabled={busyActionId === "vault.access.resolve"}
                  >
                    拒绝
                  </button>
                </div>
              </div>
            ))}
          </div>
          <div className="meta-grid">
            {(vaultAuthorization?.active_grants ?? []).slice(0, 4).map((grant) => (
              <span key={grant.grant_id}>
                {grant.subject_key || grant.grant_id}: {grant.status}
              </span>
            ))}
          </div>
        </article>
      </div>

      <div className="section-grid">
        <article className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">申请查看敏感内容</p>
              <h3>先申请，再查看</h3>
            </div>
            <span className="tone-chip neutral">
              Requests {vaultAuthorization?.active_requests.length ?? 0}
            </span>
          </div>
          <p className="muted">
            选中上方记录后，这里会自动带入 scope、内容类型和目标条目。只需要补充查看原因。
          </p>
          <div className="form-grid">
            <label>
              申请范围
              <select
                value={memoryAccessDraft.scopeId}
                onChange={(event) => onUpdateMemoryAccessDraft("scopeId", event.target.value)}
              >
                <option value="">沿用当前结果范围</option>
                {memoryScopeOptions.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <label>
              申请内容类型
              <select
                value={memoryAccessDraft.partition}
                onChange={(event) =>
                  onUpdateMemoryAccessDraft("partition", event.target.value)
                }
              >
                <option value="">未指定</option>
                {memoryPartitionOptions.map((item) => (
                  <option key={item} value={item}>
                    {formatMemoryPartition(item)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              申请目标条目
              <input
                value={memoryAccessDraft.subjectKey}
                onChange={(event) =>
                  onUpdateMemoryAccessDraft("subjectKey", event.target.value)
                }
                placeholder="例如 credential:db"
              />
            </label>
            <label>
              申请原因
              <input
                value={memoryAccessDraft.reason}
                onChange={(event) => onUpdateMemoryAccessDraft("reason", event.target.value)}
                placeholder="例如 临时排障、核对配置"
              />
            </label>
          </div>
          <div className="action-row">
            <button
              type="button"
              className="secondary-button"
              onClick={onRequestVaultAccess}
              disabled={busyActionId === "vault.access.request"}
            >
              发起授权申请
            </button>
          </div>
        </article>

        <article className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">检索敏感内容</p>
              <h3>有授权后再精确搜索</h3>
            </div>
            <span className="tone-chip neutral">
              Retrievals {vaultAuthorization?.recent_retrievals.length ?? 0}
            </span>
          </div>
          <p className="muted">
            如果只想查看某条敏感记录，直接填目标条目；如果结果较多，再用关键词缩小范围。
          </p>
          <div className="form-grid">
            <label>
              检索范围
              <select
                value={memoryRetrieveDraft.scopeId}
                onChange={(event) =>
                  onUpdateMemoryRetrieveDraft("scopeId", event.target.value)
                }
              >
                <option value="">沿用当前结果范围</option>
                {memoryScopeOptions.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <label>
              检索内容类型
              <select
                value={memoryRetrieveDraft.partition}
                onChange={(event) =>
                  onUpdateMemoryRetrieveDraft("partition", event.target.value)
                }
              >
                <option value="">未指定</option>
                {memoryPartitionOptions.map((item) => (
                  <option key={item} value={item}>
                    {formatMemoryPartition(item)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              检索目标条目
              <input
                value={memoryRetrieveDraft.subjectKey}
                onChange={(event) =>
                  onUpdateMemoryRetrieveDraft("subjectKey", event.target.value)
                }
                placeholder="例如 credential:db"
              />
            </label>
            <label>
              检索关键词
              <input
                value={memoryRetrieveDraft.query}
                onChange={(event) => onUpdateMemoryRetrieveDraft("query", event.target.value)}
                placeholder="例如 password / Database"
              />
            </label>
          </div>
          <details className="disclosure-card">
            <summary>高级参数</summary>
            <div className="form-grid">
              <label>
                Grant ID
                <input
                  value={memoryRetrieveDraft.grantId}
                  onChange={(event) =>
                    onUpdateMemoryRetrieveDraft("grantId", event.target.value)
                  }
                  placeholder="留空则自动匹配"
                />
              </label>
            </div>
          </details>
          <div className="action-row">
            <button
              type="button"
              className="secondary-button"
              onClick={onRetrieveVault}
              disabled={busyActionId === "vault.retrieve"}
            >
              执行 Vault 检索
            </button>
          </div>
          <div className="meta-grid">
            {(vaultAuthorization?.recent_retrievals ?? []).slice(0, 4).map((item) => (
              <span key={item.retrieval_id}>
                {item.subject_key || item.retrieval_id}: {item.reason_code} / {item.result_count}
              </span>
            ))}
          </div>
        </article>
      </div>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">高级工具</p>
            <h3>导出与恢复</h3>
          </div>
          <span className="tone-chip neutral">仅在需要迁移、审计或恢复时使用</span>
        </div>
        <details className="disclosure-card">
          <summary>打开导出与恢复参数</summary>
          <div className="form-grid">
            <label>
              导出 Scope IDs
              <input
                value={memoryExportDraft.scopeIds}
                onChange={(event) => onUpdateMemoryExportDraft("scopeIds", event.target.value)}
                placeholder="scope-a,scope-b"
              />
            </label>
            <label className="checkbox-line">
              <input
                type="checkbox"
                checked={memoryExportDraft.includeHistory}
                onChange={(event) =>
                  onUpdateMemoryExportDraft("includeHistory", event.target.checked)
                }
              />
              导出包含历史
            </label>
            <label className="checkbox-line">
              <input
                type="checkbox"
                checked={memoryExportDraft.includeVaultRefs}
                onChange={(event) =>
                  onUpdateMemoryExportDraft("includeVaultRefs", event.target.checked)
                }
              />
              导出包含 Vault 引用
            </label>
            <label>
              Snapshot Ref
              <input
                value={memoryRestoreDraft.snapshotRef}
                onChange={(event) =>
                  onUpdateMemoryRestoreDraft("snapshotRef", event.target.value)
                }
                placeholder="/path/to/memory-export.zip"
              />
            </label>
            <label>
              Restore Scope Mode
              <input
                value={memoryRestoreDraft.targetScopeMode}
                onChange={(event) =>
                  onUpdateMemoryRestoreDraft("targetScopeMode", event.target.value)
                }
                placeholder="current_project"
              />
            </label>
            <label>
              Restore Scope IDs
              <input
                value={memoryRestoreDraft.scopeIds}
                onChange={(event) =>
                  onUpdateMemoryRestoreDraft("scopeIds", event.target.value)
                }
                placeholder="scope-a,scope-b"
              />
            </label>
          </div>
          <div className="action-row">
            <button
              type="button"
              className="ghost-button"
              onClick={onInspectMemoryExport}
              disabled={busyActionId === "memory.export.inspect"}
            >
              Export Inspect
            </button>
            <button
              type="button"
              className="ghost-button"
              onClick={onVerifyMemoryRestore}
              disabled={busyActionId === "memory.restore.verify"}
            >
              Restore Verify
            </button>
          </div>
        </details>
      </article>

      {lastMemoryAction ? (
        <article className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">Latest Memory Action</p>
              <h3>{lastMemoryAction.action_id}</h3>
            </div>
            <span className={`tone-chip ${statusTone(lastMemoryAction.status)}`}>
              {lastMemoryAction.code}
            </span>
          </div>
          <pre className="config-editor">{formatJson(lastMemoryAction.data)}</pre>
        </article>
      ) : null}
    </section>
  );
}
