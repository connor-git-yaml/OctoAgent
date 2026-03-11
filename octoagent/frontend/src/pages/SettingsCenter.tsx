import { useEffect, useState } from "react";
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
  AgentProfileItem,
  ConfigFieldHint,
  SetupReviewSummary,
  SetupRiskItem,
} from "../types";

type FieldState = Record<string, string | boolean>;
type FieldErrors = Record<string, string>;

const DEFAULT_AGENT_NAME = "OctoAgent";
const DEFAULT_AGENT_PERSONA =
  "通用个人助手，优先帮助用户完成当前任务，并在需要时给出下一步建议。";
const DEFAULT_TRUSTED_PROXY_CIDRS = "127.0.0.1/32\n::1/128";
const DEFAULT_MEMORY_TIMEOUT = "5";
const DEFAULT_MEMORY_SCOPE_LIMIT = "4";
const DEFAULT_MEMORY_PER_SCOPE_LIMIT = "3";
const DEFAULT_MEMORY_MAX_HITS = "4";

interface MemoryAccessPolicyDraft {
  allow_vault: boolean;
  include_history: boolean;
}

interface MemoryRecallDraft {
  post_filter_mode: string;
  rerank_mode: string;
  min_keyword_overlap: string;
  scope_limit: string;
  per_scope_limit: string;
  max_hits: string;
}

const MEMORY_RECALL_PRESETS: Array<{
  id: string;
  label: string;
  description: string;
  values: MemoryRecallDraft;
}> = [
  {
    id: "conservative",
    label: "保守召回",
    description: "更少噪声，优先只带回最贴近当前话题的少量记录。",
    values: {
      post_filter_mode: "keyword_overlap",
      rerank_mode: "heuristic",
      min_keyword_overlap: "2",
      scope_limit: "2",
      per_scope_limit: "2",
      max_hits: "3",
    },
  },
  {
    id: "balanced",
    label: "平衡默认",
    description: "适合大多数日常对话和任务，先用这组值通常最稳妥。",
    values: {
      post_filter_mode: "keyword_overlap",
      rerank_mode: "heuristic",
      min_keyword_overlap: "1",
      scope_limit: DEFAULT_MEMORY_SCOPE_LIMIT,
      per_scope_limit: DEFAULT_MEMORY_PER_SCOPE_LIMIT,
      max_hits: DEFAULT_MEMORY_MAX_HITS,
    },
  },
  {
    id: "wide",
    label: "广覆盖",
    description: "适合追踪长链路任务，会带回更多候选，但噪声也会更高。",
    values: {
      post_filter_mode: "none",
      rerank_mode: "heuristic",
      min_keyword_overlap: "1",
      scope_limit: "6",
      per_scope_limit: "4",
      max_hits: "8",
    },
  },
];

interface AgentDraft {
  scope: string;
  name: string;
  persona_summary: string;
  model_alias: string;
  tool_profile: string;
  memory_access_policy: MemoryAccessPolicyDraft;
  context_budget_policy: {
    memory_recall: MemoryRecallDraft;
  };
}

interface BlockingGuide {
  guide_id: string;
  section_id: string;
  section_label: string;
  risk: SetupRiskItem;
}

interface FieldGuide {
  title: string;
  description: string;
  example?: string;
  exampleLabel?: string;
  actions?: Array<{ label: string; value: string }>;
}

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

function parsePositiveInt(
  value: string,
  fallback: number,
  minimum: number,
  maximum: number
): number {
  const parsed = Number.parseInt(value.trim(), 10);
  if (Number.isNaN(parsed)) {
    return fallback;
  }
  return Math.max(minimum, Math.min(maximum, parsed));
}

function memoryAccessPolicyFromProfile(profile: AgentProfileItem | null): MemoryAccessPolicyDraft {
  const raw =
    profile?.memory_access_policy &&
    typeof profile.memory_access_policy === "object" &&
    !Array.isArray(profile.memory_access_policy)
      ? profile.memory_access_policy
      : {};
  return {
    allow_vault: Boolean(raw.allow_vault),
    include_history: Boolean(raw.include_history),
  };
}

function memoryRecallDraftFromProfile(profile: AgentProfileItem | null): MemoryRecallDraft {
  const budget =
    profile?.context_budget_policy &&
    typeof profile.context_budget_policy === "object" &&
    !Array.isArray(profile.context_budget_policy)
      ? profile.context_budget_policy
      : {};
  const raw =
    budget.memory_recall && typeof budget.memory_recall === "object" && !Array.isArray(budget.memory_recall)
      ? (budget.memory_recall as Record<string, unknown>)
      : {};
  return {
    post_filter_mode: String(raw.post_filter_mode ?? "keyword_overlap") || "keyword_overlap",
    rerank_mode: String(raw.rerank_mode ?? "heuristic") || "heuristic",
    min_keyword_overlap: String(raw.min_keyword_overlap ?? "1") || "1",
    scope_limit: String(raw.scope_limit ?? DEFAULT_MEMORY_SCOPE_LIMIT) || DEFAULT_MEMORY_SCOPE_LIMIT,
    per_scope_limit:
      String(raw.per_scope_limit ?? DEFAULT_MEMORY_PER_SCOPE_LIMIT) || DEFAULT_MEMORY_PER_SCOPE_LIMIT,
    max_hits: String(raw.max_hits ?? DEFAULT_MEMORY_MAX_HITS) || DEFAULT_MEMORY_MAX_HITS,
  };
}

function buildAgentDraft(profile: AgentProfileItem | null): AgentDraft {
  return {
    scope: profile?.scope ?? "project",
    name: profile?.name?.trim() ? profile.name : DEFAULT_AGENT_NAME,
    persona_summary:
      profile?.persona_summary?.trim() ? profile.persona_summary : DEFAULT_AGENT_PERSONA,
    model_alias: profile?.model_alias ?? "main",
    tool_profile: profile?.tool_profile ?? "standard",
    memory_access_policy: memoryAccessPolicyFromProfile(profile),
    context_budget_policy: {
      memory_recall: memoryRecallDraftFromProfile(profile),
    },
  };
}

function buildAgentDraftSyncKey(profile: AgentProfileItem | null): string {
  return JSON.stringify(profile ?? null);
}

function readActiveAgentProfile(source: unknown): AgentProfileItem | null {
  if (!source || typeof source !== "object" || Array.isArray(source)) {
    return null;
  }
  const payload = source as Record<string, unknown>;
  return {
    profile_id: String(payload.profile_id ?? ""),
    scope: String(payload.scope ?? "project"),
    project_id: String(payload.project_id ?? ""),
    name: String(payload.name ?? ""),
    persona_summary: String(payload.persona_summary ?? ""),
    model_alias: String(payload.model_alias ?? "main"),
    tool_profile: String(payload.tool_profile ?? "standard"),
    memory_access_policy:
      payload.memory_access_policy &&
      typeof payload.memory_access_policy === "object" &&
      !Array.isArray(payload.memory_access_policy)
        ? (payload.memory_access_policy as Record<string, unknown>)
        : {},
    context_budget_policy:
      payload.context_budget_policy &&
      typeof payload.context_budget_policy === "object" &&
      !Array.isArray(payload.context_budget_policy)
        ? (payload.context_budget_policy as Record<string, unknown>)
        : {},
    updated_at:
      typeof payload.updated_at === "string" || payload.updated_at === null
        ? payload.updated_at
        : null,
  };
}

function groupLabel(groupId: string): { title: string; description: string } {
  switch (groupId) {
    case "main-agent":
      return {
        title: "主 Agent",
        description: "主 Agent 的名称、模型别名和默认运行配置。",
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

function detectPrimaryProviderId(fieldState: FieldState): string {
  const providers = parseJsonFieldValue(fieldState.providers, []) as Array<Record<string, unknown>>;
  const firstEnabled = providers.find(
    (item) => typeof item?.id === "string" && item.enabled !== false
  );
  return typeof firstEnabled?.id === "string" ? firstEnabled.id : "openrouter";
}

function providerExampleJson(): string {
  return JSON.stringify(
    [
      {
        id: "openrouter",
        name: "OpenRouter",
        auth_type: "api_key",
        api_key_env: "OPENROUTER_API_KEY",
        enabled: true,
      },
    ],
    null,
    2
  );
}

function modelAliasesExampleJson(providerId: string): string {
  return JSON.stringify(
    {
      main: {
        provider: providerId,
        model: "openai/gpt-4.1-mini",
      },
      cheap: {
        provider: providerId,
        model: "openai/gpt-4.1-nano",
      },
    },
    null,
    2
  );
}

function buildAgentProfilePayload(agentDraft: AgentDraft): Record<string, unknown> {
  return {
    ...agentDraft,
    memory_access_policy: {
      allow_vault: agentDraft.memory_access_policy.allow_vault,
      include_history: agentDraft.memory_access_policy.include_history,
    },
    context_budget_policy: {
      memory_recall: {
        post_filter_mode:
          agentDraft.context_budget_policy.memory_recall.post_filter_mode || "keyword_overlap",
        rerank_mode:
          agentDraft.context_budget_policy.memory_recall.rerank_mode || "heuristic",
        min_keyword_overlap: parsePositiveInt(
          agentDraft.context_budget_policy.memory_recall.min_keyword_overlap,
          1,
          1,
          8
        ),
        scope_limit: parsePositiveInt(
          agentDraft.context_budget_policy.memory_recall.scope_limit,
          4,
          1,
          8
        ),
        per_scope_limit: parsePositiveInt(
          agentDraft.context_budget_policy.memory_recall.per_scope_limit,
          3,
          1,
          12
        ),
        max_hits: parsePositiveInt(
          agentDraft.context_budget_policy.memory_recall.max_hits,
          4,
          1,
          20
        ),
      },
    },
  };
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
  fieldState: FieldState,
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
    return {
      title: "这里填什么",
      description: usingEchoMode
        ? "如果你现在只是先体验本地 Web，这里可以先留空。准备接 OpenRouter / OpenAI 时，再填一个 provider。"
        : "这里填写模型提供方列表。通常先填一个 provider 就够了，例如 OpenRouter。",
      exampleLabel: "OpenRouter 示例",
      example: providerExampleJson(),
      actions: [
        { label: "填入 OpenRouter 示例", value: providerExampleJson() },
        { label: "清空", value: "[]" },
      ],
    };
  }
  if (hint.field_path === "model_aliases") {
    const providerId = detectPrimaryProviderId(fieldState);
    return {
      title: "这里填什么",
      description: usingEchoMode
        ? "体验模式下可以先不填。准备接真实模型时，至少补一个 main，建议再补一个 cheap。"
        : "这里把业务里的通用别名映射到真实模型。主 Agent 至少需要 main，便宜模型建议叫 cheap。",
      exampleLabel: `使用 ${providerId} 的别名示例`,
      example: modelAliasesExampleJson(providerId),
      actions: [
        { label: "填入 main / cheap 示例", value: modelAliasesExampleJson(providerId) },
        { label: "清空", value: "{}" },
      ],
    };
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
    return {
      title: "什么时候才需要改",
      description:
        "只有当你的 bridge API 路径或鉴权方式跟默认约定不一致时才需要调整。大多数情况下保留默认值即可。",
    };
  }
  return null;
}

function collectBlockingGuides(review: SetupReviewSummary): BlockingGuide[] {
  return [
    ...review.provider_runtime_risks.map((risk) => ({
      guide_id: `provider-${risk.risk_id}`,
      section_id: "main-agent",
      section_label: "主 Agent 与模型",
      risk,
    })),
    ...review.channel_exposure_risks.map((risk) => ({
      guide_id: `channel-${risk.risk_id}`,
      section_id: "channels",
      section_label: "渠道接入",
      risk,
    })),
    ...review.agent_autonomy_risks.map((risk) => ({
      guide_id: `agent-${risk.risk_id}`,
      section_id: "governance",
      section_label: "主 Agent",
      risk,
    })),
    ...review.tool_skill_readiness_risks.map((risk) => ({
      guide_id: `skill-${risk.risk_id}`,
      section_id: "governance",
      section_label: "主 Agent",
      risk,
    })),
    ...review.secret_binding_risks.map((risk) => ({
      guide_id: `secret-${risk.risk_id}`,
      section_id: "advanced",
      section_label: "更多设置",
      risk,
    })),
  ].filter((item) => item.risk.blocking);
}

function guideButtonLabel(guide: BlockingGuide): string {
  if (guide.section_id === "governance") {
    return "去填写主 Agent 信息";
  }
  return `去“${guide.section_label}”处理`;
}

export default function SettingsCenter() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const config = snapshot!.resources.config;
  const selector = snapshot!.resources.project_selector;
  const memory = snapshot!.resources.memory;
  const delegation = snapshot!.resources.delegation;
  const setup = snapshot!.resources.setup_governance;
  const policyProfiles = snapshot!.resources.policy_profiles;
  const skillGovernance = snapshot!.resources.skill_governance;
  const activeAgentProfile = readActiveAgentProfile(
    setup.agent_governance.details["active_agent_profile"]
  );
  const activeAgentProfileSyncKey = buildAgentDraftSyncKey(activeAgentProfile);
  const [fieldState, setFieldState] = useState<FieldState>(() =>
    buildFieldState(config.ui_hints, config.current_value)
  );
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [policyProfileId, setPolicyProfileId] = useState(policyProfiles.active_profile_id);
  const [agentDraft, setAgentDraft] = useState<AgentDraft>(() =>
    buildAgentDraft(activeAgentProfile)
  );
  const [review, setReview] = useState<SetupReviewSummary>(setup.review);

  useEffect(() => {
    setFieldState(buildFieldState(config.ui_hints, config.current_value));
    setFieldErrors({});
  }, [config.generated_at]);

  useEffect(() => {
    setPolicyProfileId(policyProfiles.active_profile_id);
  }, [policyProfiles.generated_at, policyProfiles.active_profile_id]);

  useEffect(() => {
    setAgentDraft(buildAgentDraft(activeAgentProfile));
  }, [activeAgentProfileSyncKey]);

  useEffect(() => {
    setReview(setup.review);
  }, [setup.generated_at]);

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
  const otherGroupIds = ["main-agent", "channels", "advanced"].filter(
    (groupId) => (groupedHints[groupId] ?? []).length > 0
  );

  function buildSetupDraft() {
    const result = buildConfigPayload(config.current_value, config.ui_hints, fieldState);
    setFieldErrors(result.errors);
    if (Object.keys(result.errors).length > 0) {
      return null;
    }
    return {
      config: result.config,
      policy_profile_id: policyProfileId,
      agent_profile: buildAgentProfilePayload({
        ...agentDraft,
        scope: agentDraft.scope || "project",
      }),
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

  const currentPolicy =
    policyProfiles.profiles.find((item) => item.profile_id === policyProfileId) ?? null;
  const blockedSkills = skillGovernance.items.filter((item) => item.blocking);
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
  const memoryMode =
    String(
      fieldState["memory.backend_mode"] ??
        getValueAtPath(config.current_value, "memory.backend_mode") ??
        "local_only"
    )
      .trim()
      .toLowerCase() || "local_only";
  const blockingGuides = collectBlockingGuides(review);

  function updateFieldValue(fieldPath: string, value: string | boolean) {
    setFieldState((state) => ({
      ...state,
      [fieldPath]: value,
    }));
  }

  function scrollToSection(sectionId: string) {
    document
      .getElementById(`settings-group-${sectionId}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function updateMemoryAccessPolicy(
    key: keyof MemoryAccessPolicyDraft,
    value: boolean
  ) {
    setAgentDraft((state) => ({
      ...state,
      memory_access_policy: {
        ...state.memory_access_policy,
        [key]: value,
      },
    }));
  }

  function updateMemoryRecallDraft(
    key: keyof MemoryRecallDraft,
    value: string
  ) {
    setAgentDraft((state) => ({
      ...state,
      context_budget_policy: {
        ...state.context_budget_policy,
        memory_recall: {
          ...state.context_budget_policy.memory_recall,
          [key]: value,
        },
      },
    }));
  }

  function applyMemoryRecallPreset(presetId: string) {
    const preset = MEMORY_RECALL_PRESETS.find((item) => item.id === presetId);
    if (!preset) {
      return;
    }
    setAgentDraft((state) => ({
      ...state,
      context_budget_policy: {
        ...state.context_budget_policy,
        memory_recall: { ...preset.values },
      },
    }));
  }

  function renderHintFields(hints: ConfigFieldHint[]) {
    return (
      <div className="wb-form-grid">
        {hints.map((hint) => {
          const currentValue = fieldState[hint.field_path] ?? "";
          const options = selectOptions(config.schema, hint);
          const error = fieldErrors[hint.field_path];
          const fieldGuide = buildFieldGuide(hint, fieldState, usingEchoMode);
          return (
            <label
              key={hint.field_path}
              className={`wb-field ${hint.multiline ? "wb-field-span-2" : ""}`}
            >
              <span>{hint.label}</span>
              {hint.help_text ? <small>{hint.help_text}</small> : null}
              {fieldGuide ? (
                <div className="wb-field-guide">
                  <strong>{fieldGuide.title}</strong>
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
                </div>
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
    <div className="wb-page">
      <section className="wb-hero">
        <div>
          <p className="wb-kicker">Settings</p>
          <h1>先检查配置，再保存生效</h1>
          <p>这里可以统一调整主 Agent、Memory、权限级别、技能状态和基础连接配置。</p>
        </div>
        <div className="wb-hero-actions">
          <button
            type="button"
            className="wb-button wb-button-secondary"
            onClick={() => void handleReview()}
            disabled={busyActionId === "setup.review" || busyActionId === "setup.apply"}
          >
            检查配置
          </button>
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={() => void handleApply()}
            disabled={busyActionId === "setup.review" || busyActionId === "setup.apply"}
          >
            保存配置
          </button>
        </div>
      </section>

      <div className="wb-card-grid wb-card-grid-4">
        <article className={`wb-card wb-card-accent is-${summaryTone(review)}`}>
          <p className="wb-card-label">配置状态</p>
          <strong>{review.ready ? "可以保存" : "需要处理"}</strong>
          <span>阻塞项 {review.blocking_reasons.length}</span>
          <span>提醒 {review.warnings.length}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">当前 Project</p>
          <strong>{selector.current_project_id}</strong>
          <span>工作区 {selector.current_workspace_id}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">安全等级</p>
          <strong>{currentPolicy?.label ?? policyProfiles.active_profile_id}</strong>
          <span>{currentPolicy?.approval_policy ?? "未选择"}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">技能状态</p>
          <strong>{skillGovernance.items.length}</strong>
          <span>阻塞 {blockedSkills.length}</span>
          <span>不可用 {unavailableSkills.length}</span>
        </article>
      </div>

      <div className="wb-split">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">保存前检查</p>
              <h3>先确认是否可用，再决定是否保存</h3>
            </div>
          </div>
          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>下一步</strong>
              <div className="wb-note-stack">
                {review.next_actions.map((item) => (
                  <span key={item}>{item}</span>
                ))}
              </div>
            </div>
            <div className="wb-note">
              <strong>当前模式</strong>
              <span>
                {usingEchoMode
                  ? "你现在处于体验模式，可以先跑通 Web 和任务流，真实模型稍后再接。"
                  : "你正在准备接入真实模型，请优先完成 Provider 和模型别名配置。"}
              </span>
            </div>
            {blockingGuides.length > 0 ? (
              <div className="wb-note">
                <strong>需要先处理的问题</strong>
                <div className="wb-note-stack">
                  {blockingGuides.map((guide) => (
                    <div key={guide.guide_id} className="wb-guide-card">
                      <strong>{guide.risk.title}</strong>
                      <span>{guide.risk.summary}</span>
                      {guide.risk.recommended_action ? <small>{guide.risk.recommended_action}</small> : null}
                      <button
                        type="button"
                        aria-label={guideButtonLabel(guide)}
                        className="wb-button wb-button-tertiary wb-button-inline"
                        onClick={() => scrollToSection(guide.section_id)}
                      >
                        {guideButtonLabel(guide)}
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            {renderRiskList("模型与运行连接", review.provider_runtime_risks)}
            {renderRiskList("渠道暴露范围", review.channel_exposure_risks)}
            {renderRiskList("Agent 自主性", review.agent_autonomy_risks)}
            {renderRiskList("工具与技能", review.tool_skill_readiness_risks)}
            {renderRiskList("密钥绑定", review.secret_binding_risks)}
          </div>
        </section>

        <section id="settings-group-governance" className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">主 Agent</p>
              <h3>名称、Persona 和默认能力</h3>
            </div>
          </div>

          <div className="wb-form-grid">
            <label className="wb-field">
              <span>主 Agent 名称</span>
              <input
                type="text"
                value={agentDraft.name}
                onChange={(event) =>
                  setAgentDraft((state) => ({ ...state, name: event.target.value }))
                }
              />
            </label>

            <label className="wb-field">
              <span>默认模型别名</span>
              <input
                type="text"
                value={agentDraft.model_alias}
                onChange={(event) =>
                  setAgentDraft((state) => ({ ...state, model_alias: event.target.value }))
                }
              />
            </label>

            <label className="wb-field">
              <span>工具权限模板</span>
              <select
                value={agentDraft.tool_profile}
                onChange={(event) =>
                  setAgentDraft((state) => ({ ...state, tool_profile: event.target.value }))
                }
              >
                <option value="minimal">minimal</option>
                <option value="standard">standard</option>
                <option value="privileged">privileged</option>
              </select>
            </label>

            <label className="wb-field">
              <span>安全等级</span>
              <select
                value={policyProfileId}
                onChange={(event) => setPolicyProfileId(event.target.value)}
              >
                {policyProfiles.profiles.map((profile) => (
                  <option key={profile.profile_id} value={profile.profile_id}>
                    {profile.label} · {profile.approval_policy}
                  </option>
                ))}
              </select>
            </label>

            <label className="wb-field wb-field-span-2">
              <span>Persona（角色说明）</span>
              <small>这就是主 Agent 的 Persona，会影响它默认的语气、侧重点和处理方式。</small>
              <textarea
                rows={4}
                value={agentDraft.persona_summary}
                placeholder="例如：通用个人助手，优先帮助我处理当前任务，并在需要时提醒风险。"
                onChange={(event) =>
                  setAgentDraft((state) => ({
                    ...state,
                    persona_summary: event.target.value,
                  }))
                }
              />
            </label>
          </div>

          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>{setup.agent_governance.label}</strong>
              <span>{setup.agent_governance.summary}</span>
            </div>
            <div className="wb-note">
              <strong>{setup.tools_skills.label}</strong>
              <span>{setup.tools_skills.summary}</span>
            </div>
            {skillGovernance.items.slice(0, 4).map((item) => (
              <div key={item.item_id} className="wb-note">
                <strong>
                  {item.label} · {item.availability}
                </strong>
                <span>
                  {item.missing_requirements.length > 0
                    ? item.missing_requirements.join("；")
                    : "当前 capability pack 可用"}
                </span>
              </div>
            ))}
          </div>
        </section>
      </div>

      <div className="wb-card-grid wb-card-grid-3">
        <article className="wb-card">
          <p className="wb-card-label">记忆状态</p>
          <strong>{memory.status}</strong>
          <span>当前结论 {memory.summary.sor_current_count}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">工作状态</p>
          <strong>{delegation.works.length}</strong>
          <span>当前项目可见工作数</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">连接状态</p>
          <strong>{setup.provider_runtime.status}</strong>
          <span>{setup.channel_access.status}</span>
        </article>
      </div>

      <section id="settings-group-memory" className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">Memory</p>
            <h3>先决定系统记什么、怎么找，再决定要不要接远端 bridge</h3>
          </div>
        </div>

        <div className="wb-card-grid wb-card-grid-4">
          <article className="wb-card">
            <p className="wb-card-label">当前模式</p>
            <strong>{memoryMode === "memu" ? "MemU bridge" : "本地记忆"}</strong>
            <span>
              {memoryMode === "memu"
                ? "适合需要跨会话检索和高级回放"
                : "先把基础记忆链路跑通"}
            </span>
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
          <div className="wb-inline-banner is-error">
            <strong>Memory 当前有提醒</strong>
            <span>{memory.warnings.join("；")}</span>
          </div>
        ) : (
          <div className="wb-inline-banner is-muted">
            <strong>推荐做法</strong>
            <span>
              首次体验先保持本地记忆 + 保守召回；只有在你明确需要远端检索后端时，再切到
              MemU bridge。
            </span>
          </div>
        )}

        <div className="wb-note-stack">
          <div className="wb-note">
            <strong>记忆权限</strong>
            <span>
              这里决定主 Agent 默认能不能读取历史版本、能不能把 Vault 引用一起带回上下文。
            </span>
          </div>
          <div className="wb-note">
            <strong>检索策略</strong>
            <span>
              后过滤与重排会影响 recall 的精度和保守程度；建议先用默认值，只有明确发现命中过多或过少时再调。
            </span>
          </div>
          <div className="wb-note">
            <strong>快速预设</strong>
            <span>如果你现在还不确定这些参数，先选一个预设，再按需要微调会更省事。</span>
            <div className="wb-inline-actions wb-inline-actions-wrap">
              {MEMORY_RECALL_PRESETS.map((preset) => (
                <button
                  key={preset.id}
                  type="button"
                  className="wb-button wb-button-tertiary wb-button-inline"
                  onClick={() => applyMemoryRecallPreset(preset.id)}
                >
                  {preset.label}
                </button>
              ))}
            </div>
            <div className="wb-note-stack">
              {MEMORY_RECALL_PRESETS.map((preset) => (
                <small key={`${preset.id}-copy`}>
                  {preset.label}：{preset.description}
                </small>
              ))}
            </div>
          </div>
        </div>

        <div className="wb-form-grid">
          <label className="wb-field">
            <span>允许带回 Vault 引用</span>
            <small>打开后，主 Agent 在 recall 时可以把受控 Vault 引用也纳入候选。</small>
            <input
              type="checkbox"
              checked={agentDraft.memory_access_policy.allow_vault}
              onChange={(event) =>
                updateMemoryAccessPolicy("allow_vault", event.target.checked)
              }
            />
          </label>

          <label className="wb-field">
            <span>默认包含历史版本</span>
            <small>打开后，会把旧版本 SoR 一起纳入 recall，适合需要看演变过程的场景。</small>
            <input
              type="checkbox"
              checked={agentDraft.memory_access_policy.include_history}
              onChange={(event) =>
                updateMemoryAccessPolicy("include_history", event.target.checked)
              }
            />
          </label>

          <label className="wb-field">
            <span>后过滤策略</span>
            <small>默认用关键词重叠做一道保守过滤；命中太少时再改成 none。</small>
            <select
              value={agentDraft.context_budget_policy.memory_recall.post_filter_mode}
              onChange={(event) =>
                updateMemoryRecallDraft("post_filter_mode", event.target.value)
              }
            >
              <option value="keyword_overlap">keyword_overlap · 保守过滤</option>
              <option value="none">none · 不额外过滤</option>
            </select>
          </label>

          <label className="wb-field">
            <span>重排策略</span>
            <small>默认启用 heuristic；如果你只想保留原始搜索顺序，可以改成 none。</small>
            <select
              value={agentDraft.context_budget_policy.memory_recall.rerank_mode}
              onChange={(event) =>
                updateMemoryRecallDraft("rerank_mode", event.target.value)
              }
            >
              <option value="heuristic">heuristic · 优先更贴近当前主题</option>
              <option value="none">none · 保留原始顺序</option>
            </select>
          </label>

          <label className="wb-field">
            <span>最低关键词重叠</span>
            <small>数字越大越严格。默认 1；只有误命中明显偏多时再调高。</small>
            <input
              type="text"
              value={agentDraft.context_budget_policy.memory_recall.min_keyword_overlap}
              onChange={(event) =>
                updateMemoryRecallDraft("min_keyword_overlap", event.target.value)
              }
            />
          </label>

          <label className="wb-field">
            <span>最多查几个 Scope</span>
            <small>默认 4。范围越大越全，但也更容易把上下文拉散。</small>
            <input
              type="text"
              value={agentDraft.context_budget_policy.memory_recall.scope_limit}
              onChange={(event) => updateMemoryRecallDraft("scope_limit", event.target.value)}
            />
          </label>

          <label className="wb-field">
            <span>每个 Scope 最多带回几条</span>
            <small>默认 3。太大容易噪声多，太小可能漏掉关键上下文。</small>
            <input
              type="text"
              value={agentDraft.context_budget_policy.memory_recall.per_scope_limit}
              onChange={(event) =>
                updateMemoryRecallDraft("per_scope_limit", event.target.value)
              }
            />
          </label>

          <label className="wb-field">
            <span>总命中上限</span>
            <small>默认 4。这个值越大，主上下文越容易变重。</small>
            <input
              type="text"
              value={agentDraft.context_budget_policy.memory_recall.max_hits}
              onChange={(event) => updateMemoryRecallDraft("max_hits", event.target.value)}
            />
          </label>
        </div>

        {memoryBasicHints.length > 0 ? (
          <>
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">连接配置</p>
                <h3>先选本地模式还是远端 MemU bridge</h3>
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
                <h3>只有 bridge API 约定不一致时才需要改这些路径</h3>
              </div>
            </div>
            {renderHintFields(memoryAdvancedHints)}
          </>
        ) : null}
      </section>

      {otherGroupIds.map((groupId) => {
        const hints = groupedHints[groupId] ?? [];
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
    </div>
  );
}
