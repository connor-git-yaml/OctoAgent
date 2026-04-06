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
  solution: "历史方案",
};

const RETRIEVAL_LABELS: Record<string, string> = {
  "sqlite-metadata": "本地元数据",
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
  retrievalLabel: string;
  hasStoredRecords: boolean;
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
];

function normalizeWhitespace(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function truncateValue(value: string, limit = 160): string {
  if (value.length <= limit) {
    return value;
  }
  return `${value.slice(0, limit - 1)}…`;
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
    normalized.includes("task_id:") ||
    normalized.startsWith("add:worker_tool:") ||
    normalized.startsWith("update:worker_tool:")
  );
}

function shouldHideRecord(record: MemoryRecordProjection): boolean {
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
    // 从 content (投影为 summary) 中提取首行作为标题
    const firstLine = record.summary.split("\n").find((l) => l.trim().length > 5);
    if (firstLine) {
      return truncateValue(normalizeWhitespace(firstLine), 50);
    }
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

  // Fragment: 优先展示实际 content，即使匹配了占位符模式
  if (record.layer === "fragment" && summary && summary.length > 10) {
    // 过滤纯技术性摘要（工具证据），但保留对话摘要
    if (!isTechnicalSummary(summary)) {
      return truncateValue(summary, 160);
    }
  }

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

export function formatMemoryMode(_mode?: string): string {
  return "内建记忆引擎";
}

export function formatRetrievalLabel(value: string, _mode?: string): string {
  if (!value) {
    return "内建记忆引擎";
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
      if (record.layer === "fragment") return "待整理";
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
    .filter((v): v is string => v !== undefined)
    .filter((value, index, all) => all.indexOf(value) === index);
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
  _memoryMode: string,
  _missingSetupItems: string[],
  visibleRecordCount = memory.records.length
): MemoryNarrative {
  const retrievalLabel =
    memory.retrieval_profile?.active_backend_label?.trim() ||
    formatRetrievalLabel(memory.retrieval_backend);
  const isDegraded = memory.backend_state === "degraded" || memory.backend_state === "unavailable";
  const totalStoredRecords =
    memory.summary.sor_current_count +
    memory.summary.fragment_count +
    memory.summary.vault_ref_count;
  const hasStoredRecords = totalStoredRecords > 0;
  const hasVisibleRecords = visibleRecordCount > 0;
  const memoryWarnings = uniqueOptions(
    [...memory.warnings, ...(memory.retrieval_profile?.warnings ?? [])].map(normalizeMemoryWarning)
  );

  let heroTone: MemoryNarrative["heroTone"] = "success";
  let heroTitle = "记忆";
  let heroSummary = "";
  let stateLabel = "运行中";

  if (isDegraded) {
    heroTone = "danger";
    stateLabel = "异常";
    heroTitle = "记忆连接异常";
    heroSummary = "已有数据不会丢失。请检查 Settings > Memory 配置是否正确。";
  } else if (!hasVisibleRecords && hasStoredRecords) {
    heroTone = "warning";
    stateLabel = "运行中";
    heroTitle = "当前筛选没有命中记忆";
    heroSummary = "系统里已有记忆，试试调整或清空筛选条件。";
  } else if (hasVisibleRecords) {
    heroTone = "success";
    stateLabel = memory.backend_state === "syncing" ? "更新中" : "运行中";
    const readableCount = memory.summary.sor_readable_count ?? memory.summary.sor_current_count;
    heroTitle =
      readableCount > 0
        ? `${readableCount} 条记忆事实`
        : memory.summary.pending_consolidation_count > 0
          ? `${memory.summary.pending_consolidation_count} 条待整理`
          : "Memory 正在整理上下文";
    heroSummary = "";
  } else {
    heroTone = "warning";
    stateLabel = "已就绪";
    heroTitle = "还没有记忆内容";
    heroSummary = "去 Chat 对话或导入历史内容后，这里会出现记忆。";
  }

  return {
    heroTone,
    heroTitle,
    heroSummary,
    stateLabel,
    retrievalLabel,
    hasStoredRecords,
    memoryWarnings,
  };
}
