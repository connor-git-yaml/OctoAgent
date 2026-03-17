import type { MemoryConsoleDocument } from "../../types";
import { formatMemoryMode } from "./shared";

interface MemoryHeroSectionProps {
  memory: MemoryConsoleDocument;
  memoryMode: string;
  bridgeTransport: string;
  heroTone: "success" | "warning" | "danger";
  heroTitle: string;
  heroSummary: string;
  stateLabel: string;
  retrievalLabel: string;
}

export default function MemoryHeroSection({
  memory,
  memoryMode,
  bridgeTransport,
  heroTone,
  heroTitle,
  heroSummary,
  stateLabel,
  retrievalLabel,
}: MemoryHeroSectionProps) {
  const retrievalProfile = memory.retrieval_profile;
  const engineLabel = retrievalProfile?.engine_label || formatMemoryMode(memoryMode);
  const transportLabel = retrievalProfile?.transport_label || bridgeTransport || "内建";

  return (
    <section className="wb-hero wb-hero-memory">
      <div className="wb-hero-copy">
        <p className="wb-kicker">Memory</p>
        <h1>{heroTitle}</h1>
        <p>{heroSummary}</p>
        <div className="wb-chip-row">
          <span className="wb-chip">引擎 {engineLabel}</span>
          <span className={`wb-chip ${heroTone === "success" ? "is-success" : "is-warning"}`}>
            状态 {stateLabel}
          </span>
          <span className="wb-chip">检索 {retrievalLabel}</span>
          <span className="wb-chip">接入 {transportLabel}</span>
        </div>
      </div>

      <div className="wb-hero-insights">
        <article className="wb-hero-metric">
          <p className="wb-card-label">当前结论</p>
          <strong>{memory.summary.sor_current_count}</strong>
        </article>
        <article className="wb-hero-metric">
          <p className="wb-card-label">新增片段</p>
          <strong>{memory.summary.fragment_count}</strong>
        </article>
        <article className="wb-hero-metric">
          <p className="wb-card-label">待处理</p>
          <strong>{memory.summary.pending_replay_count + memory.summary.vault_ref_count}</strong>
        </article>
      </div>
    </section>
  );
}
