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

interface AgentDraft {
  scope: string;
  name: string;
  persona_summary: string;
  model_alias: string;
  tool_profile: string;
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

function buildAgentDraft(profile: AgentProfileItem | null): AgentDraft {
  return {
    scope: profile?.scope ?? "project",
    name: profile?.name?.trim() ? profile.name : DEFAULT_AGENT_NAME,
    persona_summary:
      profile?.persona_summary?.trim() ? profile.persona_summary : DEFAULT_AGENT_PERSONA,
    model_alias: profile?.model_alias ?? "main",
    tool_profile: profile?.tool_profile ?? "standard",
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

function buildFieldGuide(
  hint: ConfigFieldHint,
  fieldState: FieldState,
  usingEchoMode: boolean
): FieldGuide | null {
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

  function buildSetupDraft() {
    const result = buildConfigPayload(config.current_value, config.ui_hints, fieldState);
    setFieldErrors(result.errors);
    if (Object.keys(result.errors).length > 0) {
      return null;
    }
    return {
      config: result.config,
      policy_profile_id: policyProfileId,
      agent_profile: {
        ...agentDraft,
        scope: agentDraft.scope || "project",
      },
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

  return (
    <div className="wb-page">
      <section className="wb-hero">
        <div>
          <p className="wb-kicker">Settings</p>
          <h1>先检查配置，再保存生效</h1>
          <p>这里可以统一调整主 Agent、权限级别、技能状态和基础连接配置。</p>
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
          <span>当前记录 {memory.summary.sor_current_count}</span>
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

      {Object.entries(groupedHints).map(([groupId, hints]) => {
        const group = groupLabel(groupId);
        return (
          <section key={groupId} id={`settings-group-${groupId}`} className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">{group.title}</p>
                <h3>{group.description}</h3>
              </div>
            </div>
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
                        onChange={(event) =>
                          updateFieldValue(hint.field_path, event.target.checked)
                        }
                      />
                    ) : hint.widget === "select" && options.length > 0 ? (
                      <select
                        value={String(currentValue)}
                        onChange={(event) =>
                          updateFieldValue(hint.field_path, event.target.value)
                        }
                      >
                        {options.map((option) => (
                          <option key={option} value={option}>
                            {option}
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
                        onChange={(event) =>
                          updateFieldValue(hint.field_path, event.target.value)
                        }
                      />
                    ) : (
                      <input
                        type="text"
                        value={String(currentValue)}
                        placeholder={hint.placeholder}
                        onChange={(event) =>
                          updateFieldValue(hint.field_path, event.target.value)
                        }
                      />
                    )}
                    {hint.description ? <small>{hint.description}</small> : null}
                    {error ? <small className="wb-field-error">{error}</small> : null}
                  </label>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}
