/**
 * 审批中心纯逻辑域 — F145（零 React/零 IO，L4 主战场）
 *
 * - unified diff 文本 → DiffLineRow[] 解析（服务端已产 diff，前端不重跑 jsdiff）
 * - accept/reject 失败 → 人话呈现映射（conflict 终态 / pending 可重试 三态分流）
 * - 三源候选 → 卡片摘要文案（非技术用户人话）
 */
import type { DiffLineRow } from "../../components/diff/DiffBody";
import {
  ApprovalActionError,
  type CompactCandidate,
  type ConsolidationCandidate,
} from "../../api/approval-center";

// ---------------------------------------------------------------------------
// unified diff 解析
// ---------------------------------------------------------------------------

/** 服务端「无行级差异」哨兵文案（behavior_compact.py `_unified_diff` 空 diff 输出） */
const SERVER_EMPTY_DIFF_SENTINEL = "（无行级差异）";

/**
 * 把服务端 unified diff 文本解析为 DiffLineRow[]（供 DiffLineList 渲染）。
 *
 * 面向非技术用户的裁剪：
 * - 文件头（`---` / `+++`）丢弃——卡片标题已说明是哪个文件；
 * - hunk 标记（`@@`）丢弃，hunk 之间插一行 "⋯" 分隔（unchanged）；
 * - `\ No newline at end of file` 技术噪音丢弃；
 * - 空文本 / 服务端「（无行级差异）」哨兵 → 返回 []（调用方展示无差异文案）；
 * - 其余无前缀行（如服务端超长截断尾注）保留为 unchanged 原文。
 */
export function parseUnifiedDiff(diffText: string): DiffLineRow[] {
  const trimmed = diffText.trim();
  if (trimmed === "" || trimmed === SERVER_EMPTY_DIFF_SENTINEL) {
    return [];
  }
  const rows: DiffLineRow[] = [];
  let seenHunk = false;
  for (const line of diffText.split("\n")) {
    if (line.startsWith("+++") || line.startsWith("---")) {
      continue;
    }
    if (line.startsWith("@@")) {
      if (seenHunk) {
        rows.push({ kind: "unchanged", text: "⋯" });
      }
      seenHunk = true;
      continue;
    }
    if (line.startsWith("\\")) {
      // "\ No newline at end of file"
      continue;
    }
    if (line.startsWith("+")) {
      rows.push({ kind: "added", text: line.slice(1) });
      continue;
    }
    if (line.startsWith("-")) {
      rows.push({ kind: "removed", text: line.slice(1) });
      continue;
    }
    if (line.startsWith(" ")) {
      rows.push({ kind: "unchanged", text: line.slice(1) });
      continue;
    }
    if (line === "") {
      continue;
    }
    // 无前缀行（服务端截断尾注等）：按原文保留
    rows.push({ kind: "unchanged", text: line });
  }
  return rows;
}

// ---------------------------------------------------------------------------
// accept/reject 失败呈现映射（spec D4）
// ---------------------------------------------------------------------------

export interface ApprovalFailurePresentation {
  /** toast 人话文案 */
  message: string;
  /** true = 候选已终态/被处理，卡片应移除（不诱导反复重试） */
  removeCard: boolean;
}

/**
 * accept/reject 失败 → 人话呈现（HTTP 409 两义，按后端 body.status 分流）。
 *
 * REST detail 技术文案不直接上 UI（调用方可 console.warn 留诊断）。
 */
export function mapApprovalFailure(err: unknown): ApprovalFailurePresentation {
  if (err instanceof ApprovalActionError) {
    switch (err.resultStatus) {
      case "conflict":
        return {
          message: "这条提议在等待期间已失效，已自动关闭。",
          removeCard: true,
        };
      case "not_found":
        return { message: "这条提议已被处理。", removeCard: true };
      case "pending":
        return { message: "处理没有成功，请稍后重试。", removeCard: false };
      default:
        return { message: "操作失败，请重试。", removeCard: false };
    }
  }
  return { message: "操作失败，请重试。", removeCard: false };
}

// ---------------------------------------------------------------------------
// 卡片摘要文案（人话）
// ---------------------------------------------------------------------------

/** F127 记忆合并卡摘要 */
export function consolidationSummary(candidate: ConsolidationCandidate): string {
  return `建议把 ${candidate.source_count} 条相似记忆合并为一条`;
}

/** F111 规则精简卡摘要 */
export function compactSummary(candidate: CompactCandidate): string {
  return `建议精简「${candidate.file_id}」：约 ${candidate.size_before} 字 → 约 ${candidate.size_after} 字`;
}

/** ISO 时间戳 → 相对时间人话（与 CandidateCard 同规则；纯函数可测版本） */
export function formatRelativeTime(isoString: string, now: number = Date.now()): string {
  const then = new Date(isoString).getTime();
  if (Number.isNaN(then)) return isoString;
  const diffMin = Math.floor((now - then) / 60000);
  if (diffMin < 1) return "刚刚";
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour} 小时前`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 30) return `${diffDay} 天前`;
  return new Date(isoString).toLocaleDateString("zh-CN");
}
