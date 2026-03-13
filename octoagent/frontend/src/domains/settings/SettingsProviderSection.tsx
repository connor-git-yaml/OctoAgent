import type { ConfigFieldHint } from "../../types";
import {
  generateSecretValue,
  providerStatus,
  type ModelAliasDraftItem,
  type ProviderDraftItem,
  type ProviderRuntimeDetails,
} from "./shared";

interface ProviderOption {
  value: string;
  label: string;
}

interface SettingsProviderSectionProps {
  usingEchoMode: boolean;
  fieldState: Record<string, string | boolean>;
  providerDrafts: ProviderDraftItem[];
  aliasDrafts: ModelAliasDraftItem[];
  defaultProvider: ProviderDraftItem;
  providerRuntimeDetails: ProviderRuntimeDetails;
  providerSelectOptions: ProviderOption[];
  proxyUrlHint?: ConfigFieldHint;
  masterKeyHint?: ConfigFieldHint;
  secretValues: Record<string, string>;
  savedEnvNames: Set<string>;
  connectBusy: boolean;
  onFieldValueChange: (fieldPath: string, value: string | boolean) => void;
  onSecretValueChange: (envName: string, value: string) => void;
  onAddProviderDraft: (providerId: string) => void;
  onUpdateProviderAt: (index: number, patch: Partial<ProviderDraftItem>) => void;
  onMoveProviderToFront: (index: number) => void;
  onRemoveProviderAt: (index: number) => void;
  onRestoreRecommendedAliases: (providerId?: string) => void;
  onAddAliasDraft: () => void;
  onUpdateAliasAt: (index: number, patch: Partial<ModelAliasDraftItem>) => void;
  onRemoveAliasDraft: (index: number) => void;
  onOpenAIOAuthConnect: () => Promise<void>;
}

export default function SettingsProviderSection({
  usingEchoMode,
  fieldState,
  providerDrafts,
  aliasDrafts,
  defaultProvider,
  providerRuntimeDetails,
  providerSelectOptions,
  proxyUrlHint,
  masterKeyHint,
  secretValues,
  savedEnvNames,
  connectBusy,
  onFieldValueChange,
  onSecretValueChange,
  onAddProviderDraft,
  onUpdateProviderAt,
  onMoveProviderToFront,
  onRemoveProviderAt,
  onRestoreRecommendedAliases,
  onAddAliasDraft,
  onUpdateAliasAt,
  onRemoveAliasDraft,
  onOpenAIOAuthConnect,
}: SettingsProviderSectionProps) {
  return (
    <>
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
            onClick={() => onFieldValueChange("runtime.llm_mode", "echo")}
          >
            <span className="wb-card-label">体验模式</span>
            <strong>先跑通页面和任务流</strong>
            <p>不依赖真实模型，适合先检查控制台和交互。</p>
          </button>
          <button
            type="button"
            className={`wb-mode-card ${!usingEchoMode ? "is-active" : ""}`}
            onClick={() => onFieldValueChange("runtime.llm_mode", "litellm")}
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
                    onFieldValueChange("runtime.litellm_proxy_url", event.target.value)
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
                    onFieldValueChange("runtime.master_key_env", event.target.value)
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
                    onSecretValueChange(
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
                      onSecretValueChange(
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
                onClick={() => onAddProviderDraft("openrouter")}
              >
                添加 OpenRouter
              </button>
              <button
                type="button"
                className="wb-button wb-button-tertiary wb-button-inline"
                onClick={() => onAddProviderDraft("openai")}
              >
                添加 OpenAI
              </button>
              <button
                type="button"
                className="wb-button wb-button-tertiary wb-button-inline"
                onClick={() => onAddProviderDraft("anthropic")}
              >
                添加 Anthropic
              </button>
              <button
                type="button"
                className="wb-button wb-button-tertiary wb-button-inline"
                onClick={() => onAddProviderDraft("openai-codex")}
              >
                添加 OpenAI Auth
              </button>
              <button
                type="button"
                className="wb-button wb-button-secondary wb-button-inline"
                onClick={() => onAddProviderDraft("custom")}
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
                const status = providerStatus(
                  provider,
                  providerRuntimeDetails,
                  savedEnvNames,
                  secretValues
                );
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
                            onClick={() => onMoveProviderToFront(index)}
                          >
                            设为默认
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="wb-button wb-button-tertiary wb-button-inline"
                          onClick={() => onRemoveProviderAt(index)}
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
                          onChange={(event) => onUpdateProviderAt(index, { name: event.target.value })}
                        />
                      </label>
                      <label className="wb-field">
                        <span>Provider ID</span>
                        <input
                          type="text"
                          value={provider.id}
                          onChange={(event) => onUpdateProviderAt(index, { id: event.target.value })}
                        />
                      </label>
                      <label className="wb-field">
                        <span>鉴权方式</span>
                        <select
                          value={provider.auth_type}
                          onChange={(event) =>
                            onUpdateProviderAt(index, {
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
                            onUpdateProviderAt(index, { api_key_env: event.target.value })
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
                              onUpdateProviderAt(index, { enabled: event.target.checked })
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
                            onClick={() => void onOpenAIOAuthConnect()}
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
                            onSecretValueChange(provider.api_key_env, event.target.value)
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
              onClick={() => onRestoreRecommendedAliases(defaultProvider.id)}
            >
              恢复 main / cheap
            </button>
            <button
              type="button"
              className="wb-button wb-button-secondary wb-button-inline"
              onClick={onAddAliasDraft}
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
                  onChange={(event) => onUpdateAliasAt(index, { alias: event.target.value })}
                />
              </label>
              <label className="wb-field">
                <span>Provider</span>
                <select
                  value={item.provider}
                  onChange={(event) => onUpdateAliasAt(index, { provider: event.target.value })}
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
                  onChange={(event) => onUpdateAliasAt(index, { model: event.target.value })}
                />
              </label>
              <label className="wb-field wb-field-span-2">
                <span>说明</span>
                <input
                  type="text"
                  value={item.description}
                  placeholder="例如：主力模型 / 低成本模型"
                  onChange={(event) => onUpdateAliasAt(index, { description: event.target.value })}
                />
              </label>
              <label className="wb-field">
                <span>推理强度</span>
                <select
                  value={item.thinking_level}
                  onChange={(event) =>
                    onUpdateAliasAt(index, {
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
                  onClick={() => onRemoveAliasDraft(index)}
                  disabled={aliasDrafts.length <= 1}
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
