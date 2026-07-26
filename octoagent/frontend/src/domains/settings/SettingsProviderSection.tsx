import {
  providerStatus,
  reasoningSupportCopy,
  reasoningSupportStateForAlias,
  type ModelAliasDraftItem,
  type ProviderDraftItem,
  type ProviderRuntimeDetails,
} from "./shared";
import {
  readSecretDraft,
  secretReplacementValues,
  type SecretDraftCommand,
  type SecretDrafts,
} from "./secretMutation";

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
  secretDrafts: SecretDrafts;
  savedEnvNames: Set<string>;
  connectBusy: boolean;
  onSecretDraftChange: (
    envName: string,
    configured: boolean,
    command: SecretDraftCommand,
  ) => void;
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
  secretDrafts,
  savedEnvNames,
  connectBusy,
  onSecretDraftChange,
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
  const replacementValues = secretReplacementValues(secretDrafts);
  return (
    <>
      <section id="settings-group-models" className="f149-settings-panel">
        <div className="f149-settings-panel-head">
          <div>
            <h3 style={{ fontSize: "1.1rem", margin: 0 }}>模型供应商配置</h3>
          </div>
          <span className="f149-settings-status-pill is-active">共 {providerDrafts.length} 个</span>
        </div>

            <div className="f149-settings-provider-preset-row">
              <button
                type="button"
                className="f149-settings-button f149-settings-button-tertiary f149-settings-button-inline"
                onClick={() => onAddProviderDraft("openrouter")}
              >
                添加 OpenRouter
              </button>
              <button
                type="button"
                className="f149-settings-button f149-settings-button-tertiary f149-settings-button-inline"
                onClick={() => onAddProviderDraft("openai")}
              >
                添加 OpenAI
              </button>
              <button
                type="button"
                className="f149-settings-button f149-settings-button-tertiary f149-settings-button-inline"
                onClick={() => onAddProviderDraft("anthropic")}
              >
                添加 Anthropic
              </button>
              <button
                type="button"
                className="f149-settings-button f149-settings-button-tertiary f149-settings-button-inline"
                onClick={() => onAddProviderDraft("openai-codex")}
              >
                添加 OpenAI Auth
              </button>
              <button
                type="button"
                className="f149-settings-button f149-settings-button-secondary f149-settings-button-inline"
                onClick={() => onAddProviderDraft("custom")}
              >
                添加自定义 Provider
              </button>
            </div>

            <div className="f149-settings-provider-list">
              {providerDrafts.length === 0 ? (
                <div className="f149-settings-empty-state">
                  <strong>还没有 Provider</strong>
                  <span>添加 Provider 后即可配置模型别名。</span>
                </div>
              ) : null}

              {providerDrafts.map((provider, index) => {
                const status = providerStatus(
                  provider,
                  providerRuntimeDetails,
                  savedEnvNames,
                  replacementValues,
                );
                const providerName = provider.name?.trim() || provider.id || `Provider ${index + 1}`;
                const isOAuthProvider =
                  provider.id === "openai-codex" && provider.auth_type === "oauth";
                const secretConfigured = savedEnvNames.has(provider.api_key_env);
                const secretDraft = readSecretDraft(
                  secretDrafts,
                  provider.api_key_env,
                  secretConfigured,
                );
                return (
                  <article
                    key={`${provider.id}-${index}`}
                    className={`f149-settings-provider-item ${index === 0 ? "is-default" : ""}`}
                  >
                    <div data-testid={`settings-provider-ordinary-${provider.id}`}>
                      <div className="f149-settings-provider-card-head">
                        <strong style={{ fontSize: "0.95rem" }}>{providerName}</strong>
                        <div className="f149-settings-inline-actions f149-settings-inline-actions-wrap">
                          <span className={`f149-settings-status-pill ${status.tone}`}>{status.label}</span>
                          {index !== 0 ? (
                            <button
                              type="button"
                              className="f149-settings-button f149-settings-button-tertiary f149-settings-button-inline"
                              onClick={() => onMoveProviderToFront(index)}
                            >
                              设为默认
                            </button>
                          ) : null}
                          <button
                            type="button"
                            className="f149-settings-button f149-settings-button-tertiary f149-settings-button-inline"
                            onClick={() => onRemoveProviderAt(index)}
                          >
                            删除
                          </button>
                        </div>
                      </div>

                      <div className="f149-settings-form-grid f149-settings-provider-form">
                        <label className="f149-settings-field">
                          <span>显示名称</span>
                          <input
                            type="text"
                            value={provider.name}
                            onChange={(event) =>
                              onUpdateProviderAt(index, { name: event.target.value })
                            }
                          />
                        </label>
                        <label className="f149-settings-field">
                          <span>启用状态</span>
                          <div className="f149-settings-provider-toggle-row">
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
                        <div className="f149-settings-note">
                          <strong>账户连接</strong>
                          <span>
                            {providerRuntimeDetails.openai_oauth_connected
                              ? "账户已连接"
                              : "账户尚未连接"}
                          </span>
                          <div className="f149-settings-inline-actions f149-settings-inline-actions-wrap">
                            <button
                              type="button"
                              className="f149-settings-button f149-settings-button-secondary f149-settings-button-inline"
                              onClick={() => void onOpenAIOAuthConnect()}
                              disabled={connectBusy}
                            >
                              {providerRuntimeDetails.openai_oauth_connected
                                ? "重新连接账户"
                                : "连接账户"}
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="f149-settings-note">
                          <div className="f149-settings-provider-card-head">
                            <div>
                              <strong>访问密钥</strong>
                              <span>
                                {secretConfigured ? "●●●●●●●● 已配置" : "尚未配置"}
                              </span>
                            </div>
                            <small>已保存的值不会显示</small>
                          </div>
                          {secretConfigured && secretDraft.mode === "keep" ? (
                            <div className="f149-settings-inline-actions f149-settings-inline-actions-wrap">
                              <span>保留现有值</span>
                              <button
                                type="button"
                                className="f149-settings-button f149-settings-button-secondary f149-settings-button-inline"
                                aria-label={`重新输入 ${providerName} 访问密钥`}
                                onClick={() =>
                                  onSecretDraftChange(
                                    provider.api_key_env,
                                    secretConfigured,
                                    { type: "replace", value: "" },
                                  )
                                }
                              >
                                重新输入
                              </button>
                              <button
                                type="button"
                                className="f149-settings-button f149-settings-button-tertiary f149-settings-button-inline"
                                aria-label={`移除 ${providerName} 访问密钥`}
                                onClick={() =>
                                  onSecretDraftChange(
                                    provider.api_key_env,
                                    secretConfigured,
                                    { type: "remove" },
                                  )
                                }
                              >
                                移除
                              </button>
                            </div>
                          ) : null}
                          {secretDraft.mode === "replace" ? (
                            <label className="f149-settings-field">
                              <span>
                                {secretConfigured ? "修改访问密钥" : "输入访问密钥"}
                              </span>
                              <input
                                type="password"
                                autoComplete="new-password"
                                aria-label={`${providerName} 新的访问密钥`}
                                value={secretDraft.value}
                                placeholder="现有值不可查看，修改时请重新输入"
                                onChange={(event) =>
                                  onSecretDraftChange(
                                    provider.api_key_env,
                                    secretConfigured,
                                    {
                                      type: "replace",
                                      value: event.target.value,
                                    },
                                  )
                                }
                              />
                              <button
                                type="button"
                                className="f149-settings-button f149-settings-button-tertiary f149-settings-button-inline"
                                aria-label={`取消修改 ${providerName} 访问密钥`}
                                onClick={() =>
                                  onSecretDraftChange(
                                    provider.api_key_env,
                                    secretConfigured,
                                    { type: "keep" },
                                  )
                                }
                              >
                                取消修改
                              </button>
                            </label>
                          ) : null}
                          {secretDraft.mode === "remove" ? (
                            <div className="f149-settings-inline-actions f149-settings-inline-actions-wrap">
                              <span>保存后会移除现有密钥</span>
                              <button
                                type="button"
                                className="f149-settings-button f149-settings-button-tertiary f149-settings-button-inline"
                                onClick={() =>
                                  onSecretDraftChange(
                                    provider.api_key_env,
                                    secretConfigured,
                                    { type: "keep" },
                                  )
                                }
                              >
                                保留现有值
                              </button>
                            </div>
                          ) : null}
                        </div>
                      )}
                    </div>

                    <details
                      className="f149-settings-note"
                      role="group"
                      aria-label={`${providerName} 高级设置`}
                    >
                      <summary>高级</summary>
                      <div className="f149-settings-form-grid f149-settings-settings-provider-form">
                        <label className="f149-settings-field">
                          <span>Provider ID</span>
                          <input
                            type="text"
                            value={provider.id}
                            onChange={(event) =>
                              onUpdateProviderAt(index, { id: event.target.value })
                            }
                          />
                        </label>
                        <label className="f149-settings-field">
                          <span>鉴权方式</span>
                          <select
                            value={provider.auth_type}
                            onChange={(event) =>
                              onUpdateProviderAt(index, {
                                auth_type:
                                  event.target.value === "oauth" ? "oauth" : "api_key",
                              })
                            }
                          >
                            <option value="api_key">API Key</option>
                            <option value="oauth">OAuth</option>
                          </select>
                        </label>
                        <label className="f149-settings-field">
                          <span>环境变量名</span>
                          <input
                            type="text"
                            value={provider.api_key_env}
                            onChange={(event) =>
                              onUpdateProviderAt(index, {
                                api_key_env: event.target.value,
                              })
                            }
                          />
                          <small>填写变量名，不填写真实密钥。</small>
                        </label>
                        <label className="f149-settings-field f149-settings-field-span-2">
                          <span>API Base URL</span>
                          <input
                            type="text"
                            value={provider.base_url}
                            placeholder="留空使用 Provider 默认地址"
                            onChange={(event) =>
                              onUpdateProviderAt(index, {
                                base_url: event.target.value,
                              })
                            }
                          />
                        </label>
                        {isOAuthProvider &&
                        providerRuntimeDetails.openai_oauth_profile ? (
                          <span>
                            授权配置：{providerRuntimeDetails.openai_oauth_profile}
                          </span>
                        ) : null}
                      </div>
                    </details>
                  </article>
                );
              })}
            </div>
      </section>

      <section id="settings-group-aliases" className="f149-settings-panel">
        <div className="f149-settings-panel-head">
          <div>
            <h3 style={{ fontSize: "1.1rem", margin: 0 }}>模型别名</h3>
            <p className="f149-settings-panel-copy" style={{ marginTop: "0.35rem" }}>
              这里负责定义 alias 本身。Memory 绑定在本页配置；主 Agent / Worker 使用哪个 alias，请到 Agents 页面选择。
            </p>
          </div>
          <div className="f149-settings-inline-actions f149-settings-inline-actions-wrap">
            <button
              type="button"
              className="f149-settings-button f149-settings-button-tertiary f149-settings-button-inline"
              onClick={() => onRestoreRecommendedAliases(defaultProvider.id)}
            >
              恢复 main / cheap
            </button>
            <button
              type="button"
              className="f149-settings-button f149-settings-button-secondary f149-settings-button-inline"
              onClick={onAddAliasDraft}
            >
              新增别名
            </button>
          </div>
        </div>

        <div className="f149-settings-alias-editor">
          {providerSelectOptions.length === 0 ? (
            <div className="f149-settings-empty-state">
              <strong>先添加 Provider</strong>
              <span>别名需绑定到已有 Provider。</span>
            </div>
          ) : null}
          {aliasDrafts.length === 0 ? (
            <div className="f149-settings-empty-state">
              <strong>还没有模型别名</strong>
              <span>至少需要一个 main 别名。</span>
            </div>
          ) : null}
          {aliasDrafts.map((item, index) => (
            <div key={`${item.alias}-${index}`} className="f149-settings-alias-row">
              {(() => {
                const reasoningState = reasoningSupportStateForAlias(item.provider, item.model);
                return (
                  <>
                    <label className="f149-settings-field">
                      <span>别名</span>
                      <input
                        type="text"
                        value={item.alias}
                        onChange={(event) => onUpdateAliasAt(index, { alias: event.target.value })}
                      />
                    </label>
                    <label className="f149-settings-field">
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
                    <label className="f149-settings-field f149-settings-field-span-2">
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
                    <label className="f149-settings-field f149-settings-field-span-2">
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
                    <label className="f149-settings-field">
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
                    <div className="f149-settings-alias-actions">
                      <button
                        type="button"
                        className="f149-settings-button f149-settings-button-tertiary f149-settings-button-inline"
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
