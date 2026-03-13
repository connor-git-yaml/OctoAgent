import type {
  ConfigFieldHint,
  ControlPlaneSnapshot,
  ControlPlaneSupportStatus,
} from "../types";

export type SnapshotResourceRoute =
  | "wizard"
  | "config"
  | "project-selector"
  | "sessions"
  | "worker-profiles"
  | "context-frames"
  | "policy-profiles"
  | "capability-pack"
  | "skill-governance"
  | "setup-governance"
  | "delegation"
  | "pipelines"
  | "automation"
  | "diagnostics"
  | "memory"
  | "import-workbench";

export const RESOURCE_ROUTE_BY_TYPE: Record<string, SnapshotResourceRoute> = {
  wizard_session: "wizard",
  config_schema: "config",
  project_selector: "project-selector",
  session_projection: "sessions",
  worker_profiles: "worker-profiles",
  context_continuity: "context-frames",
  policy_profiles: "policy-profiles",
  capability_pack: "capability-pack",
  skill_governance: "skill-governance",
  setup_governance: "setup-governance",
  delegation_plane: "delegation",
  skill_pipeline: "pipelines",
  automation_job: "automation",
  diagnostics_summary: "diagnostics",
  memory_console: "memory",
  import_workbench: "import-workbench",
};

export const SNAPSHOT_RESOURCE_KEY_BY_ROUTE: Record<
  SnapshotResourceRoute,
  keyof ControlPlaneSnapshot["resources"]
> = {
  wizard: "wizard",
  config: "config",
  "project-selector": "project_selector",
  sessions: "sessions",
  "worker-profiles": "worker_profiles",
  "context-frames": "context_continuity",
  "policy-profiles": "policy_profiles",
  "capability-pack": "capability_pack",
  "skill-governance": "skill_governance",
  "setup-governance": "setup_governance",
  delegation: "delegation",
  pipelines: "pipelines",
  automation: "automation",
  diagnostics: "diagnostics",
  memory: "memory",
  "import-workbench": "imports",
};

export function makeRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function formatDateTime(value?: string | null): string {
  if (!value) {
    return "未记录";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatRelativeStatus(value?: string | null): string {
  return (value ?? "unknown").replace(/[_-]/g, " ");
}

const WORKER_TEMPLATE_FALLBACK_NAMES: Record<string, string> = {
  general: "Butler",
  ops: "运行保障",
  research: "调研整理",
  dev: "开发实现",
};

export function formatWorkerTemplateName(
  name?: string | null,
  baseArchetype?: string | null
): string {
  const normalized = (name ?? "")
    .replace(/\s*Root Agent\s*/gi, " ")
    .replace(/\s+Root$/i, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (normalized) {
    return normalized;
  }
  const fallback = baseArchetype ? WORKER_TEMPLATE_FALLBACK_NAMES[baseArchetype] : "";
  return fallback || "未命名模板";
}

export function formatWorkerTemplateLabel(
  name?: string | null,
  baseArchetype?: string | null
): string {
  const templateName = formatWorkerTemplateName(name, baseArchetype);
  return /模板$/.test(templateName) ? templateName : `${templateName} 模板`;
}

export function formatSupportStatus(status?: ControlPlaneSupportStatus): string {
  switch (status) {
    case "supported":
      return "可用";
    case "degraded":
      return "降级";
    case "hidden":
      return "隐藏";
    case "unsupported":
      return "不支持";
    default:
      return "未知";
  }
}

export function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function isIndexSegment(segment: string): boolean {
  return /^\d+$/.test(segment);
}

export function getValueAtPath(
  source: Record<string, unknown>,
  path: string
): unknown {
  const parts = path.split(".").filter(Boolean);
  let current: unknown = source;
  for (const part of parts) {
    if (Array.isArray(current)) {
      const index = Number(part);
      if (Number.isNaN(index)) {
        return undefined;
      }
      current = current[index];
      continue;
    }
    if (!current || typeof current !== "object") {
      return undefined;
    }
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

export function setValueAtPath(
  source: Record<string, unknown>,
  path: string,
  value: unknown
): void {
  const parts = path.split(".").filter(Boolean);
  if (parts.length === 0) {
    return;
  }
  let current: unknown = source;
  for (let index = 0; index < parts.length - 1; index += 1) {
    const part = parts[index]!;
    const nextPart = parts[index + 1]!;
    if (Array.isArray(current)) {
      const slot = Number(part);
      if (Number.isNaN(slot)) {
        return;
      }
      const existing = current[slot];
      if (!existing || typeof existing !== "object") {
        current[slot] = isIndexSegment(nextPart) ? [] : {};
      }
      current = current[slot];
      continue;
    }
    if (!current || typeof current !== "object") {
      return;
    }
    const record = current as Record<string, unknown>;
    const existing = record[part];
    if (!existing || typeof existing !== "object") {
      record[part] = isIndexSegment(nextPart) ? [] : {};
    }
    current = record[part];
  }
  const lastPart = parts[parts.length - 1] ?? path;
  if (Array.isArray(current)) {
    const slot = Number(lastPart);
    if (!Number.isNaN(slot)) {
      current[slot] = value;
    }
    return;
  }
  if (!current || typeof current !== "object") {
    return;
  }
  (current as Record<string, unknown>)[lastPart] = value;
}

export function findSchemaNode(
  schema: Record<string, unknown>,
  path: string
): Record<string, unknown> | null {
  const parts = path.split(".").filter(Boolean);
  let current: Record<string, unknown> | null = schema;
  for (const part of parts) {
    if (!current) {
      return null;
    }
    if (isIndexSegment(part)) {
      const items = current.items;
      if (!items || typeof items !== "object" || Array.isArray(items)) {
        return null;
      }
      current = items as Record<string, unknown>;
      continue;
    }
    const properties = current.properties;
    if (!properties || typeof properties !== "object" || Array.isArray(properties)) {
      return null;
    }
    const next = (properties as Record<string, unknown>)[part];
    if (!next || typeof next !== "object" || Array.isArray(next)) {
      return null;
    }
    current = next as Record<string, unknown>;
  }
  return current;
}

export function widgetValueToFieldState(
  hint: ConfigFieldHint,
  rawValue: unknown
): string | boolean {
  if (hint.widget === "toggle") {
    return Boolean(rawValue);
  }
  if (hint.widget === "string-list") {
    return Array.isArray(rawValue)
      ? rawValue.map((item) => String(item)).join("\n")
      : "";
  }
  if (hint.widget === "provider-list" || hint.widget === "alias-map") {
    return JSON.stringify(rawValue ?? (hint.widget === "provider-list" ? [] : {}), null, 2);
  }
  if (rawValue === undefined || rawValue === null) {
    return "";
  }
  return String(rawValue);
}

export function parseFieldStateValue(
  hint: ConfigFieldHint,
  fieldValue: string | boolean
): { value: unknown; error: string | null } {
  if (hint.widget === "toggle") {
    return { value: Boolean(fieldValue), error: null };
  }
  if (hint.widget === "string-list") {
    const rendered = String(fieldValue)
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean);
    return { value: rendered, error: null };
  }
  if (hint.widget === "provider-list" || hint.widget === "alias-map") {
    const rawText = String(fieldValue).trim();
    if (!rawText) {
      return {
        value: hint.widget === "provider-list" ? [] : {},
        error: null,
      };
    }
    try {
      return { value: JSON.parse(rawText), error: null };
    } catch {
      return {
        value: null,
        error: `${hint.label} 需要是合法 JSON`,
      };
    }
  }
  return { value: String(fieldValue), error: null };
}

export function categoryForHint(hint: ConfigFieldHint): string {
  if (hint.section === "channels") {
    return "channels";
  }
  if (hint.section.startsWith("memory")) {
    return "memory";
  }
  if (hint.section === "providers" || hint.section === "models" || hint.section === "runtime") {
    return "main-agent";
  }
  return "advanced";
}
