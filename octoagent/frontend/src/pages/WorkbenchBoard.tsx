import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import type {
  ControlPlaneCapability,
  WorkProjectionItem,
  WorkerPlanProposal,
} from "../types";
import { formatDateTime, formatSupportStatus } from "../workbench/utils";

const ACTIVE_WORK_STATUSES = new Set(["created", "assigned", "running", "escalated"]);
const WAITING_WORK_STATUSES = new Set(["waiting_approval", "waiting_input", "paused"]);
const DONE_WORK_STATUSES = new Set(["succeeded", "merged", "cancelled", "failed", "timed_out"]);

const WORK_STATUS_LABELS: Record<string, string> = {
  created: "已创建",
  assigned: "已分派",
  running: "执行中",
  escalated: "需升级处理",
  waiting_approval: "等待审批",
  waiting_input: "等待输入",
  paused: "已暂停",
  succeeded: "已完成",
  merged: "已合并",
  cancelled: "已取消",
  failed: "已失败",
  timed_out: "已超时",
};

const WORKER_TYPE_LABELS: Record<string, string> = {
  general: "通用 Worker",
  ops: "Ops Worker",
  research: "Research Worker",
  dev: "Dev Worker",
};

const TARGET_KIND_LABELS: Record<string, string> = {
  worker: "Worker",
  subagent: "Subagent",
  acp_runtime: "ACP Runtime",
  graph_agent: "Graph Agent",
};

function bucketWorks(works: WorkProjectionItem[]) {
  return {
    active: works.filter((item) => ACTIVE_WORK_STATUSES.has(item.status)),
    waiting: works.filter((item) => WAITING_WORK_STATUSES.has(item.status)),
    done: works.filter((item) => DONE_WORK_STATUSES.has(item.status)),
  };
}

function getCapability(work: WorkProjectionItem, actionId: string) {
  return work.capabilities.find((item) => item.action_id === actionId) ?? null;
}

function workPriority(work: WorkProjectionItem): number {
  if (work.status === "waiting_approval" || work.status === "waiting_input") {
    return 5;
  }
  if (work.status === "paused" || work.status === "escalated") {
    return 4;
  }
  if (work.status === "running" || work.status === "assigned" || work.status === "created") {
    return 3;
  }
  if (work.status === "failed" || work.status === "timed_out") {
    return 2;
  }
  return 1;
}

function sortWorks(works: WorkProjectionItem[]): WorkProjectionItem[] {
  return [...works].sort((left, right) => {
    const priorityDiff = workPriority(right) - workPriority(left);
    if (priorityDiff !== 0) {
      return priorityDiff;
    }
    return (right.updated_at ?? "").localeCompare(left.updated_at ?? "");
  });
}

function formatWorkStatus(status: string): string {
  return WORK_STATUS_LABELS[status] ?? status;
}

function formatWorkerType(workerType: string): string {
  return WORKER_TYPE_LABELS[workerType] ?? workerType;
}

function formatTargetKind(targetKind: string): string {
  return TARGET_KIND_LABELS[targetKind] ?? targetKind;
}

function formatWorkSummary(work: WorkProjectionItem): string {
  if (work.status === "waiting_approval") {
    return "这条工作停在审批环节，先确认是否继续执行。";
  }
  if (work.status === "waiting_input") {
    return "系统正在等你补上下文或下一步要求。";
  }
  if (work.status === "paused") {
    return "当前已暂停，通常需要你恢复或改方向。";
  }
  if (work.status === "escalated") {
    return "系统已经把它标成升级处理，优先检查原因。";
  }
  if (work.status === "running" || work.status === "assigned" || work.status === "created") {
    return "正在推进中，重点看拆分、子工作、Worker 方案和最近更新时间。";
  }
  if (work.status === "failed" || work.status === "timed_out") {
    return "这条工作没有顺利完成，通常需要重试或调整方案。";
  }
  return "这条工作已经收尾，可回看结果或做清理。";
}

function formatRuntimeHint(work: WorkProjectionItem): string {
  const runtimeStatus = String(work.runtime_summary.runtime_status ?? "").trim();
  if (runtimeStatus) {
    return runtimeStatus;
  }
  const requestedToolProfile = String(work.runtime_summary.requested_tool_profile ?? "").trim();
  if (requestedToolProfile) {
    return `请求的 tool profile: ${requestedToolProfile}`;
  }
  const requestedWorkerType = String(work.runtime_summary.requested_worker_type ?? "").trim();
  if (requestedWorkerType) {
    return `请求的 worker 类型: ${requestedWorkerType}`;
  }
  return "运行态摘要暂未提供额外细节。";
}

function disabledCapabilityReasons(capabilities: ControlPlaneCapability[]): string[] {
  return capabilities
    .filter((capability) => !capability.enabled && capability.reason)
    .map((capability) => capability.reason)
    .filter((reason, index, all) => all.indexOf(reason) === index);
}

function WorkerPlanPanel({
  plan,
  busyActionId,
  onApply,
}: {
  plan: WorkerPlanProposal;
  busyActionId: string | null;
  onApply: () => Promise<void>;
}) {
  return (
    <div className="wb-panel">
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">Worker Review</p>
          <h3>{plan.summary || "已生成治理方案"}</h3>
        </div>
        <button
          type="button"
          className="wb-button wb-button-primary"
          disabled={busyActionId === "worker.apply"}
          onClick={() => void onApply()}
        >
          批准并执行
        </button>
      </div>

      <div className="wb-note-stack">
        <div className="wb-note">
          <strong>proposal</strong>
          <span>{plan.proposal_kind}</span>
        </div>
        {plan.warnings.map((warning) => (
          <div key={warning} className="wb-note">
            <strong>warning</strong>
            <span>{warning}</span>
          </div>
        ))}
        {plan.assignments.map((assignment) => (
          <div
            key={`${assignment.worker_type}-${assignment.title}-${assignment.objective}`}
            className="wb-note"
          >
            <strong>
              {assignment.title || assignment.worker_type} · {assignment.tool_profile}
            </strong>
            <span>
              {assignment.target_kind} / {assignment.objective}
            </span>
            {assignment.reason ? <small>{assignment.reason}</small> : null}
          </div>
        ))}
        {plan.merge_candidate_ids.length > 0 ? (
          <div className="wb-note">
            <strong>merge candidates</strong>
            <span>{plan.merge_candidate_ids.join(", ")}</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function WorkSection({
  title,
  summary,
  works,
  busyActionId,
  splitDrafts,
  workerPlans,
  onSplitDraftChange,
  onAction,
}: {
  title: string;
  summary: string;
  works: WorkProjectionItem[];
  busyActionId: string | null;
  splitDrafts: Record<string, string>;
  workerPlans: Record<string, WorkerPlanProposal>;
  onSplitDraftChange: (workId: string, nextValue: string) => void;
  onAction: (work: WorkProjectionItem, actionId: string) => Promise<void>;
}) {
  return (
    <section className="wb-panel">
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">{title}</p>
          <h3>{works.length} 条</h3>
        </div>
        <span className="wb-panel-copy">{summary}</span>
      </div>

      {works.length === 0 ? (
        <div className="wb-empty-state">
          <strong>当前没有内容</strong>
          <span>这一区域为空时，说明目前没有对应状态的工作。</span>
        </div>
      ) : (
        <div className="wb-work-list">
          {works.map((work) => {
            const visibleActions = work.capabilities.filter(
              (capability) =>
                capability.action_id !== "work.split" &&
                capability.action_id !== "worker.review" &&
                capability.enabled
            );
            const disabledReasons = disabledCapabilityReasons(work.capabilities);
            const canSplit = Boolean(getCapability(work, "work.split")?.enabled);
            const workerReviewCapability = getCapability(work, "worker.review");
            const workerPlan = workerPlans[work.work_id];
            const requestedToolProfile = String(
              work.runtime_summary.requested_tool_profile ?? ""
            ).trim();

            return (
              <article key={work.work_id} className="wb-work-card">
                <div className="wb-work-head">
                  <div>
                    <div className="wb-inline-meta">
                      <span className="wb-chip">{formatWorkerType(work.selected_worker_type)}</span>
                      <span className="wb-chip">{formatTargetKind(work.target_kind)}</span>
                      {work.parent_work_id ? (
                        <span className="wb-chip">Child Work</span>
                      ) : (
                        <span className="wb-chip">Root Work</span>
                      )}
                    </div>
                    <strong>{work.title}</strong>
                    <p>{formatWorkSummary(work)}</p>
                  </div>
                  <span className={`wb-status-pill is-${work.status}`}>
                    {formatWorkStatus(work.status)}
                  </span>
                </div>

                <div className="wb-chip-row">
                  <span className="wb-chip">Route {work.route_reason}</span>
                  <span className="wb-chip">Child {work.child_work_count}</span>
                  <span className="wb-chip">Tools {work.selected_tools.length}</span>
                  {requestedToolProfile ? (
                    <span className="wb-chip">{requestedToolProfile}</span>
                  ) : null}
                  {work.merge_ready ? (
                    <span className="wb-chip is-success">可合并</span>
                  ) : null}
                  <span className="wb-chip">更新于 {formatDateTime(work.updated_at)}</span>
                </div>

                <div className="wb-work-detail-grid">
                  <div className="wb-detail-block">
                    <p className="wb-card-label">任务上下文</p>
                    <div className="wb-key-value-list">
                      <span>Task</span>
                      <Link className="wb-text-link" to={`/tasks/${work.task_id}`}>
                        {work.task_id}
                      </Link>
                      <span>Work ID</span>
                      <strong>{work.work_id}</strong>
                      <span>Owner</span>
                      <strong>{work.owner_id || "未标记"}</strong>
                    </div>
                  </div>

                  <div className="wb-detail-block">
                    <p className="wb-card-label">运行提示</p>
                    <p>{formatRuntimeHint(work)}</p>
                    {work.selected_tools.length > 0 ? (
                      <div className="wb-chip-row">
                        {work.selected_tools.slice(0, 4).map((tool) => (
                          <span key={tool} className="wb-chip">
                            {tool}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </div>

                {disabledReasons.length > 0 ? (
                  <div className="wb-inline-banner is-muted">
                    <strong>当前限制</strong>
                    <span>{disabledReasons.join("；")}</span>
                  </div>
                ) : null}

                <div className="wb-inline-actions wb-inline-actions-wrap">
                  <Link className="wb-button wb-button-tertiary" to={`/tasks/${work.task_id}`}>
                    打开任务
                  </Link>
                  {visibleActions.map((capability) => (
                    <button
                      key={capability.action_id}
                      type="button"
                      className="wb-button wb-button-secondary"
                      disabled={busyActionId === capability.action_id}
                      onClick={() => void onAction(work, capability.action_id)}
                    >
                      {capability.label} · {formatSupportStatus(capability.support_status)}
                    </button>
                  ))}
                  {workerReviewCapability ? (
                    <button
                      type="button"
                      className="wb-button wb-button-secondary"
                      disabled={!workerReviewCapability.enabled || busyActionId === "worker.review"}
                      onClick={() => void onAction(work, "worker.review")}
                    >
                      评审 Worker 方案
                    </button>
                  ) : null}
                </div>

                {canSplit ? (
                  <div className="wb-split-form">
                    <label className="wb-field">
                      <span>拆分成子目标</span>
                      <textarea
                        rows={3}
                        value={splitDrafts[work.work_id] ?? ""}
                        placeholder={"每行一个 objective\n例如：整理依赖\n补测试\n输出摘要"}
                        onChange={(event) =>
                          onSplitDraftChange(work.work_id, event.target.value)
                        }
                      />
                    </label>
                    <button
                      type="button"
                      className="wb-button wb-button-primary"
                      disabled={
                        busyActionId === "work.split" ||
                        !(splitDrafts[work.work_id] ?? "").trim()
                      }
                      onClick={() => void onAction(work, "work.split")}
                    >
                      创建 child works
                    </button>
                  </div>
                ) : null}

                {workerPlan && workerReviewCapability?.enabled ? (
                  <WorkerPlanPanel
                    plan={workerPlan}
                    busyActionId={busyActionId}
                    onApply={() => onAction(work, "worker.apply")}
                  />
                ) : null}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

export default function WorkbenchBoard() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const works = snapshot!.resources.delegation.works;
  const pendingTotal = snapshot!.resources.sessions.operator_summary?.total_pending ?? 0;
  const buckets = bucketWorks(works);
  const sortedWorks = sortWorks(works);
  const mergeReadyCount = works.filter((work) => work.merge_ready).length;
  const workerTypeCounts = works.reduce<Record<string, number>>((accumulator, work) => {
    accumulator[work.selected_worker_type] =
      (accumulator[work.selected_worker_type] ?? 0) + 1;
    return accumulator;
  }, {});
  const workerTypeEntries = Object.entries(workerTypeCounts).sort(
    (left, right) => right[1] - left[1]
  );
  const priorityWorks = sortedWorks.filter((work) => workPriority(work) >= 4).slice(0, 3);
  const [splitDrafts, setSplitDrafts] = useState<Record<string, string>>({});
  const [workerPlans, setWorkerPlans] = useState<Record<string, WorkerPlanProposal>>({});

  useEffect(() => {
    const reviewableWorkIds = new Set(
      works
        .filter((work) => Boolean(getCapability(work, "worker.review")?.enabled))
        .map((work) => work.work_id)
    );
    setWorkerPlans((state) => {
      const next: Record<string, WorkerPlanProposal> = {};
      let changed = false;
      Object.entries(state).forEach(([workId, plan]) => {
        if (reviewableWorkIds.has(workId)) {
          next[workId] = plan;
          return;
        }
        changed = true;
      });
      return changed ? next : state;
    });
  }, [works]);

  const heroTitle =
    buckets.waiting.length > 0
      ? `先处理 ${buckets.waiting.length} 条等待中的工作`
      : buckets.active.length > 0
        ? `${buckets.active.length} 条工作正在推进`
        : works.length > 0
          ? "当前没有阻塞中的工作"
          : "还没有运行中的工作";
  const heroSummary =
    works.length > 0
      ? "先看等待审批或输入的工作，再看运行中的分支、Worker 治理方案和可合并项。"
      : "当你在 Chat 发起请求后，新 work 会出现在这里；之后可以继续拆分、重试和收尾。";

  async function handleWorkAction(work: WorkProjectionItem, actionId: string) {
    if (actionId === "work.split") {
      const draft = splitDrafts[work.work_id]?.trim();
      if (!draft) {
        return;
      }
      const objectives = draft
        .split("\n")
        .map((item) => item.trim())
        .filter(Boolean);
      const result = await submitAction(actionId, { work_id: work.work_id, objectives });
      if (result) {
        setSplitDrafts((state) => ({ ...state, [work.work_id]: "" }));
      }
      return;
    }
    if (actionId === "worker.review") {
      if (!getCapability(work, "worker.review")?.enabled) {
        return;
      }
      const result = await submitAction(actionId, {
        work_id: work.work_id,
        objective: work.title,
      });
      const plan = result?.data.plan;
      if (plan && typeof plan === "object" && !Array.isArray(plan)) {
        setWorkerPlans((state) => ({
          ...state,
          [work.work_id]: plan as WorkerPlanProposal,
        }));
      }
      return;
    }
    if (actionId === "worker.apply") {
      if (!getCapability(work, "worker.review")?.enabled) {
        return;
      }
      const plan = workerPlans[work.work_id];
      if (!plan) {
        return;
      }
      const result = await submitAction(actionId, { work_id: work.work_id, plan });
      if (result) {
        setWorkerPlans((state) => {
          const next = { ...state };
          delete next[work.work_id];
          return next;
        });
      }
      return;
    }
    await submitAction(actionId, { work_id: work.work_id });
  }

  function updateSplitDraft(workId: string, nextValue: string) {
    setSplitDrafts((state) => ({ ...state, [workId]: nextValue }));
  }

  return (
    <div className="wb-page">
      <section className="wb-hero wb-hero-work">
        <div className="wb-hero-copy">
          <p className="wb-kicker">Work</p>
          <h1>{heroTitle}</h1>
          <p>{heroSummary}</p>
          <div className="wb-chip-row">
            {workerTypeEntries.slice(0, 3).map(([workerType, count]) => (
              <span key={workerType} className="wb-chip">
                {formatWorkerType(workerType)} {count}
              </span>
            ))}
            {mergeReadyCount > 0 ? (
              <span className="wb-chip is-success">可合并 {mergeReadyCount}</span>
            ) : null}
            {pendingTotal > 0 ? (
              <span className="wb-chip is-warning">待确认 {pendingTotal}</span>
            ) : null}
          </div>
        </div>

        <div className="wb-hero-insights">
          <article className="wb-hero-metric">
            <p className="wb-card-label">进行中</p>
            <strong>{buckets.active.length}</strong>
            <span>包含 created / assigned / running / escalated</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">等待处理</p>
            <strong>{buckets.waiting.length}</strong>
            <span>审批、输入或暂停都会集中在这里</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">已结束</p>
            <strong>{buckets.done.length}</strong>
            <span>成功、失败、取消和超时都会留痕</span>
          </article>
        </div>
      </section>

      <div className="wb-split">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">现在最该看</p>
              <h3>优先处理阻塞或升级项</h3>
            </div>
            <div className="wb-inline-actions">
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={() => void submitAction("work.refresh", {})}
                disabled={busyActionId === "work.refresh"}
              >
                刷新 Work
              </button>
              <Link className="wb-button wb-button-primary" to="/chat">
                去 Chat 发起新任务
              </Link>
            </div>
          </div>

          {priorityWorks.length === 0 ? (
            <div className="wb-empty-state">
              <strong>没有明显阻塞项</strong>
              <span>如果想继续推进，可以去 Chat 发起新任务，或检查运行中的工作。</span>
            </div>
          ) : (
            <div className="wb-priority-list">
              {priorityWorks.map((work) => (
                <Link
                  key={work.work_id}
                  className="wb-priority-card"
                  to={`/tasks/${work.task_id}`}
                >
                  <div>
                    <strong>{work.title}</strong>
                    <p>{formatWorkSummary(work)}</p>
                  </div>
                  <div className="wb-list-meta">
                    <span className={`wb-status-pill is-${work.status}`}>
                      {formatWorkStatus(work.status)}
                    </span>
                    <small>{formatDateTime(work.updated_at)}</small>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </section>

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">分布概览</p>
              <h3>先按角色理解当前负载</h3>
            </div>
          </div>

          <div className="wb-stat-grid">
            <article className="wb-note">
              <strong>Worker 类型</strong>
              <div className="wb-chip-row">
                {workerTypeEntries.length > 0 ? (
                  workerTypeEntries.map(([workerType, count]) => (
                    <span key={workerType} className="wb-chip">
                      {formatWorkerType(workerType)} {count}
                    </span>
                  ))
                ) : (
                  <span>当前没有 work。</span>
                )}
              </div>
            </article>

            <article className="wb-note">
              <strong>子工作与合并</strong>
              <span>
                共 {works.reduce((sum, work) => sum + work.child_work_count, 0)} 个 child work，
                其中 {mergeReadyCount} 条已满足合并条件。
              </span>
            </article>

            <article className="wb-note">
              <strong>阅读建议</strong>
              <span>
                先看等待区，再看运行区；如果某条 work 支持 Worker Review，优先先看治理方案再执行。
              </span>
            </article>
          </div>
        </section>
      </div>

      <div className="wb-section-stack">
        <WorkSection
          title="等待处理"
          summary="审批、补充输入和暂停的工作都应该优先看。"
          works={sortWorks(buckets.waiting)}
          busyActionId={busyActionId}
          splitDrafts={splitDrafts}
          workerPlans={workerPlans}
          onSplitDraftChange={updateSplitDraft}
          onAction={handleWorkAction}
        />

        <WorkSection
          title="进行中"
          summary="这里看执行中的主干、已拆出的分支和待审批的 Worker 方案。"
          works={sortWorks(buckets.active)}
          busyActionId={busyActionId}
          splitDrafts={splitDrafts}
          workerPlans={workerPlans}
          onSplitDraftChange={updateSplitDraft}
          onAction={handleWorkAction}
        />

        <WorkSection
          title="已结束"
          summary="这里保留历史结果，方便回看、重试和清理。"
          works={sortWorks(buckets.done)}
          busyActionId={busyActionId}
          splitDrafts={splitDrafts}
          workerPlans={workerPlans}
          onSplitDraftChange={updateSplitDraft}
          onAction={handleWorkAction}
        />
      </div>
    </div>
  );
}
