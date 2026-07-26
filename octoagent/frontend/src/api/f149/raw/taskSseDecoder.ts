import type { components } from "../../../generated/f149/task-sse";

type TaskStatus = components["schemas"]["TaskStatus"];
export type RawTaskSseFrame = unknown;
export type RawTaskSseObject = Record<string, RawTaskSseFrame>;
export type SafeDiagnosticJson =
  | null
  | boolean
  | number
  | string
  | SafeDiagnosticJson[]
  | { [key: string]: SafeDiagnosticJson };

const FRAME_KEYS = [
  "actor",
  "event_id",
  "final",
  "payload",
  "task_id",
  "task_seq",
  "ts",
  "type",
] as const;
const TASK_STATUSES = new Set<TaskStatus>([
  "CREATED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
  "QUEUED",
  "WAITING_INPUT",
  "WAITING_APPROVAL",
  "PAUSED",
  "REJECTED",
]);
const SENSITIVE_KEY_PARTS = [
  "api_key",
  "authorization",
  "cookie",
  "credential",
  "password",
  "private_key",
  "secret",
  "token",
] as const;
const SENSITIVE_TEXT_PATTERN =
  /(?:secret|token|password|api[_-]?key|authorization|credential|cookie|private[_-]?key|bearer\s+\S+|sk-[a-z0-9_-]{8,})/i;
const MAX_DIAGNOSTIC_DEPTH = 4;
const MAX_DIAGNOSTIC_ITEMS = 16;
const MAX_DIAGNOSTIC_STRING_LENGTH = 267;
const MAX_DIAGNOSTIC_BYTES = 4096;
const MAX_MODEL_ID_LENGTH = 512;
const MAX_MODEL_RESPONSE_LENGTH = 8192;
const MAX_MODEL_ERROR_LENGTH = 512;
const REDACTED_VALUE = "[REDACTED]";
const TRUNCATED_VALUE = "[TRUNCATED]";

interface TaskSseBaseProjection {
  eventId: string;
  taskId: string;
  taskSeq: number;
  timestamp: string;
  sourceType: string;
  actor: string;
  final: boolean;
}

export type TaskSseProjection =
  | (TaskSseBaseProjection & {
      kind: "state-transition";
      toStatus: TaskStatus;
    })
  | (TaskSseBaseProjection & {
      kind: "artifact-refresh";
    })
  | (TaskSseBaseProjection & {
      kind: "diagnostic";
      diagnostic: SafeDiagnosticJson;
      truncated: boolean;
    });

export type TaskSseDecodeResult =
  { ok: true; event: TaskSseProjection } | { ok: false; reason: string };

function invalid(reason: string): TaskSseDecodeResult {
  return { ok: false, reason };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    (Object.getPrototypeOf(value) === Object.prototype ||
      Object.getPrototypeOf(value) === null)
  );
}

function hasExactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return (
    actual.length === expected.length &&
    actual.every((key, index) => key === expected[index])
  );
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isNullableBoundedString(
  value: unknown,
  maxLength: number,
): value is string | null {
  return (
    value === null ||
    (isNonEmptyString(value) && value.length <= maxLength)
  );
}

function isSensitiveKey(key: string): boolean {
  const normalized = key.toLowerCase().replace(/-/g, "_");
  return SENSITIVE_KEY_PARTS.some((part) => normalized.includes(part));
}

function decodeSafeDiagnostic(
  value: unknown,
  depth = 0,
): SafeDiagnosticJson | undefined {
  if (
    value === null ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  ) {
    return value;
  }
  if (typeof value === "string") {
    if (
      value.length > MAX_DIAGNOSTIC_STRING_LENGTH ||
      (value !== REDACTED_VALUE &&
        value !== TRUNCATED_VALUE &&
        SENSITIVE_TEXT_PATTERN.test(value))
    ) {
      return undefined;
    }
    return value;
  }
  if (depth >= MAX_DIAGNOSTIC_DEPTH) {
    return undefined;
  }
  if (Array.isArray(value)) {
    if (value.length > MAX_DIAGNOSTIC_ITEMS) {
      return undefined;
    }
    const result: SafeDiagnosticJson[] = [];
    for (const item of value) {
      const decoded = decodeSafeDiagnostic(item, depth + 1);
      if (decoded === undefined) {
        return undefined;
      }
      result.push(decoded);
    }
    return result;
  }
  if (!isRecord(value) || Object.keys(value).length > MAX_DIAGNOSTIC_ITEMS) {
    return undefined;
  }
  const result: { [key: string]: SafeDiagnosticJson } = {};
  for (const [key, item] of Object.entries(value)) {
    if (isSensitiveKey(key) && item !== REDACTED_VALUE) {
      return undefined;
    }
    const decoded = decodeSafeDiagnostic(item, depth + 1);
    if (decoded === undefined) {
      return undefined;
    }
    result[key] = decoded;
  }
  return result;
}

function decodeBase(
  value: Record<string, unknown>,
): TaskSseBaseProjection | null {
  if (
    !hasExactKeys(value, FRAME_KEYS) ||
    !isNonEmptyString(value.event_id) ||
    !isNonEmptyString(value.task_id) ||
    !Number.isInteger(value.task_seq) ||
    (value.task_seq as number) < 0 ||
    !isNonEmptyString(value.ts) ||
    !isNonEmptyString(value.type) ||
    !isNonEmptyString(value.actor) ||
    typeof value.final !== "boolean"
  ) {
    return null;
  }
  return {
    eventId: value.event_id,
    taskId: value.task_id,
    taskSeq: value.task_seq as number,
    timestamp: value.ts,
    sourceType: value.type,
    actor: value.actor,
    final: value.final,
  };
}

function decodeModelCallDiagnostic(
  base: TaskSseBaseProjection,
  payload: Record<string, unknown>,
): TaskSseDecodeResult | null {
  const contracts = {
    MODEL_CALL_STARTED: {
      kind: "model_call_started",
      phase: "started",
      keys: ["artifact_ref", "kind", "skill_id"],
      textKey: null,
      maxTextLength: 0,
    },
    MODEL_CALL_COMPLETED: {
      kind: "model_call_completed",
      phase: "completed",
      keys: [
        "artifact_ref",
        "kind",
        "response_summary",
        "skill_id",
      ],
      textKey: "response_summary",
      maxTextLength: MAX_MODEL_RESPONSE_LENGTH,
    },
    MODEL_CALL_FAILED: {
      kind: "model_call_failed",
      phase: "failed",
      keys: ["artifact_ref", "error", "kind", "skill_id"],
      textKey: "error",
      maxTextLength: MAX_MODEL_ERROR_LENGTH,
    },
  } as const;
  const contract = contracts[base.sourceType as keyof typeof contracts];
  if (!contract) {
    return null;
  }
  if (payload.kind === "diagnostic") {
    return null;
  }
  if (
    payload.kind !== contract.kind ||
    !hasExactKeys(payload, contract.keys) ||
    !isNullableBoundedString(payload.skill_id, MAX_MODEL_ID_LENGTH) ||
    !isNullableBoundedString(payload.artifact_ref, MAX_MODEL_ID_LENGTH)
  ) {
    return invalid("model-call");
  }
  if (
    contract.textKey !== null &&
    (!isNonEmptyString(payload[contract.textKey]) ||
      (payload[contract.textKey] as string).length > contract.maxTextLength)
  ) {
    return invalid("model-call");
  }
  return {
    ok: true,
    event: {
      ...base,
      kind: "diagnostic",
      diagnostic: {
        phase: contract.phase,
      },
      truncated: false,
    },
  };
}

export function decodeTaskSseFrame(
  value: RawTaskSseFrame,
): TaskSseDecodeResult {
  if (!isRecord(value)) {
    return invalid("frame-shape");
  }
  const base = decodeBase(value);
  if (!base || !isRecord(value.payload)) {
    return invalid("frame-fields");
  }
  const payload = value.payload;

  if (
    base.sourceType === "STATE_TRANSITION" &&
    payload.kind === "state_transition"
  ) {
    if (
      !hasExactKeys(payload, ["kind", "to_status"]) ||
      typeof payload.to_status !== "string" ||
      !TASK_STATUSES.has(payload.to_status as TaskStatus)
    ) {
      return invalid("state-transition");
    }
    return {
      ok: true,
      event: {
        ...base,
        kind: "state-transition",
        toStatus: payload.to_status as TaskStatus,
      },
    };
  }

  if (
    base.sourceType === "ARTIFACT_CREATED" &&
    payload.kind === "artifact_refresh"
  ) {
    if (
      !hasExactKeys(payload, ["kind", "refresh_artifacts"]) ||
      payload.refresh_artifacts !== true
    ) {
      return invalid("artifact-refresh");
    }
    return {
      ok: true,
      event: {
        ...base,
        kind: "artifact-refresh",
      },
    };
  }

  const modelCall = decodeModelCallDiagnostic(base, payload);
  if (modelCall) {
    return modelCall;
  }

  if (
    payload.kind !== "diagnostic" ||
    !hasExactKeys(payload, [
      "diagnostic",
      "kind",
      "source_type",
      "truncated",
    ]) ||
    payload.source_type !== base.sourceType ||
    typeof payload.truncated !== "boolean"
  ) {
    return invalid("payload-kind");
  }
  const diagnostic = decodeSafeDiagnostic(payload.diagnostic);
  if (
    diagnostic === undefined ||
    new TextEncoder().encode(JSON.stringify(payload)).byteLength >
      MAX_DIAGNOSTIC_BYTES
  ) {
    return invalid("diagnostic-boundary");
  }
  return {
    ok: true,
    event: {
      ...base,
      kind: "diagnostic",
      diagnostic,
      truncated: payload.truncated,
    },
  };
}
