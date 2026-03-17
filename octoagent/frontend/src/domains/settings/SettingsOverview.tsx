import { Link } from "react-router-dom";
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
  selector,
  onQuickConnect,
  onReview,
  onApply,
  connectBusy,
  onScrollToSection,
}: SettingsOverviewProps) {
  const reviewBlockingCount = review.blocking_reasons.length;
  const currentProject =
    selector.available_projects.find((item) => item.project_id === selector.current_project_id) ??
    null;
  const currentWorkspace =
    selector.available_workspaces.find(
      (item) => item.workspace_id === selector.current_workspace_id
    ) ?? null;
  const primaryTitle = usingEchoMode
    ? "先连上至少一个模型 Provider"
    : review.ready
      ? "现在已经可以回聊天验证"
      : `还差 ${Math.max(reviewBlockingCount, 1)} 项才能稳定开始`;
  const primaryActionLabel = usingEchoMode
    ? "连接真实模型"
    : review.ready
      ? "保存后回聊天验证"
      : "先检查阻塞项";
  const statusChipLabel = usingEchoMode
    ? review.ready
      ? "可以先保存"
      : `还差 ${reviewBlockingCount} 项`
    : review.ready
      ? "可以回聊天验证"
      : `还差 ${reviewBlockingCount} 项`;

  return (
    <>
      <section
        id="settings-group-overview"
        className="wb-hero wb-settings-hero wb-settings-hero-refined"
      >
        <div className="wb-hero-copy">
          <p className="wb-kicker">Settings</p>
          <h1>{primaryTitle}</h1>
          <div className="wb-chip-row">
            <span className="wb-chip">
              {usingEchoMode ? "未连接真实模型" : "已连接真实模型"}
            </span>
            <span className="wb-chip">
              {currentProject?.name ?? selector.current_project_id} /{" "}
              {currentWorkspace?.name ?? selector.current_workspace_id}
            </span>
            <span className={`wb-chip ${review.ready ? "is-success" : "is-warning"}`} role="status">
              {statusChipLabel}
            </span>
          </div>
        </div>
        <div className="wb-settings-hero-actions">
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={usingEchoMode ? onQuickConnect : review.ready ? onApply : onQuickConnect}
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
          {usingEchoMode ? (
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={onApply}
              disabled={connectBusy}
            >
              保存配置
            </button>
          ) : review.ready ? (
            <Link className="wb-button wb-button-secondary" to="/">
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

      <nav className="wb-settings-section-nav" aria-label="Settings sections">
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("overview")}>
          概览
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("models")}>
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
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("review")}>
          保存检查
        </button>
      </nav>
    </>
  );
}
