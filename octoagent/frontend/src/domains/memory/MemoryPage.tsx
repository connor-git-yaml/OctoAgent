import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useWorkbench } from "../../components/shell/WorkbenchLayout";
import { formatDateTime } from "../../workbench/utils";
import MemoryDetailModal from "./MemoryDetailModal";
import MemoryFiltersSection from "./MemoryFiltersSection";
import MemoryHeroSection from "./MemoryHeroSection";
import MemoryRetrievalLifecycleSection from "./MemoryRetrievalLifecycleSection";
import MemoryResultsSection from "./MemoryResultsSection";
import MemoryAdvancedInfo from "./MemoryAdvancedInfo";
import { resolveResourcePageState } from "../shared/resourcePageState";
import type {
  MemoryConsoleDocument,
  RetrievalPlatformDocument,
} from "../../types";
import {
  buildMemoryDisplayRecords,
  buildMemoryNarrative,
  formatLayerLabel,
  formatPartitionLabel,
  translateWarning,
  type MemoryNarrative,
  uniqueOptions,
} from "./shared";
import "./MemoryPage.css";

export default function MemoryPage() {
  const {
    snapshot,
    loading,
    error,
    authError,
    refreshSnapshot,
  } = useWorkbench();
  const memory = snapshot?.resources?.memory ?? null;
  const config = snapshot?.resources?.config ?? null;
  const retrievalPlatform = snapshot?.resources?.retrieval_platform ?? null;

  const resolution = resolveResourcePageState({
    loading,
    hasContent: Boolean(snapshot && memory && config),
    connected: Boolean(snapshot),
    error:
      authError ??
      (error
        ? new Error(error)
        : !memory || !config
          ? new Error("memory-resource-missing")
          : null),
  });

  if (resolution.owner === "global-auth") {
    return null;
  }
  if (resolution.state.kind !== "ready" || !memory) {
    const isPermissionDenied = resolution.state.kind === "permission-denied";
    const isLoading = resolution.state.kind === "loading";
    return (
      <main className="f149-memory-page">
        <section
          className={`f149-memory-state${isPermissionDenied ? " is-permission" : ""}`}
          aria-live="polite"
        >
          {isLoading ? <span className="f149-memory-state-mark" aria-hidden="true" /> : null}
          <strong>
            {isLoading
              ? "正在整理记忆"
              : isPermissionDenied
                ? "当前账号没有权限查看记忆"
                : "记忆加载失败"}
          </strong>
          <p>
            {isLoading
              ? "正在读取已经保存的背景，请稍候。"
              : isPermissionDenied
                ? "这部分内容需要管理员授权。你仍可以继续使用对话。"
                : "暂时没能读取已经保存的背景，请稍后重试。"}
          </p>
          {!isLoading ? (
            <div className="f149-memory-state-actions">
              {!isPermissionDenied ? (
                <button type="button" onClick={() => void refreshSnapshot()}>
                  重试
                </button>
              ) : null}
              <Link to="/">回到对话</Link>
            </div>
          ) : null}
        </section>
      </main>
    );
  }

  return (
    <MemoryReadyPage
      memory={memory}
      retrievalPlatform={retrievalPlatform}
    />
  );
}

interface MemoryReadyPageProps {
  memory: MemoryConsoleDocument;
  retrievalPlatform: RetrievalPlatformDocument | null;
}

function MemoryReadyPage({
  memory,
  retrievalPlatform,
}: MemoryReadyPageProps) {
  const { submitAction, busyActionId } = useWorkbench();
  const defaultSummary = {
    sor_current_count: 0,
    sor_readable_count: 0,
    sor_history_count: 0,
    fragment_count: 0,
    pending_consolidation_count: 0,
    pending_replay_count: 0,
    vault_ref_count: 0,
    proposal_count: 0,
    next_consolidation_at: "",
    scope_count: 0,
  };
  const memoryResource = {
    ...memory,
    summary: memory.summary ?? defaultSummary,
    warnings: memory.warnings ?? [],
    available_scopes: memory.available_scopes ?? [],
    available_layers: memory.available_layers ?? [],
    available_partitions: memory.available_partitions ?? [],
    records: memory.records ?? [],
    filters: memory.filters ?? {
      query: "",
      scope_id: "",
      layer: "",
      partition: "",
      include_history: false,
      include_vault_refs: false,
      limit: 50,
    },
  };
  const filters = memoryResource.filters;
  const records = memoryResource.records;
  const [scopeDraft, setScopeDraft] = useState(filters.scope_id ?? "");
  const [queryDraft, setQueryDraft] = useState(filters.query);
  const [layerDraft, setLayerDraft] = useState(filters.layer);
  const [partitionDraft, setPartitionDraft] = useState(filters.partition);
  const [includeHistoryDraft, setIncludeHistoryDraft] = useState(filters.include_history);
  const [includeVaultRefsDraft, setIncludeVaultRefsDraft] = useState(
    filters.include_vault_refs
  );
  const [limitDraft, setLimitDraft] = useState(String(filters.limit || 50));
  const [showDetailModal, setShowDetailModal] = useState(false);
  const [selectedRecordId, setSelectedRecordId] = useState("");
  const detailTriggerRef = useRef<HTMLElement | null>(null);
  const displayRecords = buildMemoryDisplayRecords(records);

  useEffect(() => {
    setScopeDraft(filters.scope_id ?? "");
    setQueryDraft(filters.query);
    setLayerDraft(filters.layer);
    setPartitionDraft(filters.partition);
    setIncludeHistoryDraft(filters.include_history);
    setIncludeVaultRefsDraft(filters.include_vault_refs);
    setLimitDraft(String(filters.limit || 50));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 用 generated_at 代替 filters 对象引用，避免 snapshot 轮询时覆盖用户草稿
  }, [memory.generated_at]);

  const scopeOptions = uniqueOptions([
    "",
    ...memoryResource.available_scopes,
    filters.scope_id ?? "",
  ]);
  const layerOptions = uniqueOptions([
    "",
    ...memoryResource.available_layers,
    filters.layer,
    "sor",
    "fragment",
    "vault",
    "derived",
  ]);
  const partitionOptions = uniqueOptions([
    "",
    ...memoryResource.available_partitions,
    filters.partition,
  ]);
  const narrative: MemoryNarrative = buildMemoryNarrative(
    memoryResource,
    "builtin",
    [],
    displayRecords.length
  );
  const selectedRecord =
    displayRecords.find((record) => record.record.record_id === selectedRecordId) ?? null;

  // retrieval lifecycle 数据
  const rpCorpora = retrievalPlatform?.corpora ?? [];
  const rpGenerations = retrievalPlatform?.generations ?? [];
  const rpBuildJobs = retrievalPlatform?.build_jobs ?? [];
  const memoryCorpus =
    rpCorpora.find((item) => item.corpus_kind === "memory") ?? null;
  const activeGeneration =
    rpGenerations.find(
      (item) => item.generation_id === memoryCorpus?.active_generation_id
    ) ?? null;
  const pendingGeneration =
    rpGenerations.find(
      (item) => item.generation_id === memoryCorpus?.pending_generation_id
    ) ?? null;
  const pendingBuildJob =
    rpBuildJobs.find(
      (item) => item.generation_id === pendingGeneration?.generation_id
    ) ?? null;
  const rollbackCandidate =
    rpGenerations.find(
      (item) =>
        item.corpus_kind === "memory" &&
        !item.is_active &&
        Boolean(item.rollback_deadline) &&
        new Date(item.rollback_deadline || "").getTime() > Date.now()
    ) ?? null;

  async function refreshMemory() {
    await submitAction("memory.query", {
      project_id: memoryResource.active_project_id,
      scope_id: scopeDraft,
      query: queryDraft.trim(),
      layer: layerDraft,
      partition: partitionDraft,
      include_history: includeHistoryDraft,
      include_vault_refs: includeVaultRefsDraft,
      limit: Number(limitDraft) || 50,
    });
  }

  async function resetFilters() {
    setScopeDraft("");
    setQueryDraft("");
    setLayerDraft("");
    setPartitionDraft("");
    setIncludeHistoryDraft(false);
    setIncludeVaultRefsDraft(false);
    setLimitDraft("50");
    await submitAction("memory.query", {
      project_id: memoryResource.active_project_id,
      scope_id: "",
      query: "",
      layer: "",
      partition: "",
      include_history: false,
      include_vault_refs: false,
      limit: 50,
    });
  }

  async function startEmbeddingMigration() {
    await submitAction("retrieval.index.start", {
      project_id: memoryResource.active_project_id,
    });
  }

  async function cancelEmbeddingMigration(generationId: string) {
    await submitAction("retrieval.index.cancel", {
      generation_id: generationId,
      project_id: memoryResource.active_project_id,
    });
  }

  async function cutoverEmbeddingMigration(generationId: string) {
    await submitAction("retrieval.index.cutover", {
      generation_id: generationId,
      project_id: memoryResource.active_project_id,
    });
  }

  async function rollbackEmbeddingMigration(generationId: string) {
    await submitAction("retrieval.index.rollback", {
      generation_id: generationId,
      project_id: memoryResource.active_project_id,
    });
  }

  const [consolidateMessage, setConsolidateMessage] = useState("");
  const [consolidateIsError, setConsolidateIsError] = useState(false);

  async function consolidateMemory() {
    setConsolidateMessage("");
    setConsolidateIsError(false);
    const result = await submitAction("memory.consolidate", {
      project_id: memoryResource.active_project_id,
    });
    const data = result?.data as { errors?: unknown[]; message?: string } | undefined;
    const hasErrors = (data?.errors?.length ?? 0) > 0;
    const msg = data?.message || result?.message || "整理完成";
    setConsolidateMessage(msg);
    setConsolidateIsError(hasErrors);
  }

  function handleSelectRecord(
    record: (typeof displayRecords)[number],
    trigger: HTMLElement
  ) {
    detailTriggerRef.current = trigger;
    setSelectedRecordId(record.record.record_id);
    setShowDetailModal(true);
  }

  const handleCloseModal = useCallback(() => {
    setShowDetailModal(false);
    window.requestAnimationFrame(() => detailTriggerRef.current?.focus());
  }, []);

  return (
    <main className="f149-memory-page">
      <MemoryHeroSection
        memory={memoryResource}
        heroTone={narrative.heroTone}
        heroTitle={narrative.heroTitle}
        heroSummary={narrative.heroSummary}
        stateLabel={narrative.stateLabel}
        retrievalLabel={narrative.retrievalLabel}
      />

      {narrative.memoryWarnings.length > 0 ? (
        <div className="wb-inline-banner is-error">
          <strong>注意</strong>
          <span>{narrative.memoryWarnings.map(translateWarning).join("；")}</span>
        </div>
      ) : null}

      <MemoryRetrievalLifecycleSection
        memoryCorpus={memoryCorpus}
        activeGeneration={activeGeneration}
        pendingGeneration={pendingGeneration}
        pendingBuildJob={pendingBuildJob}
        rollbackCandidate={rollbackCandidate}
        busyActionId={busyActionId}
        onStartMigration={startEmbeddingMigration}
        onCancelMigration={cancelEmbeddingMigration}
        onCutoverMigration={cutoverEmbeddingMigration}
        onRollbackMigration={rollbackEmbeddingMigration}
      />

      <MemoryFiltersSection
        scopeDraft={scopeDraft}
        scopeOptions={scopeOptions}
        queryDraft={queryDraft}
        layerDraft={layerDraft}
        partitionDraft={partitionDraft}
        includeHistoryDraft={includeHistoryDraft}
        includeVaultRefsDraft={includeVaultRefsDraft}
        limitDraft={limitDraft}
        layerOptions={layerOptions}
        partitionOptions={partitionOptions}
        retrievalLabel={narrative.retrievalLabel}
        updatedAt={memoryResource.updated_at}
        busyActionId={busyActionId}
        onScopeChange={setScopeDraft}
        onQueryChange={setQueryDraft}
        onLayerChange={setLayerDraft}
        onPartitionChange={setPartitionDraft}
        onIncludeHistoryChange={setIncludeHistoryDraft}
        onIncludeVaultRefsChange={setIncludeVaultRefsDraft}
        onLimitChange={setLimitDraft}
        onResetFilters={resetFilters}
        onRefreshMemory={refreshMemory}
        formatLayerLabel={formatLayerLabel}
        formatPartitionLabel={formatPartitionLabel}
        formatDateTime={formatDateTime}
      />

      <MemoryResultsSection
        memory={memoryResource}
        records={displayRecords}
        hasStoredRecords={narrative.hasStoredRecords}
        busyActionId={busyActionId}
        consolidateMessage={consolidateMessage}
        consolidateIsError={consolidateIsError}
        onResetFilters={resetFilters}
        onSelectRecord={handleSelectRecord}
        onConsolidate={consolidateMemory}
      />

      <MemoryDetailModal
        selectedRecord={selectedRecord}
        open={showDetailModal}
        onClose={handleCloseModal}
        onSubmitAction={submitAction}
        busyActionId={busyActionId ?? undefined}
      />

      <MemoryAdvancedInfo
        records={records}
        retrievalBackend={memoryResource.retrieval_backend}
        embeddingTarget={
          memoryResource.retrieval_profile?.bindings?.find(
            (item) => item.binding_key === "embedding"
          )?.effective_target ?? ""
        }
      />
    </main>
  );
}
