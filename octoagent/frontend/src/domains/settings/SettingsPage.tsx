import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useWorkbench } from "../../components/shell/WorkbenchLayout";
import { categoryForHint, getValueAtPath } from "../../workbench/utils";
import type {
  ConfigFieldHint,
  SetupReviewSummary,
} from "../../types";
import SettingsHintFields from "./SettingsHintFields";
import SettingsOverview from "./SettingsOverview";
import SettingsProviderSection from "./SettingsProviderSection";
import {
  CUSTOM_PROVIDER_FIELD_PATHS,
  buildConfigPayload,
  buildDefaultAliasDrafts,
  buildFieldState,
  buildProviderPreset,
  envPresence,
  generateSecretValue,
  groupLabel,
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

export default function SettingsPage() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const location = useLocation();
  const config = snapshot!.resources.config;
  const selector = snapshot!.resources.project_selector;
  const memory = snapshot!.resources.memory;
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
  const defaultProvider =
    activeProviders[0] ?? providerDrafts[0] ?? buildProviderPreset("openrouter");
  const providerSelectOptions = providerDrafts
    .map((item) => ({
      value: item.id,
      label: item.name?.trim() ? `${item.name} · ${item.id}` : item.id,
    }))
    .filter((item) => item.value.trim());
  const savedEnvNames = envPresence(providerRuntimeDetails);
  const masterKeyHint = config.ui_hints["runtime.master_key_env"];
  const proxyUrlHint = config.ui_hints["runtime.litellm_proxy_url"];

  function buildSetupDraft(secretStateOverride?: Record<string, string>) {
    const result = buildConfigPayload(config.current_value, config.ui_hints, fieldState);
    setFieldErrors(result.errors);
    if (Object.keys(result.errors).length > 0) {
      return null;
    }
    return {
      config: result.config,
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
        setup={setup}
        providerDraftCount={providerDrafts.length}
        activeProvidersCount={activeProviders.length}
        aliasDraftCount={aliasDrafts.length}
        defaultProviderId={defaultProvider.id}
        memoryLabel={memoryMode === "memu" ? "MemU bridge" : "本地记忆"}
        memoryStatus={memory.backend_state || memory.status}
        onQuickConnect={() => void handleQuickConnect()}
        onReview={() => void handleReview()}
        onApply={() => void handleApply()}
        connectBusy={connectBusy}
        onScrollToSection={scrollToSection}
      />

      <SettingsProviderSection
        usingEchoMode={usingEchoMode}
        fieldState={fieldState}
        providerDrafts={providerDrafts}
        aliasDrafts={aliasDrafts}
        defaultProvider={defaultProvider}
        providerRuntimeDetails={providerRuntimeDetails}
        providerSelectOptions={providerSelectOptions}
        proxyUrlHint={proxyUrlHint}
        masterKeyHint={masterKeyHint}
        secretValues={secretValues}
        savedEnvNames={savedEnvNames}
        connectBusy={connectBusy}
        onFieldValueChange={updateFieldValue}
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

      <div className="wb-inline-banner is-muted">
        <strong>Agent 能力管理已移到 Agents</strong>
        <span>
          Skill / MCP Provider 的安装、当前项目默认启用范围，以及 Butler / Worker 的绑定，
          现在统一放在 Agents &gt; Providers。
        </span>
        <Link className="wb-button wb-button-tertiary wb-button-inline" to="/agents?view=providers">
          打开 Agents &gt; Providers
        </Link>
      </div>

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
            <SettingsHintFields
              hints={memoryBasicHints}
              schema={config.schema}
              fieldState={fieldState}
              fieldErrors={fieldErrors}
              usingEchoMode={usingEchoMode}
              onFieldValueChange={updateFieldValue}
            />
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
            <SettingsHintFields
              hints={memoryAdvancedHints}
              schema={config.schema}
              fieldState={fieldState}
              fieldErrors={fieldErrors}
              usingEchoMode={usingEchoMode}
              onFieldValueChange={updateFieldValue}
            />
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
