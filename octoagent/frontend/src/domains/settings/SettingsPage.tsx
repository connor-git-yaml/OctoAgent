import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { useWorkbench } from "../../components/shell/WorkbenchLayout";
import { categoryForHint, getValueAtPath } from "../../workbench/utils";
import type { ConfigFieldHint, SetupReviewSummary } from "../../types";
import PendingChangesBar from "./PendingChangesBar";
import SettingsErrorModal, {
  type SettingsErrorItem,
  type SettingsErrorKind,
} from "./SettingsErrorModal";
import SettingsHintFields from "./SettingsHintFields";
import SettingsOverview from "./SettingsOverview";
import SettingsProviderSection from "./SettingsProviderSection";
import { translateWarning } from "../memory/shared";
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

const EMPTY_REVIEW: SetupReviewSummary = {
  ready: false,
  risk_level: "unknown",
  warnings: [],
  blocking_reasons: [],
  next_actions: [],
  provider_runtime_risks: [],
  channel_exposure_risks: [],
  agent_autonomy_risks: [],
  tool_skill_readiness_risks: [],
  secret_binding_risks: [],
};

export default function SettingsPage() {
  const { snapshot, submitAction, busyActionId, error: workbenchError } =
    useWorkbench();
  const location = useLocation();
  const config = snapshot!.resources.config;
  const selector = snapshot!.resources.project_selector;
  const memory = snapshot!.resources.memory;
  const retrievalPlatform = snapshot!.resources.retrieval_platform ?? null;
  const setup = snapshot!.resources.setup_governance;
  const [fieldState, setFieldState] = useState<FieldState>(() =>
    buildFieldState(config?.ui_hints ?? {}, config?.current_value ?? {})
  );
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [review, setReview] = useState<SetupReviewSummary>(setup?.review ?? EMPTY_REVIEW);
  const [secretValues, setSecretValues] = useState<Record<string, string>>({});
  const [savedSecretEnvNames, setSavedSecretEnvNames] = useState<string[]>([]);
  const [pendingRuntimeRefresh, setPendingRuntimeRefresh] = useState(false);

  // Feature 079 Phase 1：统一错误展示。buildSetupDraft 返回 null 时或后端
  // submitAction 返回 null 时，都通过这个 modal 告诉用户具体原因。
  // 不再依赖"偶尔被 ErrorBoundary 吞掉"的 inline banner 作为唯一提示。
  const [errorModal, setErrorModal] = useState<{
    open: boolean;
    kind: SettingsErrorKind;
    items: SettingsErrorItem[];
  }>({ open: false, kind: "field", items: [] });
  // 追踪最近一次保存动作的生命周期，用来判断 workbench.error 是哪个 action 触发的
  const pendingSaveActionRef = useRef<string | null>(null);

  useEffect(() => {
    setFieldState(buildFieldState(config?.ui_hints ?? {}, config?.current_value ?? {}));
    setFieldErrors({});
  }, [config?.generated_at]);

  useEffect(() => {
    setReview(setup?.review ?? EMPTY_REVIEW);
  }, [setup?.generated_at]);

  useEffect(() => {
    setSecretValues({});
  }, [setup.generated_at, config.generated_at]);

  // Feature 079 Phase 1：保存类 action 失败时自动弹错误 modal。
  // workbench.error 是 useWorkbenchData 在 action 抛异常时 setError 的结果；
  // 用 pendingSaveActionRef 限定只有刚发过 setup.* 的 action 才会触发 modal，
  // 避免展示其它 action（比如 agent_profile.update_resource_limits）的错误。
  useEffect(() => {
    if (!workbenchError) {
      return;
    }
    if (!pendingSaveActionRef.current) {
      return;
    }
    if (busyActionId === pendingSaveActionRef.current) {
      // action 还没返回
      return;
    }
    const rawMessage = String(workbenchError);
    const kind: SettingsErrorKind = /review|blocking|SETUP_REVIEW_BLOCKED/i.test(
      rawMessage
    )
      ? "review"
      : "runtime";
    const [head, ...rest] = rawMessage
      .split(/[、;；]/)
      .map((part) => part.trim())
      .filter(Boolean);
    const items: SettingsErrorItem[] = rest.length === 0
      ? [{ id: "apply_error", title: head || rawMessage }]
      : [
          { id: "apply_error_head", title: head },
          ...rest.map((entry, idx) => ({ id: `apply_error_${idx}`, title: entry })),
        ];
    setErrorModal({ open: true, kind, items });
    pendingSaveActionRef.current = null;
  }, [workbenchError, busyActionId]);

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

  const groupedHints = Object.values(config?.ui_hints ?? {})
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
  const providerRuntimeDetails = readProviderRuntimeDetails(setup.provider_runtime?.details ?? {});
  const providerDrafts = parseProviderDrafts(fieldState.providers);
  const aliasDrafts = normalizeAliasDrafts(parseAliasDrafts(fieldState.model_aliases));

  // Feature 079 Phase 1：检测"当前 React state 与 snapshot.current_value 有没有
  // 未保存差异"。两端都通过 stringifyProviderDrafts / stringifyAliasDrafts 过一道
  // canonical 规范化，避免 key 顺序 / null vs "" 之类的伪差异产生误报。
  const pendingChangeCategories: string[] = (() => {
    const categories: string[] = [];

    const savedProvidersRaw = getValueAtPath(config.current_value, "providers") ?? [];
    const savedProvidersCanonical = stringifyProviderDrafts(
      parseProviderDrafts(JSON.stringify(savedProvidersRaw)),
    );
    const draftProvidersCanonical = stringifyProviderDrafts(providerDrafts);
    if (savedProvidersCanonical !== draftProvidersCanonical) {
      categories.push("providers");
    }

    const savedAliasesRaw = getValueAtPath(config.current_value, "model_aliases") ?? {};
    const savedAliasesCanonical = stringifyAliasDrafts(
      normalizeAliasDrafts(parseAliasDrafts(JSON.stringify(savedAliasesRaw))),
    );
    const draftAliasesCanonical = stringifyAliasDrafts(aliasDrafts);
    if (savedAliasesCanonical !== draftAliasesCanonical) {
      categories.push("model_aliases");
    }

    const savedRuntimeRaw = getValueAtPath(config.current_value, "runtime") ?? {};
    const savedRuntime =
      savedRuntimeRaw && typeof savedRuntimeRaw === "object"
        ? (savedRuntimeRaw as Record<string, unknown>)
        : {};
    // fieldState 里未设的 runtime 项回落到 snapshot 值，避免出现 undefined !== saved 的伪差异
    const draftRuntime = {
      llm_mode: fieldState["runtime.llm_mode"] ?? savedRuntime.llm_mode ?? "",
      litellm_proxy_url:
        fieldState["runtime.litellm_proxy_url"] ?? savedRuntime.litellm_proxy_url ?? "",
      master_key_env:
        fieldState["runtime.master_key_env"] ?? savedRuntime.master_key_env ?? "",
    };
    const canonicalSavedRuntime = {
      llm_mode: savedRuntime.llm_mode ?? "",
      litellm_proxy_url: savedRuntime.litellm_proxy_url ?? "",
      master_key_env: savedRuntime.master_key_env ?? "",
    };
    if (JSON.stringify(canonicalSavedRuntime) !== JSON.stringify(draftRuntime)) {
      categories.push("runtime");
    }

    const unsavedSecrets = Object.entries(secretValues).filter(
      ([, value]) => String(value ?? "").trim(),
    );
    if (unsavedSecrets.length > 0) {
      categories.push("secrets");
    }
    return categories;
  })();
  const hasPendingChanges = pendingChangeCategories.length > 0;
  const saveBusy =
    busyActionId === "setup.apply" || busyActionId === "setup.quick_connect";
  const activeProviders = providerDrafts.filter((item) => item.enabled);
  const defaultProvider =
    activeProviders[0] ?? providerDrafts[0] ?? buildProviderPreset("openrouter");
  const providerSelectOptions = providerDrafts
    .map((item) => ({
      value: item.id,
      label: item.name?.trim() ? `${item.name} · ${item.id}` : item.id,
    }))
    .filter((item) => String(item.value ?? "").trim());
  const savedEnvNames = new Set([
    ...envPresence(providerRuntimeDetails),
    ...savedSecretEnvNames,
  ]);
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

  useEffect(() => {
    if (activeProviders.length === 0) {
      setPendingRuntimeRefresh(false);
    }
  }, [activeProviders.length]);

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
      // Feature 079 Phase 1：把 fieldErrors 强制弹 modal，避免 silent return
      // 让用户以为"点了保存但没反应"。
      setErrorModal({
        open: true,
        kind: "field",
        items: Object.entries(result.errors).map(([path, msg]) => ({
          id: path,
          title: path,
          detail: String(msg),
        })),
      });
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

  function draftRequiresRuntimeRefresh(draft: {
    config: Record<string, unknown>;
    secret_values: Record<string, string>;
  }) {
    const currentManagedState = {
      runtime: {
        llm_mode: getValueAtPath(config.current_value, "runtime.llm_mode") ?? "",
        litellm_proxy_url: getValueAtPath(config.current_value, "runtime.litellm_proxy_url") ?? "",
        master_key_env: getValueAtPath(config.current_value, "runtime.master_key_env") ?? "",
      },
      providers: getValueAtPath(config.current_value, "providers") ?? [],
      model_aliases: getValueAtPath(config.current_value, "model_aliases") ?? {},
    };
    const nextManagedState = {
      runtime: {
        llm_mode: getValueAtPath(draft.config, "runtime.llm_mode") ?? "",
        litellm_proxy_url: getValueAtPath(draft.config, "runtime.litellm_proxy_url") ?? "",
        master_key_env: getValueAtPath(draft.config, "runtime.master_key_env") ?? "",
      },
      providers: getValueAtPath(draft.config, "providers") ?? [],
      model_aliases: getValueAtPath(draft.config, "model_aliases") ?? {},
    };
    if (JSON.stringify(currentManagedState) !== JSON.stringify(nextManagedState)) {
      return true;
    }
    return Object.keys(draft.secret_values).length > 0;
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
    const requiresRuntimeRefresh = draftRequiresRuntimeRefresh(draft);
    // 直接调 setup.apply——后端会内部执行 review 并在必要时 block。
    // 不在前端单独 gate review.ready，否则 secret_missing blocking
    // 会阻止用户首次保存密钥（密钥和 config 需要一起提交）。
    // Feature 079 Phase 1：标记"下一次 workbench.error 变化是 setup.apply 触发的"，
    // 让 useEffect 知道要把 error 映射到错误 modal。
    pendingSaveActionRef.current = "setup.apply";
    const result = await submitAction("setup.apply", { draft });
    const appliedReview = result?.data.review;
    if (appliedReview && typeof appliedReview === "object" && !Array.isArray(appliedReview)) {
      setReview(appliedReview as SetupReviewSummary);
    }
    const savedSecrets = result?.data?.saved_secrets as Record<string, unknown> | undefined;
    if (savedSecrets && typeof savedSecrets === "object") {
      const litellmNames = Array.isArray(savedSecrets.litellm_env_names)
        ? (savedSecrets.litellm_env_names as unknown[]).map((item) => String(item))
        : [];
      const runtimeNames = Array.isArray(savedSecrets.runtime_env_names)
        ? (savedSecrets.runtime_env_names as unknown[]).map((item) => String(item))
        : [];
      const merged = [...litellmNames, ...runtimeNames].filter((name) => name.trim());
      if (merged.length > 0) {
        setSavedSecretEnvNames((current) =>
          Array.from(new Set([...current, ...merged]))
        );
      }
    }
    setPendingRuntimeRefresh(requiresRuntimeRefresh);
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
      setPendingRuntimeRefresh(false);
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
    pendingSaveActionRef.current = "setup.quick_connect";
    const result = await submitAction("setup.quick_connect", { draft });
    const appliedReview = result?.data.review;
    if (appliedReview && typeof appliedReview === "object" && !Array.isArray(appliedReview)) {
      setReview(appliedReview as SetupReviewSummary);
    }
    const savedSecrets = result?.data?.saved_secrets as Record<string, unknown> | undefined;
    if (savedSecrets && typeof savedSecrets === "object") {
      const litellmNames = Array.isArray(savedSecrets.litellm_env_names)
        ? (savedSecrets.litellm_env_names as unknown[]).map((item) => String(item))
        : [];
      const runtimeNames = Array.isArray(savedSecrets.runtime_env_names)
        ? (savedSecrets.runtime_env_names as unknown[]).map((item) => String(item))
        : [];
      const merged = [...litellmNames, ...runtimeNames].filter((name) => name.trim());
      if (merged.length > 0) {
        setSavedSecretEnvNames((current) =>
          Array.from(new Set([...current, ...merged]))
        );
      }
    }
    if (result) {
      setPendingRuntimeRefresh(false);
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
            base_url: "",
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
      {/* Feature 079 Phase 1：错误 modal 用 portal 渲染到 body，不受本子树异常影响 */}
      <SettingsErrorModal
        open={errorModal.open}
        kind={errorModal.kind}
        items={errorModal.items}
        onClose={() => setErrorModal((prev) => ({ ...prev, open: false }))}
      />
      {/* Feature 079 Phase 1：未保存变更 sticky bar。授权完成但未 apply 时最显眼 */}
      <PendingChangesBar
        hasChanges={hasPendingChanges}
        categories={pendingChangeCategories}
        busy={saveBusy}
        onSave={() => void handleApply()}
      />
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

      <section id="settings-group-memory" className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <h3 style={{ fontSize: "1.1rem", margin: 0 }}>记忆</h3>
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
            <strong>记忆服务提醒</strong>
            <span>{(memory.warnings ?? []).map(translateWarning).join("；")}</span>
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
                {review.ready ? "就绪" : "需要检查"}
              </span>
            </div>
            <div className="wb-inline-actions wb-inline-actions-wrap">
              {pendingRuntimeRefresh ? (
                <div className="wb-inline-banner is-warning" role="alert">
                  <strong>配置已保存，但当前连接尚未刷新</strong>
                  <span>要让刚保存的 Provider、模型别名和密钥立即生效，请再执行一次连接刷新。</span>
                  <button
                    type="button"
                    className="wb-button wb-button-secondary"
                    onClick={() => void handleQuickConnect()}
                    disabled={connectBusy}
                  >
                    立即刷新连接
                  </button>
                </div>
              ) : null}
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
