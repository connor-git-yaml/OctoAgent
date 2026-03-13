import { Link } from "react-router-dom";
import type {
  PolicyProfileItem,
  ProjectSelectorDocument,
  SetupGovernanceDocument,
  SetupReviewSummary,
} from "../../types";
import { summaryTone } from "./shared";

interface SettingsOverviewProps {
  usingEchoMode: boolean;
  review: SetupReviewSummary;
  selector: ProjectSelectorDocument;
  setup: SetupGovernanceDocument;
  providerDraftCount: number;
  activeProvidersCount: number;
  aliasDraftCount: number;
  defaultProviderId: string;
  memoryLabel: string;
  memoryStatus: string;
  selectedSkillCount: number;
  blockedSkillCount: number;
  unavailableSkillCount: number;
  currentPolicy: PolicyProfileItem | null;
  onQuickConnect: () => void;
  onReview: () => void;
  onApply: () => void;
  connectBusy: boolean;
  onScrollToSection: (sectionId: string) => void;
}

export default function SettingsOverview({
  usingEchoMode,
  review,
  selector,
  setup,
  providerDraftCount,
  activeProvidersCount,
  aliasDraftCount,
  defaultProviderId,
  memoryLabel,
  memoryStatus,
  selectedSkillCount,
  blockedSkillCount,
  unavailableSkillCount,
  currentPolicy,
  onQuickConnect,
  onReview,
  onApply,
  connectBusy,
  onScrollToSection,
}: SettingsOverviewProps) {
  const reviewBlockingCount = review.blocking_reasons.length;
  const reviewWarningCount = review.warnings.length;
  const primaryTitle = usingEchoMode
    ? "先把真实模型接起来"
    : review.ready
      ? "配置已经够用，先保存再验证"
      : "还差几项设置才能稳定开始";
  const primarySummary = usingEchoMode
    ? "现在还是体验模式。最少先配好 1 个 Provider、密钥和运行连接，再开始第一次真实对话。"
    : review.ready
      ? "当前草稿已经可以开始聊天。先保存这次修改，再回聊天页发第一条消息。"
      : "不用一次看完所有配置。先完成最少必要步骤，再回来继续扩展能力。";
  const primaryActionLabel = review.ready
    ? "先保存当前修改"
    : usingEchoMode
      ? "连接并启用真实模型"
      : "检查并准备连接";
  const checklistItems = review.ready
    ? [
        "保存当前配置，确保这次修改已经生效。",
        "回到聊天页发第一条消息，验证真实模型和 Butler 是否正常响应。",
        "需要更多能力时，再回来补 Provider、Memory 或安全开关。",
      ]
    : [
        review.next_actions[0] ??
          (usingEchoMode
            ? "先添加一个 Provider，并填好密钥或完成 OAuth。"
            : "先处理 review 里提示的阻塞项。"),
        "执行一次配置检查，确认阻塞项和提醒是否已经收口。",
        usingEchoMode
          ? "连接成功后回聊天页发第一条消息。"
          : "保存配置后回聊天页验证一次真实对话。",
      ];
  const minimumStatusValue = usingEchoMode ? "体验模式" : "真实模型模式";
  const minimumStatusHint = usingEchoMode
    ? "当前只能先体验页面和流程。"
    : "当前已经接入真实模型链路。";
  const nextValidationLabel = review.ready ? "聊天页" : "保存检查";
  const nextValidationHint = review.ready
    ? "保存后直接回聊天页发第一条消息。"
    : "先在当前页做一次检查，再决定保存或一键接入。";

  return (
    <>
      <section
        id="settings-group-overview"
        className="wb-hero wb-settings-hero wb-settings-hero-refined"
      >
        <div className="wb-hero-copy">
          <p className="wb-kicker">Settings</p>
          <h1>{primaryTitle}</h1>
          <p>{primarySummary}</p>
          <div className="wb-chip-row">
            <span className="wb-chip">{minimumStatusValue}</span>
            <span className="wb-chip">
              当前 Project {selector.current_project_id} / {selector.current_workspace_id}
            </span>
            <span className={`wb-chip ${review.ready ? "is-success" : "is-warning"}`} role="status">
              {review.ready ? "可以开始聊天" : `还差 ${reviewBlockingCount} 项`}
            </span>
          </div>
        </div>
        <div className="wb-settings-hero-actions">
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={review.ready ? onApply : onQuickConnect}
            disabled={connectBusy}
          >
            {primaryActionLabel}
          </button>
          <button
            type="button"
            className="wb-button wb-button-secondary"
            onClick={onReview}
            disabled={connectBusy}
          >
            检查配置
          </button>
          {review.ready ? (
            <Link className="wb-button wb-button-secondary" to="/chat">
              回聊天验证
            </Link>
          ) : (
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={onApply}
              disabled={connectBusy}
            >
              保存配置
            </button>
          )}
        </div>
      </section>

      <div className="wb-settings-summary-grid">
        <article className={`wb-card wb-card-accent is-${summaryTone(review)}`}>
          <p className="wb-card-label">当前模式</p>
          <strong>{minimumStatusValue}</strong>
          <span>{minimumStatusHint}</span>
          <span>{setup.provider_runtime.status}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">还差几项</p>
          <strong>{review.ready ? 0 : reviewBlockingCount}</strong>
          <span>阻塞项 {reviewBlockingCount}</span>
          <span>提醒 {reviewWarningCount}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">最先要配什么</p>
          <strong>{checklistItems[0]}</strong>
          <span>已启用 Provider {activeProvidersCount}</span>
          <span>当前草稿 {providerDraftCount}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">配完去哪验证</p>
          <strong>{nextValidationLabel}</strong>
          <span>{nextValidationHint}</span>
          <span>默认模型来源 {defaultProviderId || "未设置"}</span>
        </article>
      </div>

      <section className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">最少必要步骤</p>
            <h3>先按这 3 步走通一次</h3>
          </div>
          <div className="wb-inline-actions wb-inline-actions-wrap">
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={onReview}
              disabled={connectBusy}
            >
              先检查配置
            </button>
            {review.ready ? (
              <Link className="wb-button wb-button-tertiary" to="/chat">
                回聊天验证
              </Link>
            ) : null}
          </div>
        </div>

        <div className="wb-note-stack">
          {checklistItems.map((item) => (
            <div key={item} className="wb-note">
              <strong>下一步</strong>
              <span>{item}</span>
            </div>
          ))}
          <div className="wb-note">
            <strong>这页下面还有什么</strong>
            <span>
              Providers、模型别名、Memory、安全和渠道都还在下面；只想先用起来，不必一次看完。
            </span>
          </div>
        </div>

        <div className="wb-card-grid wb-card-grid-3">
          <article className="wb-card">
            <p className="wb-card-label">模型与别名</p>
            <strong>{providerDraftCount}</strong>
            <span>已启用 {activeProvidersCount}</span>
            <span>别名 {aliasDraftCount}</span>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">记忆与能力</p>
            <strong>{memoryLabel}</strong>
            <span>{memoryStatus}</span>
            <span>
              默认技能 {selectedSkillCount} / 阻塞 {blockedSkillCount} / 不可用 {unavailableSkillCount}
            </span>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">当前默认边界</p>
            <strong>{currentPolicy?.label ?? "未选择"}</strong>
            <span>{currentPolicy?.approval_policy ?? "未选择"}</span>
          </article>
        </div>
      </section>

      <nav className="wb-settings-section-nav" aria-label="Settings sections">
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("overview")}>
          概览
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("main-agent")}>
          Providers
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("aliases")}>
          模型别名
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("channels")}>
          渠道
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("memory")}>
          Memory
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("governance")}>
          安全与能力
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("review")}>
          保存检查
        </button>
      </nav>
    </>
  );
}
