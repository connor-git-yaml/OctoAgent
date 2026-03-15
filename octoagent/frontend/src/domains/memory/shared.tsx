import type {
  ConfigFieldHint,
  MemoryConsoleDocument,
  MemoryRecordProjection,
  OperatorActionKind,
  OperatorInboxItem,
} from "../../types";

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
  derived_type: "派生类型",
  confidence: "置信度",
};

const OPERATOR_KIND_LABELS: Record<string, string> = {
  approval: "审批",
  pairing_request: "配对请求",
  retryable_failure: "可重试失败",
  alert: "提醒",
};

export interface MemoryGuideItem {
  title: string;
  summary: string;
  state: "done" | "todo" | "optional";
}

export interface MemoryDisplayRecord {
  record: MemoryRecordProjection;
  title: string;
  summary: string;
  statusLabel: string;
  derivedTypeLabel: string;
  confidenceLabel: string;
  metadataPreview: Array<[string, string]>;
  metadataDetails: Array<[string, string]>;
}

export interface MemoryNarrative {
  heroTone: "success" | "warning" | "danger";
  heroTitle: string;
  heroSummary: string;
  stateLabel: string;
  nextActionTitle: string;
  nextActionSummary: string;
  showNextActionPanel: boolean;
  guideItems: MemoryGuideItem[];
  missingSetupItems: string[];
  retrievalLabel: string;
  usingFallback: boolean;
  isDegraded: boolean;
  hasVisibleRecords: boolean;
  hasStoredRecords: boolean;
  hasBacklog: boolean;
  totalStoredRecords: number;
  memoryWarnings: string[];
}

const DERIVED_TYPE_LABELS: Record<string, string> = {
  summary: "摘要",
  relation: "关系判断",
  entity: "实体归纳",
  topic: "主题归纳",
  tom: "ToM 判断",
};

const PLACEHOLDER_SUMMARY_PATTERNS = [
  /\bmemory updated\b/i,
  /\bsensitive memory\b/i,
  /\bhealth note updated\b/i,
  /\bworker tool evidence writeback\b/i,
];

const INTERNAL_RECORD_SOURCES = new Set([
  "agent_context.worker_tool_writeback",
  "before_compaction_flush",
  "context_compaction",
]);

function normalizeWhitespace(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function truncateValue(value: string, limit = 160): string {
  if (value.length <= limit) {
    return value;
  }
  return `${value.slice(0, limit - 1)}…`;
}

function readRecordSource(record: MemoryRecordProjection): string {
  const value = record.metadata.source;
  return typeof value === "string" ? value.trim() : "";
}

function formatDerivedTypeLabel(value: unknown): string {
  if (typeof value !== "string") {
    return "";
  }
  const normalized = value.trim().toLowerCase();
  if (!normalized) {
    return "";
  }
  return DERIVED_TYPE_LABELS[normalized] ?? value.trim();
}

function isPlaceholderSummary(record: MemoryRecordProjection, summary: string): boolean {
  if (!summary) {
    return true;
  }
  if (
    record.layer === "vault" &&
    record.requires_vault_authorization &&
    /^vault:\/\/.+/i.test(summary)
  ) {
    return true;
  }
  return PLACEHOLDER_SUMMARY_PATTERNS.some((pattern) => pattern.test(summary));
}

function isTechnicalSubjectKey(subjectKey: string): boolean {
  const normalized = subjectKey.trim().toLowerCase();
  if (!normalized) {
    return false;
  }
  return (
    normalized.startsWith("worker_tool:") ||
    normalized.startsWith("memory.") ||
    normalized.startsWith("operator.") ||
    normalized.startsWith("system.")
  );
}

function isTechnicalSummary(summary: string): boolean {
  const normalized = summary.trim().toLowerCase();
  if (!normalized) {
    return false;
  }
  return (
    (normalized.includes("tool_name:") && normalized.includes("output_summary:")) ||
    normalized.includes("response_artifact_ref:") ||
    normalized.includes("task_id:")
  );
}

function shouldHideRecord(record: MemoryRecordProjection): boolean {
  const source = readRecordSource(record);
  if (INTERNAL_RECORD_SOURCES.has(source)) {
    return true;
  }
  if (isTechnicalSubjectKey(record.subject_key)) {
    return true;
  }
  if (isTechnicalSummary(record.summary)) {
    return true;
  }
  return false;
}

function formatMetadataValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return truncateValue(normalizeWhitespace(value), 180);
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? String(value) : "";
  }
  if (typeof value === "boolean") {
    return value ? "是" : "否";
  }
  try {
    return truncateValue(JSON.stringify(value, null, 0), 180);
  } catch {
    return truncateValue(String(value), 180);
  }
}

function buildMetadataEntries(
  record: MemoryRecordProjection,
  limit?: number
): Array<[string, string]> {
  const entries = Object.entries(record.metadata)
    .filter(([key, value]) => {
      const formatted = formatMetadataValue(value);
      if (!formatted) {
        return false;
      }
      return !key.endsWith("_id") && !key.endsWith("_ref") && !key.endsWith("_refs");
    })
    .map(([key, value]) => [METADATA_LABELS[key] ?? key, formatMetadataValue(value)] as [string, string]);
  if (typeof limit === "number") {
    return entries.slice(0, limit);
  }
  return entries;
}

function buildRecordTitle(record: MemoryRecordProjection): string {
  const subjectKey = record.subject_key.trim();
  if (record.layer === "derived") {
    if (subjectKey) {
      return subjectKey;
    }
    const derivedTypeLabel = formatDerivedTypeLabel(record.metadata.derived_type);
    if (derivedTypeLabel) {
      return derivedTypeLabel;
    }
  }
  if (subjectKey && !isTechnicalSubjectKey(subjectKey)) {
    return subjectKey;
  }
  if (record.layer === "vault") {
    return `${formatPartitionLabel(record.partition)}受控记忆`;
  }
  if (record.layer === "fragment") {
    return `${formatPartitionLabel(record.partition)}待整理片段`;
  }
  if (record.layer === "derived") {
    return `${formatPartitionLabel(record.partition)}派生记忆`;
  }
  if (record.layer === "sor") {
    return `${formatPartitionLabel(record.partition)}现行结论`;
  }
  return formatRecordTitle(record);
}

function buildRecordSummary(record: MemoryRecordProjection): string {
  const summary = normalizeWhitespace(record.summary);
  if (summary && !isPlaceholderSummary(record, summary) && !isTechnicalSummary(summary)) {
    return summary;
  }
  if (record.layer === "vault") {
    return `这条记录关联 ${formatPartitionLabel(record.partition)} 类受控记忆，查看原文仍需授权。`;
  }
  if (record.layer === "derived") {
    const derivedTypeLabel = formatDerivedTypeLabel(record.metadata.derived_type);
    return derivedTypeLabel
      ? `这是一条${derivedTypeLabel}，由已有记忆进一步归纳得出。`
      : "这是一条从已有记忆进一步归纳出的派生结论。";
  }
  if (record.layer === "fragment") {
    return "这是一条尚未整理成现行结论的记忆片段。";
  }
  if (record.layer === "sor") {
    return `这是一条 ${formatPartitionLabel(record.partition)} 类现行结论，系统暂未生成更友好的摘要。`;
  }
  return describeRecord(record);
}

function buildConfidenceLabel(record: MemoryRecordProjection): string {
  const value = record.metadata.confidence;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "";
  }
  const ratio = Math.max(0, Math.min(1, value));
  return `${Math.round(ratio * 100)}%`;
}

export function buildMemoryDisplayRecords(
  records: MemoryRecordProjection[] | undefined | null
): MemoryDisplayRecord[] {
  return (Array.isArray(records) ? records : [])
    .filter((record) => !shouldHideRecord(record))
    .map((record) => ({
      record,
      title: buildRecordTitle(record),
      summary: buildRecordSummary(record),
      statusLabel: formatRecordStatus(record),
      derivedTypeLabel: formatDerivedTypeLabel(record.metadata.derived_type),
      confidenceLabel: buildConfidenceLabel(record),
      metadataPreview: buildMetadataEntries(record, 4),
      metadataDetails: buildMetadataEntries(record),
    }));
}

export function formatLayerLabel(layer: string): string {
  return LAYER_LABELS[layer] ?? layer;
}

export function formatPartitionLabel(partition: string): string {
  return PARTITION_LABELS[partition] ?? (partition || "未分类");
}

export function formatMemoryMode(mode: string): string {
  return MEMORY_MODE_LABELS[mode] ?? "本地记忆";
}

export function formatRetrievalLabel(value: string, mode: string): string {
  if (!value) {
    return formatMemoryMode(mode);
  }
  return RETRIEVAL_LABELS[value] ?? value;
}

export function formatRecordTitle(record: MemoryRecordProjection): string {
  if (record.subject_key) {
    return record.subject_key;
  }
  if (record.layer === "fragment") {
    return "未命名片段";
  }
  return "未命名记录";
}

export function formatRecordStatus(record: MemoryRecordProjection): string {
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

export function describeRecord(record: MemoryRecordProjection): string {
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

export function uniqueOptions(values: Array<string | undefined>): string[] {
  return values
    .filter(Boolean)
    .filter((value, index, all) => all.indexOf(value) === index) as string[];
}

export function metadataPreviewEntries(
  record: MemoryRecordProjection
): Array<[string, string]> {
  return buildMetadataEntries(record, 4);
}

export function metadataDetailEntries(
  record: MemoryRecordProjection
): Array<[string, string]> {
  return buildMetadataEntries(record);
}

export function formatRecoveryTime(value: string | null | undefined): string {
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

export function formatOperatorKind(kind: string): string {
  return OPERATOR_KIND_LABELS[kind] ?? kind;
}

export function normalizeMemoryWarning(message: string): string {
  return message
    .replace(/memory backend/g, "记忆服务")
    .replace(/SQLite fallback/g, "本地回退")
    .replace(/scope/g, "范围")
    .replace(/replay/g, "补齐")
    .replace(/sync/g, "同步");
}

export function readConfigSection(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

export function fieldLabel(
  hints: Record<string, ConfigFieldHint>,
  fieldPath: string,
  fallback: string
): string {
  return hints[fieldPath]?.label || fallback;
}

export function renderOperatorMeta(item: OperatorInboxItem): string {
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

export function mapQuickAction(
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

export function buildMemoryNarrative(
  memory: MemoryConsoleDocument,
  memoryMode: string,
  missingSetupItems: string[],
  visibleRecordCount = memory.records.length
): MemoryNarrative {
  const retrievalLabel =
    memory.retrieval_profile?.active_backend_label?.trim() ||
    formatRetrievalLabel(memory.retrieval_backend, memoryMode);
  const usingFallback =
    memoryMode === "memu" &&
    Boolean(memory.retrieval_backend) &&
    memory.retrieval_backend !== "memu";
  const isDegraded = memory.backend_state === "degraded" || memory.backend_state === "unavailable";
  const totalStoredRecords =
    memory.summary.sor_current_count +
    memory.summary.fragment_count +
    memory.summary.vault_ref_count;
  const backlogCount = Math.max(memory.summary.pending_replay_count, memory.summary.fragment_count);
  const hasStoredRecords = totalStoredRecords > 0;
  const hasBacklog =
    memory.summary.fragment_count > 0 || memory.summary.pending_replay_count > 0;
  const hasVisibleRecords = visibleRecordCount > 0;
  const memoryWarnings = uniqueOptions(
    [...memory.warnings, ...(memory.retrieval_profile?.warnings ?? [])].map(normalizeMemoryWarning)
  );

  const hasActiveFilters =
    Boolean(memory.filters.query.trim()) ||
    Boolean(memory.filters.layer) ||
    Boolean(memory.filters.partition) ||
    memory.filters.include_history ||
    memory.filters.include_vault_refs;

  let heroTone: MemoryNarrative["heroTone"] = "success";
  let heroTitle = "Memory 正在帮你保留关键上下文";
  let heroSummary =
    "你现在看到的是系统已经整理成可读结论的内容，可以直接用来判断它记住了什么。";
  let stateLabel = "运行中";
  let nextActionTitle = "下一步建议";
  let nextActionSummary =
    "继续在 Chat 里对话，或导入一段历史消息；Memory 会把这些内容整理成你能读懂的结论。";
  let showNextActionPanel = true;
  const guideItems: MemoryGuideItem[] = [];

  if (missingSetupItems.length > 0) {
    heroTone = "warning";
    stateLabel = "待补配置";
    heroTitle = "增强记忆还没配完整";
    heroSummary = `你已经选择了增强记忆，但还缺少 ${missingSetupItems.join("、")}。补齐后保存，再回到这里刷新即可。`;
    nextActionSummary =
      "如果你只是想先让基础 Memory 工作，也可以保持本地记忆模式，不需要额外服务。";
    guideItems.push(
      ...missingSetupItems.map((item) => ({
        title: `补齐 ${item}`,
        summary: "去 Settings > Memory 填好这项并保存配置，然后回到本页刷新。",
        state: "todo" as const,
      }))
    );
  } else if (isDegraded) {
    heroTone = "danger";
    stateLabel = usingFallback ? "降级回退" : "当前异常";
    heroTitle = usingFallback ? "Memory 目前在本地回退运行" : "Memory 当前连接不稳定";
    heroSummary = usingFallback
      ? "增强记忆已经配置过，但这次暂时回退到了本地路径。已有结论还能看，跨会话检索可能暂时不完整。"
      : "当前结果可能不完整，但已有数据不会丢。建议先检查 Settings 里的 Memory 配置，再决定是否打开 Advanced 排查。";
    nextActionSummary =
      "优先检查 Memory 设置里的模式、接入方式，以及 Bridge 地址 / 本地命令 / API Key 是否匹配；如果提醒还在，再去 Advanced 看运行诊断。";
    guideItems.push(
      {
        title: "先确认 Memory 设置",
        summary: "打开 Settings > Memory，确认当前模式、接入方式和最小配置是否仍然正确。",
        state: "todo",
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
    heroTitle = hasActiveFilters
      ? "Memory 在工作，只是当前筛选太窄"
      : "Memory 已有内容，但当前视图还没命中可读结果";
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
    );
    if (hasBacklog) {
      guideItems.push({
        title: "整理最新记忆",
        summary: "如果刚完成聊天或导入，整理一次会让新片段更快变成可读结论。",
        state: "optional",
      });
    }
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
    if (hasBacklog) {
      nextActionTitle = "还有新的内容待整理";
      nextActionSummary =
        "现在这些记忆已经能直接看，但最近新增的片段还没完全沉淀成结论。需要时再整理一次即可。";
      guideItems.push({
        title: "整理最新记忆",
        summary: `当前还有 ${backlogCount} 条片段或积压待处理，整理后会更快变成稳定结论。`,
        state: "todo",
      });
    } else {
      showNextActionPanel = false;
      nextActionTitle = "";
      nextActionSummary = "";
    }
  } else {
    heroTone = "warning";
    stateLabel = "已就绪";
    heroTitle = "Memory 已经就绪，等第一批内容进入";
    heroSummary =
      memoryMode === "local_only"
        ? "现在是本地记忆模式，不需要额外服务。先去 Chat 对话或导入一段历史，回来这里就能看到结果。"
        : "增强记忆已经配置完成，但当前还没有足够内容形成可读结论。先去聊天或导入，再回来刷新。";
    nextActionSummary =
      "最短路径是先产生内容，再回来查看；只有明确需要跨会话检索时，才需要继续折腾增强记忆。";
    guideItems.push(
      {
        title: "先去产生第一批内容",
        summary: "去 Chat 发起一段对话，或者导入一段历史消息，再回来刷新本页。",
        state: "todo",
      },
    );
  }

  if (guideItems.length === 0) {
    showNextActionPanel = false;
  }

  return {
    heroTone,
    heroTitle,
    heroSummary,
    stateLabel,
    nextActionTitle,
    nextActionSummary,
    showNextActionPanel,
    guideItems,
    missingSetupItems,
    retrievalLabel,
    usingFallback,
    isDegraded,
    hasVisibleRecords,
    hasStoredRecords,
    hasBacklog,
    totalStoredRecords,
    memoryWarnings,
  };
}
