/**
 * ProposalCard — F127 记忆合并 / F111 规则精简共用提议卡（F145）
 *
 * 卡面只有人话（类型标签 + 摘要 + 相对时间 + 可选正文）；理由/diff/来源预览等
 * 决策材料进 <details> 折叠区（CLAUDE.md Web UI 规范：技术信息放折叠区）。
 * accept/reject 的失败呈现（conflict 终态/可重试分流）由页面层处理——本卡只管
 * busy 态与触发回调。
 */
import { useState, type CSSProperties, type ReactNode } from "react";
import { formatRelativeTime } from "./approvalModels";

/** 折叠详情区（tokens inline，不进 index.css——余量仅 3 行，DiffBody 先例） */
const DETAILS_STYLE: CSSProperties = {
  margin: "var(--space-sm) 0",
};

const DETAILS_SUMMARY_STYLE: CSSProperties = {
  cursor: "pointer",
  color: "var(--cp-muted)",
  userSelect: "none",
};

export interface ProposalCardProps {
  /** 类型标签（人话）：如「记忆合并」「规则精简」 */
  typeLabel: string;
  /** 一句话摘要（人话） */
  summary: string;
  /** ISO 创建时间（展示为相对时间） */
  createdAt: string;
  /** 可选正文（如 F127 合并后的记忆内容） */
  body?: ReactNode;
  /** 折叠区决策材料（理由 / diff / 来源预览） */
  details?: ReactNode;
  /** 敏感提议标记（F127 is_sensitive） */
  sensitive?: boolean;
  /** 接受（页面层负责 API + toast + 移除） */
  onAccept: () => Promise<void>;
  /** 拒绝 */
  onReject: () => Promise<void>;
  /** L1 锚点（仅需要登记的卡传入；见 e2e/selectors.ts） */
  rootTestId?: string;
  acceptTestId?: string;
}

export default function ProposalCard({
  typeLabel,
  summary,
  createdAt,
  body,
  details,
  sensitive,
  onAccept,
  onReject,
  rootTestId,
  acceptTestId,
}: ProposalCardProps) {
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
    } catch {
      // 失败呈现（toast/移除分流）由页面层回调内部处理；这里兜底吞掉，
      // 防止回调实现漏 catch 时产生 unhandled rejection——卡片只负责恢复 idle。
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="wb-candidate-card" data-testid={rootTestId}>
      <div className="wb-candidate-card-meta">
        <span className="wb-candidate-card-category">{typeLabel}</span>
        {sensitive && (
          <span className="wb-candidate-card-category">敏感内容</span>
        )}
        <span className="wb-candidate-card-time">
          {formatRelativeTime(createdAt)}
        </span>
      </div>

      <p className="wb-candidate-card-content">{summary}</p>
      {body}

      {details && (
        <details style={DETAILS_STYLE}>
          <summary style={DETAILS_SUMMARY_STYLE}>查看详情</summary>
          {details}
        </details>
      )}

      <div className="wb-candidate-card-actions">
        <button
          type="button"
          className="wb-button wb-button-primary"
          onClick={() => void run(onAccept)}
          disabled={busy}
          data-testid={acceptTestId}
        >
          {busy ? "处理中…" : "接受"}
        </button>
        <button
          type="button"
          className="wb-button wb-button-tertiary"
          onClick={() => void run(onReject)}
          disabled={busy}
        >
          拒绝
        </button>
      </div>
    </article>
  );
}
