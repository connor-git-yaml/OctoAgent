const SCOPE_LABELS: Record<string, string> = {
  project_shared: "项目共享",
  butler_private: "Butler 私有",
  worker_private: "Worker 私有",
};

function formatScopeLabel(scopeId: string): string {
  if (!scopeId) return "全部作用域";
  // 尝试从 scope_id 中提取 kind 标签
  const lower = scopeId.toLowerCase();
  if (lower.includes("/shared/") || lower.includes("project_shared"))
    return SCOPE_LABELS.project_shared;
  if (lower.includes("butler_private") || lower.includes("/butler/"))
    return SCOPE_LABELS.butler_private;
  if (lower.includes("/private/")) return SCOPE_LABELS.worker_private;
  // 截取最后一段作为可读标签
  const parts = scopeId.split("/");
  return parts[parts.length - 1] || scopeId;
}

const STATUS_LABELS: Record<string, string> = {
  "": "当前有效",
  current: "当前有效",
  archived: "已归档",
  all: "全部状态",
};

interface MemoryFiltersSectionProps {
  scopeDraft: string;
  scopeOptions: string[];
  queryDraft: string;
  layerDraft: string;
  partitionDraft: string;
  statusDraft?: string;
  includeHistoryDraft: boolean;
  includeVaultRefsDraft: boolean;
  limitDraft: string;
  layerOptions: string[];
  partitionOptions: string[];
  retrievalLabel: string;
  updatedAt: string;
  busyActionId: string | null;
  onScopeChange: (value: string) => void;
  onQueryChange: (value: string) => void;
  onLayerChange: (value: string) => void;
  onPartitionChange: (value: string) => void;
  onStatusChange?: (value: string) => void;
  onIncludeHistoryChange: (value: boolean) => void;
  onIncludeVaultRefsChange: (value: boolean) => void;
  onLimitChange: (value: string) => void;
  onResetFilters: () => Promise<void>;
  onRefreshMemory: () => Promise<void>;
  formatLayerLabel: (layer: string) => string;
  formatPartitionLabel: (partition: string) => string;
  formatDateTime: (value?: string | null) => string;
}

export default function MemoryFiltersSection({
  scopeDraft,
  scopeOptions,
  queryDraft,
  layerDraft,
  partitionDraft,
  statusDraft,
  includeHistoryDraft,
  includeVaultRefsDraft,
  limitDraft,
  layerOptions,
  partitionOptions,
  retrievalLabel,
  updatedAt,
  busyActionId,
  onScopeChange,
  onQueryChange,
  onLayerChange,
  onPartitionChange,
  onStatusChange,
  onIncludeHistoryChange,
  onIncludeVaultRefsChange,
  onLimitChange,
  onResetFilters,
  onRefreshMemory,
  formatLayerLabel,
  formatPartitionLabel,
  formatDateTime,
}: MemoryFiltersSectionProps) {
  return (
    <section className="wb-panel">
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">筛选与刷新</p>
          <h3>调整这次想看的记忆范围</h3>
        </div>
        <div className="wb-inline-actions">
          <button
            type="button"
            className="wb-button wb-button-secondary"
            onClick={() => void onResetFilters()}
            disabled={busyActionId === "memory.query"}
          >
            清空筛选
          </button>
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={() => void onRefreshMemory()}
            disabled={busyActionId === "memory.query"}
          >
            重新查看
          </button>
        </div>
      </div>

      <div className="wb-toolbar-grid">
        {scopeOptions.length > 1 ? (
          <label className="wb-field">
            <span>作用域</span>
            <select value={scopeDraft} onChange={(event) => onScopeChange(event.target.value)}>
              {scopeOptions.map((option) => (
                <option key={option || "all-scopes"} value={option}>
                  {formatScopeLabel(option)}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        <label className="wb-field">
          <span>关键词</span>
          <input
            type="text"
            value={queryDraft}
            placeholder="例如：客户偏好、发布计划、数据库"
            onChange={(event) => onQueryChange(event.target.value)}
          />
        </label>

        <label className="wb-field">
          <span>记忆类型</span>
          <select value={layerDraft} onChange={(event) => onLayerChange(event.target.value)}>
            {layerOptions.map((option) => (
              <option key={option || "all-layers"} value={option}>
                {option ? formatLayerLabel(option) : "全部类型"}
              </option>
            ))}
          </select>
        </label>

        <label className="wb-field">
          <span>主题分区</span>
          <select value={partitionDraft} onChange={(event) => onPartitionChange(event.target.value)}>
            {partitionOptions.map((option) => (
              <option key={option || "all-partitions"} value={option}>
                {option ? formatPartitionLabel(option) : "全部分区"}
              </option>
            ))}
          </select>
        </label>

        {/* T038: status 筛选选项 */}
        {onStatusChange ? (
          <label className="wb-field">
            <span>记忆状态</span>
            <select value={statusDraft || ""} onChange={(event) => onStatusChange(event.target.value)}>
              {Object.entries(STATUS_LABELS).map(([value, label]) => (
                <option key={value || "default-status"} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        <label className="wb-field">
          <span>最多显示</span>
          <select value={limitDraft} onChange={(event) => onLimitChange(event.target.value)}>
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
            onChange={(event) => onIncludeHistoryChange(event.target.checked)}
          />
          <span>包含历史版本</span>
        </label>
        <label className="wb-toggle">
          <input
            type="checkbox"
            checked={includeVaultRefsDraft}
            onChange={(event) => onIncludeVaultRefsChange(event.target.checked)}
          />
          <span>包含受保护引用</span>
        </label>
        <span className="wb-panel-copy">
          当前检索方式：{retrievalLabel}，更新时间 {formatDateTime(updatedAt)}
        </span>
      </div>
    </section>
  );
}
