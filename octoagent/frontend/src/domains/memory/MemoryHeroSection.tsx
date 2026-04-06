import type { MemoryConsoleDocument } from "../../types";

interface MemoryHeroSectionProps {
  memory: MemoryConsoleDocument;
  heroTone: "success" | "warning" | "danger";
  heroTitle: string;
  heroSummary: string;
  stateLabel: string;
  retrievalLabel: string;
}

function formatNextConsolidation(isoString: string): string {
  if (!isoString) return "未调度";
  try {
    const d = new Date(isoString);
    if (Number.isNaN(d.getTime())) return "未调度";
    const now = new Date();
    const diffMs = d.getTime() - now.getTime();
    if (diffMs < 0) return "即将执行";
    const diffMin = Math.round(diffMs / 60000);
    if (diffMin < 60) return `${diffMin} 分钟后`;
    const diffH = Math.floor(diffMin / 60);
    const remainMin = diffMin % 60;
    return remainMin > 0 ? `${diffH}h${remainMin}m 后` : `${diffH} 小时后`;
  } catch {
    return "未调度";
  }
}

export default function MemoryHeroSection({
  memory,
  heroTone,
  heroTitle,
  heroSummary,
  stateLabel,
  retrievalLabel,
}: MemoryHeroSectionProps) {
  const retrievalProfile = memory.retrieval_profile;
  const engineLabel = retrievalProfile?.engine_label || "内建记忆引擎";
  const readableCount = memory.summary.sor_readable_count ?? memory.summary.sor_current_count;

  return (
    <section className="wb-hero wb-hero-memory">
      <div className="wb-hero-copy">
        <p className="wb-kicker">记忆</p>
        <h1>{heroTitle}</h1>
        <p>{heroSummary}</p>
        <div className="wb-chip-row">
          <span className="wb-chip">引擎 {engineLabel}</span>
          <span className={`wb-chip ${heroTone === "success" ? "is-success" : "is-warning"}`}>
            状态 {stateLabel}
          </span>
          <span className="wb-chip">检索 {retrievalLabel}</span>
        </div>
      </div>

      <div className="wb-hero-insights">
        <article className="wb-hero-metric">
          <p className="wb-card-label">记忆事实</p>
          <strong>{readableCount}</strong>
        </article>
        <article className="wb-hero-metric">
          <p className="wb-card-label">待整理</p>
          <strong>{memory.summary.pending_consolidation_count}</strong>
        </article>
        <article className="wb-hero-metric">
          <p className="wb-card-label">下次整理</p>
          <strong>{formatNextConsolidation(memory.summary.next_consolidation_at)}</strong>
        </article>
      </div>
    </section>
  );
}
