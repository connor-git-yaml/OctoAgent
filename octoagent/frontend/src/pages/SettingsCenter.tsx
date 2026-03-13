import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import {
  categoryForHint,
  deepClone,
  findSchemaNode,
  getValueAtPath,
  parseFieldStateValue,
  setValueAtPath,
  widgetValueToFieldState,
} from "../workbench/utils";
import type {
  ConfigFieldHint,
  SkillGovernanceItem,
  SetupReviewSummary,
  SetupRiskItem,
} from "../types";

type FieldState = Record<string, string | boolean>;
type FieldErrors = Record<string, string>;
type SkillSelectionState = Record<string, boolean>;

const DEFAULT_TRUSTED_PROXY_CIDRS = "127.0.0.1/32\n::1/128";
const DEFAULT_MEMORY_TIMEOUT = "5";

interface FieldGuide {
  title: string;
  description: string;
  example?: string;
  exampleLabel?: string;
  actions?: Array<{ label: string; value: string }>;
}

interface ProviderDraftItem {
  id: string;
  name: string;
  auth_type: "api_key" | "oauth";
  api_key_env: string;
  enabled: boolean;
}

interface ModelAliasDraftItem {
  alias: string;
  provider: string;
  model: string;
  description: string;
  thinking_level: "" | "xhigh" | "high" | "medium" | "low";
}

interface ProviderRuntimeDetails {
  provider_entries?: Array<Record<string, unknown>>;
  litellm_env_names?: string[];
  runtime_env_names?: string[];
  credential_profiles?: Array<Record<string, unknown>>;
  openai_oauth_connected?: boolean;
  openai_oauth_profile?: string;
}

const PROVIDER_PRESETS: Record<
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

const CUSTOM_PROVIDER_FIELD_PATHS = new Set([
  "runtime.llm_mode",
  "runtime.litellm_proxy_url",
  "runtime.master_key_env",
  "providers",
  "model_aliases",
]);

function buildFieldState(
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

function buildConfigPayload(
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

function groupLabel(groupId: string): { title: string; description: string } {
  switch (groupId) {
    case "main-agent":
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

function selectOptions(
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

function summaryTone(
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

function renderRiskList(title: string, risks: SetupRiskItem[]) {
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

function parseJsonFieldValue(
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

function optionLabelForHint(hint: ConfigFieldHint, option: string): string {
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

function buildFieldGuide(
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
  if (hint.field_path === "providers") {
    return null;
  }
  if (hint.field_path === "model_aliases") {
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
  if (
    hint.widget === "string-list" &&
    hint.field_path.startsWith("channels.telegram.")
  ) {
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

function readProviderRuntimeDetails(source: unknown): ProviderRuntimeDetails {
  if (!source || typeof source !== "object" || Array.isArray(source)) {
    return {};
  }
  return source as ProviderRuntimeDetails;
}

function buildSkillSelectionState(items: SkillGovernanceItem[]): SkillSelectionState {
  return Object.fromEntries(items.map((item) => [item.item_id, item.selected]));
}

function buildSkillSelectionSyncKey(items: SkillGovernanceItem[]): string {
  return JSON.stringify(
    items.map((item) => ({
      item_id: item.item_id,
      selected: item.selected,
      enabled_by_default: item.enabled_by_default,
    }))
  );
}

function buildSkillSelectionPayload(
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

function parseProviderDrafts(rawValue: string | boolean | undefined): ProviderDraftItem[] {
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

function stringifyProviderDrafts(items: ProviderDraftItem[]): string {
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

function parseAliasDrafts(rawValue: string | boolean | undefined): ModelAliasDraftItem[] {
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

function stringifyAliasDrafts(items: ModelAliasDraftItem[]): string {
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

function buildProviderPreset(providerId: string): ProviderDraftItem {
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

function buildDefaultAliasDrafts(providerId: string): ModelAliasDraftItem[] {
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

function envPresence(details: ProviderRuntimeDetails): Set<string> {
  return new Set([
    ...(Array.isArray(details.litellm_env_names) ? details.litellm_env_names : []),
    ...(Array.isArray(details.runtime_env_names) ? details.runtime_env_names : []),
  ]);
}

function generateSecretValue(): string {
  if (typeof globalThis.crypto !== "undefined" && "getRandomValues" in globalThis.crypto) {
    const bytes = new Uint8Array(24);
    globalThis.crypto.getRandomValues(bytes);
    return `sk-${Array.from(bytes, (item) => item.toString(16).padStart(2, "0")).join("")}`;
  }
  return `sk-${Math.random().toString(16).slice(2)}${Math.random()
    .toString(16)
    .slice(2)}`;
}

export default function SettingsCenter() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const location = useLocation();
  const config = snapshot!.resources.config;
  const selector = snapshot!.resources.project_selector;
  const memory = snapshot!.resources.memory;
  const setup = snapshot!.resources.setup_governance;
  const policyProfiles = snapshot!.resources.policy_profiles;
  const skillGovernance = snapshot!.resources.skill_governance;
  const skillSelectionSyncKey = buildSkillSelectionSyncKey(skillGovernance.items);
  const [fieldState, setFieldState] = useState<FieldState>(() =>
    buildFieldState(config.ui_hints, config.current_value)
  );
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [policyProfileId, setPolicyProfileId] = useState(policyProfiles.active_profile_id);
  const [skillSelection, setSkillSelection] = useState<SkillSelectionState>(() =>
    buildSkillSelectionState(skillGovernance.items)
  );
  const [review, setReview] = useState<SetupReviewSummary>(setup.review);
  const [secretValues, setSecretValues] = useState<Record<string, string>>({});

  useEffect(() => {
    setFieldState(buildFieldState(config.ui_hints, config.current_value));
    setFieldErrors({});
  }, [config.generated_at]);

  useEffect(() => {
    setPolicyProfileId(policyProfiles.active_profile_id);
  }, [policyProfiles.generated_at, policyProfiles.active_profile_id]);

  useEffect(() => {
    setSkillSelection(buildSkillSelectionState(skillGovernance.items));
  }, [skillSelectionSyncKey]);

  useEffect(() => {
    setReview(setup.review);
  }, [setup.generated_at]);

  useEffect(() => {
    setSecretValues({});
  }, [setup.generated_at, config.generated_at]);

  useEffect(() => {
    if (!location.hash) {
      return;
    }
    const targetId = location.hash.slice(1);
    requestAnimationFrame(() => {
      document.getElementById(targetId)?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    });
  }, [location.hash, config.generated_at]);

  const groupedHints = Object.values(config.ui_hints)
    .sort((left, right) => left.order - right.order)
    .reduce<Record<string, ConfigFieldHint[]>>((groups, hint) => {
      const key = categoryForHint(hint);
      groups[key] = [...(groups[key] ?? []), hint];
      return groups;
    }, {});
  const memoryHints = groupedHints.memory ?? [];
  const memoryBasicHints = memoryHints.filter((hint) => hint.section === "memory-basic");
  const memoryAdvancedHints = memoryHints.filter((hint) => hint.section === "memory-advanced");
  const otherGroupIds = ["channels", "advanced"].filter(
    (groupId) => (groupedHints[groupId] ?? []).length > 0
  );
  const providerRuntimeDetails = readProviderRuntimeDetails(setup.provider_runtime.details);
  const providerDrafts = parseProviderDrafts(fieldState.providers);
  const aliasDrafts = parseAliasDrafts(fieldState.model_aliases);
  const activeProviders = providerDrafts.filter((item) => item.enabled);
  const defaultProvider = activeProviders[0] ?? providerDrafts[0] ?? buildProviderPreset("openrouter");
  const providerSelectOptions = providerDrafts
    .map((item) => ({
      value: item.id,
      label: item.name?.trim() ? `${item.name} · ${item.id}` : item.id,
    }))
    .filter((item) => item.value.trim());
  const savedEnvNames = envPresence(providerRuntimeDetails);
  const masterKeyHint = config.ui_hints["runtime.master_key_env"];
  const proxyUrlHint = config.ui_hints["runtime.litellm_proxy_url"];
  const reviewBlockingCount = review.blocking_reasons.length;
  const reviewWarningCount = review.warnings.length;
  const reviewNextActions = review.next_actions.slice(0, 3);
  function buildSetupDraft(secretStateOverride?: Record<string, string>) {
    const result = buildConfigPayload(config.current_value, config.ui_hints, fieldState);
    setFieldErrors(result.errors);
    if (Object.keys(result.errors).length > 0) {
      return null;
    }
    return {
      config: result.config,
      skill_selection: buildSkillSelectionPayload(skillGovernance.items, skillSelection),
      secret_values: Object.fromEntries(
        Object.entries(secretStateOverride ?? secretValues).filter(([, value]) => value.trim())
      ),
    };
  }

  async function handleReview() {
    const draft = buildSetupDraft();
    if (!draft) {
      return;
    }
    const result = await submitAction("setup.review", { draft });
    const nextReview = result?.data.review;
    if (nextReview && typeof nextReview === "object" && !Array.isArray(nextReview)) {
      setReview(nextReview as SetupReviewSummary);
    }
  }

  async function handleApply() {
    const draft = buildSetupDraft();
    if (!draft) {
      return;
    }
    const reviewResult = await submitAction("setup.review", { draft });
    const nextReview = reviewResult?.data.review;
    if (nextReview && typeof nextReview === "object" && !Array.isArray(nextReview)) {
      const parsedReview = nextReview as SetupReviewSummary;
      setReview(parsedReview);
      if (!parsedReview.ready) {
        return;
      }
    } else if (!review.ready) {
      return;
    }
    const result = await submitAction("setup.apply", { draft });
    const appliedReview = result?.data.review;
    if (appliedReview && typeof appliedReview === "object" && !Array.isArray(appliedReview)) {
      setReview(appliedReview as SetupReviewSummary);
    }
  }

  async function handleQuickConnect() {
    const masterKeyEnv = String(
      fieldState["runtime.master_key_env"] ?? "LITELLM_MASTER_KEY"
    );
    const nextSecretValues = { ...secretValues };
    if (
      !nextSecretValues[masterKeyEnv]?.trim() &&
      !savedEnvNames.has(masterKeyEnv)
    ) {
      nextSecretValues[masterKeyEnv] = generateSecretValue();
      setSecretValues((state) => ({
        ...state,
        [masterKeyEnv]: nextSecretValues[masterKeyEnv],
      }));
    }
    const draft = buildSetupDraft(nextSecretValues);
    if (!draft) {
      return;
    }
    const needsOpenAIOAuth =
      defaultProvider.id === "openai-codex" &&
      defaultProvider.auth_type === "oauth" &&
      !providerRuntimeDetails.openai_oauth_connected;
    if (needsOpenAIOAuth) {
      const connected = await handleOpenAIOAuthConnect();
      if (!connected) {
        return;
      }
    }
    const reviewResult = await submitAction("setup.review", { draft });
    const nextReview = reviewResult?.data.review;
    if (nextReview && typeof nextReview === "object" && !Array.isArray(nextReview)) {
      const parsedReview = nextReview as SetupReviewSummary;
      setReview(parsedReview);
      if (!parsedReview.ready) {
        return;
      }
    } else if (!review.ready) {
      return;
    }
    const result = await submitAction("setup.quick_connect", { draft });
    const appliedReview = result?.data.review;
    if (appliedReview && typeof appliedReview === "object" && !Array.isArray(appliedReview)) {
      setReview(appliedReview as SetupReviewSummary);
    }
  }

  const currentPolicy =
    policyProfiles.profiles.find((item) => item.profile_id === policyProfileId) ?? null;
  const selectedSkills = skillGovernance.items.filter(
    (item) => skillSelection[item.item_id] ?? item.selected
  );
  const blockedSkills = selectedSkills.filter((item) => item.blocking);
  const unavailableSkills = skillGovernance.items.filter(
    (item) => item.availability !== "available"
  );
  const runtimeMode =
    String(
      fieldState["runtime.llm_mode"] ??
        getValueAtPath(config.current_value, "runtime.llm_mode") ??
        "echo"
    )
      .trim()
      .toLowerCase() || "echo";
  const usingEchoMode = runtimeMode === "echo";
  const connectBusy =
    busyActionId === "setup.review" ||
    busyActionId === "setup.apply" ||
    busyActionId === "setup.quick_connect" ||
    busyActionId === "provider.oauth.openai_codex";
  const memoryMode =
    String(
      fieldState["memory.backend_mode"] ??
        getValueAtPath(config.current_value, "memory.backend_mode") ??
        "local_only"
    )
      .trim()
      .toLowerCase() || "local_only";

  function updateFieldValue(fieldPath: string, value: string | boolean) {
    setFieldState((state) => ({
      ...state,
      [fieldPath]: value,
    }));
  }

  function updateSecretValue(envName: string, value: string) {
    setSecretValues((state) => ({
      ...state,
      [envName]: value,
    }));
  }

  function updateSkillSelection(itemId: string, selected: boolean) {
    setSkillSelection((state) => ({
      ...state,
      [itemId]: selected,
    }));
  }

  function updateProviders(nextProviders: ProviderDraftItem[]) {
    updateFieldValue("providers", stringifyProviderDrafts(nextProviders));
  }

  function updateAliases(nextAliases: ModelAliasDraftItem[]) {
    updateFieldValue("model_aliases", stringifyAliasDrafts(nextAliases));
  }

  function ensureProviderForAliases(): string {
    const candidateId = activeProviders[0]?.id ?? providerDrafts[0]?.id;
    if (candidateId) {
      return candidateId;
    }
    const fallbackProvider = buildProviderPreset("openrouter");
    updateProviders([fallbackProvider]);
    return fallbackProvider.id;
  }

  function updateProviderAt(index: number, patch: Partial<ProviderDraftItem>) {
    const current = providerDrafts[index];
    if (!current) {
      return;
    }
    const nextProviders = [...providerDrafts];
    const nextProvider: ProviderDraftItem = {
      ...current,
      ...patch,
    };
    nextProviders[index] = nextProvider;
    updateProviders(nextProviders);
    if (patch.id && patch.id !== current.id) {
      updateAliases(
        aliasDrafts.map((item) =>
          item.provider === current.id ? { ...item, provider: patch.id ?? "" } : item
        )
      );
    }
  }

  function moveProviderToFront(index: number) {
    const current = providerDrafts[index];
    if (!current) {
      return;
    }
    const nextProviders = [...providerDrafts];
    nextProviders.splice(index, 1);
    nextProviders.unshift({
      ...current,
      enabled: true,
    });
    updateProviders(nextProviders);
  }

  function addProviderDraft(providerId: string) {
    if (providerId !== "custom") {
      const existingIndex = providerDrafts.findIndex((item) => item.id === providerId);
      if (existingIndex >= 0) {
        updateProviderAt(existingIndex, { enabled: true });
        moveProviderToFront(existingIndex);
        updateFieldValue("runtime.llm_mode", "litellm");
        return;
      }
    }
    const customIndex = providerDrafts.length + 1;
    const preset =
      providerId === "custom"
        ? {
            id: `custom-provider-${customIndex}`,
            name: `Custom Provider ${customIndex}`,
            auth_type: "api_key" as const,
            api_key_env: `CUSTOM_PROVIDER_${customIndex}_API_KEY`,
            enabled: true,
          }
        : buildProviderPreset(providerId);
    const nextProviders = [...providerDrafts, preset];
    updateProviders(nextProviders);
    updateFieldValue("runtime.llm_mode", "litellm");
    if (aliasDrafts.length === 0) {
      updateAliases(buildDefaultAliasDrafts(preset.id));
    }
  }

  function removeProviderAt(index: number) {
    const target = providerDrafts[index];
    if (!target) {
      return;
    }
    const nextProviders = providerDrafts.filter((_, providerIndex) => providerIndex !== index);
    const fallbackProviderId = nextProviders.find((item) => item.enabled)?.id ?? nextProviders[0]?.id ?? "";
    updateProviders(nextProviders);
    updateAliases(
      aliasDrafts.map((item) =>
        item.provider === target.id ? { ...item, provider: fallbackProviderId } : item
      )
    );
  }

  function restoreRecommendedAliases(providerId?: string) {
    const nextProviderId = providerId?.trim() ? providerId : ensureProviderForAliases();
    updateAliases(buildDefaultAliasDrafts(nextProviderId));
  }

  function providerStatus(provider: ProviderDraftItem): {
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

  function updateAliasAt(index: number, patch: Partial<ModelAliasDraftItem>) {
    const nextAliases = [...aliasDrafts];
    nextAliases[index] = {
      ...nextAliases[index],
      ...patch,
    };
    updateAliases(nextAliases);
  }

  function addAliasDraft() {
    updateAliases([
      ...aliasDrafts,
      {
        alias: `alias_${aliasDrafts.length + 1}`,
        provider: ensureProviderForAliases(),
        model: "",
        description: "",
        thinking_level: "",
      },
    ]);
  }

  function removeAliasDraft(index: number) {
    updateAliases(aliasDrafts.filter((_, itemIndex) => itemIndex !== index));
  }

  async function handleOpenAIOAuthConnect() {
    const existingIndex = providerDrafts.findIndex((item) => item.id === "openai-codex");
    if (existingIndex >= 0) {
      updateProviderAt(existingIndex, { auth_type: "oauth", enabled: true });
      moveProviderToFront(existingIndex);
    } else {
      updateProviders([buildProviderPreset("openai-codex"), ...providerDrafts]);
      if (aliasDrafts.length === 0) {
        updateAliases(buildDefaultAliasDrafts("openai-codex"));
      }
    }
    updateFieldValue("runtime.llm_mode", "litellm");
    const oauthProvider =
      providerDrafts.find((item) => item.id === "openai-codex") ?? buildProviderPreset("openai-codex");
    const envName = oauthProvider.api_key_env || "OPENAI_API_KEY";
    const result = await submitAction("provider.oauth.openai_codex", {
      env_name: envName,
      profile_name: "openai-codex-default",
    });
    if (result) {
      setSecretValues((state) => {
        const next = { ...state };
        delete next[envName];
        return next;
      });
    }
    return result !== null;
  }

  function scrollToSection(sectionId: string) {
    document
      .getElementById(`settings-group-${sectionId}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderHintFields(hints: ConfigFieldHint[]) {
    return (
      <div className="wb-form-grid">
        {hints.map((hint) => {
          const currentValue = fieldState[hint.field_path] ?? "";
          const options = selectOptions(config.schema, hint);
          const error = fieldErrors[hint.field_path];
          const fieldGuide = buildFieldGuide(hint, usingEchoMode);
          return (
            <label
              key={hint.field_path}
              className={`wb-field ${hint.multiline ? "wb-field-span-2" : ""}`}
            >
              <span>{hint.label}</span>
              {hint.help_text ? <small>{hint.help_text}</small> : null}
              {fieldGuide ? (
                <details className="wb-field-guide wb-field-guide-disclosure">
                  <summary>{fieldGuide.title}</summary>
                  <p>{fieldGuide.description}</p>
                  {fieldGuide.actions?.length ? (
                    <div className="wb-inline-actions wb-inline-actions-wrap">
                      {fieldGuide.actions.map((action) => (
                        <button
                          key={action.label}
                          type="button"
                          aria-label={action.label}
                          className="wb-button wb-button-tertiary wb-button-inline"
                          onClick={() => updateFieldValue(hint.field_path, action.value)}
                        >
                          {action.label}
                        </button>
                      ))}
                    </div>
                  ) : null}
                  {fieldGuide.example ? (
                    <div className="wb-field-guide-sample">
                      <small>{fieldGuide.exampleLabel ?? "示例"}</small>
                      <pre>{fieldGuide.example}</pre>
                    </div>
                  ) : null}
                </details>
              ) : null}
              {hint.widget === "toggle" ? (
                <input
                  type="checkbox"
                  checked={Boolean(currentValue)}
                  onChange={(event) => updateFieldValue(hint.field_path, event.target.checked)}
                />
              ) : hint.widget === "select" && options.length > 0 ? (
                <select
                  value={String(currentValue)}
                  onChange={(event) => updateFieldValue(hint.field_path, event.target.value)}
                >
                  {options.map((option) => (
                    <option key={option} value={option}>
                      {optionLabelForHint(hint, option)}
                    </option>
                  ))}
                </select>
              ) : hint.widget === "string-list" ||
                hint.widget === "provider-list" ||
                hint.widget === "alias-map" ? (
                <textarea
                  rows={hint.widget === "string-list" ? 4 : 8}
                  value={String(currentValue)}
                  placeholder={hint.placeholder}
                  onChange={(event) => updateFieldValue(hint.field_path, event.target.value)}
                />
              ) : (
                <input
                  type="text"
                  value={String(currentValue)}
                  placeholder={hint.placeholder}
                  onChange={(event) => updateFieldValue(hint.field_path, event.target.value)}
                />
              )}
              {hint.description ? <small>{hint.description}</small> : null}
              {error ? <small className="wb-field-error">{error}</small> : null}
            </label>
          );
        })}
      </div>
    );
  }

  return (
    <div className="wb-page wb-settings-page">
      <section id="settings-group-overview" className="wb-hero wb-settings-hero wb-settings-hero-refined">
        <div className="wb-hero-copy">
          <p className="wb-kicker">Settings</p>
          <h1>系统连接与默认能力</h1>
          <p>统一管理 Provider、模型别名、渠道入口、Memory 后端和平台级安全开关。</p>
          <div className="wb-chip-row">
            <span className="wb-chip">{usingEchoMode ? "体验模式" : "真实模型模式"}</span>
            <span className="wb-chip">
              当前 Project {selector.current_project_id} / {selector.current_workspace_id}
            </span>
            <span className={`wb-chip ${review.ready ? "is-success" : "is-warning"}`} role="status">
              {review.ready ? "检查通过" : `待处理 ${reviewBlockingCount}`}
            </span>
          </div>
        </div>
        <div className="wb-settings-hero-actions">
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={() => void handleQuickConnect()}
            disabled={connectBusy}
          >
            {usingEchoMode ? "连接并启用真实模型" : "保存并重新连接"}
          </button>
          <button
            type="button"
            className="wb-button wb-button-secondary"
            onClick={() => void handleReview()}
            disabled={connectBusy}
          >
            检查配置
          </button>
          <button
            type="button"
            className="wb-button wb-button-secondary"
            onClick={() => void handleApply()}
            disabled={connectBusy}
          >
            保存配置
          </button>
        </div>
      </section>

      <div className="wb-settings-summary-grid">
        <article className={`wb-card wb-card-accent is-${summaryTone(review)}`}>
          <p className="wb-card-label">配置状态</p>
          <strong>{review.ready ? "可以保存" : "需要处理"}</strong>
          <span>阻塞项 {reviewBlockingCount}</span>
          <span>提醒 {reviewWarningCount}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">接入模式</p>
          <strong>{usingEchoMode ? "Echo" : "LiteLLM"}</strong>
          <span>{setup.provider_runtime.status}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">Providers</p>
          <strong>{providerDrafts.length}</strong>
          <span>已启用 {activeProviders.length}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">模型别名</p>
          <strong>{aliasDrafts.length}</strong>
          <span>默认引用 {defaultProvider.id || "未设置"}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">Memory</p>
          <strong>{memoryMode === "memu" ? "MemU bridge" : "本地记忆"}</strong>
          <span>{memory.backend_state || memory.status}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">默认技能</p>
          <strong>{selectedSkills.length}</strong>
          <span>阻塞 {blockedSkills.length} / 不可用 {unavailableSkills.length}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">安全等级</p>
          <strong>{currentPolicy?.label ?? policyProfiles.active_profile_id}</strong>
          <span>{currentPolicy?.approval_policy ?? "未选择"}</span>
        </article>
      </div>

      <nav className="wb-settings-section-nav" aria-label="Settings sections">
        <button type="button" className="wb-section-chip" onClick={() => scrollToSection("overview")}>
          概览
        </button>
        <button type="button" className="wb-section-chip" onClick={() => scrollToSection("main-agent")}>
          Providers
        </button>
        <button type="button" className="wb-section-chip" onClick={() => scrollToSection("aliases")}>
          模型别名
        </button>
        <button type="button" className="wb-section-chip" onClick={() => scrollToSection("channels")}>
          渠道
        </button>
        <button type="button" className="wb-section-chip" onClick={() => scrollToSection("memory")}>
          Memory
        </button>
        <button type="button" className="wb-section-chip" onClick={() => scrollToSection("governance")}>
          安全与能力
        </button>
        <button type="button" className="wb-section-chip" onClick={() => scrollToSection("review")}>
          保存检查
        </button>
      </nav>

      <section id="settings-group-main-agent" className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">Models & Providers</p>
            <h3>先确定接入模式，再管理多个 Provider</h3>
            <p className="wb-panel-copy">
              `Settings` 只处理平台连接层。模型别名会引用这里的 Provider 和真实模型名。
            </p>
          </div>
        </div>

        <div className="wb-settings-mode-row">
          <button
            type="button"
            className={`wb-mode-card ${usingEchoMode ? "is-active" : ""}`}
            onClick={() => updateFieldValue("runtime.llm_mode", "echo")}
          >
            <span className="wb-card-label">体验模式</span>
            <strong>先跑通页面和任务流</strong>
            <p>不依赖真实模型，适合先检查控制台和交互。</p>
          </button>
          <button
            type="button"
            className={`wb-mode-card ${!usingEchoMode ? "is-active" : ""}`}
            onClick={() => updateFieldValue("runtime.llm_mode", "litellm")}
          >
            <span className="wb-card-label">真实模型模式</span>
            <strong>通过 LiteLLM 连接 Provider</strong>
            <p>支持多个 Provider 并存，别名按 provider + model 路由。</p>
          </button>
        </div>

        <div className="wb-provider-layout">
          <div className="wb-provider-card">
            <div className="wb-provider-card-head">
              <div>
                <p className="wb-card-label">Gateway</p>
                <strong>LiteLLM 运行参数</strong>
              </div>
              <span className={`wb-status-pill ${usingEchoMode ? "is-draft" : "is-ready"}`}>
                {usingEchoMode ? "Echo" : "LiteLLM"}
              </span>
            </div>

            <div className="wb-note">
              <strong>当前默认接入</strong>
              <span>
                {usingEchoMode
                  ? "当前仍是体验模式。需要真实模型时，再启用 LiteLLM 并补齐 Provider。"
                  : `默认 Provider 为 ${defaultProvider.name || defaultProvider.id}。新的推荐别名会优先引用它。`}
              </span>
            </div>

            <div className="wb-form-grid">
              <label className="wb-field">
                <span>LiteLLM 代理地址</span>
                <input
                  type="text"
                  value={String(fieldState["runtime.litellm_proxy_url"] ?? "")}
                  placeholder={proxyUrlHint?.placeholder ?? "http://localhost:4000"}
                  onChange={(event) =>
                    updateFieldValue("runtime.litellm_proxy_url", event.target.value)
                  }
                />
                <small>{proxyUrlHint?.description || "通常保持本地默认地址即可。"}</small>
              </label>
              <label className="wb-field">
                <span>{masterKeyHint?.label ?? "LiteLLM Master Key 环境变量名"}</span>
                <input
                  type="text"
                  value={String(fieldState["runtime.master_key_env"] ?? "LITELLM_MASTER_KEY")}
                  onChange={(event) =>
                    updateFieldValue("runtime.master_key_env", event.target.value)
                  }
                />
              </label>
              <label className="wb-field wb-field-span-2">
                <span>LiteLLM Master Key 值</span>
                <input
                  type="password"
                  value={
                    secretValues[
                      String(fieldState["runtime.master_key_env"] ?? "LITELLM_MASTER_KEY")
                    ] ?? ""
                  }
                  placeholder={
                    savedEnvNames.has(
                      String(fieldState["runtime.master_key_env"] ?? "LITELLM_MASTER_KEY")
                    )
                      ? "本地已存在值；重新输入才会覆盖"
                      : "生成或输入一串随机长字符串"
                  }
                  onChange={(event) =>
                    updateSecretValue(
                      String(fieldState["runtime.master_key_env"] ?? "LITELLM_MASTER_KEY"),
                      event.target.value
                    )
                  }
                />
                <div className="wb-inline-actions wb-inline-actions-wrap">
                  <button
                    type="button"
                    className="wb-button wb-button-tertiary wb-button-inline"
                    onClick={() =>
                      updateSecretValue(
                        String(fieldState["runtime.master_key_env"] ?? "LITELLM_MASTER_KEY"),
                        generateSecretValue()
                      )
                    }
                  >
                    生成随机 Master Key
                  </button>
                </div>
              </label>
            </div>
          </div>

          <div className="wb-provider-card">
            <div className="wb-provider-card-head">
              <div>
                <p className="wb-card-label">Providers</p>
                <strong>多个 Provider 可同时存在</strong>
              </div>
              <span className="wb-status-pill is-active">共 {providerDrafts.length} 个</span>
            </div>

            <div className="wb-provider-preset-row">
              <button
                type="button"
                className="wb-button wb-button-tertiary wb-button-inline"
                onClick={() => addProviderDraft("openrouter")}
              >
                添加 OpenRouter
              </button>
              <button
                type="button"
                className="wb-button wb-button-tertiary wb-button-inline"
                onClick={() => addProviderDraft("openai")}
              >
                添加 OpenAI
              </button>
              <button
                type="button"
                className="wb-button wb-button-tertiary wb-button-inline"
                onClick={() => addProviderDraft("anthropic")}
              >
                添加 Anthropic
              </button>
              <button
                type="button"
                className="wb-button wb-button-tertiary wb-button-inline"
                onClick={() => addProviderDraft("openai-codex")}
              >
                添加 OpenAI Auth
              </button>
              <button
                type="button"
                className="wb-button wb-button-secondary wb-button-inline"
                onClick={() => addProviderDraft("custom")}
              >
                添加自定义 Provider
              </button>
            </div>

            <div className="wb-provider-list">
              {providerDrafts.length === 0 ? (
                <div className="wb-empty-state">
                  <strong>还没有 Provider</strong>
                  <span>先添加一个 Provider，再为模型别名选择 provider + model。</span>
                </div>
              ) : null}

              {providerDrafts.map((provider, index) => {
                const status = providerStatus(provider);
                const providerName = provider.name?.trim() || provider.id || `Provider ${index + 1}`;
                const isOAuthProvider =
                  provider.id === "openai-codex" && provider.auth_type === "oauth";
                return (
                  <article
                    key={`${provider.id}-${index}`}
                    className={`wb-provider-item ${index === 0 ? "is-default" : ""}`}
                  >
                    <div className="wb-provider-card-head">
                      <div>
                        <p className="wb-card-label">{index === 0 ? "默认 Provider" : "Provider"}</p>
                        <strong>{providerName}</strong>
                        <div className="wb-provider-meta">
                          <span>ID {provider.id}</span>
                          <span>{provider.auth_type === "oauth" ? "OAuth" : "API Key"}</span>
                        </div>
                      </div>
                      <div className="wb-inline-actions wb-inline-actions-wrap">
                        <span className={`wb-status-pill ${status.tone}`}>{status.label}</span>
                        {index !== 0 ? (
                          <button
                            type="button"
                            className="wb-button wb-button-tertiary wb-button-inline"
                            onClick={() => moveProviderToFront(index)}
                          >
                            设为默认
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="wb-button wb-button-tertiary wb-button-inline"
                          onClick={() => removeProviderAt(index)}
                        >
                          删除
                        </button>
                      </div>
                    </div>

                    <div className="wb-form-grid wb-settings-provider-form">
                      <label className="wb-field">
                        <span>显示名称</span>
                        <input
                          type="text"
                          value={provider.name}
                          onChange={(event) => updateProviderAt(index, { name: event.target.value })}
                        />
                      </label>
                      <label className="wb-field">
                        <span>Provider ID</span>
                        <input
                          type="text"
                          value={provider.id}
                          onChange={(event) => updateProviderAt(index, { id: event.target.value })}
                        />
                      </label>
                      <label className="wb-field">
                        <span>鉴权方式</span>
                        <select
                          value={provider.auth_type}
                          onChange={(event) =>
                            updateProviderAt(index, {
                              auth_type: event.target.value === "oauth" ? "oauth" : "api_key",
                            })
                          }
                        >
                          <option value="api_key">API Key</option>
                          <option value="oauth">OAuth</option>
                        </select>
                      </label>
                      <label className="wb-field">
                        <span>环境变量名</span>
                        <input
                          type="text"
                          value={provider.api_key_env}
                          onChange={(event) =>
                            updateProviderAt(index, { api_key_env: event.target.value })
                          }
                        />
                        <small>这里只填写环境变量名。</small>
                      </label>
                      <label className="wb-field wb-field-span-2">
                        <span>启用状态</span>
                        <div className="wb-provider-toggle-row">
                          <input
                            type="checkbox"
                            checked={provider.enabled}
                            aria-label={`启用 ${providerName}`}
                            onChange={(event) =>
                              updateProviderAt(index, { enabled: event.target.checked })
                            }
                          />
                          <span>{provider.enabled ? "已启用" : "已停用"}</span>
                        </div>
                      </label>
                    </div>

                    {isOAuthProvider ? (
                      <div className="wb-note">
                        <strong>OpenAI Auth</strong>
                        <span>
                          {providerRuntimeDetails.openai_oauth_profile
                            ? `当前凭证 ${providerRuntimeDetails.openai_oauth_profile}`
                            : "当前还没有本地授权凭证。"}
                        </span>
                        <div className="wb-inline-actions wb-inline-actions-wrap">
                          <button
                            type="button"
                            className="wb-button wb-button-secondary wb-button-inline"
                            onClick={() => void handleOpenAIOAuthConnect()}
                            disabled={connectBusy}
                          >
                            {providerRuntimeDetails.openai_oauth_connected
                              ? "重新连接 OpenAI Auth"
                              : "连接 OpenAI Auth"}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <label className="wb-field wb-field-span-2">
                        <span>API Key / Token</span>
                        <input
                          type="password"
                          value={secretValues[provider.api_key_env] ?? ""}
                          placeholder={
                            savedEnvNames.has(provider.api_key_env)
                              ? "本地已存在值；重新输入才会覆盖"
                              : "粘贴真实 API Key"
                          }
                          onChange={(event) =>
                            updateSecretValue(provider.api_key_env, event.target.value)
                          }
                        />
                      </label>
                    )}
                  </article>
                );
              })}
            </div>
          </div>
        </div>
      </section>

      <section id="settings-group-aliases" className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">模型别名</p>
            <h3>别名只引用 provider + model</h3>
            <p className="wb-panel-copy">业务侧统一使用 alias，底层 Provider 和模型切换都在这里完成。</p>
          </div>
          <div className="wb-inline-actions wb-inline-actions-wrap">
            <button
              type="button"
              className="wb-button wb-button-tertiary wb-button-inline"
              onClick={() => restoreRecommendedAliases(defaultProvider.id)}
            >
              恢复 main / cheap
            </button>
            <button
              type="button"
              className="wb-button wb-button-secondary wb-button-inline"
              onClick={() => addAliasDraft()}
            >
              新增别名
            </button>
          </div>
        </div>

        <div className="wb-alias-editor">
          {providerSelectOptions.length === 0 ? (
            <div className="wb-empty-state">
              <strong>先添加 Provider</strong>
              <span>模型别名需要绑定到现有 Provider；可以先添加 OpenRouter 或 OpenAI。</span>
            </div>
          ) : null}
          {aliasDrafts.length === 0 ? (
            <div className="wb-empty-state">
              <strong>还没有模型别名</strong>
              <span>建议至少保留 `main`，需要低成本路由时再添加 `cheap`。</span>
            </div>
          ) : null}
          {aliasDrafts.map((item, index) => (
            <div key={`${item.alias}-${index}`} className="wb-alias-row">
              <label className="wb-field">
                <span>别名</span>
                <input
                  type="text"
                  value={item.alias}
                  onChange={(event) => updateAliasAt(index, { alias: event.target.value })}
                />
              </label>
              <label className="wb-field">
                <span>Provider</span>
                <select
                  value={item.provider}
                  onChange={(event) => updateAliasAt(index, { provider: event.target.value })}
                >
                  <option value="">选择 Provider</option>
                  {providerSelectOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="wb-field wb-field-span-2">
                <span>模型名</span>
                <input
                  type="text"
                  value={item.model}
                  placeholder={defaultProvider.id === "openai-codex" ? "gpt-5.4" : "openrouter/auto"}
                  onChange={(event) => updateAliasAt(index, { model: event.target.value })}
                />
              </label>
              <label className="wb-field wb-field-span-2">
                <span>说明</span>
                <input
                  type="text"
                  value={item.description}
                  placeholder="例如：主力模型 / 低成本模型"
                  onChange={(event) => updateAliasAt(index, { description: event.target.value })}
                />
              </label>
              <label className="wb-field">
                <span>推理强度</span>
                <select
                  value={item.thinking_level}
                  onChange={(event) =>
                    updateAliasAt(index, {
                      thinking_level: event.target.value as ModelAliasDraftItem["thinking_level"],
                    })
                  }
                >
                  <option value="">默认</option>
                  <option value="xhigh">xhigh</option>
                  <option value="high">high</option>
                  <option value="medium">medium</option>
                  <option value="low">low</option>
                </select>
              </label>
              <div className="wb-alias-actions">
                <button
                  type="button"
                  className="wb-button wb-button-tertiary wb-button-inline"
                  onClick={() => removeAliasDraft(index)}
                  disabled={aliasDrafts.length <= 1}
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section id="settings-group-governance" className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">安全与能力</p>
            <h3>平台级安全开关与默认能力范围</h3>
          </div>
        </div>

        <div className="wb-card-grid wb-card-grid-3">
          <article className="wb-card">
            <p className="wb-card-label">安全等级</p>
            <strong>{currentPolicy?.label ?? policyProfiles.active_profile_id}</strong>
            <span>{currentPolicy?.approval_policy ?? "未选择"}</span>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">Tools & Skills</p>
            <strong>{setup.tools_skills.label}</strong>
            <span>{setup.tools_skills.summary}</span>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">默认能力数</p>
            <strong>{selectedSkills.length}</strong>
            <span>不可用 {unavailableSkills.length}</span>
          </article>
        </div>

        <div className="wb-note-stack">
          {skillGovernance.items.map((item) => {
            const selected = skillSelection[item.item_id] ?? item.selected;
            return (
              <label key={item.item_id} className="wb-note">
                <strong>
                  {item.label} · {selected ? "已启用" : "未启用"}
                </strong>
                <span>
                  {item.missing_requirements.length > 0
                    ? item.missing_requirements.join("；")
                    : "当前 capability pack 可用"}
                </span>
                <small>
                  {item.source_kind} · 默认{item.enabled_by_default ? "开启" : "关闭"} · 当前{" "}
                  {item.availability}
                </small>
                <input
                  type="checkbox"
                  aria-label={`启用 ${item.label}`}
                  checked={selected}
                  onChange={(event) => updateSkillSelection(item.item_id, event.target.checked)}
                />
              </label>
            );
          })}
        </div>
      </section>

      <section id="settings-group-memory" className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">Memory</p>
            <h3>平台级 Memory 后端与 bridge 连接</h3>
          </div>
        </div>

        <div className="wb-card-grid wb-card-grid-4">
          <article className="wb-card">
            <p className="wb-card-label">当前模式</p>
            <strong>{memoryMode === "memu" ? "MemU bridge" : "本地记忆"}</strong>
            <span>{memoryMode === "memu" ? "远端检索与回放" : "本地优先"}</span>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">后端健康</p>
            <strong>{memory.backend_state || memory.status}</strong>
            <span>{memory.backend_id || "未标记"}</span>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">当前结论</p>
            <strong>{memory.summary.sor_current_count}</strong>
            <span>片段 {memory.summary.fragment_count}</span>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">待处理积压</p>
            <strong>{memory.summary.pending_replay_count}</strong>
            <span>Vault refs {memory.summary.vault_ref_count}</span>
          </article>
        </div>

        {memory.warnings.length > 0 ? (
          <div className="wb-inline-banner is-error" role="alert">
            <strong>Memory 当前有提醒</strong>
            <span>{memory.warnings.join("；")}</span>
          </div>
        ) : (
          <div className="wb-inline-banner is-muted">
            <strong>推荐做法</strong>
            <span>首次使用先保持本地记忆；只有需要远端检索后端时再切到 MemU bridge。</span>
          </div>
        )}

        {memoryBasicHints.length > 0 ? (
          <>
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">连接配置</p>
                <h3>基础连接</h3>
              </div>
            </div>
            {renderHintFields(memoryBasicHints)}
          </>
        ) : null}

        {memoryAdvancedHints.length > 0 ? (
          <>
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">高阶连接</p>
                <h3>仅在 bridge 协议不一致时调整</h3>
              </div>
            </div>
            {renderHintFields(memoryAdvancedHints)}
          </>
        ) : null}
      </section>

      {otherGroupIds.map((groupId) => {
        const hints = (groupedHints[groupId] ?? []).filter(
          (hint) => !CUSTOM_PROVIDER_FIELD_PATHS.has(hint.field_path)
        );
        if (hints.length === 0) {
          return null;
        }
        const group = groupLabel(groupId);
        return (
          <section key={groupId} id={`settings-group-${groupId}`} className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">{group.title}</p>
                <h3>{group.description}</h3>
              </div>
            </div>
            {renderHintFields(hints)}
          </section>
        );
      })}

      <section id="settings-group-review" className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">保存检查</p>
            <h3>先看风险，再决定保存或一键接入</h3>
          </div>
        </div>

        <div className="wb-settings-review-grid">
          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>下一步</strong>
              <div className="wb-note-stack">
                {reviewNextActions.length > 0 ? (
                  reviewNextActions.map((item) => <span key={item}>{item}</span>)
                ) : (
                  <span>当前没有额外提示。</span>
                )}
              </div>
            </div>
            <div className="wb-note">
              <strong>当前模式</strong>
              <span>
                {usingEchoMode
                  ? "你现在处于体验模式，可以先完成页面和渠道配置。"
                  : "你正在准备接入真实模型，请先确认 Provider 和 alias。"}
              </span>
            </div>
            {review.agent_autonomy_risks.length > 0 ? (
              <div className="wb-note">
                <strong>其他模块仍有阻塞项</strong>
                <span>{review.agent_autonomy_risks.map((risk) => risk.title).join("；")}</span>
              </div>
            ) : null}
            {renderRiskList("模型与运行连接", review.provider_runtime_risks)}
            {renderRiskList("渠道暴露范围", review.channel_exposure_risks)}
            {renderRiskList("工具与技能", review.tool_skill_readiness_risks)}
            {renderRiskList("密钥绑定", review.secret_binding_risks)}
          </div>

          <div className="wb-provider-card">
            <div className="wb-provider-card-head">
              <div>
                <p className="wb-card-label">本页动作</p>
                <strong>检查、保存或一键接入</strong>
              </div>
              <span className={`wb-status-pill ${review.ready ? "is-ready" : "is-warning"}`}>
                {review.ready ? "Ready" : "Needs review"}
              </span>
            </div>
            <div className="wb-note">
              <strong>检查配置</strong>
              <span>先执行 `setup.review`，确认阻塞项和风险摘要。</span>
            </div>
            <div className="wb-inline-actions wb-inline-actions-wrap">
              <button
                type="button"
                className="wb-button wb-button-primary"
                onClick={() => void handleQuickConnect()}
                disabled={connectBusy}
              >
                {usingEchoMode ? "连接并启用真实模型" : "保存并重新连接"}
              </button>
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={() => void handleReview()}
                disabled={connectBusy}
              >
                检查配置
              </button>
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={() => void handleApply()}
                disabled={connectBusy}
              >
                保存配置
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
