import type {
  ConfigFieldHint,
  SkillGovernanceItem,
  SetupReviewSummary,
  SetupRiskItem,
} from "../../types";
import {
  deepClone,
  findSchemaNode,
  getValueAtPath,
  parseFieldStateValue,
  setValueAtPath,
  widgetValueToFieldState,
} from "../../workbench/utils";

export type FieldState = Record<string, string | boolean>;
export type FieldErrors = Record<string, string>;
export type SkillSelectionState = Record<string, boolean>;

export const DEFAULT_TRUSTED_PROXY_CIDRS = "127.0.0.1/32\n::1/128";
export const DEFAULT_MEMORY_TIMEOUT = "5";

export interface FieldGuide {
  title: string;
  description: string;
  example?: string;
  exampleLabel?: string;
  actions?: Array<{ label: string; value: string }>;
}

export interface ProviderDraftItem {
  id: string;
  name: string;
  auth_type: "api_key" | "oauth";
  api_key_env: string;
  enabled: boolean;
}

export interface ModelAliasDraftItem {
  alias: string;
  provider: string;
  model: string;
  description: string;
  thinking_level: "" | "xhigh" | "high" | "medium" | "low";
}

export type ReasoningSupportState = "supported" | "unsupported" | "pending";

export interface ProviderRuntimeDetails {
  provider_entries?: Array<Record<string, unknown>>;
  litellm_env_names?: string[];
  runtime_env_names?: string[];
  credential_profiles?: Array<Record<string, unknown>>;
  openai_oauth_connected?: boolean;
  openai_oauth_profile?: string;
}

export const PROVIDER_PRESETS: Record<
  string,
  Omit<ProviderDraftItem, "enabled">
> = {
  openrouter: {
    id: "openrouter",
    name: "OpenRouter",
    auth_type: "api_key",
    api_key_env: "OPENROUTER_API_KEY",
  },
  openai: {
    id: "openai",
    name: "OpenAI",
    auth_type: "api_key",
    api_key_env: "OPENAI_API_KEY",
  },
  anthropic: {
    id: "anthropic",
    name: "Anthropic",
    auth_type: "api_key",
    api_key_env: "ANTHROPIC_API_KEY",
  },
  "openai-codex": {
    id: "openai-codex",
    name: "OpenAI Codex (ChatGPT Pro OAuth)",
    auth_type: "oauth",
    api_key_env: "OPENAI_API_KEY",
  },
};

const OPENROUTER_REASONING_PATTERNS = [
  /(^|\/)(deepseek-r1|deepseek-r1-distill)\b/i,
  /(^|\/)qwq\b/i,
  /(^|\/)(o1|o3|o4)\b/i,
  /(^|\/)gpt-5\b/i,
  /claude-3\.7-sonnet-thinking/i,
  /claude-opus-4-thinking/i,
  /gemini-2\.5-(pro|flash-thinking)/i,
];

export const CUSTOM_PROVIDER_FIELD_PATHS = new Set([
  "runtime.llm_mode",
  "runtime.litellm_proxy_url",
  "runtime.master_key_env",
  "providers",
  "model_aliases",
]);

export function buildFieldState(
  hints: Record<string, ConfigFieldHint>,
  currentValue: Record<string, unknown>
): FieldState {
  return Object.fromEntries(
    Object.values(hints).map((hint) => [
      hint.field_path,
      widgetValueToFieldState(hint, getValueAtPath(currentValue, hint.field_path)),
    ])
  );
}

export function buildConfigPayload(
  baseConfig: Record<string, unknown>,
  hints: Record<string, ConfigFieldHint>,
  fieldState: FieldState
): { config: Record<string, unknown>; errors: FieldErrors } {
  const nextConfig = deepClone(baseConfig);
  const errors: FieldErrors = {};

  Object.values(hints).forEach((hint) => {
    const parsed = parseFieldStateValue(hint, fieldState[hint.field_path] ?? "");
    if (parsed.error) {
      errors[hint.field_path] = parsed.error;
      return;
    }
    setValueAtPath(nextConfig, hint.field_path, parsed.value);
  });

  return { config: nextConfig, errors };
}

export function groupLabel(groupId: string): { title: string; description: string } {
  switch (groupId) {
    case "models":
      return {
        title: "Providers",
        description: "配置模型 Provider、LiteLLM 和别名路由。",
      };
    case "channels":
      return {
        title: "渠道接入",
        description: "管理 Web、Telegram 等入口的连接方式和可见范围。",
      };
    case "memory":
      return {
        title: "Memory",
        description: "先选记忆的连接方式，再决定默认检索范围和高级召回策略。",
      };
    default:
      return {
        title: "更多设置",
        description: "这里是进阶配置，通常在基础功能跑通后再调整。",
      };
  }
}

export function selectOptions(
  schema: Record<string, unknown>,
  hint: ConfigFieldHint
): string[] {
  const schemaNode = findSchemaNode(schema, hint.field_path);
  const rawEnum = schemaNode?.enum;
  if (!Array.isArray(rawEnum)) {
    return [];
  }
  return rawEnum.map((item) => String(item));
}

export function summaryTone(
  review: SetupReviewSummary
): "success" | "warning" | "danger" {
  if (review.blocking_reasons.length > 0) {
    return "danger";
  }
  if (review.warnings.length > 0) {
    return "warning";
  }
  return "success";
}

export function renderRiskList(title: string, risks: SetupRiskItem[]) {
  if (risks.length === 0) {
    return null;
  }
  return (
    <div className="wb-note">
      <strong>{title}</strong>
      <div className="wb-note-stack">
        {risks.map((risk) => (
          <div key={risk.risk_id}>
            <span>
              {risk.blocking ? "阻塞" : "提示"} · {risk.title}
            </span>
            <p>{risk.summary}</p>
            {risk.recommended_action ? <small>{risk.recommended_action}</small> : null}
          </div>
        ))}
      </div>
    </div>
  );
}

export function parseJsonFieldValue(
  rawValue: string | boolean | undefined,
  fallback: Record<string, unknown> | unknown[]
): Record<string, unknown> | unknown[] {
  const value = typeof rawValue === "string" ? rawValue.trim() : "";
  if (!value) {
    return fallback;
  }
  try {
    const parsed = JSON.parse(value);
    if (parsed && typeof parsed === "object") {
      return parsed as Record<string, unknown> | unknown[];
    }
  } catch {
    return fallback;
  }
  return fallback;
}

export function optionLabelForHint(hint: ConfigFieldHint, option: string): string {
  if (hint.field_path === "runtime.llm_mode") {
    if (option === "echo") {
      return "echo · 体验模式";
    }
    if (option === "litellm") {
      return "litellm · 连接真实模型";
    }
  }
  if (hint.field_path === "memory.backend_mode") {
    if (option === "local_only") {
      return "local_only · 只用本地记忆";
    }
    if (option === "memu") {
      return "memu · 连接远端 MemU bridge";
    }
  }
  return option;
}

export function buildFieldGuide(
  hint: ConfigFieldHint,
  usingEchoMode: boolean
): FieldGuide | null {
  if (hint.field_path === "memory.bridge_api_key_env") {
    return {
      title: "安全建议",
      description:
        "这里仍然只填环境变量名。如果你的 bridge 暂时不需要鉴权，可以先留空。",
    };
  }
  if (hint.widget === "env-ref") {
    return {
      title: "怎么填",
      description:
        "这里只填写环境变量名，不要把真实 token 或 API Key 直接贴进来。真实值应放在 ~/.octoagent/.env 或 ~/.octoagent/.env.litellm 里。",
    };
  }
  if (hint.field_path === "runtime.llm_mode") {
    return {
      title: "推荐选择",
      description: usingEchoMode
        ? "首次体验建议先保持 echo，这样不需要额外配置模型，也能跑通 Web 和任务流。"
        : "当你准备接入真实模型时，再切换到 litellm 并补齐 Provider 与模型别名。",
    };
  }
  if (hint.field_path === "memory.backend_mode") {
    return {
      title: "怎么选",
      description:
        "如果你只是想让系统先能记住聊天和工作过程，保持 local_only 就够了。只有明确要接远端 MemU bridge 时，再切到 memu。",
    };
  }
  if (hint.field_path === "memory.bridge_url") {
    return {
      title: "这里填什么",
      description:
        "这里只填 bridge 的基础地址，不要自己拼 `/memory/search` 之类的接口路径。常见写法是 https://memory.example.com。",
    };
  }
  if (hint.field_path === "memory.bridge_timeout_seconds") {
    return {
      title: "推荐范围",
      description:
        "一般 5 秒足够；如果 bridge 在异地或经常冷启动，可以调到 8-10 秒。不是越大越好，过大只会让失败反馈更慢。",
      actions: [
        { label: "恢复 5 秒", value: DEFAULT_MEMORY_TIMEOUT },
        { label: "改成 8 秒", value: "8" },
      ],
    };
  }
  if (hint.field_path === "providers" || hint.field_path === "model_aliases") {
    return null;
  }
  if (hint.field_path === "front_door.trusted_proxy_cidrs") {
    return {
      title: "填写格式",
      description: "每行一个 CIDR。只在 trusted_proxy 模式下需要，本机默认值通常就是下面这两个。",
      exampleLabel: "本机默认值",
      example: DEFAULT_TRUSTED_PROXY_CIDRS,
      actions: [{ label: "恢复本机默认值", value: DEFAULT_TRUSTED_PROXY_CIDRS }],
    };
  }
  if (hint.widget === "string-list" && hint.field_path.startsWith("channels.telegram.")) {
    return {
      title: "填写格式",
      description: "每行一项。可以填 Telegram 用户 ID、群组 ID 或用户名，按字段含义分别填写。",
    };
  }
  if (hint.field_path.startsWith("memory.bridge_")) {
    if (
      hint.field_path !== "memory.bridge_url" &&
      hint.field_path !== "memory.bridge_api_key_env" &&
      hint.field_path !== "memory.bridge_timeout_seconds"
    ) {
      return null;
    }
    return {
      title: "什么时候才需要改",
      description:
        "只有当你的 bridge API 路径或鉴权方式跟默认约定不一致时才需要调整。大多数情况下保留默认值即可。",
    };
  }
  return null;
}

export function readProviderRuntimeDetails(source: unknown): ProviderRuntimeDetails {
  if (!source || typeof source !== "object" || Array.isArray(source)) {
    return {};
  }
  return source as ProviderRuntimeDetails;
}

export function buildSkillSelectionState(items: SkillGovernanceItem[]): SkillSelectionState {
  return Object.fromEntries(items.map((item) => [item.item_id, item.selected]));
}

export function buildSkillSelectionSyncKey(items: SkillGovernanceItem[]): string {
  return JSON.stringify(
    items.map((item) => ({
      item_id: item.item_id,
      selected: item.selected,
      enabled_by_default: item.enabled_by_default,
    }))
  );
}

export function buildSkillSelectionPayload(
  items: SkillGovernanceItem[],
  selectionState: SkillSelectionState
): Record<string, string[]> {
  const selected_item_ids: string[] = [];
  const disabled_item_ids: string[] = [];
  items.forEach((item) => {
    const selected = selectionState[item.item_id] ?? item.selected;
    if (selected && !item.enabled_by_default) {
      selected_item_ids.push(item.item_id);
    }
    if (!selected && item.enabled_by_default) {
      disabled_item_ids.push(item.item_id);
    }
  });
  return {
    selected_item_ids,
    disabled_item_ids,
  };
}

export function parseProviderDrafts(rawValue: string | boolean | undefined): ProviderDraftItem[] {
  const providers = parseJsonFieldValue(rawValue, []) as Array<Record<string, unknown>>;
  return providers
    .filter((item) => item && typeof item === "object")
    .map((item): ProviderDraftItem => ({
      id: String(item.id ?? ""),
      name: String(item.name ?? ""),
      auth_type: item.auth_type === "oauth" ? "oauth" : "api_key",
      api_key_env: String(item.api_key_env ?? ""),
      enabled: item.enabled !== false,
    }))
    .filter((item) => item.id.trim());
}

export function stringifyProviderDrafts(items: ProviderDraftItem[]): string {
  return JSON.stringify(
    items.map((item) => ({
      id: item.id,
      name: item.name,
      auth_type: item.auth_type,
      api_key_env: item.api_key_env,
      enabled: item.enabled,
    })),
    null,
    2
  );
}

export function parseAliasDrafts(rawValue: string | boolean | undefined): ModelAliasDraftItem[] {
  const aliases = parseJsonFieldValue(rawValue, {}) as Record<string, Record<string, unknown>>;
  return Object.entries(aliases)
    .filter(([, item]) => item && typeof item === "object" && !Array.isArray(item))
    .map(([alias, item]) => ({
      alias,
      provider: String(item.provider ?? ""),
      model: String(item.model ?? ""),
      description: String(item.description ?? ""),
      thinking_level:
        item.thinking_level === "xhigh" ||
        item.thinking_level === "high" ||
        item.thinking_level === "medium" ||
        item.thinking_level === "low"
          ? item.thinking_level
          : "",
    }));
}

export function stringifyAliasDrafts(items: ModelAliasDraftItem[]): string {
  return JSON.stringify(
    Object.fromEntries(
      items
        .filter((item) => item.alias.trim())
        .map((item) => [
          item.alias.trim(),
          {
            provider: item.provider,
            model: item.model,
            description: item.description,
            ...(item.thinking_level ? { thinking_level: item.thinking_level } : {}),
          },
        ])
    ),
    null,
    2
  );
}

export function buildProviderPreset(providerId: string): ProviderDraftItem {
  const preset = PROVIDER_PRESETS[providerId] ?? {
    id: providerId,
    name: providerId.replace("-", " ").trim() || "Custom Provider",
    auth_type: "api_key",
    api_key_env: `${providerId.toUpperCase().replace(/[^A-Z0-9]+/g, "_")}_API_KEY`,
  };
  return {
    ...preset,
    enabled: true,
  };
}

export function buildDefaultAliasDrafts(providerId: string): ModelAliasDraftItem[] {
  if (providerId === "openai-codex") {
    return [
      {
        alias: "main",
        provider: providerId,
        model: "gpt-5.4",
        description: "主力模型",
        thinking_level: "xhigh",
      },
      {
        alias: "cheap",
        provider: providerId,
        model: "gpt-5.4",
        description: "低成本模型",
        thinking_level: "low",
      },
    ];
  }
  const model = providerId === "openrouter" ? "openrouter/auto" : `${providerId}/auto`;
  return [
    {
      alias: "main",
      provider: providerId,
      model,
      description: "主力模型",
      thinking_level: "",
    },
    {
      alias: "cheap",
      provider: providerId,
      model,
      description: "低成本模型",
      thinking_level: "",
    },
  ];
}

export function envPresence(details: ProviderRuntimeDetails): Set<string> {
  return new Set([
    ...(Array.isArray(details.litellm_env_names) ? details.litellm_env_names : []),
    ...(Array.isArray(details.runtime_env_names) ? details.runtime_env_names : []),
  ]);
}

export function generateSecretValue(): string {
  if (typeof globalThis.crypto !== "undefined" && "getRandomValues" in globalThis.crypto) {
    const bytes = new Uint8Array(24);
    globalThis.crypto.getRandomValues(bytes);
    return `sk-${Array.from(bytes, (item) => item.toString(16).padStart(2, "0")).join("")}`;
  }
  return `sk-${Math.random().toString(16).slice(2)}${Math.random()
    .toString(16)
    .slice(2)}`;
}

export function providerStatus(
  provider: ProviderDraftItem,
  providerRuntimeDetails: ProviderRuntimeDetails,
  savedEnvNames: Set<string>,
  secretValues: Record<string, string>
): {
  label: string;
  tone: string;
} {
  if (!provider.enabled) {
    return { label: "已停用", tone: "is-draft" };
  }
  if (provider.id === "openai-codex" && provider.auth_type === "oauth") {
    return {
      label: providerRuntimeDetails.openai_oauth_connected ? "已授权" : "待授权",
      tone: providerRuntimeDetails.openai_oauth_connected ? "is-ready" : "is-warning",
    };
  }
  if (savedEnvNames.has(provider.api_key_env) || Boolean(secretValues[provider.api_key_env]?.trim())) {
    return { label: "已配置密钥", tone: "is-ready" };
  }
  return { label: "待填密钥", tone: "is-warning" };
}

export function reasoningSupportStateForAlias(
  providerId: string,
  modelName: string
): ReasoningSupportState {
  const provider = providerId.trim().toLowerCase();
  const model = modelName.trim().toLowerCase();
  if (!provider || !model) {
    return "pending";
  }
  if (provider === "openai-codex") {
    return "supported";
  }
  if (provider === "openai") {
    return model.startsWith("gpt-5") ||
      model.startsWith("o1") ||
      model.startsWith("o3") ||
      model.startsWith("o4")
      ? "supported"
      : "unsupported";
  }
  if (provider === "anthropic") {
    return model.includes("thinking") ? "supported" : "unsupported";
  }
  if (provider === "openrouter") {
    return OPENROUTER_REASONING_PATTERNS.some((pattern) => pattern.test(model))
      ? "supported"
      : "unsupported";
  }
  return "unsupported";
}

export function normalizeAliasDrafts(items: ModelAliasDraftItem[]): ModelAliasDraftItem[] {
  return items.map((item) => {
    if (
      item.thinking_level &&
      reasoningSupportStateForAlias(item.provider, item.model) !== "supported"
    ) {
      return {
        ...item,
        thinking_level: "",
      };
    }
    return item;
  });
}

export function reasoningSupportCopy(
  providerId: string,
  modelName: string
): string {
  const state = reasoningSupportStateForAlias(providerId, modelName);
  if (state === "supported") {
    return "当前 alias 看起来支持推理强度，可以按需要选择。";
  }
  if (state === "pending") {
    return "先填 Provider 和模型名，再判断是否支持推理强度。";
  }
  return "这个 alias 当前不在支持名单里。保存时会自动清空，后端也会忽略 reasoning 参数。";
}
