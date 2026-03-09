import { useWorkbench } from "../components/shell/WorkbenchLayout";
import { formatDateTime } from "../workbench/utils";

export default function MemoryCenter() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const memory = snapshot!.resources.memory;

  return (
    <div className="wb-page">
      <section className="wb-hero wb-hero-compact">
        <div>
          <p className="wb-kicker">Memory</p>
          <h1>先回答系统记住了什么，再解释为什么</h1>
          <p>
            这里先用 `MemoryConsoleDocument` 做一层用户摘要，subject history /
            proposal / vault 细节后续继续接入。
          </p>
        </div>
        <div className="wb-hero-actions">
          <button
            type="button"
            className="wb-button wb-button-secondary"
            onClick={() =>
              void submitAction("memory.query", {
                project_id: memory.active_project_id,
                workspace_id: memory.active_workspace_id,
              })
            }
            disabled={busyActionId === "memory.query"}
          >
            刷新摘要
          </button>
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={() => void submitAction("memory.flush", {})}
            disabled={busyActionId === "memory.flush"}
          >
            触发 flush
          </button>
        </div>
      </section>

      <div className="wb-card-grid wb-card-grid-4">
        <article className="wb-card">
          <p className="wb-card-label">Current SoR</p>
          <strong>{memory.summary.sor_current_count}</strong>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">Fragments</p>
          <strong>{memory.summary.fragment_count}</strong>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">Vault refs</p>
          <strong>{memory.summary.vault_ref_count}</strong>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">Proposals</p>
          <strong>{memory.summary.proposal_count}</strong>
        </article>
      </div>

      <section className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">当前记录</p>
            <h3>{memory.records.length} 条摘要</h3>
          </div>
        </div>
        <div className="wb-list">
          {memory.records.map((record) => (
            <article key={record.record_id} className="wb-list-row is-static">
              <div>
                <strong>{record.subject_key}</strong>
                <p>{record.summary}</p>
              </div>
              <div className="wb-list-meta">
                <span className={`wb-status-pill is-${record.status.toLowerCase()}`}>
                  {record.status}
                </span>
                <small>{formatDateTime(record.updated_at ?? record.created_at)}</small>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
