/**
 * API Client -- REST 封装，优先消费 Feature 026 control-plane canonical routes
 */

import type {
  ActionRequestEnvelope,
  ActionResultEnvelope,
  BackupBundle,
  CapabilityPackDocument,
  ContextContinuityDocument,
  ControlPlaneActionResponse,
  ControlPlaneEventsResponse,
  ControlPlaneSnapshot,
  DelegationPlaneDocument,
  DiagnosticsSummaryDocument,
  ExportFilter,
  ExportManifest,
  ImportRunDocument,
  ImportSourceDocument,
  ImportWorkbenchDocument,
  MemoryConsoleDocument,
  MemoryProposalAuditDocument,
  MemorySubjectHistoryDocument,
  OperatorActionRequest,
  OperatorActionResult,
  OperatorInboxResponse,
  SkillPipelineDocument,
  ProjectSelectorDocument,
  RecoverySummary,
  SessionProjectionDocument,
  TaskDetailResponse,
  TaskListResponse,
  UpdateAttemptSummary,
  VaultAuthorizationDocument,
  WizardSessionDocument,
  ConfigSchemaDocument,
  AutomationJobDocument,
} from "../types";

const BASE_URL = "";
const FRONT_DOOR_TOKEN_STORAGE_KEY = "octoagent.frontdoorToken";
const FRONT_DOOR_TOKEN_SESSION_STORAGE_KEY = "octoagent.frontdoorToken.session";
const FRONT_DOOR_ERROR_CODES = new Set([
  "FRONT_DOOR_LOOPBACK_ONLY",
  "FRONT_DOOR_LOOPBACK_PROXY_REJECTED",
  "FRONT_DOOR_TOKEN_REQUIRED",
  "FRONT_DOOR_TOKEN_INVALID",
  "FRONT_DOOR_TOKEN_ENV_MISSING",
  "FRONT_DOOR_TRUSTED_PROXY_REQUIRED",
  "FRONT_DOOR_PROXY_TOKEN_REQUIRED",
  "FRONT_DOOR_PROXY_TOKEN_INVALID",
  "FRONT_DOOR_PROXY_TOKEN_ENV_MISSING",
  "FRONT_DOOR_CONFIG_INVALID",
  "FRONT_DOOR_MODE_UNSUPPORTED",
]);

type ControlResourceName =
  | "wizard"
  | "config"
  | "project-selector"
  | "sessions"
  | "context-frames"
  | "capability-pack"
  | "delegation"
  | "pipelines"
  | "automation"
  | "diagnostics"
  | "memory"
  | "import-workbench";

interface MemoryResourceQuery {
  projectId?: string;
  workspaceId?: string;
  scopeId?: string;
  partition?: string;
  layer?: string;
  query?: string;
  includeHistory?: boolean;
  includeVaultRefs?: boolean;
  limit?: number;
  status?: string;
  source?: string;
  subjectKey?: string;
}

type ApiErrorPayload = {
  code?: string;
  message?: string;
  hint?: string;
};

type FrontDoorTokenStorageMode = "session" | "persistent";

let memoryFrontDoorToken = "";
let memoryFrontDoorTokenMode: FrontDoorTokenStorageMode = "session";

export class ApiError extends Error {
  status: number;
  code?: string;
  hint?: string;

  constructor(message: string, options: { status: number; code?: string; hint?: string }) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.hint = options.hint;
  }
}

function readStorage(storage: Storage | undefined, key: string): string {
  if (!storage) {
    return "";
  }
  try {
    return storage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function writeStorage(storage: Storage | undefined, key: string, value: string): boolean {
  if (!storage) {
    return false;
  }
  try {
    storage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function removeStorage(storage: Storage | undefined, key: string): void {
  if (!storage) {
    return;
  }
  try {
    storage.removeItem(key);
  } catch {
    // 忽略浏览器存储不可用场景，回退到内存模式。
  }
}

function getSessionStorage(): Storage | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }
  return window.sessionStorage;
}

function getLocalStorage(): Storage | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }
  return window.localStorage;
}

function parseErrorPayload(body: unknown): ApiErrorPayload {
  if (!body || typeof body !== "object") {
    return {};
  }
  const payload = body as Record<string, unknown>;
  const error =
    payload.error && typeof payload.error === "object"
      ? (payload.error as Record<string, unknown>)
      : payload.detail && typeof payload.detail === "object"
        ? (payload.detail as Record<string, unknown>)
        : payload.result && typeof payload.result === "object"
          ? (payload.result as Record<string, unknown>)
          : null;
  if (!error) {
    return {};
  }
  return {
    code: typeof error.code === "string" ? error.code : undefined,
    message: typeof error.message === "string" ? error.message : undefined,
    hint: typeof error.hint === "string" ? error.hint : undefined,
  };
}

async function buildApiError(resp: Response, body?: unknown): Promise<ApiError> {
  const resolvedBody = body ?? (await resp.json().catch(() => null));
  const payload = parseErrorPayload(resolvedBody);
  return new ApiError(payload.message ?? `HTTP ${resp.status}`, {
    status: resp.status,
    code: payload.code,
    hint: payload.hint,
  });
}

export function getFrontDoorToken(): string {
  const sessionToken = readStorage(
    getSessionStorage(),
    FRONT_DOOR_TOKEN_SESSION_STORAGE_KEY
  ).trim();
  if (sessionToken) {
    memoryFrontDoorToken = sessionToken;
    memoryFrontDoorTokenMode = "session";
    return sessionToken;
  }
  const persistentToken = readStorage(getLocalStorage(), FRONT_DOOR_TOKEN_STORAGE_KEY).trim();
  if (persistentToken) {
    memoryFrontDoorToken = persistentToken;
    memoryFrontDoorTokenMode = "persistent";
    return persistentToken;
  }
  return memoryFrontDoorToken;
}

export function getFrontDoorTokenStorageMode(): FrontDoorTokenStorageMode {
  const sessionToken = readStorage(
    getSessionStorage(),
    FRONT_DOOR_TOKEN_SESSION_STORAGE_KEY
  ).trim();
  if (sessionToken) {
    return "session";
  }
  const persistentToken = readStorage(getLocalStorage(), FRONT_DOOR_TOKEN_STORAGE_KEY).trim();
  if (persistentToken) {
    return "persistent";
  }
  return memoryFrontDoorTokenMode;
}

export function saveFrontDoorToken(
  token: string,
  options?: { persist?: boolean }
): void {
  const normalized = token.trim();
  const persist = options?.persist ?? false;
  memoryFrontDoorToken = normalized;
  memoryFrontDoorTokenMode = persist ? "persistent" : "session";

  removeStorage(getSessionStorage(), FRONT_DOOR_TOKEN_SESSION_STORAGE_KEY);
  removeStorage(getLocalStorage(), FRONT_DOOR_TOKEN_STORAGE_KEY);

  if (!normalized) {
    return;
  }

  const targetStorage = persist ? getLocalStorage() : getSessionStorage();
  const targetKey = persist
    ? FRONT_DOOR_TOKEN_STORAGE_KEY
    : FRONT_DOOR_TOKEN_SESSION_STORAGE_KEY;
  const written = writeStorage(targetStorage, targetKey, normalized);
  if (!written) {
    memoryFrontDoorTokenMode = persist ? "persistent" : "session";
  }
}

export function clearFrontDoorToken(): void {
  memoryFrontDoorToken = "";
  memoryFrontDoorTokenMode = "session";
  removeStorage(getSessionStorage(), FRONT_DOOR_TOKEN_SESSION_STORAGE_KEY);
  removeStorage(getLocalStorage(), FRONT_DOOR_TOKEN_STORAGE_KEY);
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function isFrontDoorApiError(error: unknown): error is ApiError {
  return isApiError(error) && Boolean(error.code && FRONT_DOOR_ERROR_CODES.has(error.code));
}

export function buildFrontDoorSseUrl(path: string): string {
  const token = getFrontDoorToken();
  if (!token) {
    return path;
  }
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}access_token=${encodeURIComponent(token)}`;
}

async function apiRequest(path: string, init?: RequestInit): Promise<Response> {
  const token = getFrontDoorToken();
  const headers = new Headers(init?.headers ?? undefined);
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return fetch(`${BASE_URL}${path}`, {
    headers,
    ...init,
  });
}

export async function frontDoorRequest(
  path: string,
  init?: RequestInit
): Promise<Response> {
  return apiRequest(path, init);
}

/** 通用 JSON fetch，非 2xx 直接抛错 */
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await apiRequest(path, init);
  if (!resp.ok) {
    throw await buildApiError(resp);
  }
  return resp.json() as Promise<T>;
}

function buildQueryString(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (
      value === undefined ||
      value === null ||
      value === "" ||
      (typeof value === "number" && Number.isNaN(value))
    ) {
      continue;
    }
    search.set(key, String(value));
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
}

/** GET /api/control/snapshot -- control plane 首屏快照 */
export async function fetchControlSnapshot(): Promise<ControlPlaneSnapshot> {
  return apiFetch<ControlPlaneSnapshot>("/api/control/snapshot");
}

/** GET /api/control/resources/* -- 单资源刷新 */
export async function fetchControlResource(
  resource: "wizard"
): Promise<WizardSessionDocument>;
export async function fetchControlResource(
  resource: "config"
): Promise<ConfigSchemaDocument>;
export async function fetchControlResource(
  resource: "project-selector"
): Promise<ProjectSelectorDocument>;
export async function fetchControlResource(
  resource: "sessions"
): Promise<SessionProjectionDocument>;
export async function fetchControlResource(
  resource: "context-frames"
): Promise<ContextContinuityDocument>;
export async function fetchControlResource(
  resource: "capability-pack"
): Promise<CapabilityPackDocument>;
export async function fetchControlResource(
  resource: "delegation"
): Promise<DelegationPlaneDocument>;
export async function fetchControlResource(
  resource: "pipelines"
): Promise<SkillPipelineDocument>;
export async function fetchControlResource(
  resource: "automation"
): Promise<AutomationJobDocument>;
export async function fetchControlResource(
  resource: "diagnostics"
): Promise<DiagnosticsSummaryDocument>;
export async function fetchControlResource(
  resource: "memory"
): Promise<MemoryConsoleDocument>;
export async function fetchControlResource(
  resource: "import-workbench"
): Promise<ImportWorkbenchDocument>;
export async function fetchControlResource(
  resource: ControlResourceName
): Promise<
  | WizardSessionDocument
  | ConfigSchemaDocument
  | ProjectSelectorDocument
  | SessionProjectionDocument
  | ContextContinuityDocument
  | CapabilityPackDocument
  | DelegationPlaneDocument
  | SkillPipelineDocument
  | AutomationJobDocument
  | DiagnosticsSummaryDocument
  | MemoryConsoleDocument
  | ImportWorkbenchDocument
> {
  return apiFetch(`/api/control/resources/${resource}`);
}

export async function fetchMemoryConsole(
  params: MemoryResourceQuery = {}
): Promise<MemoryConsoleDocument> {
  return apiFetch<MemoryConsoleDocument>(
    `/api/control/resources/memory${buildQueryString({
      project_id: params.projectId,
      workspace_id: params.workspaceId,
      scope_id: params.scopeId,
      partition: params.partition,
      layer: params.layer,
      query: params.query,
      include_history: params.includeHistory,
      include_vault_refs: params.includeVaultRefs,
      limit: params.limit,
    })}`
  );
}

export async function fetchMemorySubjectHistory(
  subjectKey: string,
  params: MemoryResourceQuery = {}
): Promise<MemorySubjectHistoryDocument> {
  return apiFetch<MemorySubjectHistoryDocument>(
    `/api/control/resources/memory-subjects/${encodeURIComponent(subjectKey)}${buildQueryString(
      {
        project_id: params.projectId,
        workspace_id: params.workspaceId,
        scope_id: params.scopeId,
      }
    )}`
  );
}

export async function fetchMemoryProposals(
  params: MemoryResourceQuery = {}
): Promise<MemoryProposalAuditDocument> {
  return apiFetch<MemoryProposalAuditDocument>(
    `/api/control/resources/memory-proposals${buildQueryString({
      project_id: params.projectId,
      workspace_id: params.workspaceId,
      scope_id: params.scopeId,
      status: params.status,
      source: params.source,
      limit: params.limit,
    })}`
  );
}

export async function fetchVaultAuthorization(
  params: MemoryResourceQuery = {}
): Promise<VaultAuthorizationDocument> {
  return apiFetch<VaultAuthorizationDocument>(
    `/api/control/resources/vault-authorization${buildQueryString({
      project_id: params.projectId,
      workspace_id: params.workspaceId,
      scope_id: params.scopeId,
      subject_key: params.subjectKey,
    })}`
  );
}

export async function fetchImportWorkbench(params: {
  projectId?: string;
  workspaceId?: string;
} = {}): Promise<ImportWorkbenchDocument> {
  return apiFetch<ImportWorkbenchDocument>(
    `/api/control/resources/import-workbench${buildQueryString({
      project_id: params.projectId,
      workspace_id: params.workspaceId,
    })}`
  );
}

export async function fetchImportSource(
  sourceId: string
): Promise<ImportSourceDocument> {
  return apiFetch<ImportSourceDocument>(
    `/api/control/resources/import-sources/${encodeURIComponent(sourceId)}`
  );
}

export async function fetchImportRun(runId: string): Promise<ImportRunDocument> {
  return apiFetch<ImportRunDocument>(
    `/api/control/resources/import-runs/${encodeURIComponent(runId)}`
  );
}

/** GET /api/control/events -- 读取 control-plane event stream */
export async function fetchControlEvents(
  after?: string,
  limit = 100
): Promise<ControlPlaneEventsResponse> {
  const params = new URLSearchParams();
  if (after) {
    params.set("after", after);
  }
  params.set("limit", String(limit));
  return apiFetch<ControlPlaneEventsResponse>(
    `/api/control/events?${params.toString()}`
  );
}

/** POST /api/control/actions -- 执行统一 control-plane action */
export async function executeControlAction(
  body: ActionRequestEnvelope
): Promise<ActionResultEnvelope> {
  const resp = await apiRequest("/api/control/actions", {
    method: "POST",
    body: JSON.stringify(body),
  });
  const rawPayload = await resp.json().catch(() => null);
  const payload = (rawPayload as ControlPlaneActionResponse | null) ?? null;
  if (payload?.result) {
    return payload.result;
  }
  if (!resp.ok) {
    throw await buildApiError(resp, rawPayload);
  }
  throw new Error("control action 返回体缺少 result");
}

/** GET /api/tasks -- 任务列表查询 */
export async function fetchTasks(status?: string): Promise<TaskListResponse> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiFetch<TaskListResponse>(`/api/tasks${qs}`);
}

/** GET /api/tasks/{id} -- 任务详情查询 */
export async function fetchTaskDetail(
  taskId: string
): Promise<TaskDetailResponse> {
  return apiFetch<TaskDetailResponse>(`/api/tasks/${taskId}`);
}

/** GET /api/ops/recovery -- 最近一次恢复准备度摘要 */
export async function fetchRecoverySummary(): Promise<RecoverySummary> {
  return apiFetch<RecoverySummary>("/api/ops/recovery");
}

/** GET /api/ops/update/status -- 最近一次升级摘要 */
export async function fetchUpdateStatus(): Promise<UpdateAttemptSummary> {
  return apiFetch<UpdateAttemptSummary>("/api/ops/update/status");
}

/** POST /api/ops/backup/create -- 触发 backup create */
export async function triggerBackupCreate(label?: string): Promise<BackupBundle> {
  return apiFetch<BackupBundle>("/api/ops/backup/create", {
    method: "POST",
    body: JSON.stringify({ label: label ?? null }),
  });
}

/** POST /api/ops/update/dry-run -- 触发 update dry-run */
export async function triggerUpdateDryRun(): Promise<UpdateAttemptSummary> {
  return apiFetch<UpdateAttemptSummary>("/api/ops/update/dry-run", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

/** POST /api/ops/update/apply -- 触发真实 update */
export async function triggerUpdateApply(
  wait = false
): Promise<UpdateAttemptSummary> {
  return apiFetch<UpdateAttemptSummary>("/api/ops/update/apply", {
    method: "POST",
    body: JSON.stringify({ wait }),
  });
}

/** POST /api/ops/restart -- 触发 runtime restart */
export async function triggerRestart(): Promise<UpdateAttemptSummary> {
  return apiFetch<UpdateAttemptSummary>("/api/ops/restart", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

/** POST /api/ops/verify -- 触发 runtime verify */
export async function triggerVerify(): Promise<UpdateAttemptSummary> {
  return apiFetch<UpdateAttemptSummary>("/api/ops/verify", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

/** POST /api/ops/export/chats -- 触发 chats export */
export async function triggerExportChats(
  filters: ExportFilter = {}
): Promise<ExportManifest> {
  return apiFetch<ExportManifest>("/api/ops/export/chats", {
    method: "POST",
    body: JSON.stringify({
      task_id: filters.task_id ?? null,
      thread_id: filters.thread_id ?? null,
      since: filters.since ?? null,
      until: filters.until ?? null,
    }),
  });
}

/** GET /api/operator/inbox -- 旧 operator inbox，保留兼容 */
export async function fetchOperatorInbox(): Promise<OperatorInboxResponse> {
  return apiFetch<OperatorInboxResponse>("/api/operator/inbox");
}

/** POST /api/operator/actions -- 旧 operator 动作，保留兼容 */
export async function submitOperatorAction(
  body: OperatorActionRequest
): Promise<OperatorActionResult> {
  return apiFetch<OperatorActionResult>("/api/operator/actions", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
