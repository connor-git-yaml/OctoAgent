import type {
  ProjectSelectorDocument,
  SetupReviewSummary,
} from "../../types";
import "./SettingsOverview.css";

interface SettingsOverviewProps {
  usingEchoMode: boolean;
  review: SetupReviewSummary;
  selector: ProjectSelectorDocument;
  onQuickConnect: () => void;
  onReview: () => void;
  onApply: () => void;
  connectBusy: boolean;
  onScrollToSection: (sectionId: string) => void;
}

export default function SettingsOverview({
  usingEchoMode,
  review,
  selector: _selector,
  onQuickConnect,
  onReview,
  onApply,
  connectBusy,
  onScrollToSection,
}: SettingsOverviewProps) {
  const reviewBlockingCount = review.blocking_reasons.length;
  const subtitle = usingEchoMode
    ? "先连上至少一个模型 Provider"
    : review.ready
      ? ""
      : `还差 ${Math.max(reviewBlockingCount, 1)} 项才能稳定开始`;

  return (
    <>
      <section
        id="settings-group-overview"
        className="f149-settings-hero"
      >
        <div className="f149-settings-hero-copy">
          <h1>设置</h1>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
        <div className="f149-settings-hero-actions">
          <button
            type="button"
            className="f149-settings-button is-primary"
            onClick={usingEchoMode ? onQuickConnect : onApply}
            disabled={connectBusy}
          >
            {usingEchoMode ? "连接真实模型" : "保存配置"}
          </button>
          <button
            type="button"
            className="f149-settings-button"
            onClick={onReview}
            disabled={connectBusy}
          >
            检查配置
          </button>
        </div>
      </section>

      <nav className="f149-settings-nav" aria-label="设置导航">
        <button type="button" className="f149-settings-chip" onClick={() => onScrollToSection("overview")}>
          概览
        </button>
        <button type="button" className="f149-settings-chip" onClick={() => onScrollToSection("models")}>
          供应商
        </button>
        <button type="button" className="f149-settings-chip" onClick={() => onScrollToSection("aliases")}>
          模型别名
        </button>
        <button type="button" className="f149-settings-chip" onClick={() => onScrollToSection("memory")}>
          记忆
        </button>
        <button type="button" className="f149-settings-chip" onClick={() => onScrollToSection("channels")}>
          渠道
        </button>
        <button type="button" className="f149-settings-chip" onClick={() => onScrollToSection("resource-limits")}>
          资源限制
        </button>
        <button type="button" className="f149-settings-chip" onClick={() => onScrollToSection("review")}>
          保存检查
        </button>
        <button
          type="button"
          className="f149-settings-chip is-advanced"
          onClick={() => onScrollToSection("maintenance")}
        >
          高级
        </button>
      </nav>
    </>
  );
}
