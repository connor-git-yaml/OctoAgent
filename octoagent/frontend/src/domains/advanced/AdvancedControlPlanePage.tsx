import { useState } from "react";
import { Link } from "react-router-dom";
import { useWorkbench } from "../../components/shell/WorkbenchLayout";
import ControlPlane from "../../pages/ControlPlane";
import { PageIntro } from "../../ui/primitives";
import { formatDateTime } from "../../workbench/utils";

const ACTIVE_WORK_STATUSES = new Set(["created", "assigned", "running", "escalated"]);

function countDegradedResources(
  snapshot: NonNullable<ReturnType<typeof useWorkbench>["snapshot"]>
): number {
  return Object.values(snapshot.resources).filter((resource) => {
    const degraded =
      resource && typeof resource === "object" && "degraded" in resource
        ? (resource as { degraded?: { is_degraded?: boolean } }).degraded
        : null;
    return Boolean(degraded?.is_degraded);
  }).length;
}

export default function AdvancedControlPlanePage() {
  const { snapshot, refreshSnapshot } = useWorkbench();
  const [showLegacyConsole, setShowLegacyConsole] = useState(false);

  if (!snapshot) {
    return null;
  }

  const diagnostics = snapshot.resources.diagnostics;
  const sessions = snapshot.resources.sessions;
  const delegation = snapshot.resources.delegation;
  const degradedResources = countDegradedResources(snapshot);
  const activeWorkCount = delegation.works.filter((item) =>
    ACTIVE_WORK_STATUSES.has(item.status)
  ).length;

  return (
    <div className="wb-page wb-advanced-stack">
      <PageIntro
        kicker="Advanced"
        title="高级诊断与恢复"
        summary="这里保留深度诊断、资源核对与恢复入口。日常路径继续留在 Home、Agents、Settings 和 Work。"
        actions={
          <>
            <button
              type="button"
              className="wb-button wb-button-primary"
              onClick={() => void refreshSnapshot()}
            >
              刷新共享快照
            </button>
            <Link className="wb-button wb-button-secondary" to="/settings">
              回到设置
            </Link>
          </>
        }
      />

      <div className="wb-card-grid wb-card-grid-4">
        <article className="wb-card">
          <p className="wb-card-label">运行状态</p>
          <strong>{diagnostics.overall_status}</strong>
          <span>快照时间 {formatDateTime(snapshot.generated_at)}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">待处理事项</p>
          <strong>{sessions.operator_summary?.total_pending ?? 0}</strong>
          <span>审批 {sessions.operator_summary?.approvals ?? 0}</span>
          <span>配对请求 {sessions.operator_summary?.pairing_requests ?? 0}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">活跃 Work</p>
          <strong>{activeWorkCount}</strong>
          <span>总 work {delegation.works.length}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">降级资源</p>
          <strong>{degradedResources}</strong>
          <span>优先先处理 diagnostics 与 setup warning</span>
        </article>
      </div>

      <section className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">先做什么</p>
            <h3>按风险逐级处理，而不是直接翻原始控制台</h3>
          </div>
        </div>
        <div className="wb-action-list">
          <Link className="wb-action-card" to="/work">
            <strong>先看运行中的 Work</strong>
            <span>如果已经有失败或卡住的 work，先确认影响范围和当前状态。</span>
          </Link>
          <Link className="wb-action-card" to="/agents">
            <strong>检查 Agent 授权与 Provider</strong>
            <span>很多异常来自 Agent 没挂对 provider、skill 或 MCP provider。</span>
          </Link>
          <Link className="wb-action-card" to="/settings">
            <strong>回到平台设置</strong>
            <span>Provider、渠道、Memory 或安全配置不一致时，优先回设置页修正。</span>
          </Link>
        </div>
      </section>

      <section className="wb-panel">
        <div className="wb-panel-head wb-advanced-actions">
          <div>
            <p className="wb-card-label">Legacy Console</p>
            <h3>需要时再展开原始控制台</h3>
            <p>
              它仍然保留完整诊断与资源检查能力，但不再作为高级页的首屏默认内容。
            </p>
          </div>
          <button
            type="button"
            className="wb-button wb-button-secondary wb-advanced-console-toggle"
            onClick={() => setShowLegacyConsole((current) => !current)}
          >
            {showLegacyConsole ? "收起详细控制台" : "打开详细控制台"}
          </button>
        </div>
      </section>

      {showLegacyConsole ? <ControlPlane initialSnapshot={snapshot} /> : null}
    </div>
  );
}
