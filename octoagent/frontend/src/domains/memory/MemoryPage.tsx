import { useEffect, useState } from "react";
import { useWorkbench } from "../../components/shell/WorkbenchLayout";
import type {
  OperatorActionKind,
  OperatorInboxItem,
  RecoverySummary,
} from "../../types";
import { formatDateTime } from "../../workbench/utils";
import MemoryActionsSection from "./MemoryActionsSection";
import MemoryFiltersSection from "./MemoryFiltersSection";
import MemoryHeroSection from "./MemoryHeroSection";
import MemoryInspectorSection from "./MemoryInspectorSection";
import MemoryResultsSection from "./MemoryResultsSection";
import {
  buildMemoryDisplayRecords,
  buildMemoryNarrative,
  fieldLabel,
  formatLayerLabel,
  formatPartitionLabel,
  mapQuickAction,
  readConfigSection,
  type MemoryNarrative,
  uniqueOptions,
} from "./shared";

export default function MemoryPage() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const memory = snapshot!.resources.memory;
  const config = snapshot!.resources.config;
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
    String(memoryConfig.backend_mode ?? "local_only").trim().toLowerCase() || "local_only";
  const bridgeTransport =
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
  const operatorItems = sessions.operator_items ?? [];
  const operatorSummary = sessions.operator_summary;
  const recoverySummary = diagnostics.recovery_summary as Partial<RecoverySummary>;
  const focusedSession =
    sessions.sessions.find((item) => item.session_id === sessions.focused_session_id) ?? null;
  const canExportFocusedSession = Boolean(sessions.focused_session_id || sessions.focused_thread_id);
  const exportTargetLabel =
    focusedSession?.title ||
    sessions.focused_thread_id ||
    sessions.focused_session_id ||
    "未选中会话";
  const selectedRecord =
    displayRecords.find((record) => record.record.record_id === selectedRecordId) ?? null;

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

  async function flushMemory() {
    await submitAction("memory.flush", {
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
        heroTone={narrative.heroTone}
        heroTitle={narrative.heroTitle}
        heroSummary={narrative.heroSummary}
        stateLabel={narrative.stateLabel}
        retrievalLabel={narrative.retrievalLabel}
        nextActionTitle={narrative.nextActionTitle}
        nextActionSummary={narrative.nextActionSummary}
        guideItems={narrative.guideItems}
        hasVisibleRecords={narrative.hasVisibleRecords}
        hasStoredRecords={narrative.hasStoredRecords}
        hasBacklog={narrative.hasBacklog}
        isDegraded={narrative.isDegraded}
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

      <MemoryActionsSection
        operatorItems={operatorItems}
        operatorSummary={operatorSummary}
        recoverySummary={recoverySummary}
        exportTargetLabel={exportTargetLabel}
        canExportFocusedSession={canExportFocusedSession}
        busyActionId={busyActionId}
        onOperatorAction={handleOperatorAction}
        onRefreshRecoverySummary={refreshRecoverySummary}
        onBackupCreate={handleBackupCreate}
        onExportChats={handleExportChats}
      />
    </div>
  );
}
