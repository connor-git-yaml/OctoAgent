import {
  providerStatus,
  reasoningSupportCopy,
  reasoningSupportStateForAlias,
  type ModelAliasDraftItem,
  type ProviderDraftItem,
  type ProviderRuntimeDetails,
} from "./shared";

interface ProviderOption {
  value: string;
  label: string;
}

interface SettingsProviderSectionProps {
  providerDrafts: ProviderDraftItem[];
  aliasDrafts: ModelAliasDraftItem[];
  defaultProvider: ProviderDraftItem;
  providerRuntimeDetails: ProviderRuntimeDetails;
  providerSelectOptions: ProviderOption[];
  secretValues: Record<string, string>;
  savedEnvNames: Set<string>;
  connectBusy: boolean;
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
  providerDrafts,
  aliasDrafts,
  defaultProvider,
  providerRuntimeDetails,
  providerSelectOptions,
  secretValues,
  savedEnvNames,
  connectBusy,
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
      <section id="settings-group-models" className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <h3 style={{ fontSize: "1.1rem", margin: 0 }}>Model Providers 配置</h3>
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

            <div className="wb-provider-list" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
              {providerDrafts.length === 0 ? (
                <div className="wb-empty-state">
                  <strong>还没有 Provider</strong>
                  <span>添加 Provider 后即可配置模型别名。</span>
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
                        <strong style={{ fontSize: "0.95rem" }}>{providerName}</strong>
                        <div className="wb-provider-meta">
                          <span>{provider.id}</span>
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
                        <small>填写变量名，非实际密钥。</small>
                      </label>
                      <label className="wb-field wb-field-span-2">
                        <span>API Base URL</span>
                        <input
                          type="text"
                          value={provider.base_url}
                          placeholder="留空使用 Provider 默认地址"
                          onChange={(event) =>
                            onUpdateProviderAt(index, { base_url: event.target.value })
                          }
                        />
                        <small>SiliconFlow、DeepSeek、本地 vLLM / Ollama 等自定义网关通常需要填写。</small>
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
                            ? `凭证 ${providerRuntimeDetails.openai_oauth_profile}`
                            : "未授权"}
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
                              ? "已配置，重新输入将覆盖"
                              : "输入 API Key"
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
      </section>

      <section id="settings-group-aliases" className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <h3 style={{ fontSize: "1.1rem", margin: 0 }}>模型别名</h3>
            <p className="wb-panel-copy" style={{ marginTop: "0.35rem" }}>
              这里负责定义 alias 本身。Memory 绑定在本页配置；主 Agent / Worker 使用哪个 alias，请到 Agents 页面选择。
            </p>
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

        <div className="wb-alias-editor" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
          {providerSelectOptions.length === 0 ? (
            <div className="wb-empty-state">
              <strong>先添加 Provider</strong>
              <span>别名需绑定到已有 Provider。</span>
            </div>
          ) : null}
          {aliasDrafts.length === 0 ? (
            <div className="wb-empty-state">
              <strong>还没有模型别名</strong>
              <span>至少需要一个 main 别名。</span>
            </div>
          ) : null}
          {aliasDrafts.map((item, index) => (
            <div key={`${item.alias}-${index}`} className="wb-alias-row">
              {(() => {
                const reasoningState = reasoningSupportStateForAlias(item.provider, item.model);
                return (
                  <>
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
                        placeholder={
                          defaultProvider.id === "openai-codex" ? "gpt-5.4" : "openrouter/auto"
                        }
                        onChange={(event) => onUpdateAliasAt(index, { model: event.target.value })}
                      />
                    </label>
                    <label className="wb-field wb-field-span-2">
                      <span>说明</span>
                      <input
                        type="text"
                        value={item.description}
                        placeholder="例如：主力模型 / 低成本模型"
                        onChange={(event) =>
                          onUpdateAliasAt(index, { description: event.target.value })
                        }
                      />
                      {item.alias === "compaction" ? (
                        <small>
                          上下文压缩（推荐轻量模型如 haiku / gpt-4o-mini）。Fallback: compaction → summarizer → main
                        </small>
                      ) : null}
                    </label>
                    <label className="wb-field">
                      <span>推理强度</span>
                      <select
                        value={item.thinking_level}
                        disabled={reasoningState !== "supported"}
                        onChange={(event) =>
                          onUpdateAliasAt(index, {
                            thinking_level:
                              event.target.value as ModelAliasDraftItem["thinking_level"],
                          })
                        }
                      >
                        <option value="">默认</option>
                        <option value="xhigh">xhigh</option>
                        <option value="high">high</option>
                        <option value="medium">medium</option>
                        <option value="low">low</option>
                      </select>
                      <small>{reasoningSupportCopy(item.provider, item.model)}</small>
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
                  </>
                );
              })()}
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
