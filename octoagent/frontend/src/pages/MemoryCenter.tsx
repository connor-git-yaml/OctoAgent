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

const PARTITION_LABELS: Record<string, string> = {
  core: "核心信息",
  profile: "个人资料",
  work: "工作事项",
  health: "健康（敏感）",
  finance: "财务（敏感）",
  chat: "对话内容",
  contact: "联系人",
};

const MEMORY_MODE_LABELS: Record<string, string> = {
  local_only: "本地记忆",
  memu: "增强记忆",
};

const RETRIEVAL_LABELS: Record<string, string> = {
  memu: "增强检索",
  "sqlite-metadata": "本地回退",
};

const METADATA_LABELS: Record<string, string> = {
  source: "来源",
  owner: "归属",
  channel: "渠道",
  category: "分类",
  topic: "主题",
};

const OPERATOR_KIND_LABELS: Record<string, string> = {
  approval: "审批",
  pairing_request: "配对请求",
  retryable_failure: "可重试失败",
  alert: "提醒",
};

function formatLayerLabel(layer: string): string {
  return LAYER_LABELS[layer] ?? layer;
}

function formatPartitionLabel(partition: string): string {
  return PARTITION_LABELS[partition] ?? (partition || "未分类");
}

function formatMemoryMode(mode: string): string {
  return MEMORY_MODE_LABELS[mode] ?? "本地记忆";
}

function formatRetrievalLabel(value: string, mode: string): string {
  if (!value) {
    return formatMemoryMode(mode);
  }
  return RETRIEVAL_LABELS[value] ?? value;
}

function formatRecordTitle(record: MemoryRecordProjection): string {
  if (record.subject_key) {
    return record.subject_key;
  }
  if (record.layer === "fragment") {
    return "未命名片段";
  }
  return "未命名记录";
}

function formatRecordStatus(record: MemoryRecordProjection): string {
  const normalized = record.status.trim().toLowerCase();
  switch (normalized) {
    case "current":
      return "当前结论";
    case "history":
    case "superseded":
      return "历史版本";
    case "deleted":
      return "已删除";
    default:
      return record.status || formatLayerLabel(record.layer);
  }
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
    .filter(([key, value]) => {
      if (value === null || value === undefined || String(value).trim() === "") {
        return false;
      }
      return !key.endsWith("_id") && !key.endsWith("_ref") && !key.endsWith("_refs");
    })
    .slice(0, 4)
    .map(([key, value]) => [METADATA_LABELS[key] ?? key, String(value)]);
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

function formatOperatorKind(kind: string): string {
  return OPERATOR_KIND_LABELS[kind] ?? kind;
}

function normalizeMemoryWarning(message: string): string {
  return message
    .replace(/memory backend/g, "记忆服务")
    .replace(/SQLite fallback/g, "本地回退")
    .replace(/scope/g, "范围")
    .replace(/replay/g, "补齐")
    .replace(/sync/g, "同步");
}

function readConfigSection(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function fieldLabel(
  hints: Record<string, { label: string }>,
  fieldPath: string,
  fallback: string
): string {
  return hints[fieldPath]?.label || fallback;
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
  const totalStoredRecords =
    memory.summary.sor_current_count +
    memory.summary.fragment_count +
    memory.summary.vault_ref_count;
  const hasStoredRecords = totalStoredRecords > 0;
  const hasBacklog =
    memory.summary.fragment_count > 0 || memory.summary.pending_replay_count > 0;
  const memoryConfig = readConfigSection(readConfigSection(config.current_value).memory);
  const memoryMode =
    String(memoryConfig.backend_mode ?? "local_only").trim().toLowerCase() || "local_only";
  const bridgeUrl = String(memoryConfig.bridge_url ?? "").trim();
  const bridgeApiKeyEnv = String(memoryConfig.bridge_api_key_env ?? "").trim();
  const missingSetupItems =
    memoryMode === "memu"
      ? [
          !bridgeUrl
            ? fieldLabel(config.ui_hints, "memory.bridge_url", "MemU Bridge 地址")
            : "",
          !bridgeApiKeyEnv
            ? fieldLabel(config.ui_hints, "memory.bridge_api_key_env", "MemU API Key 环境变量")
            : "",
        ].filter(Boolean)
      : [];
  const retrievalLabel = formatRetrievalLabel(memory.retrieval_backend, memoryMode);
  const usingFallback =
    memoryMode === "memu" &&
    Boolean(memory.retrieval_backend) &&
    memory.retrieval_backend !== "memu";
  const isDegraded = memory.backend_state === "degraded" || memory.backend_state === "unavailable";
  const hasVisibleRecords = memory.records.length > 0;
  const hasActiveFilters =
    Boolean(queryDraft.trim()) ||
    Boolean(layerDraft) ||
    Boolean(partitionDraft) ||
    includeHistoryDraft ||
    includeVaultRefsDraft;
  const memoryWarnings = uniqueOptions(memory.warnings.map(normalizeMemoryWarning));
  let heroTone = "success";
  let heroTitle = "Memory 正在帮你保留关键上下文";
  let heroSummary = "你现在看到的是系统已经整理成可读结论的内容，可以直接用来判断它记住了什么。";
  let stateLabel = "运行中";
  let nextActionTitle = "下一步建议";
  let nextActionSummary =
    "继续在 Chat 里对话，或导入一段历史消息；Memory 会把这些内容整理成你能读懂的结论。";
  const guideItems: Array<{ title: string; summary: string; state: "done" | "todo" | "optional" }> = [];

  if (missingSetupItems.length > 0) {
    heroTone = "warning";
    stateLabel = "待补配置";
    heroTitle = "增强记忆还没配完整";
    heroSummary = `你已经选择了增强记忆，但还缺少 ${missingSetupItems.join("、")}。补齐后保存，再回到这里刷新即可。`;
    nextActionSummary = "如果你只是想先让基础 Memory 工作，也可以保持本地记忆模式，不需要额外服务。";
    guideItems.push(
      {
        title: "基础记忆已经可用",
        summary: "本地记忆模式不需要额外部署。只要有聊天或导入内容，系统就能开始整理摘要。",
        state: "done",
      },
      ...missingSetupItems.map((item) => ({
        title: `补齐 ${item}`,
        summary: "去 Settings > Memory 填好这项并保存配置，然后回到本页刷新。",
        state: "todo" as const,
      }))
    );
  } else if (isDegraded) {
    heroTone = "danger";
    stateLabel = usingFallback ? "降级回退" : "当前异常";
    heroTitle = usingFallback
      ? "Memory 目前在本地回退运行"
      : "Memory 当前连接不稳定";
    heroSummary = usingFallback
      ? "增强记忆已经配置过，但这次暂时回退到了本地路径。已有结论还能看，跨会话检索可能暂时不完整。"
      : "当前结果可能不完整，但已有数据不会丢。建议先检查 Settings 里的 Memory 配置，再决定是否打开 Advanced 排查。";
    nextActionSummary = "优先检查 Memory 设置里的模式、Bridge 地址和 API Key 环境变量；如果提醒还在，再去 Advanced 看运行诊断。";
    guideItems.push(
      {
        title: "先确认 Memory 设置",
        summary: "打开 Settings > Memory，确认当前模式、Bridge 地址和 API Key 环境变量是否仍然正确。",
        state: "todo",
      },
      {
        title: "已有内容仍然可读",
        summary: hasStoredRecords
          ? "你现在看到的已有结论和片段不会因为降级而消失。"
          : "即使现在没内容，基础 Memory 路径仍然可以继续接收新的聊天和导入。",
        state: "done",
      },
      {
        title: "需要时再打开 Advanced",
        summary: "只有在设置看起来正确、提醒仍然持续时，才需要进入高级诊断页面继续排查。",
        state: "optional",
      }
    );
  } else if (!hasVisibleRecords && hasStoredRecords) {
    heroTone = "warning";
    stateLabel = "正在工作";
    heroTitle = hasActiveFilters ? "Memory 在工作，只是当前筛选太窄" : "Memory 已有内容，但当前视图还没命中可读结果";
    heroSummary = hasActiveFilters
      ? "系统里已经有记忆，只是这次筛选没有命中。先清空条件再看一遍。"
      : "当前项目里已经有片段或结论，但这次视图没有命中可读摘要。可以先切换记忆类型，或者直接清空筛选。";
    nextActionSummary = "先清空筛选重新看一遍；如果最近刚有新消息或导入，也可以整理一次最新记忆。";
    guideItems.push(
      {
        title: "先清空筛选",
        summary: "大多数“什么都没看到”的情况，只是筛选条件太窄，不代表 Memory 没在工作。",
        state: "todo",
      },
      {
        title: "已有记忆内容",
        summary: `当前项目至少已经累积了 ${totalStoredRecords} 条结论、片段或受保护引用。`,
        state: "done",
      },
      {
        title: "最近有新内容时可整理一次",
        summary: "如果刚完成聊天或导入，整理最新记忆会让新片段更快变成可读结论。",
        state: hasBacklog ? "optional" : "done",
      }
    );
  } else if (hasVisibleRecords) {
    heroTone = "success";
    stateLabel = memory.backend_state === "syncing" ? "更新中" : "运行中";
    heroTitle =
      memory.summary.sor_current_count > 0
        ? `Memory 当前记住了 ${memory.summary.sor_current_count} 条现行结论`
        : "Memory 已经开始整理你的上下文";
    heroSummary =
      memoryMode === "memu" && !usingFallback
        ? "增强记忆已经接通。你可以直接阅读这些结论，必要时再回到 Settings 微调检索策略。"
        : "基础记忆已经在工作。继续聊天、导入或整理片段，系统会把更多内容沉淀成可读结论。";
    nextActionSummary = hasBacklog
      ? "如果你刚完成新的聊天或导入，建议再整理一次最新记忆，让片段更快变成结论。"
      : "现在可以直接阅读这些记录；需要更强的跨会话检索时，再去 Settings 打开增强记忆。";
    guideItems.push(
      {
        title: "现在就能使用",
        summary: "这些记录已经是给用户看的 Memory 摘要，不需要理解底层 scope 或索引实现。",
        state: "done",
      },
      {
        title: "继续积累内容",
        summary: "新的聊天、任务和导入会继续进入 Memory，形成更多结论和片段。",
        state: "done",
      },
      {
        title: memoryMode === "memu" ? "增强检索已启用" : "如需更强检索再升级",
        summary:
          memoryMode === "memu"
            ? "当前已经处于增强记忆模式。只有在需要调优时，再回到 Settings 微调。"
            : "如果你需要更稳定的跨会话检索，再到 Settings > Memory 切到增强记忆并补齐两项配置。",
        state: memoryMode === "memu" ? "done" : "optional",
      }
    );
  } else {
    heroTone = "warning";
    stateLabel = "已就绪";
    heroTitle = "Memory 已经就绪，等第一批内容进入";
    heroSummary =
      memoryMode === "local_only"
        ? "现在是本地记忆模式，不需要额外服务。先去 Chat 对话或导入一段历史，回来这里就能看到结果。"
        : "增强记忆已经配置完成，但当前还没有足够内容形成可读结论。先去聊天或导入，再回来刷新。";
    nextActionSummary = "最短路径是先产生内容，再回来查看；只有明确需要跨会话检索时，才需要继续折腾增强记忆。";
    guideItems.push(
      {
        title: "基础链路已经就绪",
        summary:
          memoryMode === "local_only"
            ? "本地记忆已经准备好，不需要额外 Memory 服务。"
            : "增强记忆的最小配置已经存在，可以继续接收新的内容。",
        state: "done",
      },
      {
        title: "先去产生第一批内容",
        summary: "去 Chat 发起一段对话，或者导入一段历史消息，再回来刷新本页。",
        state: "todo",
      },
      {
        title: "后续再决定是否增强",
        summary:
          memoryMode === "local_only"
            ? "如果你以后需要跨会话检索，再去 Settings > Memory 开启增强记忆。"
            : "如果你暂时只是在做基础验证，不需要再补更多后端细节。",
        state: "optional",
      }
    );
  }
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
            <span className="wb-chip">模式 {formatMemoryMode(memoryMode)}</span>
            <span
              className={`wb-chip ${heroTone === "success" ? "is-success" : "is-warning"}`}
            >
              状态 {stateLabel}
            </span>
            <span className="wb-chip">当前检索 {retrievalLabel}</span>
            <span className="wb-chip">更新时间 {formatDateTime(memory.updated_at)}</span>
          </div>
        </div>

        <div className="wb-hero-insights">
          <article className="wb-hero-metric">
            <p className="wb-card-label">当前结论</p>
            <strong>{memory.summary.sor_current_count}</strong>
            <span>这是已经整理成稳定结论的内容，最适合直接阅读。</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">新增片段</p>
            <strong>{memory.summary.fragment_count}</strong>
            <span>片段代表刚进入系统的新上下文，通常还会继续被整理。</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">待处理内容</p>
            <strong>{memory.summary.pending_replay_count + memory.summary.vault_ref_count}</strong>
            <span>待补齐 {memory.summary.pending_replay_count} / 需授权 {memory.summary.vault_ref_count}</span>
          </article>
        </div>
      </section>

      {memoryWarnings.length > 0 ? (
        <div className="wb-inline-banner is-error">
          <strong>当前有需要注意的情况</strong>
          <span>{memoryWarnings.join("；")}</span>
        </div>
      ) : null}

      <div className="wb-split">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">下一步</p>
              <h3>{nextActionTitle}</h3>
            </div>
            <span className={`wb-status-pill is-${heroTone}`}>{stateLabel}</span>
          </div>

          <p className="wb-panel-copy">{nextActionSummary}</p>

          <div className="wb-note-stack">
            {guideItems.map((item) => (
              <div key={`${item.title}-${item.summary}`} className="wb-note">
                <div className="wb-panel-head">
                  <strong>{item.title}</strong>
                  <span
                    className={`wb-status-pill is-${
                      item.state === "done"
                        ? "success"
                        : item.state === "optional"
                          ? "draft"
                          : "warning"
                    }`}
                  >
                    {item.state === "done" ? "已完成" : item.state === "optional" ? "可选" : "待处理"}
                  </span>
                </div>
                <span>{item.summary}</span>
              </div>
            ))}
          </div>

          <div className="wb-inline-actions wb-inline-actions-wrap">
            {(!hasVisibleRecords || !hasStoredRecords) ? (
              <Link className="wb-button wb-button-primary" to="/chat">
                去 Chat 产生内容
              </Link>
            ) : null}
            {missingSetupItems.length > 0 || isDegraded || memoryMode === "local_only" ? (
              <Link className="wb-button wb-button-secondary" to="/settings#settings-group-memory">
                打开 Settings &gt; Memory
              </Link>
            ) : null}
            {!hasVisibleRecords && hasStoredRecords ? (
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={() => void resetFilters()}
                disabled={busyActionId === "memory.query"}
              >
                清空筛选后重查
              </button>
            ) : null}
            {hasBacklog ? (
              <button
                type="button"
                className="wb-button wb-button-tertiary"
                onClick={() =>
                  void submitAction("memory.flush", {
                    project_id: memory.active_project_id,
                    workspace_id: memory.active_workspace_id,
                  })
                }
                disabled={busyActionId === "memory.flush"}
              >
                {busyActionId === "memory.flush" ? "整理中..." : "整理最新记忆"}
              </button>
            ) : null}
            {isDegraded ? (
              <Link className="wb-button wb-button-tertiary" to="/advanced">
                打开 Advanced 诊断
              </Link>
            ) : null}
          </div>
        </section>

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">为什么这样判断</p>
              <h3>这些信息决定了当前状态</h3>
            </div>
          </div>

          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>当前模式</strong>
              <span>
                {formatMemoryMode(memoryMode)}
                {memoryMode === "local_only"
                  ? "：基础链路，不需要额外 Memory 服务。"
                  : "：适合需要跨会话检索和更强回放能力的场景。"}
              </span>
            </div>
            <div className="wb-note">
              <strong>当前记忆路径</strong>
              <span>
                {retrievalLabel}
                {usingFallback
                  ? "。增强记忆暂时回退到了本地路径，但基础内容仍然可读。"
                  : "。这是这次页面实际使用的记忆检索路径。"}
              </span>
            </div>
            <div className="wb-note">
              <strong>内容覆盖范围</strong>
              <span>
                {hasStoredRecords
                  ? `当前累计 ${totalStoredRecords} 条结论、片段或受保护引用，来自 ${memory.available_scopes.length || memory.summary.scope_count || 0} 个上下文范围。`
                  : "当前还没有形成可读记忆，通常只是还没发生聊天或导入。"}
              </span>
            </div>
            <div className="wb-note">
              <strong>设置入口</strong>
              <span>
                所有模式切换和最小配置都在 Settings 的 Memory 分区，不需要单独理解 backend 部署细节。
              </span>
              <div className="wb-inline-actions">
                <Link className="wb-button wb-button-tertiary wb-button-inline" to="/settings#settings-group-memory">
                  前往 Memory 设置
                </Link>
              </div>
            </div>
          </div>
        </section>
      </div>

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
              onChange={(event) => setQueryDraft(event.target.value)}
            />
          </label>

          <label className="wb-field">
            <span>记忆类型</span>
            <select value={layerDraft} onChange={(event) => setLayerDraft(event.target.value)}>
              {layerOptions.map((option) => (
                <option key={option || "all-layers"} value={option}>
                  {option ? formatLayerLabel(option) : "全部类型"}
                </option>
              ))}
            </select>
          </label>

          <label className="wb-field">
            <span>主题分区</span>
            <select
              value={partitionDraft}
              onChange={(event) => setPartitionDraft(event.target.value)}
            >
              {partitionOptions.map((option) => (
                <option key={option || "all-partitions"} value={option}>
                  {option ? formatPartitionLabel(option) : "全部分区"}
                </option>
              ))}
            </select>
          </label>

          <label className="wb-field">
            <span>最多显示</span>
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
            <span>包含受保护引用</span>
          </label>
          <span className="wb-panel-copy">
            当前检索方式：{retrievalLabel}，更新时间{" "}
            {formatDateTime(memory.updated_at)}
          </span>
        </div>
      </section>

      <div className="wb-memory-layout">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">现在记住了什么</p>
              <h3>{memory.records.length} 条可读记忆</h3>
            </div>
            <div className="wb-chip-row">
              <span className="wb-chip">需授权 {memory.summary.vault_ref_count}</span>
              <span className="wb-chip">待整理 {memory.summary.pending_replay_count}</span>
            </div>
          </div>

          {memory.records.length === 0 ? (
            <div className="wb-empty-state">
              <strong>{hasStoredRecords ? "当前视图没有命中可读记忆" : "当前还没有可读记忆"}</strong>
              <span>
                {hasStoredRecords
                  ? "先清空筛选，或者整理最新记忆后再看一遍。"
                  : "先去 Chat 对话或导入历史内容，Memory 才会开始形成可读结论。"}
              </span>
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
                  清空筛选
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
                          <span className="wb-chip">{formatPartitionLabel(record.partition)}</span>
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
                      {record.version !== null ? (
                        <span className="wb-chip">版本 {record.version}</span>
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
                <h3>这次筛选包含哪些内容</h3>
              </div>
            </div>

            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>覆盖范围</strong>
                <span>
                  {memory.available_scopes.length > 0 || memory.summary.scope_count > 0
                    ? `当前命中了 ${memory.available_scopes.length || memory.summary.scope_count} 个上下文范围。`
                    : "当前还没有命中任何上下文范围。"}
                </span>
              </div>
              <div className="wb-note">
                <strong>记忆类型</strong>
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
                <strong>主题分区</strong>
                <div className="wb-chip-row">
                  {partitionOptions.filter(Boolean).length > 0 ? (
                    partitionOptions
                      .filter(Boolean)
                      .map((partition) => (
                        <span key={partition} className="wb-chip">
                          {formatPartitionLabel(partition)}
                        </span>
                      ))
                  ) : (
                    <span>当前记录里还没有可枚举的主题分区。</span>
                  )}
                </div>
              </div>
            </div>
          </section>

          <section className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">更多入口</p>
                <h3>需要更深入时，再打开这些页面</h3>
              </div>
            </div>

            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>Memory 设置</strong>
                <span>
                  切换本地或增强模式、补最小配置、调整召回策略，都在 Settings 的 Memory 分区。
                </span>
                <div className="wb-inline-actions">
                  <Link className="wb-button wb-button-tertiary wb-button-inline" to="/settings#settings-group-memory">
                    打开 Settings &gt; Memory
                  </Link>
                </div>
              </div>
              <div className="wb-note">
                <strong>Advanced 诊断</strong>
                <span>
                  如果连接持续异常、需要看恢复状态，或者想做更细的排查，再进入 Advanced 页面。
                </span>
                <div className="wb-inline-actions">
                  <Link className="wb-button wb-button-tertiary wb-button-inline" to="/advanced">
                    打开 Advanced
                  </Link>
                </div>
              </div>
            </div>
          </section>
        </div>
      </div>

      <div className="wb-split">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">待确认事项</p>
              <h3>处理会影响记忆与上下文的待确认事项</h3>
            </div>
            <div className="wb-chip-row">
              <span className="wb-chip">待处理 {operatorSummary?.total_pending ?? 0}</span>
              <span className="wb-chip">审批 {operatorSummary?.approvals ?? 0}</span>
              <span className="wb-chip">配对 {operatorSummary?.pairing_requests ?? 0}</span>
            </div>
          </div>

          {operatorItems.length === 0 ? (
            <div className="wb-empty-state">
              <strong>当前没有待处理事项</strong>
              <span>如果后续出现 Vault 授权、审批或配对请求，这里会直接显示。</span>
            </div>
          ) : (
            <div className="wb-note-stack">
              {operatorItems.slice(0, 6).map((item) => (
                <div key={item.item_id} className="wb-note">
                  <strong>{item.title}</strong>
                  <span>{item.summary}</span>
                  <small>
                    {formatOperatorKind(item.kind)} · {renderOperatorMeta(item)} · {formatRecoveryTime(item.created_at)}
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
              <p className="wb-card-label">备份与恢复</p>
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
              <span>{recoverySummary?.ready_for_restore ? "已就绪" : "未就绪"}</span>
              <small>
                {recoverySummary?.latest_recovery_drill?.summary ?? "尚未执行恢复演练。"}
              </small>
            </div>
            <div className="wb-note">
              <strong>导出当前会话</strong>
              <span>{exportTargetLabel}</span>
              <small>
                {canExportFocusedSession
                  ? "这里会导出你当前聚焦的会话。"
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
