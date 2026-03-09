import { useState } from "react";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import type { WorkProjectionItem } from "../types";
import { formatDateTime, formatSupportStatus } from "../workbench/utils";

const ACTIVE_WORK_STATUSES = new Set(["created", "assigned", "running", "escalated"]);
const WAITING_WORK_STATUSES = new Set(["waiting_approval", "waiting_input", "paused"]);
const DONE_WORK_STATUSES = new Set(["succeeded", "merged", "cancelled", "failed", "timed_out"]);

function bucketWorks(works: WorkProjectionItem[]) {
  return {
    active: works.filter((item) => ACTIVE_WORK_STATUSES.has(item.status)),
    waiting: works.filter((item) => WAITING_WORK_STATUSES.has(item.status)),
    done: works.filter((item) => DONE_WORK_STATUSES.has(item.status)),
  };
}

export default function WorkbenchBoard() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const works = snapshot!.resources.delegation.works;
  const buckets = bucketWorks(works);
  const [splitDrafts, setSplitDrafts] = useState<Record<string, string>>({});

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
    await submitAction(actionId, { work_id: work.work_id });
  }

  return (
    <div className="wb-page">
      <section className="wb-hero wb-hero-compact">
        <div>
          <p className="wb-kicker">Work</p>
          <h1>把 session、work 和 child work 放到一张板上</h1>
          <p>这里直接吃 `DelegationPlaneDocument`，不再要求你跳进 operator 术语里找状态。</p>
        </div>
      </section>

      <div className="wb-card-grid wb-card-grid-3">
        <article className="wb-card">
          <p className="wb-card-label">进行中</p>
          <strong>{buckets.active.length}</strong>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">等待确认 / 输入</p>
          <strong>{buckets.waiting.length}</strong>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">已结束</p>
          <strong>{buckets.done.length}</strong>
        </article>
      </div>

      <section className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">所有 Work</p>
            <h3>{works.length} 条 work 可见</h3>
          </div>
        </div>

        <div className="wb-work-list">
          {works.map((work) => (
            <article key={work.work_id} className="wb-work-card">
              <div className="wb-work-head">
                <div>
                  <strong>{work.title}</strong>
                  <p>
                    {work.selected_worker_type} / {work.target_kind} / {work.route_reason}
                  </p>
                </div>
                <span className={`wb-status-pill is-${work.status}`}>{work.status}</span>
              </div>

              <div className="wb-work-metrics">
                <span>child works {work.child_work_count}</span>
                <span>merge ready {work.merge_ready ? "yes" : "no"}</span>
                <span>updated {formatDateTime(work.updated_at)}</span>
              </div>

              <div className="wb-inline-actions wb-inline-actions-wrap">
                {work.capabilities.map((capability) => {
                  if (capability.action_id === "work.split") {
                    return null;
                  }
                  return (
                    <button
                      key={capability.action_id}
                      type="button"
                      className="wb-button wb-button-secondary"
                      disabled={!capability.enabled || busyActionId === capability.action_id}
                      onClick={() => void handleWorkAction(work, capability.action_id)}
                    >
                      {capability.label} · {formatSupportStatus(capability.support_status)}
                    </button>
                  );
                })}
              </div>

              {work.capabilities.some((item) => item.action_id === "work.split") ? (
                <div className="wb-split-form">
                  <label className="wb-field">
                    <span>拆分成子目标</span>
                    <textarea
                      rows={3}
                      value={splitDrafts[work.work_id] ?? ""}
                      placeholder={"每行一个 objective\n例如：整理依赖\n补测试\n输出摘要"}
                      onChange={(event) =>
                        setSplitDrafts((state) => ({
                          ...state,
                          [work.work_id]: event.target.value,
                        }))
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
                    onClick={() => void handleWorkAction(work, "work.split")}
                  >
                    创建 child works
                  </button>
                </div>
              ) : null}
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
