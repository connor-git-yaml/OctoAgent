import { Link } from "react-router-dom";
import type {
  ProjectOption,
  WorkerProfileItem,
  WorkerProfileRevisionItem,
  WorkProjectionItem,
  WorkspaceOption,
} from "../../types";

interface RuntimeToolEntry {
  tool_name: string;
  summary?: string | null;
  source_kind?: string | null;
  reason_code?: string | null;
  status?: string | null;
}

interface RuntimeCapabilityEntry {
  capability_id: string;
  label: string;
}

interface TemplateRuntimeRailProps {
  selectedRootAgentProfile: WorkerProfileItem | null;
  selectedRootAgentDynamicContext: WorkerProfileItem["dynamic_context"] | null;
  selectedRootAgentMountedTools: RuntimeToolEntry[];
  selectedRootAgentBlockedTools: RuntimeToolEntry[];
  selectedRootAgentDiscoveryEntrypoints: string[];
  selectedRootAgentCapabilities: RuntimeCapabilityEntry[];
  selectedRootAgentWarnings: string[];
  selectedRootAgentDisplayName: string;
  selectedRootAgentWorks: WorkProjectionItem[];
  rootAgentSpawnObjective: string;
  rootAgentRevisionLoading: boolean;
  rootAgentRevisionError: string;
  rootAgentRevisions: WorkerProfileRevisionItem[];
  busyActionId: string | null;
  availableProjects: ProjectOption[];
  availableWorkspaces: WorkspaceOption[];
  selectorProjectId: string;
  selectorWorkspaceId: string;
  onSpawnObjectiveChange: (value: string) => void;
  onSpawnFromRootAgent: (profileId: string) => void;
  onExtractRootAgentFromWork: (work: WorkProjectionItem) => void;
  formatDateTime: (value: string) => string;
  formatWorkerType: (workerType: string) => string;
  formatToolToken: (toolName: string, toolLabels: Record<string, string>) => string;
  findProjectName: (projects: ProjectOption[], projectId: string) => string;
  findWorkspaceName: (workspaces: WorkspaceOption[], workspaceId: string) => string;
  toolLabelByName: Record<string, string>;
}

export default function TemplateRuntimeRail({
  selectedRootAgentProfile,
  selectedRootAgentDynamicContext,
  selectedRootAgentMountedTools,
  selectedRootAgentBlockedTools,
  selectedRootAgentDiscoveryEntrypoints,
  selectedRootAgentCapabilities,
  selectedRootAgentWarnings,
  selectedRootAgentDisplayName,
  selectedRootAgentWorks,
  rootAgentSpawnObjective,
  rootAgentRevisionLoading,
  rootAgentRevisionError,
  rootAgentRevisions,
  busyActionId,
  availableProjects,
  availableWorkspaces,
  selectorProjectId,
  selectorWorkspaceId,
  onSpawnObjectiveChange,
  onSpawnFromRootAgent,
  onExtractRootAgentFromWork,
  formatDateTime,
  formatWorkerType,
  formatToolToken,
  findProjectName,
  findWorkspaceName,
  toolLabelByName,
}: TemplateRuntimeRailProps) {
  return (
    <aside className="wb-root-agent-runtime-rail">
      <article className="wb-root-agent-runtime-panel">
        <div className="wb-root-agent-card-head">
          <div>
            <p className="wb-card-label">当前运行状态</p>
            <strong>
              {selectedRootAgentProfile
                ? "这个模板最近是怎么工作的"
                : "先从左侧选一个模板，或先保存当前草稿"}
            </strong>
          </div>
          <span className="wb-chip">
            {selectedRootAgentDynamicContext?.updated_at
              ? formatDateTime(selectedRootAgentDynamicContext.updated_at)
              : "尚未刷新"}
          </span>
        </div>
        {selectedRootAgentProfile ? (
          <>
            <div className="wb-root-agent-context-grid">
              <div className="wb-detail-block">
                <span className="wb-card-label">活跃任务</span>
                <strong>{selectedRootAgentDynamicContext?.active_work_count ?? 0}</strong>
                <p>运行中 {selectedRootAgentDynamicContext?.running_work_count ?? 0}</p>
              </div>
              <div className="wb-detail-block">
                <span className="wb-card-label">需要处理</span>
                <strong>{selectedRootAgentDynamicContext?.attention_work_count ?? 0}</strong>
                <p>Target {selectedRootAgentDynamicContext?.latest_target_kind || "-"}</p>
              </div>
              <div className="wb-detail-block">
                <span className="wb-card-label">工具分配</span>
                <strong>{selectedRootAgentDynamicContext?.current_tool_resolution_mode || "legacy"}</strong>
                <p>
                  mounted {selectedRootAgentMountedTools.length} / blocked{" "}
                  {selectedRootAgentBlockedTools.length}
                </p>
              </div>
            </div>
            <div className="wb-key-value-list">
              <span>项目 / 工作区</span>
              <strong>
                {findProjectName(
                  availableProjects,
                  selectedRootAgentDynamicContext?.active_project_id ||
                    selectedRootAgentProfile.project_id ||
                    selectorProjectId
                )}{" "}
                /{" "}
                {findWorkspaceName(
                  availableWorkspaces,
                  selectedRootAgentDynamicContext?.active_workspace_id || selectorWorkspaceId
                )}
              </strong>
              <span>快照</span>
              <strong>{selectedRootAgentProfile.effective_snapshot_id || "-"}</strong>
              <span>最近任务</span>
              <strong>
                {selectedRootAgentDynamicContext?.latest_work_title ||
                  selectedRootAgentDynamicContext?.latest_work_id ||
                  "-"}
              </strong>
              <span>最近 Task</span>
              <strong>{selectedRootAgentDynamicContext?.latest_task_id || "-"}</strong>
            </div>
            <div className="wb-root-agent-token-stack">
              <div>
                <p className="wb-card-label">当前工具宇宙</p>
                <div className="wb-chip-row">
                  {(selectedRootAgentDynamicContext?.current_selected_tools ?? []).length > 0 ? (
                    selectedRootAgentDynamicContext!.current_selected_tools.map((tool) => (
                      <span key={tool} className="wb-chip">
                        {formatToolToken(tool, toolLabelByName)}
                      </span>
                    ))
                  ) : (
                    <span className="wb-inline-note">还没有记录 selected tools。</span>
                  )}
                </div>
              </div>
              <div>
                <p className="wb-card-label">工具发现入口</p>
                <div className="wb-chip-row">
                  {selectedRootAgentDiscoveryEntrypoints.length > 0 ? (
                    selectedRootAgentDiscoveryEntrypoints.map((tool) => (
                      <span key={tool} className="wb-chip">
                        {formatToolToken(tool, toolLabelByName)}
                      </span>
                    ))
                  ) : (
                    <span className="wb-inline-note">当前没有额外入口提示。</span>
                  )}
                </div>
              </div>
              {selectedRootAgentMountedTools.length > 0 ? (
                <div>
                  <p className="wb-card-label">已挂载工具</p>
                  <div className="wb-note-stack">
                    {selectedRootAgentMountedTools.slice(0, 4).map((tool) => (
                      <div key={`mounted-${tool.tool_name}`} className="wb-note">
                        <strong>{formatToolToken(tool.tool_name, toolLabelByName)}</strong>
                        <span>{tool.summary || tool.source_kind || "-"}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
              {selectedRootAgentBlockedTools.length > 0 ? (
                <div>
                  <p className="wb-card-label">当前被阻塞的工具</p>
                  <div className="wb-note-stack">
                    {selectedRootAgentBlockedTools.slice(0, 4).map((tool) => (
                      <div key={`blocked-${tool.tool_name}`} className="wb-note">
                        <strong>{formatToolToken(tool.tool_name, toolLabelByName)}</strong>
                        <span>{tool.summary || tool.reason_code || tool.status || "-"}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
              {selectedRootAgentCapabilities.length > 0 ? (
                <div>
                  <p className="wb-card-label">控制面能力</p>
                  <div className="wb-chip-row">
                    {selectedRootAgentCapabilities.map((capability) => (
                      <span key={capability.capability_id} className="wb-chip">
                        {capability.label}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
            {selectedRootAgentWarnings.length > 0 ? (
              <div className="wb-note-stack">
                {selectedRootAgentWarnings.map((warning) => (
                  <div key={warning} className="wb-note">
                    <strong>提醒</strong>
                    <span>{warning}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </>
        ) : (
          <div className="wb-empty-state">
            <strong>还没有运行态视图</strong>
            <span>先选一个已有模板，或者保存现在的草稿，再回来观察运行状态。</span>
          </div>
        )}
      </article>

      <article className="wb-root-agent-runtime-panel">
        <div className="wb-root-agent-card-head">
          <div>
            <p className="wb-card-label">新建任务</p>
            <strong>按这个模板启动一次新的工作</strong>
          </div>
          <Link className="wb-button wb-button-tertiary" to="/chat">
            去 Chat 观察执行
          </Link>
        </div>
        <label className="wb-field">
          <span>任务目标</span>
          <textarea
            className="wb-textarea-prose"
            value={rootAgentSpawnObjective}
            onChange={(event) => onSpawnObjectiveChange(event.target.value)}
            placeholder="例如：检查家庭 NAS 备份是否异常，并给出今天的处理建议。"
          />
        </label>
        <div className="wb-inline-actions wb-inline-actions-wrap">
          <button
            type="button"
            className="wb-button wb-button-primary"
            disabled={!selectedRootAgentProfile || busyActionId === "worker.spawn_from_profile"}
            onClick={() =>
              selectedRootAgentProfile ? onSpawnFromRootAgent(selectedRootAgentProfile.profile_id) : undefined
            }
          >
            用这个模板启动
          </button>
          <Link className="wb-button wb-button-secondary" to="/work">
            去看 Runtime Work
          </Link>
        </div>
      </article>

      <article className="wb-root-agent-runtime-panel">
        <div className="wb-root-agent-card-head">
          <div>
            <p className="wb-card-label">版本记录</p>
            <strong>这里看每次发布后的版本和快照</strong>
          </div>
          <span className="wb-chip">{selectedRootAgentDisplayName || "未选中模板"}</span>
        </div>
        {rootAgentRevisionLoading ? (
          <div className="wb-empty-state">
            <strong>正在加载版本记录</strong>
            <span>稍等一下，马上就好。</span>
          </div>
        ) : rootAgentRevisionError ? (
          <div className="wb-inline-banner is-error">
            <strong>版本记录加载失败</strong>
            <span>{rootAgentRevisionError}</span>
          </div>
        ) : rootAgentRevisions.length === 0 ? (
          <div className="wb-empty-state">
            <strong>还没有版本记录</strong>
            <span>保存草稿后点击“发布版本”，这里就会出现可追踪版本。</span>
          </div>
        ) : (
          <div className="wb-root-agent-revision-list">
            {rootAgentRevisions.map((revision) => (
              <div key={revision.revision_id} className="wb-root-agent-runtime-item">
                <div className="wb-root-agent-library-head">
                  <div>
                    <strong>版本 {revision.revision}</strong>
                    <span>{revision.change_summary || "未填写变更摘要"}</span>
                  </div>
                  <span className="wb-chip">{revision.created_by || "system"}</span>
                </div>
                <div className="wb-root-agent-library-meta">
                  <span>
                    {revision.created_at ? formatDateTime(revision.created_at) : "未记录时间"}
                  </span>
                  <span>{String(revision.snapshot_payload.profile_id || revision.revision_id)}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </article>

      <article className="wb-root-agent-runtime-panel">
        <div className="wb-root-agent-card-head">
          <div>
            <p className="wb-card-label">最近任务</p>
            <strong>这里显示最近哪些任务使用了这个模板</strong>
          </div>
          <span className="wb-chip">{selectedRootAgentWorks.length} 个 Work</span>
        </div>
        {selectedRootAgentWorks.length === 0 ? (
          <div className="wb-empty-state">
            <strong>当前还没有关联 Work</strong>
            <span>发布后从上面的“新建任务”区域启动一次，这里就会显示最近任务。</span>
          </div>
        ) : (
          <div className="wb-root-agent-work-list">
            {selectedRootAgentWorks.map((work) => (
              <div key={work.work_id} className="wb-root-agent-runtime-item">
                <div className="wb-root-agent-library-head">
                  <div>
                    <strong>{work.title || work.work_id}</strong>
                    <span>{work.route_reason || formatWorkerType(work.selected_worker_type)}</span>
                  </div>
                  <span className={`wb-status-pill is-${work.status}`}>{work.status}</span>
                </div>
                <div className="wb-key-value-list">
                  <span>Agent / Profile</span>
                  <strong>
                    {work.agent_profile_id || "-"} / {work.requested_worker_profile_id || "回退到 archetype"}
                  </strong>
                  <span>使用的模板</span>
                  <strong>{work.requested_worker_profile_id || "回退到 archetype"}</strong>
                  <span>版本 / 快照</span>
                  <strong>
                    {work.requested_worker_profile_version || "-"} /{" "}
                    {work.effective_worker_snapshot_id || "-"}
                  </strong>
                  <span>Worker / Target</span>
                  <strong>
                    {formatWorkerType(work.selected_worker_type)} / {work.target_kind || "-"}
                  </strong>
                  <span>工具分配</span>
                  <strong>{work.tool_resolution_mode || "legacy"}</strong>
                </div>
                <div className="wb-chip-row">
                  {work.selected_tools.length > 0 ? (
                    work.selected_tools.map((tool) => (
                      <span key={tool} className="wb-chip">
                        {formatToolToken(tool, toolLabelByName)}
                      </span>
                    ))
                  ) : (
                    <span className="wb-inline-note">当前 work 没有 selected tools 记录。</span>
                  )}
                </div>
                {work.blocked_tools && work.blocked_tools.length > 0 ? (
                  <div className="wb-note-stack">
                    {work.blocked_tools.slice(0, 2).map((tool) => (
                      <div key={`lineage-blocked-${work.work_id}-${tool.tool_name}`} className="wb-note">
                        <strong>{formatToolToken(tool.tool_name, toolLabelByName)}</strong>
                        <span>{tool.summary || tool.reason_code || tool.status}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
                <div className="wb-inline-actions wb-inline-actions-wrap">
                  <button
                    type="button"
                    className="wb-button wb-button-secondary"
                    onClick={() => onExtractRootAgentFromWork(work)}
                    disabled={busyActionId === "worker.extract_profile_from_runtime"}
                  >
                    从这个运行结果提炼模板
                  </button>
                  <Link className="wb-button wb-button-tertiary" to="/work">
                    去 Work 看详情
                  </Link>
                </div>
              </div>
            ))}
          </div>
        )}
      </article>
    </aside>
  );
}
