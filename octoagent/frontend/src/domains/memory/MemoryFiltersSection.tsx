interface MemoryFiltersSectionProps {
  queryDraft: string;
  layerDraft: string;
  partitionDraft: string;
  includeHistoryDraft: boolean;
  includeVaultRefsDraft: boolean;
  limitDraft: string;
  layerOptions: string[];
  partitionOptions: string[];
  retrievalLabel: string;
  updatedAt: string;
  busyActionId: string | null;
  onQueryChange: (value: string) => void;
  onLayerChange: (value: string) => void;
  onPartitionChange: (value: string) => void;
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
  queryDraft,
  layerDraft,
  partitionDraft,
  includeHistoryDraft,
  includeVaultRefsDraft,
  limitDraft,
  layerOptions,
  partitionOptions,
  retrievalLabel,
  updatedAt,
  busyActionId,
  onQueryChange,
  onLayerChange,
  onPartitionChange,
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
