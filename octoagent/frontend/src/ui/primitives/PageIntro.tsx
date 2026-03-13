import type { ReactNode } from "react";

interface PageIntroProps {
  kicker: string;
  title: string;
  summary: string;
  actions?: ReactNode;
  compact?: boolean;
}

export default function PageIntro({
  kicker,
  title,
  summary,
  actions,
  compact = false,
}: PageIntroProps) {
  return (
    <section className={`wb-hero ${compact ? "wb-hero-compact" : ""}`}>
      <div>
        <p className="wb-kicker">{kicker}</p>
        <h1>{title}</h1>
        <p>{summary}</p>
      </div>
      {actions ? <div className="wb-hero-actions">{actions}</div> : null}
    </section>
  );
}
