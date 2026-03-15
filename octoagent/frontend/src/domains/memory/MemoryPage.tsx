import { useEffect, useState } from "react";
import { useWorkbench } from "../../components/shell/WorkbenchLayout";
import { formatDateTime } from "../../workbench/utils";
import MemoryFiltersSection from "./MemoryFiltersSection";
import MemoryHeroSection from "./MemoryHeroSection";
import MemoryInspectorSection from "./MemoryInspectorSection";
import MemoryRetrievalLifecycleSection from "./MemoryRetrievalLifecycleSection";
import MemoryResultsSection from "./MemoryResultsSection";
import {
  buildMemoryDisplayRecords,
  buildMemoryNarrative,
  fieldLabel,
  formatLayerLabel,
  formatPartitionLabel,
  readConfigSection,
  type MemoryNarrative,
  uniqueOptions,
} from "./shared";

export default function MemoryPage() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const memory = snapshot!.resources.memory;
  const config = snapshot!.resources.config;
  const retrievalPlatform = snapshot!.resources.retrieval_platform ?? null;
  const [queryDraft, setQueryDraft] = useState(memory.filters.query);
  const [layerDraft, setLayerDraft] = useState(memory.filters.layer);
  const [partitionDraft, setPartitionDraft] = useState(memory.filters.partition);
  const [includeHistoryDraft, setIncludeHistoryDraft] = useState(memory.filters.include_history);
  const [includeVaultRefsDraft, setIncludeVaultRefsDraft] = useState(
    memory.filters.include_vault_refs
  );
  const [limitDraft, setLimitDraft] = useState(String(memory.filters.limit || 50));
  const [selectedRecordId, setSelectedRecordId] = useState("");
  const displayRecords = buildMemoryDisplayRecords(memory.records);

  useEffect(() => {
    setQueryDraft(memory.filters.query);
    setLayerDraft(memory.filters.layer);
    setPartitionDraft(memory.filters.partition);
    setIncludeHistoryDraft(memory.filters.include_history);
    setIncludeVaultRefsDraft(memory.filters.include_vault_refs);
    setLimitDraft(String(memory.filters.limit || 50));
  }, [memory.filters]);

  useEffect(() => {
    if (displayRecords.length === 0) {
      setSelectedRecordId("");
      return;
    }
    const hasSelectedRecord = displayRecords.some(
      (record) => record.record.record_id === selectedRecordId
    );
    if (!hasSelectedRecord) {
      setSelectedRecordId(displayRecords[0]!.record.record_id);
    }
  }, [displayRecords, selectedRecordId]);

  const layerOptions = uniqueOptions([
    "",
    ...memory.available_layers,
    memory.filters.layer,
    "sor",
    "fragment",
    "vault",
    "derived",
  ]);
  const partitionOptions = uniqueOptions([
    "",
    ...memory.available_partitions,
    memory.filters.partition,
  ]);
  const memoryConfig = readConfigSection(readConfigSection(config.current_value).memory);
  const memoryMode =
    memory.retrieval_profile?.engine_mode === "memu_compat"
      ? "memu"
      : String(memoryConfig.backend_mode ?? "local_only").trim().toLowerCase() || "local_only";
  const bridgeTransport =
    String(memory.retrieval_profile?.transport ?? "").trim().toLowerCase() ||
    String(memoryConfig.bridge_transport ?? "").trim().toLowerCase() ||
    (String(memoryConfig.bridge_command ?? "").trim() ? "command" : "http");
  const bridgeUrl = String(memoryConfig.bridge_url ?? "").trim();
  const bridgeCommand = String(memoryConfig.bridge_command ?? "").trim();
  const bridgeApiKeyEnv = String(memoryConfig.bridge_api_key_env ?? "").trim();
  const missingSetupItems =
    memoryMode === "memu"
      ? [
          bridgeTransport === "http" && !bridgeUrl
            ? fieldLabel(config.ui_hints, "memory.bridge_url", "MemU Bridge 地址")
            : "",
          bridgeTransport === "http" && !bridgeApiKeyEnv
            ? fieldLabel(config.ui_hints, "memory.bridge_api_key_env", "MemU API Key 环境变量")
            : "",
          bridgeTransport === "command" && !bridgeCommand
            ? fieldLabel(config.ui_hints, "memory.bridge_command", "MemU 本地命令")
            : "",
        ].filter(Boolean)
      : [];
  const narrative: MemoryNarrative = buildMemoryNarrative(
    memory,
    memoryMode,
    missingSetupItems,
    displayRecords.length
  );
  const selectedRecord =
    displayRecords.find((record) => record.record.record_id === selectedRecordId) ?? null;
  const memoryCorpus =
    retrievalPlatform?.corpora.find((item) => item.corpus_kind === "memory") ?? null;
  const activeGeneration =
    retrievalPlatform?.generations.find(
      (item) => item.generation_id === memoryCorpus?.active_generation_id
    ) ?? null;
  const pendingGeneration =
    retrievalPlatform?.generations.find(
      (item) => item.generation_id === memoryCorpus?.pending_generation_id
    ) ?? null;
  const pendingBuildJob =
    retrievalPlatform?.build_jobs.find(
      (item) => item.generation_id === pendingGeneration?.generation_id
    ) ?? null;
  const rollbackCandidate =
    retrievalPlatform?.generations.find(
      (item) =>
        item.corpus_kind === "memory" &&
        !item.is_active &&
        Boolean(item.rollback_deadline) &&
        new Date(item.rollback_deadline || "").getTime() > Date.now()
    ) ?? null;

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

  async function flushMemory() {
    await submitAction("memory.flush", {
      project_id: memory.active_project_id,
      workspace_id: memory.active_workspace_id,
    });
  }

  async function startEmbeddingMigration() {
    await submitAction("retrieval.index.start", {
      project_id: memory.active_project_id,
      workspace_id: memory.active_workspace_id,
    });
  }

  async function cancelEmbeddingMigration(generationId: string) {
    await submitAction("retrieval.index.cancel", {
      generation_id: generationId,
      project_id: memory.active_project_id,
      workspace_id: memory.active_workspace_id,
    });
  }

  async function cutoverEmbeddingMigration(generationId: string) {
    await submitAction("retrieval.index.cutover", {
      generation_id: generationId,
      project_id: memory.active_project_id,
      workspace_id: memory.active_workspace_id,
    });
  }

  async function rollbackEmbeddingMigration(generationId: string) {
    await submitAction("retrieval.index.rollback", {
      generation_id: generationId,
      project_id: memory.active_project_id,
      workspace_id: memory.active_workspace_id,
    });
  }

  function handleSelectRecord(record: (typeof displayRecords)[number]) {
    setSelectedRecordId(record.record.record_id);
  }

  return (
    <div className="wb-page">
      <MemoryHeroSection
        memory={memory}
        memoryMode={memoryMode}
        bridgeTransport={bridgeTransport}
        heroTone={narrative.heroTone}
        heroTitle={narrative.heroTitle}
        heroSummary={narrative.heroSummary}
        stateLabel={narrative.stateLabel}
        retrievalLabel={narrative.retrievalLabel}
        nextActionTitle={narrative.nextActionTitle}
        nextActionSummary={narrative.nextActionSummary}
        showNextActionPanel={narrative.showNextActionPanel}
        guideItems={narrative.guideItems}
        hasVisibleRecords={narrative.hasVisibleRecords}
        hasStoredRecords={narrative.hasStoredRecords}
        hasBacklog={narrative.hasBacklog}
        isDegraded={narrative.isDegraded}
        missingSetupItems={narrative.missingSetupItems}
        busyActionId={busyActionId}
        onResetFilters={resetFilters}
        onFlushMemory={flushMemory}
      />

      {narrative.memoryWarnings.length > 0 ? (
        <div className="wb-inline-banner is-error">
          <strong>当前有需要注意的情况</strong>
          <span>{narrative.memoryWarnings.join("；")}</span>
        </div>
      ) : null}

      <MemoryRetrievalLifecycleSection
        memory={memory}
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
        queryDraft={queryDraft}
        layerDraft={layerDraft}
        partitionDraft={partitionDraft}
        includeHistoryDraft={includeHistoryDraft}
        includeVaultRefsDraft={includeVaultRefsDraft}
        limitDraft={limitDraft}
        layerOptions={layerOptions}
        partitionOptions={partitionOptions}
        retrievalLabel={narrative.retrievalLabel}
        updatedAt={memory.updated_at}
        busyActionId={busyActionId}
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

      <div className="wb-memory-layout">
        <MemoryResultsSection
          memory={memory}
          records={displayRecords}
          selectedRecordId={selectedRecordId}
          hasStoredRecords={narrative.hasStoredRecords}
          busyActionId={busyActionId}
          onResetFilters={resetFilters}
          onSelectRecord={handleSelectRecord}
        />

        <MemoryInspectorSection
          memory={memory}
          selectedRecord={selectedRecord}
          layerOptions={layerOptions}
          partitionOptions={partitionOptions}
          retrievalLabel={narrative.retrievalLabel}
        />
      </div>
    </div>
  );
}
