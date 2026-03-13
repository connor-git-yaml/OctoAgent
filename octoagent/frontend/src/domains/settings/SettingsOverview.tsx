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

  return (
    <>
      <section
        id="settings-group-overview"
        className="wb-hero wb-settings-hero wb-settings-hero-refined"
      >
        <div className="wb-hero-copy">
          <p className="wb-kicker">Settings</p>
          <h1>系统连接与默认能力</h1>
          <p>统一管理 Provider、模型别名、渠道入口、Memory 后端和平台级安全开关。</p>
          <div className="wb-chip-row">
            <span className="wb-chip">{usingEchoMode ? "体验模式" : "真实模型模式"}</span>
            <span className="wb-chip">
              当前 Project {selector.current_project_id} / {selector.current_workspace_id}
            </span>
            <span className={`wb-chip ${review.ready ? "is-success" : "is-warning"}`} role="status">
              {review.ready ? "检查通过" : `待处理 ${reviewBlockingCount}`}
            </span>
          </div>
        </div>
        <div className="wb-settings-hero-actions">
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={onQuickConnect}
            disabled={connectBusy}
          >
            {usingEchoMode ? "连接并启用真实模型" : "保存并重新连接"}
          </button>
          <button
            type="button"
            className="wb-button wb-button-secondary"
            onClick={onReview}
            disabled={connectBusy}
          >
            检查配置
          </button>
          <button
            type="button"
            className="wb-button wb-button-secondary"
            onClick={onApply}
            disabled={connectBusy}
          >
            保存配置
          </button>
        </div>
      </section>

      <div className="wb-settings-summary-grid">
        <article className={`wb-card wb-card-accent is-${summaryTone(review)}`}>
          <p className="wb-card-label">配置状态</p>
          <strong>{review.ready ? "可以保存" : "需要处理"}</strong>
          <span>阻塞项 {reviewBlockingCount}</span>
          <span>提醒 {reviewWarningCount}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">接入模式</p>
          <strong>{usingEchoMode ? "Echo" : "LiteLLM"}</strong>
          <span>{setup.provider_runtime.status}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">Providers</p>
          <strong>{providerDraftCount}</strong>
          <span>已启用 {activeProvidersCount}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">模型别名</p>
          <strong>{aliasDraftCount}</strong>
          <span>默认引用 {defaultProviderId || "未设置"}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">Memory</p>
          <strong>{memoryLabel}</strong>
          <span>{memoryStatus}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">默认技能</p>
          <strong>{selectedSkillCount}</strong>
          <span>阻塞 {blockedSkillCount} / 不可用 {unavailableSkillCount}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">安全等级</p>
          <strong>{currentPolicy?.label ?? "未选择"}</strong>
          <span>{currentPolicy?.approval_policy ?? "未选择"}</span>
        </article>
      </div>

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
