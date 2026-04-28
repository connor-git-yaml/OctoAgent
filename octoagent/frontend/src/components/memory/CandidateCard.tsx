/**
 * CandidateCard — 单条记忆候选交互卡片
 * 支持 accept / edit+accept / reject，操作后乐观更新（卡片从列表移除）
 * 操作失败时 toast 提示并恢复状态
 * Feature 084 FR-8.2 / T054
 */
import { useState } from "react";
import type { MemoryCandidate } from "../../api/memory-candidates";
import { promoteCandidate, discardCandidate } from "../../api/memory-candidates";

/** 候选分类中文标签映射 */
function formatCategory(category: string): string {
  const map: Record<string, string> = {
    preference: "偏好",
    identity: "身份",
    fact: "事实",
    relationship: "关系",
    context: "背景",
    goal: "目标",
    skill: "技能",
    habit: "习惯",
    other: "其他",
  };
  return map[category] ?? category;
}

/** ISO 时间戳转相对时间描述 */
function formatRelativeTime(isoString: string): string {
  const now = Date.now();
  const then = new Date(isoString).getTime();
  if (Number.isNaN(then)) return isoString;
  const diffMs = now - then;
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "刚刚";
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour} 小时前`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 30) return `${diffDay} 天前`;
  return new Date(isoString).toLocaleDateString("zh-CN");
}

export interface CandidateCardProps {
  candidate: MemoryCandidate;
  /** 操作成功后通知父层移除该卡片 */
  onRemove: (id: string) => void;
  /** toast 通知回调 */
  onToast: (message: string, isError: boolean) => void;
}

type CardState = "idle" | "editing" | "busy";

export default function CandidateCard({ candidate, onRemove, onToast }: CandidateCardProps) {
  const [cardState, setCardState] = useState<CardState>("idle");
  const [editContent, setEditContent] = useState(candidate.fact_content);

  async function handleAccept() {
    setCardState("busy");
    try {
      await promoteCandidate(candidate.id);
      onRemove(candidate.id);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "操作失败，请重试";
      onToast(msg, true);
      setCardState("idle");
    }
  }

  async function handleEditAccept() {
    if (cardState === "idle") {
      // 进入编辑模式
      setEditContent(candidate.fact_content);
      setCardState("editing");
      return;
    }
    // 提交编辑内容
    setCardState("busy");
    try {
      await promoteCandidate(candidate.id, editContent.trim() || candidate.fact_content);
      onRemove(candidate.id);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "操作失败，请重试";
      onToast(msg, true);
      setCardState("editing");
    }
  }

  async function handleReject() {
    setCardState("busy");
    try {
      await discardCandidate(candidate.id);
      onRemove(candidate.id);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "操作失败，请重试";
      onToast(msg, true);
      setCardState("idle");
    }
  }

  function handleCancelEdit() {
    setCardState("idle");
    setEditContent(candidate.fact_content);
  }

  const isBusy = cardState === "busy";
  const isEditing = cardState === "editing";
  const confidencePct = Math.round(candidate.confidence * 100);

  return (
    <article className="wb-candidate-card">
      <div className="wb-candidate-card-meta">
        <span className="wb-candidate-card-category">{formatCategory(candidate.category)}</span>
        <span className="wb-candidate-card-confidence">{confidencePct}%</span>
        <span className="wb-candidate-card-time">{formatRelativeTime(candidate.created_at)}</span>
      </div>

      {isEditing ? (
        <textarea
          className="wb-candidate-card-edit-textarea"
          value={editContent}
          onChange={(e) => setEditContent(e.target.value)}
          rows={3}
          aria-label="编辑内容"
          disabled={isBusy}
        />
      ) : (
        <p className="wb-candidate-card-content">{candidate.fact_content}</p>
      )}

      <div className="wb-candidate-card-actions">
        {!isEditing && (
          <button
            type="button"
            className="wb-button wb-button-primary wb-candidate-btn-accept"
            onClick={() => void handleAccept()}
            disabled={isBusy}
          >
            接受
          </button>
        )}

        {isEditing ? (
          <>
            <button
              type="button"
              className="wb-button wb-button-primary wb-candidate-btn-accept"
              onClick={() => void handleEditAccept()}
              disabled={isBusy}
            >
              {isBusy ? "保存中…" : "保存并接受"}
            </button>
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={handleCancelEdit}
              disabled={isBusy}
            >
              取消
            </button>
          </>
        ) : (
          <button
            type="button"
            className="wb-button wb-button-secondary wb-candidate-btn-edit"
            onClick={() => void handleEditAccept()}
            disabled={isBusy}
          >
            编辑后接受
          </button>
        )}

        {!isEditing && (
          <button
            type="button"
            className="wb-button wb-button-tertiary wb-candidate-btn-reject"
            onClick={() => void handleReject()}
            disabled={isBusy}
          >
            {isBusy ? "处理中…" : "忽略"}
          </button>
        )}
      </div>
    </article>
  );
}
