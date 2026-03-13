import type { ReactNode } from "react";

interface InlineCalloutProps {
  title: ReactNode;
  children: ReactNode;
  tone?: "muted" | "error";
  actions?: ReactNode;
}

export default function InlineCallout({
  title,
  children,
  tone = "muted",
  actions,
}: InlineCalloutProps) {
  return (
    <div className={`wb-inline-banner is-${tone}`}>
      <div className="wb-callout-copy">
        <strong>{title}</strong>
        <span>{children}</span>
      </div>
      {actions ? <div className="wb-action-bar">{actions}</div> : null}
    </div>
  );
}
