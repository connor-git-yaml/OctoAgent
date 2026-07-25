import type { components } from "../../generated/f149/actions";
import type { ActionResultEnvelope } from "../../types";
import { executeWorkbenchActionWithRefresh } from "./controlPlaneActions";

type F149Schemas = components["schemas"];

export interface F149ActionParamsById {
  "agent_profile.update_resource_limits": F149Schemas["F149ResourceLimitsParams"];
  "behavior.read_file": F149Schemas["F149BehaviorReadParams"];
  "behavior.write_file": F149Schemas["F149BehaviorWriteParams"];
  "behavior.restore_version": F149Schemas["F149BehaviorRestoreParams"];
  "memory.consolidate": F149Schemas["F149MemoryConsolidateParams"];
  "mcp_provider.install": F149Schemas["F149McpInstallParams"];
  "mcp_provider.install_status": F149Schemas["F149McpInstallStatusParams"];
}

export interface F149ActionResultById {
  "agent_profile.update_resource_limits": F149Schemas["F149ResourceLimitsResult"];
  "behavior.read_file": F149Schemas["F149BehaviorReadResult"];
  "behavior.write_file": F149Schemas["F149BehaviorWriteResult"];
  "behavior.restore_version": F149Schemas["F149BehaviorRestoreResult"];
  "memory.consolidate": F149Schemas["F149MemoryConsolidateResult"];
  "mcp_provider.install": F149Schemas["F149McpInstallResult"];
  "mcp_provider.install_status": F149Schemas["F149McpInstallStatusResult"];
}

export type F149ActionId = keyof F149ActionParamsById;

export const F149_ACTION_IDS = [
  "agent_profile.update_resource_limits",
  "behavior.read_file",
  "behavior.write_file",
  "behavior.restore_version",
  "memory.consolidate",
  "mcp_provider.install",
  "mcp_provider.install_status",
] as const satisfies readonly F149ActionId[];

export type F149ActionCommand = {
  [ActionId in F149ActionId]: {
    actionId: ActionId;
    params: F149ActionParamsById[ActionId];
  };
}[F149ActionId];

export interface F149ActionExecutor {
  (
    actionId: string,
    params: Record<string, unknown>,
  ): Promise<ActionResultEnvelope | null>;
}

export type F149ActionRefreshHandlers = Parameters<
  typeof executeWorkbenchActionWithRefresh
>[3];

export interface F149ActionSuccess<
  ActionId extends F149ActionId = F149ActionId,
> {
  ok: true;
  actionId: ActionId;
  data: F149ActionResultById[ActionId];
  envelope: ActionResultEnvelope;
}

export type F149ActionErrorKind =
  "invalid-command" | "execution-failed" | "action-failed" | "invalid-result";

export interface F149ActionFailure {
  ok: false;
  actionId: F149ActionId | null;
  error: {
    kind: F149ActionErrorKind;
    code?: string;
  };
}

export type F149ActionOutcome<ActionId extends F149ActionId = F149ActionId> =
  F149ActionSuccess<ActionId> | F149ActionFailure;

type RuntimeObject = Record<string, unknown>;
type RuntimeValidator = (value: unknown) => boolean;

const RESOURCE_LIMIT_KEYS = [
  "max_budget_usd",
  "max_duration_seconds",
  "max_request_tokens",
  "max_response_tokens",
  "max_steps",
  "max_tool_calls",
  "repeat_signature_threshold",
] as const;

function objectValue(value: unknown): RuntimeObject | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as RuntimeObject)
    : null;
}

function hasExactKeys(
  value: RuntimeObject,
  required: readonly string[],
  optional: readonly string[] = [],
): boolean {
  const keys = Object.keys(value);
  const allowed = new Set([...required, ...optional]);
  return (
    required.every((key) => Object.prototype.hasOwnProperty.call(value, key)) &&
    keys.every((key) => allowed.has(key))
  );
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isNonEmptyString(value: unknown): value is string {
  return isString(value) && value.length > 0;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || isString(value);
}

function isNullableBoolean(value: unknown): value is boolean | null {
  return value === null || typeof value === "boolean";
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) >= 1;
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) >= 0;
}

function isNullablePositiveInteger(value: unknown): value is number | null {
  return value === null || isPositiveInteger(value);
}

function isNullableNonNegativeNumber(value: unknown): value is number | null {
  return (
    value === null ||
    (typeof value === "number" && Number.isFinite(value) && value >= 0)
  );
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(isString);
}

function isStringRecord(value: unknown): value is Record<string, string> {
  const object = objectValue(value);
  return object !== null && Object.values(object).every(isString);
}

function isJsonValue(value: unknown, seen: Set<object> = new Set()): boolean {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return true;
  }
  if (typeof value === "number") {
    return Number.isFinite(value);
  }
  if (typeof value !== "object" || seen.has(value)) {
    return false;
  }
  seen.add(value);
  const valid = Array.isArray(value)
    ? value.every((item) => isJsonValue(item, seen))
    : Object.values(value).every((item) => isJsonValue(item, seen));
  seen.delete(value);
  return valid;
}

function isResourceLimits(value: unknown): boolean {
  const object = objectValue(value);
  if (object === null || !hasExactKeys(object, RESOURCE_LIMIT_KEYS)) {
    return false;
  }
  return (
    isNullableNonNegativeNumber(object.max_budget_usd) &&
    isNullablePositiveInteger(object.max_duration_seconds) &&
    isNullablePositiveInteger(object.max_request_tokens) &&
    isNullablePositiveInteger(object.max_response_tokens) &&
    isNullablePositiveInteger(object.max_steps) &&
    isNullablePositiveInteger(object.max_tool_calls) &&
    isNullablePositiveInteger(object.repeat_signature_threshold)
  );
}

function validateResourceLimitsParams(value: unknown): boolean {
  const object = objectValue(value);
  return (
    object !== null &&
    hasExactKeys(object, ["profile_id", "resource_limits", "target_type"]) &&
    isNonEmptyString(object.profile_id) &&
    isResourceLimits(object.resource_limits) &&
    (object.target_type === "agent_profile" ||
      object.target_type === "worker_profile")
  );
}

function validateBehaviorReadParams(value: unknown): boolean {
  const object = objectValue(value);
  return (
    object !== null &&
    hasExactKeys(object, ["file_path"]) &&
    isNonEmptyString(object.file_path)
  );
}

function validateBehaviorWriteParams(value: unknown): boolean {
  const object = objectValue(value);
  return (
    object !== null &&
    hasExactKeys(object, [
      "agent_slug",
      "content",
      "file_id",
      "project_slug",
    ]) &&
    isString(object.agent_slug) &&
    isString(object.content) &&
    isNonEmptyString(object.file_id) &&
    isString(object.project_slug)
  );
}

function validateBehaviorRestoreParams(value: unknown): boolean {
  const object = objectValue(value);
  return (
    object !== null &&
    hasExactKeys(object, [
      "agent_slug",
      "confirmed",
      "file_id",
      "project_slug",
      "target_version",
    ]) &&
    isString(object.agent_slug) &&
    typeof object.confirmed === "boolean" &&
    isNonEmptyString(object.file_id) &&
    isString(object.project_slug) &&
    isPositiveInteger(object.target_version)
  );
}

function validateMemoryConsolidateParams(value: unknown): boolean {
  const object = objectValue(value);
  return (
    object !== null &&
    hasExactKeys(object, ["project_id"]) &&
    isNonEmptyString(object.project_id)
  );
}

function validateMcpInstallParams(value: unknown): boolean {
  const object = objectValue(value);
  return (
    object !== null &&
    hasExactKeys(object, ["install_source", "package_name"], ["env"]) &&
    (object.install_source === "npm" || object.install_source === "pip") &&
    isNonEmptyString(object.package_name) &&
    (!Object.prototype.hasOwnProperty.call(object, "env") ||
      isStringRecord(object.env))
  );
}

function validateMcpInstallStatusParams(value: unknown): boolean {
  const object = objectValue(value);
  return (
    object !== null &&
    hasExactKeys(object, ["task_id"]) &&
    isNonEmptyString(object.task_id)
  );
}

const PARAM_VALIDATORS: Readonly<Record<F149ActionId, RuntimeValidator>> =
  Object.freeze({
    "agent_profile.update_resource_limits": validateResourceLimitsParams,
    "behavior.read_file": validateBehaviorReadParams,
    "behavior.write_file": validateBehaviorWriteParams,
    "behavior.restore_version": validateBehaviorRestoreParams,
    "memory.consolidate": validateMemoryConsolidateParams,
    "mcp_provider.install": validateMcpInstallParams,
    "mcp_provider.install_status": validateMcpInstallStatusParams,
  });

function validateBehaviorReadResult(value: unknown): boolean {
  const object = objectValue(value);
  return (
    object !== null &&
    hasExactKeys(object, ["budget_chars", "content", "exists", "file_path"]) &&
    (object.budget_chars === null ||
      isNonNegativeInteger(object.budget_chars)) &&
    isString(object.content) &&
    typeof object.exists === "boolean" &&
    isNonEmptyString(object.file_path)
  );
}

function validateBehaviorWriteResult(value: unknown): boolean {
  const object = objectValue(value);
  return (
    object !== null &&
    hasExactKeys(object, ["file_id", "resolved_path"]) &&
    isNonEmptyString(object.file_id) &&
    isNonEmptyString(object.resolved_path)
  );
}

function validateBehaviorRestoreResult(value: unknown): boolean {
  const object = objectValue(value);
  return (
    object !== null &&
    hasExactKeys(object, [
      "file_id",
      "preview",
      "proposal",
      "restored_from_version",
      "target_version",
    ]) &&
    isNonEmptyString(object.file_id) &&
    isNullableString(object.preview) &&
    isNullableBoolean(object.proposal) &&
    isNullablePositiveInteger(object.restored_from_version) &&
    isNullablePositiveInteger(object.target_version)
  );
}

function validateMemoryConsolidateResult(value: unknown): boolean {
  const object = objectValue(value);
  return (
    object !== null &&
    hasExactKeys(object, [
      "consolidated_count",
      "errors",
      "message",
      "model_alias",
      "skipped_count",
    ]) &&
    isNonNegativeInteger(object.consolidated_count) &&
    isStringArray(object.errors) &&
    isNonEmptyString(object.message) &&
    isNullableString(object.model_alias) &&
    isNonNegativeInteger(object.skipped_count)
  );
}

function validateMcpInstallResult(value: unknown): boolean {
  const object = objectValue(value);
  return (
    object !== null &&
    hasExactKeys(object, ["server_id", "task_id"]) &&
    isNonEmptyString(object.server_id) &&
    isNonEmptyString(object.task_id)
  );
}

function validateMcpInstallStatusResult(value: unknown): boolean {
  const object = objectValue(value);
  return (
    object !== null &&
    hasExactKeys(object, [
      "error",
      "progress_message",
      "result",
      "status",
      "task_id",
    ]) &&
    isNullableString(object.error) &&
    isString(object.progress_message) &&
    (object.result === null ||
      (objectValue(object.result) !== null && isJsonValue(object.result))) &&
    isNonEmptyString(object.status) &&
    isNonEmptyString(object.task_id)
  );
}

const RESULT_VALIDATORS: Readonly<Record<F149ActionId, RuntimeValidator>> =
  Object.freeze({
    "agent_profile.update_resource_limits": validateResourceLimitsParams,
    "behavior.read_file": validateBehaviorReadResult,
    "behavior.write_file": validateBehaviorWriteResult,
    "behavior.restore_version": validateBehaviorRestoreResult,
    "memory.consolidate": validateMemoryConsolidateResult,
    "mcp_provider.install": validateMcpInstallResult,
    "mcp_provider.install_status": validateMcpInstallStatusResult,
  });

function isF149ActionId(value: unknown): value is F149ActionId {
  return (
    typeof value === "string" && F149_ACTION_IDS.includes(value as F149ActionId)
  );
}

export function decodeF149ActionCommand(
  value: unknown,
): F149ActionCommand | null {
  const object = objectValue(value);
  if (
    object === null ||
    !hasExactKeys(object, ["actionId", "params"]) ||
    !isF149ActionId(object.actionId) ||
    !PARAM_VALIDATORS[object.actionId](object.params)
  ) {
    return null;
  }
  return object as F149ActionCommand;
}

function invalidCommand(): F149ActionFailure {
  return {
    ok: false,
    actionId: null,
    error: { kind: "invalid-command" },
  };
}

function actionFailure(
  actionId: F149ActionId,
  kind: Exclude<F149ActionErrorKind, "invalid-command">,
  code?: string,
): F149ActionFailure {
  return {
    ok: false,
    actionId,
    error: code === undefined ? { kind } : { kind, code },
  };
}

export async function executeF149Action<Command extends F149ActionCommand>(
  command: Command,
  executor: F149ActionExecutor,
): Promise<F149ActionOutcome<Command["actionId"]>> {
  const decoded = decodeF149ActionCommand(command);
  if (decoded === null) {
    return invalidCommand();
  }

  let envelope: ActionResultEnvelope | null;
  try {
    envelope = await executor(decoded.actionId, decoded.params);
  } catch {
    return actionFailure(decoded.actionId, "execution-failed");
  }
  if (envelope === null) {
    return actionFailure(decoded.actionId, "execution-failed");
  }
  if (envelope.action_id !== decoded.actionId) {
    return actionFailure(decoded.actionId, "invalid-result");
  }
  if (envelope.status !== "completed") {
    return actionFailure(decoded.actionId, "action-failed", envelope.code);
  }
  if (!RESULT_VALIDATORS[decoded.actionId](envelope.data)) {
    return actionFailure(decoded.actionId, "invalid-result");
  }

  return {
    ok: true,
    actionId: decoded.actionId,
    data: envelope.data,
    envelope,
  } as F149ActionSuccess<Command["actionId"]>;
}

export async function executeF149ActionWithRefresh<
  Command extends F149ActionCommand,
>(
  command: Command,
  contractVersion: string | undefined,
  handlers: F149ActionRefreshHandlers,
): Promise<F149ActionOutcome<Command["actionId"]>> {
  return executeF149Action(command, (actionId, params) =>
    executeWorkbenchActionWithRefresh(
      contractVersion,
      actionId,
      params,
      handlers,
    ),
  );
}
