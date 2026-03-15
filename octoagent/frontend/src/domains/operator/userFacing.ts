import type {
  OperatorActionKind,
  OperatorInboxItem,
} from "../../types";

const OPERATOR_KIND_LABELS: Record<string, string> = {
  approval: "待确认",
  alert: "提醒",
  retryable_failure: "可重试失败",
  pairing_request: "协作请求",
};

const TECHNICAL_TEXT_PATTERNS = [
  /drift=/i,
  /stalled=\d+s/i,
  /worker_runtime_timeout/i,
  /max_exec/i,
  /\btimeout\b/i,
  /\btask[_-]?id\b/i,
  /\bwork[_-]?id\b/i,
  /\bruntime[._]/i,
  /\bjson\b/i,
];

function normalizeWhitespace(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function truncateText(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }
  return `${value.slice(0, maxLength - 1)}…`;
}

function looksTechnicalText(value: string): boolean {
  const normalized = normalizeWhitespace(value).toLowerCase();
  if (!normalized) {
    return false;
  }
  return TECHNICAL_TEXT_PATTERNS.some((pattern) => pattern.test(normalized));
}

function shouldReuseTitle(title: string): boolean {
  const normalized = normalizeWhitespace(title);
  if (!normalized) {
    return false;
  }
  if (normalized.length > 42) {
    return false;
  }
  return !looksTechnicalText(normalized);
}

function safeSummary(item: OperatorInboxItem): string {
  const normalized = normalizeWhitespace(item.summary);
  if (!normalized || looksTechnicalText(normalized)) {
    return "";
  }
  return truncateText(normalized, 120);
}

function extractStallSeconds(item: OperatorInboxItem): number | null {
  const text = `${item.title} ${item.summary}`.trim();
  const matched = text.match(/stalled=(\d+)s/i);
  if (matched?.[1]) {
    return Number(matched[1]);
  }
  if (typeof item.pending_age_seconds === "number" && item.pending_age_seconds > 0) {
    return Math.round(item.pending_age_seconds);
  }
  return null;
}

function formatRoughDuration(totalSeconds: number): string {
  if (totalSeconds < 60) {
    return `${totalSeconds} 秒`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (seconds === 0) {
    return `${minutes} 分钟`;
  }
  return `${minutes} 分 ${seconds} 秒`;
}

function hasQuickAction(item: OperatorInboxItem, kind: OperatorActionKind): boolean {
  return (item.quick_actions ?? []).some((action) => action.kind === kind && action.enabled);
}

function resolveTaskLabel(item: OperatorInboxItem): string {
  if (shouldReuseTitle(item.title)) {
    const normalizedTitle = truncateText(normalizeWhitespace(item.title), 30);
    if (normalizedTitle.startsWith("任务")) {
      return `“${normalizedTitle}”`;
    }
    return `任务“${normalizedTitle}”`;
  }
  return "这条任务";
}

export interface UserFacingOperatorItem {
  kindLabel: string;
  title: string;
  summary: string;
  nextStep: string;
  taskLinkTo: string | null;
}

export function formatOperatorKind(kind: string): string {
  return OPERATOR_KIND_LABELS[kind] ?? "待处理事项";
}

export function mapOperatorQuickAction(
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

export function describeOperatorItemForUser(item: OperatorInboxItem): UserFacingOperatorItem {
  const metadata = item.metadata ?? {};
  const rawText = `${item.title} ${item.summary} ${item.source_ref} ${metadata.error_type ?? ""} ${
    metadata.journal_state ?? ""
  }`.toLowerCase();
  const taskLabel = resolveTaskLabel(item);
  const taskLinkTo = item.task_id ? `/tasks/${item.task_id}` : null;
  const itemSummary = safeSummary(item);

  if (item.kind === "approval") {
    return {
      kindLabel: formatOperatorKind(item.kind),
      title: shouldReuseTitle(item.title) ? normalizeWhitespace(item.title) : `${taskLabel}需要你确认`,
      summary: itemSummary || "系统需要你点一次确认，这条任务才能继续。",
      nextStep: hasQuickAction(item, "approve_once") || hasQuickAction(item, "deny")
        ? "你可以直接在这里允许、拒绝或取消；也可以先打开对应任务再决定。"
        : "先打开对应任务，确认这一步要不要继续。",
      taskLinkTo,
    };
  }

  if (item.kind === "retryable_failure") {
    const timedOut =
      /worker_runtime_timeout|max_exec|timed[_-]?out|\btimeout\b/i.test(rawText);
    return {
      kindLabel: formatOperatorKind(item.kind),
      title: timedOut ? `${taskLabel}这次没有在时限内完成` : `${taskLabel}上次尝试没有成功`,
      summary: timedOut
        ? "这次尝试已经结束，旧任务不会自己恢复；如果还要继续，需要重新发起一次。"
        : itemSummary || "这次尝试没有成功，但你仍然可以重新发起一次。",
      nextStep: hasQuickAction(item, "retry_task")
        ? "确认还要继续时，再点“重试”；这会重新发起一次，不是让旧任务接着跑。"
        : "如果还要继续，请回聊天重新说一次。",
      taskLinkTo,
    };
  }

  if (item.kind === "pairing_request") {
    return {
      kindLabel: formatOperatorKind(item.kind),
      title: shouldReuseTitle(item.title)
        ? normalizeWhitespace(item.title)
        : "有一个外部入口想和你建立连接",
      summary: itemSummary || "确认后，这个外部入口才能继续把消息发进来。",
      nextStep: "确认这是你自己的入口后，再点同意；不认识就直接拒绝。",
      taskLinkTo,
    };
  }

  const stalledSeconds = extractStallSeconds(item);
  const stalledText = stalledSeconds ? `已经 ${formatRoughDuration(stalledSeconds)} 没有推进` : "一段时间没有推进";
  const looksStalled = /drift=no_progress|no_progress|state_machine_stall|stalled=/i.test(rawText);
  return {
    kindLabel: formatOperatorKind(item.kind),
    title:
      looksStalled
        ? `${taskLabel}${stalledSeconds ? "停住了" : "需要你看一下"}`
        : shouldReuseTitle(item.title)
          ? normalizeWhitespace(item.title)
          : "系统发现一条需要你留意的提醒",
    summary:
      looksStalled
        ? `${taskLabel}${stalledText}，可能卡住了。`
        : itemSummary || "这条提醒可能会影响部分任务或连接，建议你先看一下。",
    nextStep: hasQuickAction(item, "ack_alert")
      ? "先打开对应任务确认影响范围；确认看过后，可以直接标记为已处理。"
      : "先打开对应任务看看发生了什么，再决定是否处理。",
    taskLinkTo,
  };
}
