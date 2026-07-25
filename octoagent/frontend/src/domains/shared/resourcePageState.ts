import type { SensitivityLevel } from "../../types";
import { mapF149ErrorOwnership } from "../../api/f149/errorOwnership";

export type ResourceSurfaceState =
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "empty" }
  | { kind: "recoverable-error" }
  | { kind: "permission-denied" }
  | { kind: "not-found" }
  | { kind: "disconnected" }
  | { kind: "conflict" };

export type ResourcePageResolution =
  | {
      owner: "global-auth";
      state: null;
    }
  | {
      owner: "surface";
      state: ResourceSurfaceState;
    };

export interface ResourcePageInput {
  loading: boolean;
  hasContent: boolean;
  connected: boolean;
  error: Error | null;
}

export interface AdvancedValueInput {
  value: string;
  kind: "path" | "command";
  sensitivity: SensitivityLevel;
  sanitized: boolean;
  copyPermitted: boolean;
  maxDisplayLength?: number;
}

export type AdvancedValuePresentation =
  | {
      kind: "available";
      displayValue: string;
      copyValue: string | null;
      canCopy: boolean;
    }
  | {
      kind: "unavailable";
      displayValue: "不可显示";
      copyValue: null;
      canCopy: false;
    };

export function resolveResourcePageState(
  input: ResourcePageInput,
): ResourcePageResolution {
  if (input.error) {
    const ownership = mapF149ErrorOwnership(input.error);
    if (ownership.owner === "global-auth") {
      return {
        owner: "global-auth",
        state: null,
      };
    }
    const kind =
      ownership.state === "forbidden"
        ? "permission-denied"
        : ownership.state === "not-found"
          ? "not-found"
          : ownership.state === "conflict"
            ? "conflict"
            : "recoverable-error";
    return {
      owner: "surface",
      state: { kind },
    };
  }
  if (input.loading) {
    return {
      owner: "surface",
      state: { kind: "loading" },
    };
  }
  if (!input.connected) {
    return {
      owner: "surface",
      state: { kind: "disconnected" },
    };
  }
  if (!input.hasContent) {
    return {
      owner: "surface",
      state: { kind: "empty" },
    };
  }
  return {
    owner: "surface",
    state: { kind: "ready" },
  };
}

const SECRET_SHAPED_PATTERN =
  /(?:api[_-]?key|authorization|bearer\s+\S+|cookie|credential|password|private[_-]?key|secret|token|sk-[a-z0-9_-]{8,})/i;

function unavailableAdvancedValue(): AdvancedValuePresentation {
  return {
    kind: "unavailable",
    displayValue: "不可显示",
    copyValue: null,
    canCopy: false,
  };
}

function isWorkspaceRelativePath(value: string): boolean {
  return (
    !value.startsWith("/") &&
    !value.startsWith("~") &&
    !value.startsWith("file:") &&
    !/^[a-z]:[\\/]/i.test(value) &&
    !value.split(/[\\/]/).includes("..")
  );
}

function truncateMiddle(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }
  const sideLength = Math.floor((maxLength - 1) / 2);
  return `${value.slice(0, sideLength)}…${value.slice(-sideLength)}`;
}

export function presentAdvancedValue(
  input: AdvancedValueInput,
): AdvancedValuePresentation {
  const value = input.value.trim();
  const maxLength = input.maxDisplayLength ?? 56;
  if (
    input.sensitivity !== "operator_sensitive" ||
    !input.sanitized ||
    value.length === 0 ||
    value !== input.value ||
    maxLength < 9 ||
    SECRET_SHAPED_PATTERN.test(value) ||
    (input.kind === "path" && !isWorkspaceRelativePath(value)) ||
    (input.kind === "command" && /[\r\n]/.test(value))
  ) {
    return unavailableAdvancedValue();
  }
  const canCopy = input.copyPermitted;
  return {
    kind: "available",
    displayValue: truncateMiddle(value, maxLength),
    copyValue: canCopy ? value : null,
    canCopy,
  };
}
