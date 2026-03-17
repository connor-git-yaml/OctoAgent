import { Link } from "react-router-dom";
import type {
  IndexBuildJob,
  IndexGeneration,
  MemoryConsoleDocument,
  RetrievalCorpusState,
  RetrievalPlatformDocument,
} from "../../types";
import { ActionBar, StatusBadge } from "../../ui/primitives";

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

function selectMemoryLifecycle(retrievalPlatform: RetrievalPlatformDocument | null): {
  memoryCorpus: RetrievalCorpusState | null;
  activeGeneration: IndexGeneration | null;
  pendingGeneration: IndexGeneration | null;
  pendingBuildJob: IndexBuildJob | null;
  rollbackCandidate: IndexGeneration | null;
} {
  const corpora = retrievalPlatform?.corpora ?? [];
  const generations = retrievalPlatform?.generations ?? [];
  const buildJobs = retrievalPlatform?.build_jobs ?? [];
  const memoryCorpus =
    corpora.find((item) => item.corpus_kind === "memory") ?? null;
  const activeGeneration =
    generations.find(
      (item) => item.generation_id === memoryCorpus?.active_generation_id
    ) ?? null;
  const pendingGeneration =
    generations.find(
      (item) => item.generation_id === memoryCorpus?.pending_generation_id
    ) ?? null;
  const pendingBuildJob =
    buildJobs.find(
      (item) => item.generation_id === pendingGeneration?.generation_id
    ) ?? null;
  const rollbackCandidate =
    generations.find(
      (item) =>
        item.corpus_kind === "memory" &&
        !item.is_active &&
        Boolean(item.rollback_deadline) &&
        new Date(item.rollback_deadline || "").getTime() > Date.now()
    ) ?? null;
  return {
    memoryCorpus,
    activeGeneration,
    pendingGeneration,
    pendingBuildJob,
    rollbackCandidate,
  };
}

interface RetrievalBackgroundSectionProps {
  retrievalPlatform: RetrievalPlatformDocument | null;
  memory: MemoryConsoleDocument;
  busyActionId: string | null;
  onStartMigration: () => Promise<void>;
  onCancelMigration: (generationId: string) => Promise<void>;
  onCutoverMigration: (generationId: string) => Promise<void>;
  onRollbackMigration: (generationId: string) => Promise<void>;
}

export default function RetrievalBackgroundSection({
  retrievalPlatform,
  memory,
  busyActionId,
  onStartMigration,
  onCancelMigration,
  onCutoverMigration,
  onRollbackMigration,
}: RetrievalBackgroundSectionProps) {
  const {
    memoryCorpus,
    activeGeneration,
    pendingGeneration,
    pendingBuildJob,
    rollbackCandidate,
  } = selectMemoryLifecycle(retrievalPlatform);
  const showSection = Boolean(
    memoryCorpus &&
      (pendingGeneration ||
        rollbackCandidate ||
        memoryCorpus.state === "migration_deferred")
  );
  if (!showSection || !memoryCorpus) {
    return null;
  }

  const retrievalBusy = String(busyActionId ?? "").startsWith("retrieval.index.");
  const activeEmbeddingLabel =
    activeGeneration?.label ||
    memory.retrieval_profile?.bindings?.find((item) => item.binding_key === "embedding")
      ?.effective_label ||
    memoryCorpus.active_profile_target ||
    "当前索引";
  const desiredEmbeddingLabel =
    pendingGeneration?.label || memoryCorpus.desired_profile_target || activeEmbeddingLabel;
  const stageLabel = pendingBuildJob
    ? resolveIndexStageLabel(pendingBuildJob.stage)
    : rollbackCandidate
      ? "可回滚"
      : "等待重新发起";
  const progress = Math.max(0, Math.min(100, pendingBuildJob?.percent_complete ?? 0));

  return (
    <section className="wb-panel">
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">后台索引任务</p>
          <h3>Embedding 迁移正在后台准备，不会中断当前检索</h3>
        </div>
        <StatusBadge tone={pendingBuildJob ? "running" : "warning"}>{stageLabel}</StatusBadge>
      </div>

      <p className="wb-panel-copy">
        现在在线服务的仍是旧索引。新的 embedding 会先后台重建，完成后再切换。
      </p>

      <div className="wb-card-grid wb-card-grid-3">
        <article className="wb-card">
          <p className="wb-card-label">当前在线索引</p>
          <strong>{activeEmbeddingLabel}</strong>
          <span>当前所有 recall 继续走这一层。</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">目标 embedding</p>
          <strong>{desiredEmbeddingLabel}</strong>
          <span>
            {pendingGeneration
              ? "新索引准备好后再切换。"
              : "你可以重新发起迁移，把目标 embedding 切上来。"}
          </span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">任务说明</p>
          <strong>{stageLabel}</strong>
          <span>{pendingBuildJob?.summary || memoryCorpus.summary}</span>
        </article>
      </div>

      {pendingBuildJob ? (
        <div className="wb-progress-card">
          <div className="wb-progress-track" aria-hidden="true">
            <div className="wb-progress-fill" style={{ width: `${progress}%` }} />
          </div>
          <div className="wb-progress-meta">
            <span>
              {pendingBuildJob.processed_items}/{pendingBuildJob.total_items || "?"}
            </span>
            <span>{progress}%</span>
          </div>
        </div>
      ) : null}

      {memoryCorpus.warnings.length > 0 ? (
        <div className="wb-note">
          <strong>迁移提醒</strong>
          <span>{memoryCorpus.warnings.join("；")}</span>
        </div>
      ) : null}

      <ActionBar className="wb-inline-actions-wrap">
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

        <Link className="wb-button wb-button-tertiary" to="/memory">
          打开 Memory 查看详情
        </Link>
      </ActionBar>
    </section>
  );
}
