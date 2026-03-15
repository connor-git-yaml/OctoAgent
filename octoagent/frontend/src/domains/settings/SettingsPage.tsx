import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useWorkbench } from "../../components/shell/WorkbenchLayout";
import { categoryForHint, getValueAtPath } from "../../workbench/utils";
import type { ConfigFieldHint, SetupReviewSummary } from "../../types";
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

const MEMORY_HTTP_BASIC_FIELDS = new Set([
  "memory.backend_mode",
  "memory.bridge_transport",
  "memory.bridge_url",
  "memory.bridge_api_key_env",
  "memory.bridge_timeout_seconds",
]);

const DEFAULT_GATEWAY_PROXY_URL = "http://localhost:4000";
const DEFAULT_GATEWAY_MASTER_KEY_ENV = "LITELLM_MASTER_KEY";

const MEMORY_COMMAND_BASIC_FIELDS = new Set([
  "memory.backend_mode",
  "memory.bridge_transport",
  "memory.bridge_command",
  "memory.bridge_command_cwd",
  "memory.bridge_command_timeout_seconds",
]);

const MEMORY_BINDING_FIELDS = [
  {
    fieldPath: "memory.reasoning_model_alias",
    label: "记忆加工",
    description: "整理片段、摘要、候选结论与候选事实时优先使用。",
    fallbackLabel: "使用 main（默认）",
  },
  {
    fieldPath: "memory.expand_model_alias",
    label: "查询扩写",
    description: "把用户问题扩成更适合 recall 的查询表达。",
    fallbackLabel: "使用 main（默认）",
  },
  {
    fieldPath: "memory.embedding_model_alias",
    label: "语义检索",
    description: "绑定专用 embedding alias；未绑定时回退到内建默认层。",
    fallbackLabel: "使用内建 embedding（默认）",
  },
  {
    fieldPath: "memory.rerank_model_alias",
    label: "结果重排",
    description: "对召回结果做更稳定的最终排序。",
    fallbackLabel: "使用 heuristic（默认）",
  },
] as const;

function resolveMemoryLabel(memoryMode: string, memoryTransport: string): string {
  if (memoryMode !== "memu") {
    return "内建记忆引擎";
  }
  return memoryTransport === "command" ? "增强记忆（本地兼容）" : "增强记忆（远端兼容）";
}

function resolveMemorySummary(memoryMode: string, memoryTransport: string): string {
  if (memoryMode !== "memu") {
    return "本地治理 + 默认 retrieval 层";
  }
  return memoryTransport === "command" ? "本地 MemU 兼容接入" : "远端 MemU 兼容接入";
}

function resolveIndexStageLabel(stage: string): string {
  switch (stage) {
    case "queued":
      return "待开始";
    case "scanning":
      return "扫描中";
    case "embedding":
      return "生成向量中";
    case "writing_projection":
      return "写入 projection";
    case "catching_up":
      return "追平增量";
    case "validating":
      return "校验中";
    case "ready_to_cutover":
      return "待切换";
    case "completed":
      return "已完成";
    case "cancelled":
      return "已取消";
    case "failed":
      return "失败";
    default:
      return "处理中";
  }
}

export default function SettingsPage() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const location = useLocation();
  const config = snapshot!.resources.config;
  const selector = snapshot!.resources.project_selector;
  const memory = snapshot!.resources.memory;
  const retrievalPlatform = snapshot!.resources.retrieval_platform ?? null;
  const setup = snapshot!.resources.setup_governance;
  const behaviorSystem =
    (snapshot as {
      resources?: {
        agent_profiles?: {
          profiles?: Array<{
            behavior_system?: {
              source_chain?: string[];
              decision_modes?: string[];
              runtime_hint_fields?: string[];
              files?: Array<{
                file_id: string;
                title: string;
                layer: string;
                visibility: string;
                share_with_workers: boolean;
                source_kind: string;
                path_hint: string;
                is_advanced?: boolean;
              }>;
              worker_slice?: {
                shared_file_ids?: string[];
                layers?: string[];
              };
            };
          }>;
        };
      };
    })?.resources?.agent_profiles?.profiles?.[0]?.behavior_system ?? null;
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
  const memoryModelHints = memoryHints.filter((hint) => hint.section === "memory-models");
  const memoryCompatHints = memoryHints.filter((hint) => hint.section === "memory-compat");
  const memoryAdvancedHints = memoryHints.filter((hint) => hint.section === "memory-advanced");
  const hasMemoryModelBindings = memoryModelHints.length > 0;
  const otherGroupIds = ["channels", "advanced"].filter(
    (groupId) => (groupedHints[groupId] ?? []).length > 0
  );
  const memoryCorpus =
    retrievalPlatform?.corpora.find(
      (item) => item.corpus_kind === "memory"
    ) ?? null;
  const activeMemoryGeneration =
    retrievalPlatform?.generations.find(
      (item) => item.generation_id === memoryCorpus?.active_generation_id
    ) ?? null;
  const pendingMemoryGeneration =
    retrievalPlatform?.generations.find(
      (item) => item.generation_id === memoryCorpus?.pending_generation_id
    ) ?? null;
  const pendingMemoryBuildJob =
    retrievalPlatform?.build_jobs.find(
      (item) => item.generation_id === pendingMemoryGeneration?.generation_id
    ) ?? null;
  const rollbackCandidate =
    retrievalPlatform?.generations.find(
      (item) =>
        item.corpus_kind === "memory" &&
        !item.is_active &&
        Boolean(item.rollback_deadline) &&
        new Date(item.rollback_deadline || "").getTime() > Date.now()
    ) ?? null;
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

  async function handleStartEmbeddingMigration() {
    await submitAction("retrieval.index.start", {
      project_id: selector.current_project_id,
      workspace_id: selector.current_workspace_id,
    });
  }

  async function handleCancelEmbeddingMigration(generationId: string) {
    await submitAction("retrieval.index.cancel", {
      generation_id: generationId,
      project_id: selector.current_project_id,
      workspace_id: selector.current_workspace_id,
    });
  }

  async function handleCutoverEmbeddingMigration(generationId: string) {
    await submitAction("retrieval.index.cutover", {
      generation_id: generationId,
      project_id: selector.current_project_id,
      workspace_id: selector.current_workspace_id,
    });
  }

  async function handleRollbackEmbeddingMigration(generationId: string) {
    await submitAction("retrieval.index.rollback", {
      generation_id: generationId,
      project_id: selector.current_project_id,
      workspace_id: selector.current_workspace_id,
    });
  }

  const usingEchoMode = activeProviders.length === 0;
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
  const memoryTransport =
    String(
      fieldState["memory.bridge_transport"] ??
        getValueAtPath(config.current_value, "memory.bridge_transport") ??
        "http"
    )
      .trim()
      .toLowerCase() || "http";
  const usingMemu = memoryMode === "memu";
  const usingCommandTransport = usingMemu && memoryTransport === "command";
  const usingHttpTransport = usingMemu && memoryTransport !== "command";
  const visibleMemoryCompatHints = memoryCompatHints.filter((hint) => {
    if (!usingMemu) {
      return hint.field_path === "memory.backend_mode";
    }
    if (usingCommandTransport) {
      return MEMORY_COMMAND_BASIC_FIELDS.has(hint.field_path);
    }
    return MEMORY_HTTP_BASIC_FIELDS.has(hint.field_path);
  });
  const visibleMemoryAdvancedHints = usingHttpTransport ? memoryAdvancedHints : [];
  const memoryLabel = resolveMemoryLabel(memoryMode, memoryTransport);
  const memorySummaryLabel = resolveMemorySummary(memoryMode, memoryTransport);
  const retrievalBusy = String(busyActionId ?? "").startsWith("retrieval.index.");
  const aliasOptions = Array.from(
    new Set(aliasDrafts.map((item) => item.alias.trim()).filter((value) => value.length > 0))
  );
  function readStringField(fieldPath: string, fallback = ""): string {
    return String(fieldState[fieldPath] ?? getValueAtPath(config.current_value, fieldPath) ?? fallback).trim();
  }
  function resolveBindingSummary(
    fieldPath: string,
    fallbackLabel: string
  ): { value: string; label: string } {
    const value = readStringField(fieldPath);
    return {
      value,
      label: value || fallbackLabel,
    };
  }
  const activeEmbeddingLabel =
    activeMemoryGeneration?.label ||
    activeMemoryGeneration?.profile_target ||
    resolveBindingSummary("memory.embedding_model_alias", "内建 embedding（默认）").label;
  const desiredEmbeddingLabel =
    memoryCorpus?.desired_profile_target ||
    resolveBindingSummary("memory.embedding_model_alias", "内建 embedding（默认）").label;
  const pendingStageLabel = pendingMemoryBuildJob
    ? resolveIndexStageLabel(pendingMemoryBuildJob.stage)
    : "待开始";
  const pendingPercent = Math.max(0, Math.min(100, pendingMemoryBuildJob?.percent_complete ?? 0));
  const showMigrationCard =
    memoryCorpus !== null &&
    (Boolean(memoryCorpus.pending_generation_id) ||
      memoryCorpus.state === "migration_deferred" ||
      rollbackCandidate !== null);
  const behaviorCliSnippets = [
    {
      key: "list",
      title: "列出有效文件",
      summary: "查看当前 project 下生效的 behavior files 和来源链。",
      command: "octo behavior ls",
    },
    {
      key: "show-agents",
      title: "查看 AGENTS.md",
      summary: "确认 Butler / Worker 当前共享的总约束。",
      command: "octo behavior show AGENTS",
    },
    {
      key: "init",
      title: "初始化默认文件",
      summary: "为当前作用域生成核心文件模板。",
      command: "octo behavior init",
    },
    {
      key: "edit-agents",
      title: "准备并编辑 AGENTS.md",
      summary: "materialize 当前 project override，并交给本机编辑器处理。",
      command: "octo behavior edit AGENTS",
    },
    {
      key: "diff-agents",
      title: "查看 override diff",
      summary: "比较当前 override 相对下层来源的差异。",
      command: "octo behavior diff AGENTS",
    },
    {
      key: "apply-agents",
      title: "应用 reviewed proposal",
      summary: "把外部提案文件写回 behavior workspace。",
      command: "octo behavior apply AGENTS --from /path/to/proposal.md",
    },
  ];
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
        providerDraftCount={providerDrafts.length}
        activeProvidersCount={activeProviders.length}
        aliasDraftCount={aliasDrafts.length}
        defaultProviderId={defaultProvider.id}
        memoryLabel={memoryLabel}
        memoryStatus={memory.backend_state || memory.status}
        onQuickConnect={() => void handleQuickConnect()}
        onReview={() => void handleReview()}
        onApply={() => void handleApply()}
        connectBusy={connectBusy}
        onScrollToSection={scrollToSection}
      />

      <SettingsProviderSection
        usingEchoMode={usingEchoMode}
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
            <h3>平台级 Memory 与检索质量</h3>
            <div className="wb-chip-row">
              <span className="wb-chip">平台级</span>
              <span className="wb-chip">影响默认 recall 与未来知识库</span>
            </div>
          </div>
        </div>

        <div className="wb-card-grid wb-card-grid-4">
          <article className="wb-card">
            <p className="wb-card-label">当前模式</p>
            <strong>{memoryLabel}</strong>
            <span>{memorySummaryLabel}</span>
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
                resolveBindingSummary(
                  "memory.embedding_model_alias",
                  "内建 embedding（默认）"
                ).label
              }
            </strong>
            <span>换模型时会后台重建索引</span>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">当前结论</p>
            <strong>{memory.summary.sor_current_count}</strong>
            <span>片段 {memory.summary.fragment_count}</span>
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
            <span>
              先让默认层跑起来，再按质量需求补 `加工 / 扩写 / embedding / rerank` 模型别名。
              只有迁移旧实例或排查兼容问题时，才需要展开下面的兼容接入设置。
            </span>
          </div>
        )}

        {showMigrationCard ? (
          <div className="wb-card wb-retrieval-progress-card">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">Embedding 迁移</p>
                <h3>在线查询继续使用旧索引，直到新索引切换完成</h3>
              </div>
              {memoryCorpus ? (
                <div className="wb-chip-row">
                  <span className="wb-chip">{memoryCorpus.state}</span>
                  {pendingMemoryBuildJob ? (
                    <span className="wb-chip">{pendingStageLabel}</span>
                  ) : null}
                </div>
              ) : null}
            </div>

            <div className="wb-card-grid wb-card-grid-3">
              <article className="wb-card">
                <p className="wb-card-label">当前在线索引</p>
                <strong>{activeEmbeddingLabel}</strong>
                <span>现在所有 recall 仍继续用这一层。</span>
              </article>
              <article className="wb-card">
                <p className="wb-card-label">目标 embedding</p>
                <strong>{desiredEmbeddingLabel}</strong>
                <span>
                  {pendingMemoryGeneration
                    ? "新索引准备好后再 cutover。"
                    : "修改 embedding 绑定后会在这里生成新一代索引。"}
                </span>
              </article>
              <article className="wb-card">
                <p className="wb-card-label">当前阶段</p>
                <strong>
                  {pendingMemoryBuildJob
                    ? pendingStageLabel
                    : memoryCorpus?.state === "migration_deferred"
                      ? "等待重新发起"
                      : rollbackCandidate
                        ? "可回滚"
                        : "空闲"}
                </strong>
                <span>
                  {pendingMemoryBuildJob?.summary ||
                    memoryCorpus?.summary ||
                    "当前没有进行中的迁移。"}
                </span>
              </article>
            </div>

            {pendingMemoryBuildJob ? (
              <div className="wb-progress-card">
                <div className="wb-progress-track" aria-hidden="true">
                  <div
                    className="wb-progress-fill"
                    style={{ width: `${pendingPercent}%` }}
                  />
                </div>
                <div className="wb-progress-meta">
                  <span>
                    {pendingMemoryBuildJob.processed_items}/{pendingMemoryBuildJob.total_items || "?"}
                  </span>
                  <span>{pendingPercent}%</span>
                </div>
              </div>
            ) : null}

            {memoryCorpus?.warnings.length ? (
              <div className="wb-note">
                <strong>迁移提醒</strong>
                <span>{memoryCorpus.warnings.join("；")}</span>
              </div>
            ) : null}

            <div className="wb-chip-row">
              {pendingMemoryGeneration ? (
                pendingMemoryBuildJob?.stage === "ready_to_cutover" ? (
                  <button
                    type="button"
                    className="wb-button wb-button-primary"
                    disabled={retrievalBusy}
                    onClick={() =>
                      handleCutoverEmbeddingMigration(pendingMemoryGeneration.generation_id)
                    }
                  >
                    切换到新索引
                  </button>
                ) : pendingMemoryBuildJob?.stage === "queued" ? (
                  <button
                    type="button"
                    className="wb-button wb-button-primary"
                    disabled={retrievalBusy}
                    onClick={() => {
                      void handleStartEmbeddingMigration();
                    }}
                  >
                    开始迁移
                  </button>
                ) : null
              ) : memoryCorpus?.state === "migration_deferred" ? (
                <button
                  type="button"
                  className="wb-button wb-button-primary"
                  disabled={retrievalBusy}
                  onClick={() => {
                    void handleStartEmbeddingMigration();
                  }}
                >
                  重新发起迁移
                </button>
              )
              : null}

              {pendingMemoryBuildJob?.can_cancel && pendingMemoryGeneration ? (
                <button
                  type="button"
                  className="wb-button wb-button-secondary"
                  disabled={retrievalBusy}
                  onClick={() =>
                    handleCancelEmbeddingMigration(pendingMemoryGeneration.generation_id)
                  }
                >
                  取消迁移
                </button>
              ) : null}

              {rollbackCandidate ? (
                <button
                  type="button"
                  className="wb-button wb-button-tertiary"
                  disabled={retrievalBusy}
                  onClick={() =>
                    handleRollbackEmbeddingMigration(rollbackCandidate.generation_id)
                  }
                >
                  回滚到上一版
                </button>
              ) : null}
            </div>
          </div>
        ) : null}

        {hasMemoryModelBindings ? (
          <>
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">模型绑定</p>
                <h3>先决定默认质量层，再考虑是否升级</h3>
              </div>
            </div>
            <div className="wb-form-grid">
              {MEMORY_BINDING_FIELDS.map((item) => {
                const binding = resolveBindingSummary(item.fieldPath, item.fallbackLabel);
                const selectOptions = binding.value
                  ? Array.from(new Set([...aliasOptions, binding.value]))
                  : aliasOptions;
                return (
                  <label key={item.fieldPath} className="wb-field">
                    <span>{item.label}</span>
                    <small>{item.description}</small>
                    <select
                      aria-label={item.label}
                      value={binding.value}
                      onChange={(event) => updateFieldValue(item.fieldPath, event.target.value)}
                    >
                      <option value="">{item.fallbackLabel}</option>
                      {selectOptions.map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                    <small>当前生效：{binding.label}</small>
                  </label>
                );
              })}
            </div>

            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>最小可用</strong>
                <span>只要 `main` alias 可用，Memory 就能先完成基础加工与基础 recall。</span>
              </div>
              <div className="wb-note">
                <strong>语义检索升级</strong>
                <span>
                  如果你后来补了专用 embedding alias，系统会走后台重建；切换完成前仍继续使用旧索引。
                </span>
              </div>
            </div>
          </>
        ) : null}

        {(visibleMemoryCompatHints.length > 0 || visibleMemoryAdvancedHints.length > 0) ? (
          <details className="wb-field-guide wb-field-guide-disclosure">
            <summary>兼容接入（仅迁移旧 MemU bridge 或排障时需要）</summary>
            <p>
              这组字段属于兼容层，不是普通用户首次使用 Memory 的必要条件。只有你明确还在沿用旧
              command / http bridge，或需要排查兼容链路时，再展开修改。
            </p>
            {visibleMemoryCompatHints.length > 0 ? (
              <SettingsHintFields
                hints={visibleMemoryCompatHints}
                schema={config.schema}
                fieldState={fieldState}
                fieldErrors={fieldErrors}
                usingEchoMode={usingEchoMode}
                onFieldValueChange={updateFieldValue}
              />
            ) : null}
            {visibleMemoryAdvancedHints.length > 0 ? (
              <SettingsHintFields
                hints={visibleMemoryAdvancedHints}
                schema={config.schema}
                fieldState={fieldState}
                fieldErrors={fieldErrors}
                usingEchoMode={usingEchoMode}
                onFieldValueChange={updateFieldValue}
              />
            ) : null}
          </details>
        ) : null}
      </section>

      {behaviorSystem ? (
        <section id="settings-group-behavior" className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">Behavior Files</p>
              <h3>当前项目默认行为现在来自显式文件与运行时 hints</h3>
              <div className="wb-chip-row">
                <span className="wb-chip">项目默认</span>
                <span className="wb-chip">影响后续新会话</span>
              </div>
            </div>
          </div>

          <div className="wb-card-grid wb-card-grid-4">
            <article className="wb-card">
              <p className="wb-card-label">生效来源</p>
              <strong>{behaviorSystem.source_chain?.[0] ?? "N/A"}</strong>
              <span>{behaviorSystem.source_chain?.length ?? 0} 条 source chain</span>
            </article>
            <article className="wb-card">
              <p className="wb-card-label">核心文件</p>
              <strong>{behaviorSystem.files?.length ?? 0}</strong>
              <span>默认包含 AGENTS / USER / PROJECT / TOOLS</span>
            </article>
            <article className="wb-card">
              <p className="wb-card-label">决策模式</p>
              <strong>{behaviorSystem.decision_modes?.length ?? 0}</strong>
              <span>{behaviorSystem.decision_modes?.join(" / ") ?? "N/A"}</span>
            </article>
            <article className="wb-card">
              <p className="wb-card-label">Worker 继承</p>
              <strong>{behaviorSystem.worker_slice?.shared_file_ids?.length ?? 0}</strong>
              <span>{behaviorSystem.worker_slice?.shared_file_ids?.join(" / ") ?? "N/A"}</span>
            </article>
          </div>

          <div className="wb-inline-banner is-muted">
            <strong>当前页面先提供只读 operator 视图</strong>
            <span>
              你可以先在这里确认 effective source、共享范围和决策 contract；文件编辑当前仍建议走
              CLI，避免 Web 和本地文件系统各维护一套真相。已存在的会话会继续沿用自己的
              session-scoped project / workspace 绑定。
            </span>
          </div>

          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">当前文件</p>
              <h3>核心文件、可见性与来源链</h3>
            </div>
          </div>
          <div className="wb-settings-cli-grid">
            {(behaviorSystem.files ?? []).map((file) => (
              <article key={file.file_id} className="wb-note wb-cli-snippet">
                <strong>{file.file_id}</strong>
                <span>
                  {file.title || file.layer} · {file.visibility} · {file.source_kind}
                </span>
                <pre className="wb-cli-snippet-code">
                  {file.path_hint}
                  {"\n"}
                  share_with_workers={file.share_with_workers ? "true" : "false"}
                </pre>
              </article>
            ))}
          </div>

          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">命令行</p>
              <h3>当前可用的行为文件管理入口</h3>
            </div>
          </div>
          <div className="wb-settings-cli-grid">
            {behaviorCliSnippets.map((snippet) => (
              <article key={snippet.key} className="wb-note wb-cli-snippet">
                <strong>{snippet.title}</strong>
                <span>{snippet.summary}</span>
                <pre className="wb-cli-snippet-code">{snippet.command}</pre>
              </article>
            ))}
          </div>
        </section>
      ) : null}

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
              <strong>当前状态</strong>
              <span>
                {usingEchoMode
                  ? "当前还没有连接真实模型；没配好前系统会先自动回退。"
                  : "当前会优先使用你配置好的 Provider 和模型别名。"}
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
