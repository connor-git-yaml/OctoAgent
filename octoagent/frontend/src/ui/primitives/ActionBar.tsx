import type { ReactNode } from "react";

interface ActionBarProps {
  children: ReactNode;
  className?: string;
}

export default function ActionBar({ children, className }: ActionBarProps) {
  return <div className={`wb-action-bar${className ? ` ${className}` : ""}`}>{children}</div>;
}
