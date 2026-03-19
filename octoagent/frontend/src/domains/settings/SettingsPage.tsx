import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useWorkbench } from "../../components/shell/WorkbenchLayout";
import { categoryForHint, getValueAtPath } from "../../workbench/utils";
import type { ConfigFieldHint, SetupReviewSummary } from "../../types";
import SettingsHintFields from "./SettingsHintFields";
import SettingsOverview from "./SettingsOverview";
import SettingsProviderSection from "./SettingsProviderSection";
import SettingsResourceLimitsSection from "./SettingsResourceLimitsSection";
import {
  CUSTOM_PROVIDER_FIELD_PATHS,
  buildConfigPayload,
  buildDefaultAliasDrafts,
  buildFieldState,
  buildProviderPreset,
  envPresence,
  generateSecretValue,
  groupLabel,
  normalizeAliasDrafts,
  parseAliasDrafts,
  parseProviderDrafts,
  readProviderRuntimeDetails,
  renderRiskList,
  stringifyAliasDrafts,
  stringifyProviderDrafts,
  type FieldErrors,
  type FieldState,
  type ModelAliasDraftItem,
  type ProviderDraftItem,
} from "./shared";

const DEFAULT_GATEWAY_PROXY_URL = "http://localhost:4000";
const DEFAULT_GATEWAY_MASTER_KEY_ENV = "LITELLM_MASTER_KEY";

export default function SettingsPage() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const location = useLocation();
  const config = snapshot!.resources.config;
  const selector = snapshot!.resources.project_selector;
  const memory = snapshot!.resources.memory;
  const retrievalPlatform = snapshot!.resources.retrieval_platform ?? null;
  const setup = snapshot!.resources.setup_governance;
  const [fieldState, setFieldState] = useState<FieldState>(() =>
    buildFieldState(config.ui_hints, config.current_value)
  );
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [review, setReview] = useState<SetupReviewSummary>(setup.review);
  const [secretValues, setSecretValues] = useState<Record<string, string>>({});

  useEffect(() => {
    setFieldState(buildFieldState(config.ui_hints, config.current_value));
    setFieldErrors({});
  }, [config.generated_at]);

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
  const otherGroupIds = ["channels", "advanced"].filter(
    (groupId) => (groupedHints[groupId] ?? []).length > 0
  );
  // memoryCorpus / retrievalPlatform 的 generation 相关变量暂不使用，
  // 待 Retrieval Platform 迁移管理 UI 合入后恢复
  void retrievalPlatform;
  const providerRuntimeDetails = readProviderRuntimeDetails(setup.provider_runtime.details);
  const providerDrafts = parseProviderDrafts(fieldState.providers);
  const aliasDrafts = normalizeAliasDrafts(parseAliasDrafts(fieldState.model_aliases));
  const activeProviders = providerDrafts.filter((item) => item.enabled);
  const defaultProvider =
    activeProviders[0] ?? providerDrafts[0] ?? buildProviderPreset("openrouter");
  const providerSelectOptions = providerDrafts
    .map((item) => ({
      value: item.id,
      label: item.name?.trim() ? `${item.name} · ${item.id}` : item.id,
    }))
    .filter((item) => item.value.trim());
  const savedEnvNames = envPresence(providerRuntimeDetails);
  const gatewayProxyUrl =
    String(
      fieldState["runtime.litellm_proxy_url"] ??
        getValueAtPath(config.current_value, "runtime.litellm_proxy_url") ??
        DEFAULT_GATEWAY_PROXY_URL
    );
  const normalizedGatewayProxyUrl = gatewayProxyUrl.trim() || DEFAULT_GATEWAY_PROXY_URL;
  const gatewayMasterKeyEnvInput =
    String(
      fieldState["runtime.master_key_env"] ??
        getValueAtPath(config.current_value, "runtime.master_key_env") ??
        DEFAULT_GATEWAY_MASTER_KEY_ENV
    );
  const gatewayMasterKeyEnv =
    gatewayMasterKeyEnvInput.trim() || DEFAULT_GATEWAY_MASTER_KEY_ENV;

  function buildManagedProviderDraft(secretStateOverride?: Record<string, string>) {
    const nextSecretValues = {
      ...secretValues,
      ...(secretStateOverride ?? {}),
    };
    if (
      activeProviders.length > 0 &&
      !savedEnvNames.has(gatewayMasterKeyEnv) &&
      !nextSecretValues[gatewayMasterKeyEnv]?.trim()
    ) {
      nextSecretValues[gatewayMasterKeyEnv] = generateSecretValue();
    }
    return {
      fieldState: {
        ...fieldState,
        "runtime.llm_mode": activeProviders.length > 0 ? "litellm" : "echo",
        "runtime.litellm_proxy_url": normalizedGatewayProxyUrl,
        "runtime.master_key_env": gatewayMasterKeyEnv,
        model_aliases: stringifyAliasDrafts(aliasDrafts),
      },
      secretValues: nextSecretValues,
    };
  }

  function buildSetupDraft(secretStateOverride?: Record<string, string>) {
    const managedDraft = buildManagedProviderDraft(secretStateOverride);
    const result = buildConfigPayload(
      config.current_value,
      config.ui_hints,
      managedDraft.fieldState
    );
    setFieldErrors(result.errors);
    if (Object.keys(result.errors).length > 0) {
      return null;
    }
    if (
      managedDraft.secretValues[gatewayMasterKeyEnv]?.trim() &&
      secretValues[gatewayMasterKeyEnv] !== managedDraft.secretValues[gatewayMasterKeyEnv]
    ) {
      setSecretValues((state) => ({
        ...state,
        [gatewayMasterKeyEnv]: managedDraft.secretValues[gatewayMasterKeyEnv]!,
      }));
    }
    return {
      config: result.config,
      secret_values: Object.fromEntries(
        Object.entries(managedDraft.secretValues).filter(([, value]) => value.trim())
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
    const oauthProvider =
      providerDrafts.find((item) => item.id === "openai-codex") ??
      buildProviderPreset("openai-codex");
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

  async function handleQuickConnect() {
    const draft = buildSetupDraft();
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

  // Retrieval Platform 迁移管理 handler 暂不使用，待 UI 合入后恢复

  const usingEchoMode = activeProviders.length === 0;
  const connectBusy =
    busyActionId === "setup.review" ||
    busyActionId === "setup.apply" ||
    busyActionId === "setup.quick_connect" ||
    busyActionId === "provider.oauth.openai_codex";
  const reviewNextActions = review.next_actions.slice(0, 3);

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

  function updateProviders(nextProviders: ProviderDraftItem[]) {
    updateFieldValue("providers", stringifyProviderDrafts(nextProviders));
  }

  function updateAliases(nextAliases: ModelAliasDraftItem[]) {
    updateFieldValue("model_aliases", stringifyAliasDrafts(normalizeAliasDrafts(nextAliases)));
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
    const fallbackProviderId =
      nextProviders.find((item) => item.enabled)?.id ?? nextProviders[0]?.id ?? "";
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

  function scrollToSection(sectionId: string) {
    document
      .getElementById(`settings-group-${sectionId}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <div className="wb-page wb-settings-page">
      <SettingsOverview
        usingEchoMode={usingEchoMode}
        review={review}
        selector={selector}
        onQuickConnect={() => void handleQuickConnect()}
        onReview={() => void handleReview()}
        onApply={() => void handleApply()}
        connectBusy={connectBusy}
        onScrollToSection={scrollToSection}
      />

      <SettingsProviderSection
        providerDrafts={providerDrafts}
        aliasDrafts={aliasDrafts}
        defaultProvider={defaultProvider}
        providerRuntimeDetails={providerRuntimeDetails}
        providerSelectOptions={providerSelectOptions}
        secretValues={secretValues}
        savedEnvNames={savedEnvNames}
        connectBusy={connectBusy}
        onSecretValueChange={updateSecretValue}
        onAddProviderDraft={addProviderDraft}
        onUpdateProviderAt={updateProviderAt}
        onMoveProviderToFront={moveProviderToFront}
        onRemoveProviderAt={removeProviderAt}
        onRestoreRecommendedAliases={restoreRecommendedAliases}
        onAddAliasDraft={addAliasDraft}
        onUpdateAliasAt={updateAliasAt}
        onRemoveAliasDraft={removeAliasDraft}
        onOpenAIOAuthConnect={async () => {
          await handleOpenAIOAuthConnect();
        }}
      />

      <SettingsResourceLimitsSection
        agentProfiles={snapshot!.resources.agent_profiles ?? null}
        workerProfiles={snapshot!.resources.worker_profiles ?? null}
        onSubmit={async (targetType, profileId, limits) => {
          await submitAction("agent_profile.update_resource_limits", {
            target_type: targetType,
            profile_id: profileId,
            resource_limits: limits,
          });
        }}
        busy={busyActionId === "agent_profile.update_resource_limits"}
      />

      <section id="settings-group-memory" className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <h3 style={{ fontSize: "1.1rem", margin: 0 }}>Memory</h3>
          </div>
        </div>

        <div className="wb-card-grid wb-card-grid-4">
          <article className="wb-card">
            <p className="wb-card-label">引擎模式</p>
            <strong>内建记忆引擎</strong>
            <span>SQLite / Vault</span>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">当前状态</p>
            <strong>{memory.backend_state || memory.status}</strong>
            <span>{memory.backend_id || "未标记"}</span>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">语义检索</p>
            <strong>
              {
                String(
                  fieldState["memory.embedding_model_alias"] ??
                    getValueAtPath(config.current_value, "memory.embedding_model_alias") ??
                    ""
                ).trim() || "内建 embedding（默认）"
              }
            </strong>
            <span>换模型时会后台重建索引</span>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">当前结论</p>
            <strong>{memory.summary?.sor_current_count ?? "—"}</strong>
            <span>片段 {memory.summary?.fragment_count ?? "—"}</span>
          </article>
        </div>

        <h4 style={{ fontSize: "0.85rem", fontWeight: 600, margin: "1rem 0 0.5rem", color: "var(--cp-muted)" }}>记忆模型配置</h4>
        <div className="wb-toolbar-grid">
          {(
            [
              { key: "memory.reasoning_model_alias", label: "记忆加工", fallback: "main（默认）" },
              { key: "memory.expand_model_alias", label: "查询扩写", fallback: "main（默认）" },
              { key: "memory.embedding_model_alias", label: "语义检索", fallback: "内建 embedding" },
              { key: "memory.rerank_model_alias", label: "结果重排", fallback: "heuristic（默认）" },
            ] as const
          ).map((slot) => {
            const currentValue = String(
              fieldState[slot.key] ??
                getValueAtPath(config.current_value, slot.key) ??
                ""
            ).trim();
            // 提取默认值名称（如 "main（默认）" → "main"），用于去重
            const fallbackBase = slot.fallback.replace(/[（(].*/u, "").trim();
            const aliasKeys = aliasDrafts
              .map((item) => item.alias.trim())
              .filter((a) => a && a !== fallbackBase);
            return (
              <label key={slot.key} className="wb-field">
                <span>{slot.label}</span>
                <select
                  value={currentValue}
                  onChange={(e) => updateFieldValue(slot.key, e.target.value)}
                >
                  <option value="">{slot.fallback}</option>
                  {aliasKeys.map((alias) => (
                    <option key={alias} value={alias}>
                      {alias}
                    </option>
                  ))}
                </select>
              </label>
            );
          })}
        </div>

        {(memory.warnings ?? []).length > 0 ? (
          <div className="wb-inline-banner is-error" role="alert">
            <strong>Memory 当前有提醒</strong>
            <span>{(memory.warnings ?? []).join("；")}</span>
          </div>
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
                <h3 style={{ fontSize: "1.1rem", margin: 0 }}>{group.title}</h3>
              </div>
            </div>
            <SettingsHintFields
              hints={hints}
              schema={config.schema}
              fieldState={fieldState}
              fieldErrors={fieldErrors}
              usingEchoMode={usingEchoMode}
              onFieldValueChange={updateFieldValue}
            />
          </section>
        );
      })}

      <section id="settings-group-review" className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <h3 style={{ fontSize: "1.1rem", margin: 0 }}>保存检查</h3>
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
            <div className="wb-inline-actions wb-inline-actions-wrap">
              <button
                type="button"
                className="wb-button wb-button-primary"
                onClick={() => void handleQuickConnect()}
                disabled={connectBusy}
              >
                {usingEchoMode ? "连接真实模型" : "保存并刷新连接"}
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
