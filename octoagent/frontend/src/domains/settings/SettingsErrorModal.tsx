/**
 * SettingsErrorModal — Feature 079 Phase 1。
 *
 * 解决 buildSetupDraft silent-return 和 SETUP_REVIEW_BLOCKED 的错误被前一代
 * RootErrorBoundary（chunk 404 灾难）一同吞掉的问题。把保存失败原因 / field
 * 校验错误统一渲染到一个独立 modal，portal 到 body —— 即便 SettingsPage 子树
 * 出现异常，这个 modal 也能继续展示（因为它不在 RouteErrorBoundary 内）。
 *
 * 渲染两类错误：
 * 1. fieldErrors：前端 buildConfigPayload 返回的字段级错误（未进后端）
 * 2. blocking_reasons：后端 setup.review 返回的 blocking items（进了后端被拒）
 */

import { createPortal } from "react-dom";

export interface SettingsErrorItem {
  /** 唯一标识（用作 React key + 可滚动定位） */
  id: string;
  /** 一句话标题，用户能读 */
  title: string;
  /** 可选的详细说明（如具体字段 / 建议操作） */
  detail?: string;
  /** 可选：推荐下一步（Backend review 的 recommended_action） */
  recommendedAction?: string;
}

export type SettingsErrorKind = "field" | "review" | "runtime";

interface SettingsErrorModalProps {
  open: boolean;
  kind: SettingsErrorKind;
  items: SettingsErrorItem[];
  onClose: () => void;
}

const TITLE_BY_KIND: Record<SettingsErrorKind, string> = {
  field: "保存前，有几个字段需要修一下",
  review: "保存检查未通过，下面几项阻塞了本次保存",
  runtime: "保存请求出错了",
};

const LEAD_BY_KIND: Record<SettingsErrorKind, string> = {
  field: "以下字段的当前值不符合 schema，请先修正再点保存。",
  review: "后端把这些风险判为 blocking；你可以按推荐操作修复后，再次保存。",
  runtime: "请求没有走完，详见下方信息。可以稍后重试，或刷新后再试一次。",
};

export default function SettingsErrorModal({
  open,
  kind,
  items,
  onClose,
}: SettingsErrorModalProps) {
  if (!open) {
    return null;
  }
  return createPortal(
    <div
      className="wb-modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="settings-error-modal-title"
      onClick={(e) => {
        // 只在点击 overlay 本身时关闭（不是点到了 body 内部）
        if (e.target === e.currentTarget) {
          onClose();
        }
      }}
    >
      <div
        className="wb-modal-body"
        style={{ maxWidth: "680px", maxHeight: "80vh", overflow: "auto" }}
      >
        <div className="wb-modal-head">
          <h3 id="settings-error-modal-title" style={{ margin: 0 }}>
            {TITLE_BY_KIND[kind]}
          </h3>
          <button
            type="button"
            className="wb-button wb-button-secondary"
            onClick={onClose}
            aria-label="关闭"
          >
            关闭
          </button>
        </div>
        <p className="wb-muted" style={{ marginTop: "0.5rem" }}>
          {LEAD_BY_KIND[kind]}
        </p>
        {items.length === 0 ? (
          <p className="wb-muted">（没有更多详细信息）</p>
        ) : (
          <ul className="wb-settings-error-list">
            {items.map((item) => (
              <li key={item.id} className="wb-settings-error-item">
                <strong>{item.title}</strong>
                {item.detail ? (
                  <div className="wb-settings-error-detail">{item.detail}</div>
                ) : null}
                {item.recommendedAction ? (
                  <div className="wb-settings-error-action">
                    <em>建议：</em>
                    {item.recommendedAction}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>,
    document.body
  );
}
