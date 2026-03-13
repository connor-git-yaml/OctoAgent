import type { ReactNode } from "react";

interface HoverRevealProps {
  label: string;
  children: ReactNode;
  expanded: boolean;
  onToggle: (expanded: boolean) => void;
  ariaLabel: string;
}

export default function HoverReveal({
  label,
  children,
  expanded,
  onToggle,
  ariaLabel,
}: HoverRevealProps) {
  return (
    <div
      className="wb-hover-reveal"
      onMouseEnter={() => onToggle(true)}
      onMouseLeave={() => onToggle(false)}
    >
      <button
        type="button"
        className="wb-hover-reveal-trigger"
        aria-expanded={expanded}
        onClick={() => onToggle(!expanded)}
        onFocus={() => onToggle(true)}
        onBlur={() => onToggle(false)}
      >
        {label}
      </button>
      {expanded ? (
        <div className="wb-hover-reveal-card" role="note" aria-label={ariaLabel}>
          {children}
        </div>
      ) : null}
    </div>
  );
}
