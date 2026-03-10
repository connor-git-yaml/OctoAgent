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

interface AgentDraft {
  scope: string;
  name: string;
  persona_summary: string;
  model_alias: string;
  tool_profile: string;
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
    name: profile?.name ?? "",
    persona_summary: profile?.persona_summary ?? "",
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
            审查 Setup
          </button>
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={() => void handleApply()}
            disabled={busyActionId === "setup.review" || busyActionId === "setup.apply"}
          >
            应用 Setup
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
            {review.blocking_reasons.length > 0 ? (
              <div className="wb-note">
                <strong>阻塞项</strong>
                <div className="wb-note-stack">
                  {review.blocking_reasons.map((item) => (
                    <span key={item}>{item}</span>
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

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">主 Agent</p>
              <h3>名称、权限级别和默认能力</h3>
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
              <span>角色说明</span>
              <textarea
                rows={4}
                value={agentDraft.persona_summary}
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
          <section key={groupId} className="wb-panel">
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
                return (
                  <label
                    key={hint.field_path}
                    className={`wb-field ${hint.multiline ? "wb-field-span-2" : ""}`}
                  >
                    <span>{hint.label}</span>
                    {hint.help_text ? <small>{hint.help_text}</small> : null}
                    {hint.widget === "toggle" ? (
                      <input
                        type="checkbox"
                        checked={Boolean(currentValue)}
                        onChange={(event) =>
                          setFieldState((state) => ({
                            ...state,
                            [hint.field_path]: event.target.checked,
                          }))
                        }
                      />
                    ) : hint.widget === "select" && options.length > 0 ? (
                      <select
                        value={String(currentValue)}
                        onChange={(event) =>
                          setFieldState((state) => ({
                            ...state,
                            [hint.field_path]: event.target.value,
                          }))
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
                          setFieldState((state) => ({
                            ...state,
                            [hint.field_path]: event.target.value,
                          }))
                        }
                      />
                    ) : (
                      <input
                        type="text"
                        value={String(currentValue)}
                        placeholder={hint.placeholder}
                        onChange={(event) =>
                          setFieldState((state) => ({
                            ...state,
                            [hint.field_path]: event.target.value,
                          }))
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
