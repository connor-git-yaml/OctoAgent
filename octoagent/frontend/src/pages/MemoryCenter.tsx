import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import type {
  MemoryRecordProjection,
  OperatorActionKind,
  OperatorInboxItem,
  RecoverySummary,
} from "../types";
import { formatDateTime } from "../workbench/utils";

const LAYER_LABELS: Record<string, string> = {
  sor: "现行事实",
  fragment: "片段",
  vault: "Vault 引用",
  derived: "派生记忆",
};

const BACKEND_STATE_LABELS: Record<string, string> = {
  healthy: "健康",
  degraded: "降级",
  unavailable: "不可用",
  syncing: "同步中",
};

function formatLayerLabel(layer: string): string {
  return LAYER_LABELS[layer] ?? layer;
}

function formatBackendState(state: string): string {
  return BACKEND_STATE_LABELS[state] ?? (state || "未标记");
}

function formatRecordTitle(record: MemoryRecordProjection): string {
  if (record.subject_key) {
    return record.subject_key;
  }
  if (record.layer === "fragment") {
    return `片段 ${record.record_id.slice(0, 8)}`;
  }
  return `记录 ${record.record_id.slice(0, 8)}`;
}

function formatRecordStatus(record: MemoryRecordProjection): string {
  if (record.layer === "sor" && record.status === "current") {
    return "当前版本";
  }
  if (record.layer === "sor" && record.status === "history") {
    return "历史版本";
  }
  if (record.status) {
    return record.status;
  }
  return formatLayerLabel(record.layer);
}

function describeRecord(record: MemoryRecordProjection): string {
  if (record.summary.trim()) {
    return record.summary;
  }
  if (record.layer === "fragment") {
    return "这是一条尚未归并成现行事实的记忆片段。";
  }
  if (record.layer === "vault") {
    return "这是一条受控的 Vault 引用，需要授权后才能读取明细。";
  }
  if (record.layer === "derived") {
    return "这是一条从已有记忆派生出的摘要或判断。";
  }
  return "这条记录当前还没有可展示的摘要文本。";
}

function uniqueOptions(values: Array<string | undefined>): string[] {
  return values.filter(Boolean).filter((value, index, all) => all.indexOf(value) === index) as string[];
}

function metadataPreviewEntries(record: MemoryRecordProjection): Array<[string, string]> {
  return Object.entries(record.metadata)
    .filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== "")
    .slice(0, 4)
    .map(([key, value]) => [key, String(value)]);
}

function formatRecoveryTime(value: string | null | undefined): string {
  if (!value) {
    return "未记录";
  }
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function renderOperatorMeta(item: OperatorInboxItem): string {
  if (item.kind === "approval") {
    return item.metadata.tool_name || item.source_ref;
  }
  if (item.kind === "pairing_request") {
    return item.metadata.username || item.metadata.user_id || item.source_ref;
  }
  if (item.kind === "retryable_failure") {
    return item.metadata.error_type || item.source_ref;
  }
  return item.metadata.journal_state || item.source_ref;
}

function mapQuickAction(
  item: OperatorInboxItem,
  kind: OperatorActionKind
): { actionId: string; params: Record<string, unknown> } | null {
  if (kind === "approve_once") {
    return {
      actionId: "operator.approval.resolve",
      params: {
        approval_id: item.item_id.split(":")[1] ?? "",
        mode: "once",
      },
    };
  }
  if (kind === "approve_always") {
    return {
      actionId: "operator.approval.resolve",
      params: {
        approval_id: item.item_id.split(":")[1] ?? "",
        mode: "always",
      },
    };
  }
  if (kind === "deny") {
    return {
      actionId: "operator.approval.resolve",
      params: {
        approval_id: item.item_id.split(":")[1] ?? "",
        mode: "deny",
      },
    };
  }
  if (kind === "cancel_task") {
    return { actionId: "operator.task.cancel", params: { item_id: item.item_id } };
  }
  if (kind === "retry_task") {
    return { actionId: "operator.task.retry", params: { item_id: item.item_id } };
  }
  if (kind === "ack_alert") {
    return { actionId: "operator.alert.ack", params: { item_id: item.item_id } };
  }
  if (kind === "approve_pairing") {
    return { actionId: "channel.pairing.approve", params: { item_id: item.item_id } };
  }
  if (kind === "reject_pairing") {
    return { actionId: "channel.pairing.reject", params: { item_id: item.item_id } };
  }
  return null;
}

export default function MemoryCenter() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const memory = snapshot!.resources.memory;
  const diagnostics = snapshot!.resources.diagnostics;
  const sessions = snapshot!.resources.sessions;
  const [queryDraft, setQueryDraft] = useState(memory.filters.query);
  const [layerDraft, setLayerDraft] = useState(memory.filters.layer);
  const [partitionDraft, setPartitionDraft] = useState(memory.filters.partition);
  const [includeHistoryDraft, setIncludeHistoryDraft] = useState(memory.filters.include_history);
  const [includeVaultRefsDraft, setIncludeVaultRefsDraft] = useState(
    memory.filters.include_vault_refs
  );
  const [limitDraft, setLimitDraft] = useState(String(memory.filters.limit || 50));

  useEffect(() => {
    setQueryDraft(memory.filters.query);
    setLayerDraft(memory.filters.layer);
    setPartitionDraft(memory.filters.partition);
    setIncludeHistoryDraft(memory.filters.include_history);
    setIncludeVaultRefsDraft(memory.filters.include_vault_refs);
    setLimitDraft(String(memory.filters.limit || 50));
  }, [memory.filters]);

  const layerOptions = uniqueOptions([
    "",
    ...memory.available_layers,
    memory.filters.layer,
    "sor",
    "fragment",
    "vault",
    "derived",
  ]);
  const partitionOptions = uniqueOptions(["", ...memory.available_partitions, memory.filters.partition]);
  const healthEntries = Object.entries(memory.index_health ?? {}).slice(0, 4);
  const totalReadableRecords =
    memory.summary.sor_current_count +
    memory.summary.fragment_count +
    memory.summary.vault_ref_count +
    memory.summary.pending_replay_count;
  const heroTitle =
    memory.records.length > 0
      ? `系统当前记住了 ${memory.summary.sor_current_count} 条现行事实`
      : totalReadableRecords > 0
        ? "当前有记忆数据，但还没有命中可读摘要"
        : "还没有形成可读的记忆摘要";
  const heroSummary =
    memory.records.length > 0
      ? "先看系统现在记得什么，再看它来自哪个 layer、有没有证据、是否需要授权。"
      : "没有记录时，通常是因为还没发生聊天/导入，或当前筛选条件太窄。";
  const operatorItems = sessions.operator_items ?? [];
  const operatorSummary = sessions.operator_summary;
  const recoverySummary = diagnostics.recovery_summary as Partial<RecoverySummary>;
  const focusedSession =
    sessions.sessions.find((item) => item.session_id === sessions.focused_session_id) ?? null;
  const canExportFocusedSession = Boolean(sessions.focused_session_id || sessions.focused_thread_id);
  const exportTargetLabel =
    focusedSession?.title || sessions.focused_thread_id || sessions.focused_session_id || "未选中会话";

  async function handleOperatorAction(item: OperatorInboxItem, kind: OperatorActionKind) {
    const mapped = mapQuickAction(item, kind);
    if (!mapped) {
      return;
    }
    await submitAction(mapped.actionId, mapped.params);
  }

  async function refreshRecoverySummary() {
    await submitAction("diagnostics.refresh", {});
  }

  async function handleBackupCreate() {
    await submitAction("backup.create", { label: "memory-center" });
  }

  async function handleExportChats() {
    if (!canExportFocusedSession) {
      return;
    }
    const exportParams = sessions.focused_session_id
      ? { session_id: sessions.focused_session_id }
      : { thread_id: sessions.focused_thread_id || undefined };
    await submitAction("session.export", {
      ...exportParams,
    });
  }

  async function refreshMemory() {
    await submitAction("memory.query", {
      project_id: memory.active_project_id,
      workspace_id: memory.active_workspace_id,
      query: queryDraft.trim(),
      layer: layerDraft,
      partition: partitionDraft,
      include_history: includeHistoryDraft,
      include_vault_refs: includeVaultRefsDraft,
      limit: Number(limitDraft) || 50,
    });
  }

  async function resetFilters() {
    setQueryDraft("");
    setLayerDraft("");
    setPartitionDraft("");
    setIncludeHistoryDraft(false);
    setIncludeVaultRefsDraft(false);
    setLimitDraft("50");
    await submitAction("memory.query", {
      project_id: memory.active_project_id,
      workspace_id: memory.active_workspace_id,
      query: "",
      layer: "",
      partition: "",
      include_history: false,
      include_vault_refs: false,
      limit: 50,
    });
  }

  return (
    <div className="wb-page">
      <section className="wb-hero wb-hero-memory">
        <div className="wb-hero-copy">
          <p className="wb-kicker">Memory</p>
          <h1>{heroTitle}</h1>
          <p>{heroSummary}</p>
          <div className="wb-chip-row">
            <span className="wb-chip">Backend {memory.backend_id || "未配置"}</span>
            <span
              className={`wb-chip ${
                memory.backend_state === "healthy" ? "is-success" : "is-warning"
              }`}
            >
              状态 {formatBackendState(memory.backend_state)}
            </span>
            <span className="wb-chip">Scope {memory.summary.scope_count}</span>
            <span className="wb-chip">当前展示 {memory.records.length}</span>
          </div>
        </div>

        <div className="wb-hero-insights">
          <article className="wb-hero-metric">
            <p className="wb-card-label">Current SoR</p>
            <strong>{memory.summary.sor_current_count}</strong>
            <span>现行事实是最适合直接解释给用户的记忆层</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">Fragments</p>
            <strong>{memory.summary.fragment_count}</strong>
            <span>片段多时，通常代表还需要归并或 flush</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">History / Replay</p>
            <strong>
              {memory.summary.sor_history_count + memory.summary.pending_replay_count}
            </strong>
            <span>
              历史版本 {memory.summary.sor_history_count} / 待回放{" "}
              {memory.summary.pending_replay_count}
            </span>
          </article>
        </div>
      </section>

      {memory.warnings.length > 0 ? (
        <div className="wb-inline-banner is-error">
          <strong>Memory 当前存在提醒</strong>
          <span>{memory.warnings.join("；")}</span>
        </div>
      ) : null}

      <section className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">筛选与刷新</p>
            <h3>先把视图调成你真正想看的记忆层</h3>
          </div>
          <div className="wb-inline-actions">
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={() => void resetFilters()}
              disabled={busyActionId === "memory.query"}
            >
              清空筛选
            </button>
            <button
              type="button"
              className="wb-button wb-button-primary"
              onClick={() => void refreshMemory()}
              disabled={busyActionId === "memory.query"}
            >
              刷新摘要
            </button>
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={() =>
                void submitAction("memory.flush", {
                  project_id: memory.active_project_id,
                  workspace_id: memory.active_workspace_id,
                })
              }
              disabled={busyActionId === "memory.flush"}
            >
              触发 flush
            </button>
          </div>
        </div>

        <div className="wb-toolbar-grid">
          <label className="wb-field">
            <span>关键词</span>
            <input
              type="text"
              value={queryDraft}
              placeholder="例如：客户偏好、发布计划、数据库"
              onChange={(event) => setQueryDraft(event.target.value)}
            />
          </label>

          <label className="wb-field">
            <span>Layer</span>
            <select value={layerDraft} onChange={(event) => setLayerDraft(event.target.value)}>
              {layerOptions.map((option) => (
                <option key={option || "all-layers"} value={option}>
                  {option ? formatLayerLabel(option) : "全部 Layer"}
                </option>
              ))}
            </select>
          </label>

          <label className="wb-field">
            <span>Partition</span>
            <select
              value={partitionDraft}
              onChange={(event) => setPartitionDraft(event.target.value)}
            >
              {partitionOptions.map((option) => (
                <option key={option || "all-partitions"} value={option}>
                  {option || "全部 Partition"}
                </option>
              ))}
            </select>
          </label>

          <label className="wb-field">
            <span>Limit</span>
            <select value={limitDraft} onChange={(event) => setLimitDraft(event.target.value)}>
              {["20", "50", "100"].map((option) => (
                <option key={option} value={option}>
                  {option} 条
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="wb-toggle-row">
          <label className="wb-toggle">
            <input
              type="checkbox"
              checked={includeHistoryDraft}
              onChange={(event) => setIncludeHistoryDraft(event.target.checked)}
            />
            <span>包含历史版本</span>
          </label>
          <label className="wb-toggle">
            <input
              type="checkbox"
              checked={includeVaultRefsDraft}
              onChange={(event) => setIncludeVaultRefsDraft(event.target.checked)}
            />
            <span>包含 Vault 引用</span>
          </label>
          <span className="wb-panel-copy">
            当前检索后端: {memory.retrieval_backend || "未标记"}，更新时间{" "}
            {formatDateTime(memory.updated_at)}
          </span>
        </div>
      </section>

      <div className="wb-memory-layout">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">当前记录</p>
              <h3>{memory.records.length} 条可读摘要</h3>
            </div>
            <div className="wb-chip-row">
              <span className="wb-chip">Proposals {memory.summary.proposal_count}</span>
              <span className="wb-chip">Vault refs {memory.summary.vault_ref_count}</span>
            </div>
          </div>

          {memory.records.length === 0 ? (
            <div className="wb-empty-state">
              <strong>当前没有命中的记忆摘要</strong>
              <span>可以先清空筛选，或者去 Chat / 导入通道产生新的上下文。</span>
              <div className="wb-inline-actions">
                <Link className="wb-button wb-button-primary" to="/chat">
                  去 Chat
                </Link>
                <button
                  type="button"
                  className="wb-button wb-button-secondary"
                  onClick={() => void resetFilters()}
                  disabled={busyActionId === "memory.query"}
                >
                  重置并重查
                </button>
              </div>
            </div>
          ) : (
            <div className="wb-record-list">
              {memory.records.map((record) => {
                const metadataEntries = metadataPreviewEntries(record);

                return (
                  <article key={record.record_id} className="wb-memory-card">
                    <div className="wb-memory-head">
                      <div>
                        <div className="wb-chip-row">
                          <span className="wb-chip">{formatLayerLabel(record.layer)}</span>
                          <span className="wb-chip">{record.partition}</span>
                          <span className="wb-chip">{record.scope_id}</span>
                          {record.requires_vault_authorization ? (
                            <span className="wb-chip is-warning">需授权</span>
                          ) : null}
                        </div>
                        <strong>{formatRecordTitle(record)}</strong>
                        <p>{describeRecord(record)}</p>
                      </div>
                      <div className="wb-list-meta">
                        <span className={`wb-status-pill is-${record.status.toLowerCase()}`}>
                          {formatRecordStatus(record)}
                        </span>
                        <small>{formatDateTime(record.updated_at ?? record.created_at)}</small>
                      </div>
                    </div>

                    <div className="wb-chip-row">
                      <span className="wb-chip">证据 {record.evidence_refs.length}</span>
                      <span className="wb-chip">Proposal {record.proposal_refs.length}</span>
                      <span className="wb-chip">Derived {record.derived_refs.length}</span>
                      {record.version !== null ? (
                        <span className="wb-chip">Version {record.version}</span>
                      ) : null}
                      {record.retrieval_backend ? (
                        <span className="wb-chip">{record.retrieval_backend}</span>
                      ) : null}
                    </div>

                    {metadataEntries.length > 0 ? (
                      <div className="wb-key-value-list">
                        {metadataEntries.map(([key, value]) => (
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

        <div className="wb-section-stack">
          <section className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">当前视图</p>
                <h3>理解你现在看到的是哪一层</h3>
              </div>
            </div>

            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>Active Scope</strong>
                <span>
                  {memory.available_scopes.length > 0
                    ? memory.available_scopes.join(" / ")
                    : "当前没有 scope 命中。"}
                </span>
              </div>
              <div className="wb-note">
                <strong>可用 Layer</strong>
                <div className="wb-chip-row">
                  {layerOptions
                    .filter(Boolean)
                    .map((layer) => (
                      <span key={layer} className="wb-chip">
                        {formatLayerLabel(layer)}
                      </span>
                    ))}
                </div>
              </div>
              <div className="wb-note">
                <strong>可用 Partition</strong>
                <div className="wb-chip-row">
                  {partitionOptions.filter(Boolean).length > 0 ? (
                    partitionOptions
                      .filter(Boolean)
                      .map((partition) => (
                        <span key={partition} className="wb-chip">
                          {partition}
                        </span>
                      ))
                  ) : (
                    <span>当前记录里还没有可枚举的 partition。</span>
                  )}
                </div>
              </div>
            </div>
          </section>

          <section className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">健康与建议</p>
                <h3>先判断 Memory 是空，还是坏，还是筛错了</h3>
              </div>
            </div>

            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>Backend</strong>
                <span>
                  {memory.backend_id || "未标记"} / {formatBackendState(memory.backend_state)}
                </span>
              </div>
              <div className="wb-note">
                <strong>导向建议</strong>
                <span>
                  {memory.records.length > 0
                    ? "优先阅读现行事实和需要授权的条目，再决定是否看历史版本。"
                    : "如果这里持续为空，先去 Chat 产生上下文，或执行一次 flush / 导入。"}
                </span>
              </div>
              <div className="wb-note">
                <strong>Index Health</strong>
                {healthEntries.length > 0 ? (
                  <div className="wb-key-value-list">
                    {healthEntries.map(([key, value]) => (
                      <div key={key} className="wb-key-value-item">
                        <span>{key}</span>
                        <strong>{String(value)}</strong>
                      </div>
                    ))}
                  </div>
                ) : (
                  <span>当前没有暴露额外的索引健康信息。</span>
                )}
              </div>
            </div>
          </section>
        </div>
      </div>

      <div className="wb-split">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">Operator</p>
              <h3>处理会影响记忆与上下文的待确认事项</h3>
            </div>
            <div className="wb-chip-row">
              <span className="wb-chip">Pending {operatorSummary?.total_pending ?? 0}</span>
              <span className="wb-chip">Approvals {operatorSummary?.approvals ?? 0}</span>
              <span className="wb-chip">Pairings {operatorSummary?.pairing_requests ?? 0}</span>
            </div>
          </div>

          {operatorItems.length === 0 ? (
            <div className="wb-empty-state">
              <strong>当前没有待处理的 operator 工作项</strong>
              <span>如果后续出现 Vault 授权、审批或配对请求，这里会直接显示。</span>
            </div>
          ) : (
            <div className="wb-note-stack">
              {operatorItems.slice(0, 6).map((item) => (
                <div key={item.item_id} className="wb-note">
                  <strong>{item.title}</strong>
                  <span>{item.summary}</span>
                  <small>
                    {item.kind} · {renderOperatorMeta(item)} · {formatRecoveryTime(item.created_at)}
                  </small>
                  <div className="wb-inline-actions wb-inline-actions-wrap">
                    {item.quick_actions.map((action) => (
                      <button
                        key={`${item.item_id}-${action.kind}`}
                        type="button"
                        className={
                          action.style === "primary"
                            ? "wb-button wb-button-primary"
                            : "wb-button wb-button-secondary"
                        }
                        disabled={!action.enabled || busyActionId === mapQuickAction(item, action.kind)?.actionId}
                        onClick={() => void handleOperatorAction(item, action.kind)}
                      >
                        {action.label}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">Export & Recovery</p>
              <h3>把当前成果导出，并确认恢复准备度</h3>
            </div>
            <div className="wb-inline-actions">
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={() => void refreshRecoverySummary()}
                disabled={busyActionId === "diagnostics.refresh"}
              >
                刷新恢复状态
              </button>
            </div>
          </div>

          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>最近备份</strong>
              <span>{recoverySummary?.latest_backup?.output_path ?? "尚未创建备份"}</span>
              <small>{formatRecoveryTime(recoverySummary?.latest_backup?.created_at)}</small>
            </div>
            <div className="wb-note">
              <strong>恢复准备度</strong>
              <span>{recoverySummary?.ready_for_restore ? "READY" : "NOT READY"}</span>
              <small>
                {recoverySummary?.latest_recovery_drill?.summary ?? "尚未执行恢复演练。"}
              </small>
            </div>
            <div className="wb-note">
              <strong>导出当前会话</strong>
              <span>{exportTargetLabel}</span>
              <small>
                {canExportFocusedSession
                  ? "使用 control-plane 的 session.export 导出当前聚焦会话。"
                  : "先在 Chat 或 Work 中聚焦一个会话，这里才会启用导出。"}
              </small>
            </div>
          </div>

          <div className="wb-inline-actions wb-inline-actions-wrap">
            <button
              type="button"
              className="wb-button wb-button-primary"
              onClick={() => void handleBackupCreate()}
              disabled={busyActionId === "backup.create"}
            >
              {busyActionId === "backup.create" ? "创建中..." : "创建备份"}
            </button>
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={() => void handleExportChats()}
              disabled={!canExportFocusedSession || busyActionId === "session.export"}
            >
              {busyActionId === "session.export" ? "导出中..." : "导出当前会话"}
            </button>
            <Link className="wb-button wb-button-tertiary" to="/advanced">
              打开 Advanced Recovery
            </Link>
          </div>
        </section>
      </div>
    </div>
  );
}
