import type { ReactElement } from "react";
import type { ResourcePageResolution } from "../../domains/shared/resourcePageState";
import InlineCallout from "./InlineCallout";

export interface ResourceStateProps {
  resolution: ResourcePageResolution;
  title: string;
  detail: string;
  retryLabel?: string;
  onRetry?: () => void;
}

export default function ResourceState({
  resolution,
  title,
  detail,
  retryLabel,
  onRetry,
}: ResourceStateProps): ReactElement | null {
  if (resolution.owner === "global-auth" || resolution.state.kind === "ready") {
    return null;
  }
  const kind = resolution.state.kind;
  const isPassive = kind === "loading" || kind === "empty";
  const canRetry =
    kind === "recoverable-error" ||
    kind === "disconnected" ||
    kind === "conflict";
  const actions =
    canRetry && retryLabel && onRetry ? (
      <button type="button" onClick={onRetry}>
        {retryLabel}
      </button>
    ) : undefined;

  return (
    <section
      role={isPassive ? "status" : "alert"}
      aria-label={title}
      aria-live={kind === "loading" ? "polite" : undefined}
    >
      <InlineCallout
        title={title}
        tone={isPassive ? "muted" : "error"}
        actions={actions}
      >
        {detail}
      </InlineCallout>
    </section>
  );
}
