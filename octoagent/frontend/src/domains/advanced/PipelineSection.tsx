import type { SkillPipelineDocument } from "../../types";

interface PipelineSectionProps {
  skillPipelines: SkillPipelineDocument;
  busyActionId: string | null;
  onResumeRun: (workId: string) => void;
  onRetryNode: (workId: string) => void;
  formatDateTime: (value?: string | null) => string;
  formatJson: (value: unknown) => string;
  statusTone: (status: string) => string;
}

export default function PipelineSection({
  skillPipelines,
  busyActionId,
  onResumeRun,
  onRetryNode,
  formatDateTime,
  formatJson,
  statusTone,
}: PipelineSectionProps) {
  return (
    <section className="stack-section">
      <article className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Skill Pipeline</p>
            <h3>{skillPipelines.runs.length}</h3>
          </div>
        </div>
        <pre className="json-preview">{formatJson(skillPipelines.summary)}</pre>
      </article>

      {skillPipelines.runs.map((run) => (
        <article key={run.run_id} className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">{run.pipeline_id}</p>
              <h3>{run.run_id}</h3>
            </div>
            <span className={`tone-chip ${statusTone(run.status)}`}>{run.status}</span>
          </div>
          <div className="meta-grid">
            <span>Work {run.work_id}</span>
            <span>Task {run.task_id}</span>
            <span>Node {run.current_node_id || "-"}</span>
            <span>Pause {run.pause_reason || "-"}</span>
          </div>
          <div className="action-row">
            <button
              type="button"
              className="secondary-button"
              onClick={() => onResumeRun(run.work_id)}
              disabled={busyActionId === "pipeline.resume"}
            >
              恢复
            </button>
            <button
              type="button"
              className="ghost-button"
              onClick={() => onRetryNode(run.work_id)}
              disabled={busyActionId === "pipeline.retry_node"}
            >
              重试节点
            </button>
          </div>
          <div className="event-list">
            {run.replay_frames.map((frame) => (
              <div key={frame.frame_id} className="event-item">
                <div>
                  <strong>{frame.node_id}</strong>
                  <p>{frame.summary || frame.status}</p>
                </div>
                <small>{formatDateTime(frame.ts)}</small>
              </div>
            ))}
          </div>
        </article>
      ))}
    </section>
  );
}
