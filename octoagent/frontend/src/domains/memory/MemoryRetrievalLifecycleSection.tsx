import type {
  IndexBuildJob,
  IndexGeneration,
  RetrievalCorpusState,
} from "../../types";

interface MemoryRetrievalLifecycleSectionProps {
  memoryCorpus: RetrievalCorpusState | null;
  activeGeneration: IndexGeneration | null;
  pendingGeneration: IndexGeneration | null;
  pendingBuildJob: IndexBuildJob | null;
  rollbackCandidate: IndexGeneration | null;
  busyActionId: string | null;
  onStartMigration: () => Promise<void>;
  onCancelMigration: (generationId: string) => Promise<void>;
  onCutoverMigration: (generationId: string) => Promise<void>;
  onRollbackMigration: (generationId: string) => Promise<void>;
}

function resolveIndexStageLabel(stage: string): string {
  switch (stage) {
    case "queued":
      return "待开始";
    case "scanning":
      return "扫描中";
    case "embedding":
      return "生成向量中";
    case "writing_projection":
      return "写入索引";
    case "catching_up":
      return "追平增量";
    case "validating":
      return "校验中";
    case "ready_to_cutover":
      return "待切换";
    case "completed":
      return "已完成";
    case "cancelled":
      return "已取消";
    case "failed":
      return "失败";
    default:
      return "处理中";
  }
}

export default function MemoryRetrievalLifecycleSection({
  memoryCorpus,
  activeGeneration,
  pendingGeneration,
  pendingBuildJob,
  rollbackCandidate,
  busyActionId,
  onStartMigration,
  onCancelMigration,
  onCutoverMigration,
  onRollbackMigration,
}: MemoryRetrievalLifecycleSectionProps) {
  const retrievalBusy = String(busyActionId ?? "").startsWith("retrieval.index.");
  const showSection = Boolean(
    memoryCorpus &&
      (pendingGeneration ||
        rollbackCandidate ||
        memoryCorpus.state === "migration_deferred")
  );

  if (!showSection || !memoryCorpus) {
    return null;
  }

  const pendingStageLabel = pendingBuildJob
    ? resolveIndexStageLabel(pendingBuildJob.stage)
    : "等待重新发起";
  const pendingPercent = Math.max(
    0,
    Math.min(100, pendingBuildJob?.percent_complete ?? 0)
  );

  return (
    <section className="wb-card wb-retrieval-progress-card f149-memory-lifecycle">
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">索引生命周期</p>
          <h3>旧索引会继续服务，准备完成后再切换。</h3>
        </div>
        <div className="wb-chip-row">
          {pendingBuildJob ? <span className="wb-chip">{pendingStageLabel}</span> : null}
        </div>
      </div>

      <p className="wb-panel-copy">
        更新期间，已经保存的记忆仍然可以正常查询。
      </p>

      <div className="wb-card-grid wb-card-grid-3">
        <article className="wb-card">
          <p className="wb-card-label">当前索引</p>
          <strong>{activeGeneration ? "稳定服务中" : "继续服务中"}</strong>
          <span>在新索引切换前，查询会继续使用这一版。</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">新索引</p>
          <strong>{pendingGeneration ? "准备中" : "等待开始"}</strong>
          <span>
            {pendingGeneration
              ? "新索引准备好后再切换。"
              : "当前仍保留旧索引，不会影响已有查询。"}
          </span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">当前阶段</p>
          <strong>
            {pendingBuildJob
              ? pendingStageLabel
              : rollbackCandidate
                ? "可回滚"
                : "等待重新发起"}
          </strong>
          <span>
            {pendingBuildJob
              ? pendingBuildJob.stage === "ready_to_cutover"
                ? "新的索引已经准备好，等待你确认切换。"
                : "新的索引正在后台准备，不会中断现有查询。"
              : rollbackCandidate
                ? "上一版仍在可恢复期限内。"
                : "当前没有进行中的更新。"}
          </span>
        </article>
      </div>

      {pendingBuildJob ? (
        <div className="wb-progress-card">
          <div className="wb-progress-track" aria-hidden="true">
            <div className="wb-progress-fill" style={{ width: `${pendingPercent}%` }} />
          </div>
          <div className="wb-progress-meta">
            <span>
              {pendingBuildJob.processed_items}/{pendingBuildJob.total_items || "?"}
            </span>
            <span>{pendingPercent}%</span>
          </div>
        </div>
      ) : null}

      {memoryCorpus.warnings.length > 0 ? (
        <div className="wb-note">
          <strong>更新提醒</strong>
          <span>旧索引会继续服务，直到新的索引完成切换。</span>
        </div>
      ) : null}

      <div className="wb-inline-actions wb-inline-actions-wrap">
        {pendingGeneration ? (
          pendingBuildJob?.stage === "ready_to_cutover" ? (
            <button
              type="button"
              className="wb-button wb-button-primary"
              disabled={retrievalBusy}
              onClick={() => void onCutoverMigration(pendingGeneration.generation_id)}
            >
              切换到新索引
            </button>
          ) : pendingBuildJob?.stage === "queued" ? (
            <button
              type="button"
              className="wb-button wb-button-primary"
              disabled={retrievalBusy}
              onClick={() => void onStartMigration()}
            >
              开始迁移
            </button>
          ) : null
        ) : memoryCorpus.state === "migration_deferred" ? (
          <button
            type="button"
            className="wb-button wb-button-primary"
            disabled={retrievalBusy}
            onClick={() => void onStartMigration()}
          >
            重新发起迁移
          </button>
        ) : null}

        {pendingBuildJob?.can_cancel && pendingGeneration ? (
          <button
            type="button"
            className="wb-button wb-button-secondary"
            disabled={retrievalBusy}
            onClick={() => void onCancelMigration(pendingGeneration.generation_id)}
          >
            取消迁移
          </button>
        ) : null}

        {rollbackCandidate ? (
          <button
            type="button"
            className="wb-button wb-button-tertiary"
            disabled={retrievalBusy}
            onClick={() => void onRollbackMigration(rollbackCandidate.generation_id)}
          >
            回滚到上一版
          </button>
        ) : null}
      </div>
    </section>
  );
}
