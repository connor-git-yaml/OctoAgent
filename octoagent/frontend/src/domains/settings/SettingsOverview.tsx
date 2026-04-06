import type {
  ProjectSelectorDocument,
  SetupReviewSummary,
} from "../../types";

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
        className="wb-hero wb-settings-hero wb-settings-hero-refined"
      >
        <div className="wb-hero-copy">
          <h1 style={{ fontSize: "1.75rem" }}>设置</h1>
          {subtitle ? <p style={{ margin: 0, color: "var(--cp-muted)" }}>{subtitle}</p> : null}
        </div>
        <div className="wb-settings-hero-actions" style={{ display: "flex", gap: 10, width: "auto" }}>
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={usingEchoMode ? onQuickConnect : onApply}
            disabled={connectBusy}
          >
            {usingEchoMode ? "连接真实模型" : "保存配置"}
          </button>
          <button
            type="button"
            className="wb-button wb-button-secondary"
            onClick={onReview}
            disabled={connectBusy}
          >
            检查配置
          </button>
        </div>
      </section>

      <nav className="wb-settings-section-nav" aria-label="设置导航">
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("overview")}>
          概览
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("models")}>
          供应商
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("aliases")}>
          模型别名
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("memory")}>
          记忆
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("channels")}>
          渠道
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("resource-limits")}>
          资源限制
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("review")}>
          保存检查
        </button>
      </nav>
    </>
  );
}
