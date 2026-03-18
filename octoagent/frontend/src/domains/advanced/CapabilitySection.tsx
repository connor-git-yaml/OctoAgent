import { Link } from "react-router-dom";
import type { CapabilityPackDocument, WorkerProfilesDocument } from "../../types";

interface CapabilitySectionProps {
  rootAgentProfilesDocument: WorkerProfilesDocument;
  defaultRootAgentId: string;
  capabilityPack: CapabilityPackDocument;
  busyActionId: string | null;
  onRefreshCapabilityPack: () => void;
  onOpenDelegation: () => void;
  formatScope: (value: string) => string;
  formatProfileMode: (value: string) => string;
  formatDateTime: (value?: string | null) => string;
  formatWorkerTemplateName: (name: string, archetype: string) => string;
  statusTone: (status: string) => string;
}

export default function CapabilitySection({
  rootAgentProfilesDocument,
  defaultRootAgentId,
  capabilityPack,
  busyActionId,
  onRefreshCapabilityPack,
  onOpenDelegation,
  formatScope,
  formatProfileMode,
  formatDateTime,
  formatWorkerTemplateName,
  statusTone,
}: CapabilitySectionProps) {
  const rootAgentProfiles = rootAgentProfilesDocument.profiles ?? [];

  return (
    <section className="stack-section">
      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Worker 模板视图</p>
            <h3>{rootAgentProfiles.length}</h3>
          </div>
          <span className={`tone-chip ${statusTone(rootAgentProfilesDocument.status)}`}>
            {rootAgentProfilesDocument.status}
          </span>
        </div>
        <p className="muted">
          这里展示 `worker_profiles` canonical resource 里的模板真相：默认配置、当前运行状态、
          可用工具和最近任务。下方 bundled capability pack 仍然只代表系统内置 archetype。
        </p>
        {rootAgentProfiles.length === 0 ? (
          <div className="event-item">
            <div>
              <strong>worker_profiles 还没有数据</strong>
              <p>等后端把 canonical resource 投进 snapshot 后，这里会直接显示模板视图。</p>
            </div>
          </div>
        ) : (
          <div className="wb-root-agent-list">
            {rootAgentProfiles.map((profile) => {
              const staticConfig = profile.static_config;
              const dynamicContext = profile.dynamic_context;
              const defaultToolGroups = staticConfig.default_tool_groups ?? [];
              const staticCapabilities = staticConfig.capabilities ?? [];
              const runtimeKinds = staticConfig.runtime_kinds ?? [];
              const currentSelectedTools = dynamicContext.current_selected_tools ?? [];
              const tone =
                dynamicContext.attention_work_count > 0
                  ? "warning"
                  : dynamicContext.running_work_count > 0
                    ? "running"
                    : "neutral";

              return (
                <article
                  key={profile.profile_id}
                  className={`wb-root-agent-card ${
                    profile.warnings.length > 0 ? "has-warning" : ""
                  }`}
                >
                  <div className="wb-root-agent-card-head">
                    <div>
                      <p className="wb-card-label">模板视图</p>
                      <h3>
                        {formatWorkerTemplateName(
                          profile.name,
                          profile.static_config.base_archetype
                        )}
                      </h3>
                      <p className="wb-inline-note">
                        {profile.summary || "当前 profile 没有额外 summary。"}
                      </p>
                    </div>
                    <div className="wb-chip-row">
                      <span className="wb-chip">{formatScope(profile.scope)}</span>
                      <span className="wb-chip">{formatProfileMode(profile.mode)}</span>
                      {profile.profile_id === defaultRootAgentId ? (
                        <span className="wb-chip is-success">聊天默认</span>
                      ) : null}
                      <span className={`tone-chip ${tone}`}>
                        {dynamicContext.latest_work_status || "idle"}
                      </span>
                    </div>
                  </div>
                  <div className="wb-root-agent-console">
                    <section className="wb-root-agent-column">
                      <div className="wb-root-agent-column-head">
                        <strong>静态配置</strong>
                        <span>{profile.profile_id}</span>
                      </div>
                      <div className="wb-key-value-list">
                        <span>Archetype</span>
                        <strong>{staticConfig.base_archetype || "-"}</strong>
                        <span>Model</span>
                        <strong>{staticConfig.model_alias || "-"}</strong>
                        <span>Permission Preset</span>
                        <strong>{staticConfig.permission_preset || staticConfig.tool_profile || "-"}</strong>
                        <span>Runtime</span>
                        <strong>{runtimeKinds.join(", ") || "-"}</strong>
                      </div>
                      <div className="wb-root-agent-token-stack">
                        <div>
                          <p className="wb-card-label">默认工具组</p>
                          <div className="wb-chip-row">
                            {defaultToolGroups.length > 0 ? (
                              defaultToolGroups.map((toolGroup) => (
                                <span key={toolGroup} className="wb-chip">
                                  {toolGroup}
                                </span>
                              ))
                            ) : (
                              <span className="wb-inline-note">未标记默认工具组</span>
                            )}
                          </div>
                        </div>
                        <div>
                          <p className="wb-card-label">Capabilities</p>
                          <div className="wb-chip-row">
                            {staticCapabilities.length > 0 ? (
                              staticCapabilities.map((capability) => (
                                <span key={capability} className="wb-chip is-warning">
                                  {capability}
                                </span>
                              ))
                            ) : (
                              <span className="wb-inline-note">未标记静态能力</span>
                            )}
                          </div>
                        </div>
                      </div>
                    </section>
                    <section className="wb-root-agent-column">
                      <div className="wb-root-agent-column-head">
                        <strong>动态上下文</strong>
                        <span>
                          {dynamicContext.updated_at
                            ? formatDateTime(dynamicContext.updated_at)
                            : "未记录"}
                        </span>
                      </div>
                      <div className="wb-root-agent-context-grid">
                        <div className="wb-detail-block">
                          <span className="wb-card-label">Active</span>
                          <strong>{dynamicContext.active_work_count ?? 0}</strong>
                          <p>Running {dynamicContext.running_work_count ?? 0}</p>
                        </div>
                        <div className="wb-detail-block">
                          <span className="wb-card-label">Attention</span>
                          <strong>{dynamicContext.attention_work_count ?? 0}</strong>
                          <p>Target {dynamicContext.latest_target_kind || "-"}</p>
                        </div>
                        <div className="wb-detail-block">
                          <span className="wb-card-label">工具分配</span>
                          <strong>
                            {dynamicContext.current_tool_resolution_mode || "legacy"}
                          </strong>
                          <p>
                            mounted {(dynamicContext.current_mounted_tools ?? []).length} /
                            blocked {(dynamicContext.current_blocked_tools ?? []).length}
                          </p>
                        </div>
                      </div>
                      <div className="wb-key-value-list">
                        <span>Context</span>
                        <strong>
                          {dynamicContext.active_project_id || "-"} /{" "}
                          {dynamicContext.active_workspace_id || "-"}
                        </strong>
                        <span>Latest Work</span>
                        <strong>
                          {dynamicContext.latest_work_title ||
                            dynamicContext.latest_work_id ||
                            "-"}
                        </strong>
                        <span>Latest Task</span>
                        <strong>{dynamicContext.latest_task_id || "-"}</strong>
                        <span>Discovery</span>
                        <strong>
                          {(dynamicContext.current_discovery_entrypoints ?? []).join(", ") ||
                            "none"}
                        </strong>
                      </div>
                      <div>
                        <p className="wb-card-label">当前选中工具</p>
                        <div className="wb-chip-row">
                          {currentSelectedTools.length > 0 ? (
                            currentSelectedTools.map((tool) => (
                              <span key={tool} className="wb-chip">
                                {tool}
                              </span>
                            ))
                          ) : (
                            <span className="wb-inline-note">
                              当前没有记录 selected tools
                            </span>
                          )}
                        </div>
                      </div>
                      {(dynamicContext.current_blocked_tools ?? []).length > 0 ? (
                        <div className="event-list">
                          {dynamicContext.current_blocked_tools!
                            .slice(0, 2)
                            .map((tool) => (
                              <div
                                key={`${profile.profile_id}-${tool.tool_name}`}
                                className="event-item"
                              >
                                <div>
                                  <strong>{tool.tool_name}</strong>
                                  <p>{tool.summary || tool.reason_code || tool.status}</p>
                                </div>
                              </div>
                            ))}
                        </div>
                      ) : null}
                    </section>
                  </div>
                  {profile.capabilities.length > 0 ? (
                    <div className="wb-root-agent-cap-row">
                      <span className="wb-card-label">资源能力</span>
                      <div className="wb-chip-row">
                        {profile.capabilities.map((capability) => (
                          <span key={capability.capability_id} className="wb-chip">
                            {capability.label}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {profile.warnings.length > 0 ? (
                    <div className="event-list">
                      {profile.warnings.map((warning) => (
                        <div key={warning} className="event-item">
                          <div>
                            <strong>Warning</strong>
                            <p>{warning}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  <div className="action-row">
                    <button
                      type="button"
                      className="ghost-button"
                      onClick={onOpenDelegation}
                    >
                      查看委派链路
                    </button>
                    {dynamicContext.latest_task_id ? (
                      <Link
                        className="inline-link"
                        to={`/tasks/${dynamicContext.latest_task_id}`}
                      >
                        打开最近任务
                      </Link>
                    ) : null}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </article>

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Bundled Capability Pack</p>
            <h3>{capabilityPack.pack.pack_id}</h3>
          </div>
          <button
            type="button"
            className="secondary-button"
            onClick={onRefreshCapabilityPack}
            disabled={busyActionId === "capability.refresh"}
          >
            刷新能力包
          </button>
        </div>
        <div className="meta-grid">
          <span>Version {capabilityPack.pack.version}</span>
          <span>Tools {capabilityPack.pack.tools.length}</span>
          <span>Skills {capabilityPack.pack.skills.length}</span>
          <span>
            Bundled Worker Archetypes {capabilityPack.pack.worker_profiles.length}
          </span>
          <span>Fallback {capabilityPack.pack.fallback_toolset.join(", ") || "-"}</span>
        </div>
      </article>

      {capabilityPack.pack.worker_profiles.map((profile) => (
        <article key={profile.worker_type} className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">Bundled Worker Archetype</p>
              <h3>{profile.worker_type}</h3>
            </div>
            <span className="tone-chip neutral">
              Runtime {profile.runtime_kinds.join(", ")}
            </span>
          </div>
          <div className="meta-grid">
            <span>Capabilities {profile.capabilities.join(", ")}</span>
            <span>Model {profile.default_model_alias}</span>
            <span>Profile {profile.default_tool_profile}</span>
            <span>Groups {profile.default_tool_groups.join(", ")}</span>
          </div>
        </article>
      ))}

      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Bundled Tools</p>
            <h3>{capabilityPack.pack.tools.length}</h3>
          </div>
          <div className="wb-chip-row">
            <span className="wb-chip is-success">
              Core {capabilityPack.pack.tools.filter((t) => t.tier === "core").length}
            </span>
            <span className="wb-chip">
              Deferred {capabilityPack.pack.tools.filter((t) => t.tier !== "core").length}
            </span>
          </div>
        </div>
        <div className="event-list">
          {capabilityPack.pack.tools.map((tool) => {
            const tier = tool.tier;
            const sideEffect = tool.side_effect_level;
            return (
              <div key={tool.tool_name} className="event-item">
                <div>
                  <strong>{tool.tool_name}</strong>
                  <p>{tool.description || tool.tool_group}</p>
                  <p className="muted">
                    Entrypoints {tool.entrypoints.join(", ") || "-"} · Runtime{" "}
                    {tool.runtime_kinds.join(", ") || "-"}
                    {sideEffect ? ` · SideEffect ${sideEffect}` : ""}
                  </p>
                  {tool.availability_reason || tool.install_hint ? (
                    <p className="muted">
                      {tool.availability_reason || tool.install_hint}
                    </p>
                  ) : null}
                </div>
                <div style={{ display: "grid", gap: "0.25rem", justifyItems: "end" }}>
                  <span className={`tone-chip ${statusTone(tool.availability)}`}>
                    {tool.availability}
                  </span>
                  <span className={`wb-chip ${tier === "core" ? "is-success" : ""}`}>
                    {tier === "core" ? "Core" : "Deferred"}
                  </span>
                  <small>{tool.tags.join(", ") || tool.tool_profile}</small>
                </div>
              </div>
            );
          })}
        </div>
      </article>
    </section>
  );
}
