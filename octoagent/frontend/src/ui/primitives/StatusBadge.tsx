import type { ReactNode } from "react";

interface StatusBadgeProps {
  tone: string;
  children: ReactNode;
  className?: string;
}

export default function StatusBadge({
  tone,
  children,
  className,
}: StatusBadgeProps) {
  return (
    <span className={`wb-status-pill is-${tone}${className ? ` ${className}` : ""}`}>
      {children}
    </span>
  );
}
