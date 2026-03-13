import { Link } from "react-router-dom";
import type {
  OperatorActionKind,
  OperatorInboxItem,
  RecoverySummary,
  SessionProjectionDocument,
} from "../../types";
import {
  formatOperatorKind,
  formatRecoveryTime,
  mapQuickAction,
  renderOperatorMeta,
} from "./shared";

interface MemoryActionsSectionProps {
  operatorItems: OperatorInboxItem[];
  operatorSummary: SessionProjectionDocument["operator_summary"];
  recoverySummary: Partial<RecoverySummary>;
  exportTargetLabel: string;
  canExportFocusedSession: boolean;
  busyActionId: string | null;
  onOperatorAction: (item: OperatorInboxItem, kind: OperatorActionKind) => Promise<void>;
  onRefreshRecoverySummary: () => Promise<void>;
  onBackupCreate: () => Promise<void>;
  onExportChats: () => Promise<void>;
}

export default function MemoryActionsSection({
  operatorItems,
  operatorSummary,
  recoverySummary,
  exportTargetLabel,
  canExportFocusedSession,
  busyActionId,
  onOperatorAction,
  onRefreshRecoverySummary,
  onBackupCreate,
  onExportChats,
}: MemoryActionsSectionProps) {
  return (
    <div className="wb-split">
      <section className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">待确认事项</p>
            <h3>处理会影响记忆与上下文的待确认事项</h3>
          </div>
          <div className="wb-chip-row">
            <span className="wb-chip">待处理 {operatorSummary?.total_pending ?? 0}</span>
            <span className="wb-chip">审批 {operatorSummary?.approvals ?? 0}</span>
            <span className="wb-chip">配对 {operatorSummary?.pairing_requests ?? 0}</span>
          </div>
        </div>

        {operatorItems.length === 0 ? (
          <div className="wb-empty-state">
            <strong>当前没有待处理事项</strong>
            <span>如果后续出现 Vault 授权、审批或配对请求，这里会直接显示。</span>
          </div>
        ) : (
          <div className="wb-note-stack">
            {operatorItems.slice(0, 6).map((item) => (
              <div key={item.item_id} className="wb-note">
                <strong>{item.title}</strong>
                <span>{item.summary}</span>
                <small>
                  {formatOperatorKind(item.kind)} · {renderOperatorMeta(item)} ·{" "}
                  {formatRecoveryTime(item.created_at)}
                </small>
                <div className="wb-inline-actions wb-inline-actions-wrap">
                  {item.quick_actions.map((action) => (
                    <button
                      key={`${item.item_id}-${action.kind}`}
                      type="button"
                      className={
                        action.style === "primary"
                          ? "wb-button wb-button-primary"
                          : "wb-button wb-button-secondary"
                      }
                      disabled={
                        !action.enabled ||
                        busyActionId === mapQuickAction(item, action.kind)?.actionId
                      }
                      onClick={() => void onOperatorAction(item, action.kind)}
                    >
                      {action.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">备份与恢复</p>
            <h3>把当前成果导出，并确认恢复准备度</h3>
          </div>
          <div className="wb-inline-actions">
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={() => void onRefreshRecoverySummary()}
              disabled={busyActionId === "diagnostics.refresh"}
            >
              刷新恢复状态
            </button>
          </div>
        </div>

        <div className="wb-note-stack">
          <div className="wb-note">
            <strong>最近备份</strong>
            <span>{recoverySummary?.latest_backup?.output_path ?? "尚未创建备份"}</span>
            <small>{formatRecoveryTime(recoverySummary?.latest_backup?.created_at)}</small>
          </div>
          <div className="wb-note">
            <strong>恢复准备度</strong>
            <span>{recoverySummary?.ready_for_restore ? "已就绪" : "未就绪"}</span>
            <small>
              {recoverySummary?.latest_recovery_drill?.summary ?? "尚未执行恢复演练。"}
            </small>
          </div>
          <div className="wb-note">
            <strong>导出当前会话</strong>
            <span>{exportTargetLabel}</span>
            <small>
              {canExportFocusedSession
                ? "这里会导出你当前聚焦的会话。"
                : "先在 Chat 或 Work 中聚焦一个会话，这里才会启用导出。"}
            </small>
          </div>
        </div>

        <div className="wb-inline-actions wb-inline-actions-wrap">
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={() => void onBackupCreate()}
            disabled={busyActionId === "backup.create"}
          >
            {busyActionId === "backup.create" ? "创建中..." : "创建备份"}
          </button>
          <button
            type="button"
            className="wb-button wb-button-secondary"
            onClick={() => void onExportChats()}
            disabled={!canExportFocusedSession || busyActionId === "session.export"}
          >
            {busyActionId === "session.export" ? "导出中..." : "导出当前会话"}
          </button>
          <Link className="wb-button wb-button-tertiary" to="/advanced">
            打开 Advanced Recovery
          </Link>
        </div>
      </section>
    </div>
  );
}
