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

export interface MemoryNarrative {
  heroTone: "success" | "warning" | "danger";
  heroTitle: string;
  heroSummary: string;
  stateLabel: string;
  nextActionTitle: string;
  nextActionSummary: string;
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
  missingSetupItems: string[]
): MemoryNarrative {
  const retrievalLabel = formatRetrievalLabel(memory.retrieval_backend, memoryMode);
  const usingFallback =
    memoryMode === "memu" &&
    Boolean(memory.retrieval_backend) &&
    memory.retrieval_backend !== "memu";
  const isDegraded = memory.backend_state === "degraded" || memory.backend_state === "unavailable";
  const totalStoredRecords =
    memory.summary.sor_current_count +
    memory.summary.fragment_count +
    memory.summary.vault_ref_count;
  const hasStoredRecords = totalStoredRecords > 0;
  const hasBacklog =
    memory.summary.fragment_count > 0 || memory.summary.pending_replay_count > 0;
  const hasVisibleRecords = memory.records.length > 0;
  const memoryWarnings = uniqueOptions(memory.warnings.map(normalizeMemoryWarning));

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
  const nextActionTitle = "下一步建议";
  let nextActionSummary =
    "继续在 Chat 里对话，或导入一段历史消息；Memory 会把这些内容整理成你能读懂的结论。";
  const guideItems: MemoryGuideItem[] = [];

  if (missingSetupItems.length > 0) {
    heroTone = "warning";
    stateLabel = "待补配置";
    heroTitle = "增强记忆还没配完整";
    heroSummary = `你已经选择了增强记忆，但还缺少 ${missingSetupItems.join("、")}。补齐后保存，再回到这里刷新即可。`;
    nextActionSummary =
      "如果你只是想先让基础 Memory 工作，也可以保持本地记忆模式，不需要额外服务。";
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
    heroTitle = usingFallback ? "Memory 目前在本地回退运行" : "Memory 当前连接不稳定";
    heroSummary = usingFallback
      ? "增强记忆已经配置过，但这次暂时回退到了本地路径。已有结论还能看，跨会话检索可能暂时不完整。"
      : "当前结果可能不完整，但已有数据不会丢。建议先检查 Settings 里的 Memory 配置，再决定是否打开 Advanced 排查。";
    nextActionSummary =
      "优先检查 Memory 设置里的模式、Bridge 地址和 API Key 环境变量；如果提醒还在，再去 Advanced 看运行诊断。";
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
    nextActionSummary =
      "最短路径是先产生内容，再回来查看；只有明确需要跨会话检索时，才需要继续折腾增强记忆。";
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

  return {
    heroTone,
    heroTitle,
    heroSummary,
    stateLabel,
    nextActionTitle,
    nextActionSummary,
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
