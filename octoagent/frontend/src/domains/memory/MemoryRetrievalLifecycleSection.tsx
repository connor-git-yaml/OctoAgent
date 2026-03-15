import type {
  IndexBuildJob,
  IndexGeneration,
  MemoryConsoleDocument,
  RetrievalCorpusState,
} from "../../types";

interface MemoryRetrievalLifecycleSectionProps {
  memory: MemoryConsoleDocument;
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
      return "写入 projection";
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
  memory,
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

  const activeEmbeddingLabel =
    activeGeneration?.label ||
    memory.retrieval_profile?.bindings?.find((item) => item.binding_key === "embedding")
      ?.effective_label ||
    memoryCorpus.active_profile_target ||
    "当前索引";
  const desiredEmbeddingLabel =
    pendingGeneration?.label || memoryCorpus.desired_profile_target || activeEmbeddingLabel;
  const pendingStageLabel = pendingBuildJob
    ? resolveIndexStageLabel(pendingBuildJob.stage)
    : "等待重新发起";
  const pendingPercent = Math.max(
    0,
    Math.min(100, pendingBuildJob?.percent_complete ?? 0)
  );

  return (
    <section className="wb-card wb-retrieval-progress-card">
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">Embedding 迁移</p>
          <h3>当前查询继续使用旧索引，直到新索引切换完成</h3>
        </div>
        <div className="wb-chip-row">
          <span className="wb-chip">{memoryCorpus.state}</span>
          {pendingBuildJob ? <span className="wb-chip">{pendingStageLabel}</span> : null}
        </div>
      </div>

      <p className="wb-panel-copy">
        Memory 和未来知识库会共用这条 embedding 轨道。迁移期间，当前对话和检索不会中断。
      </p>

      <div className="wb-card-grid wb-card-grid-3">
        <article className="wb-card">
          <p className="wb-card-label">当前在线索引</p>
          <strong>{activeEmbeddingLabel}</strong>
          <span>现在所有 recall 仍继续使用这一层。</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">目标 embedding</p>
          <strong>{desiredEmbeddingLabel}</strong>
          <span>
            {pendingGeneration
              ? "新索引准备好后再切换。"
              : "你已经改了目标 embedding，但当前仍保留旧索引。"}
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
            {pendingBuildJob?.summary ||
              memoryCorpus.summary ||
              "当前没有进行中的迁移。"}
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
          <strong>迁移提醒</strong>
          <span>{memoryCorpus.warnings.join("；")}</span>
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
