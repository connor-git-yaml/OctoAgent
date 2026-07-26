import { formatDateTimeSafe } from "../../utils/formatTime";
import type { RecoverySummary, UpdateAttemptSummary } from "../../types";
import {
  DEFAULT_MAINTENANCE_RECOVERY_API,
  isValidUpdateDryRun,
  resolveMaintenanceSurfaceState,
  useMaintenanceRecoveryState,
  type MaintenanceAction,
  type MaintenanceConfirmation,
  type MaintenanceRecoveryApi,
} from "./maintenanceRecoveryState";
import "./MaintenanceRecoverySection.css";

export interface MaintenanceRecoverySectionProps {
  api?: MaintenanceRecoveryApi;
  connected: boolean;
}

const CONFIRMATIONS: Record<
  MaintenanceConfirmation,
  {
    action: MaintenanceAction;
    button: string;
    detail: string;
    inputLabel: string;
    title: string;
    word: string;
  }
> = {
  backup: {
    action: "backup",
    button: "确认创建备份",
    detail: "创建前会记录当前可恢复内容。此操作需要明确确认。",
    inputLabel: "输入 backup 继续",
    title: "创建备份",
    word: "backup",
  },
  apply: {
    action: "update-apply",
    button: "确认生效",
    detail: "将使用最近一次有效试运行的结果。请确认后再让升级生效。",
    inputLabel: "输入 apply 继续",
    title: "让升级生效",
    word: "apply",
  },
  restart: {
    action: "restart",
    button: "确认重启",
    detail: "重启期间会短暂不可用；任务是否续跑由运行时实际状态决定。",
    inputLabel: "输入 restart 继续",
    title: "重启运行时",
    word: "restart",
  },
};

function SurfaceState({
  kind,
  onRetry,
}: {
  kind:
    | "loading"
    | "empty"
    | "recoverable-error"
    | "permission-denied";
  onRetry: () => void;
}) {
  const content = {
    loading: {
      detail: "正在读取备份、升级和运行时校验状态。",
      title: "正在读取维护状态",
    },
    empty: {
      detail: "先连接至少一个模型供应商，再进行备份、升级或重启。",
      title: "先连接至少一个模型供应商",
    },
    "recoverable-error": {
      detail: "普通设置仍可使用。你可以重新加载这个高级区域。",
      title: "维护状态暂时不可用",
    },
    "permission-denied": {
      detail: "当前账号没有权限修改设置，请联系管理员。",
      title: "无权修改维护设置",
    },
  }[kind];
  const isError =
    kind === "recoverable-error" || kind === "permission-denied";

  return (
    <div
      className={`f149-maintenance-state ${isError ? "is-error" : ""}`}
      role={isError ? "alert" : "status"}
      aria-label={content.title}
    >
      <strong>{content.title}</strong>
      <span>{content.detail}</span>
      {kind === "recoverable-error" ? (
        <button type="button" onClick={onRetry}>
          重新加载
        </button>
      ) : null}
    </div>
  );
}

function MaintenanceConfirmationDrawer({
  busy,
  confirmation,
  input,
  onClose,
  onConfirm,
  onInput,
}: {
  busy: boolean;
  confirmation: MaintenanceConfirmation;
  input: string;
  onClose: () => void;
  onConfirm: (action: MaintenanceAction) => void;
  onInput: (value: string) => void;
}) {
  const contract = CONFIRMATIONS[confirmation];
  return (
    <div className="f149-maintenance-drawer-backdrop">
      <section
        className="f149-maintenance-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={contract.title}
      >
        <div className="f149-maintenance-drawer-head">
          <div>
            <span>强确认</span>
            <h3>{contract.title}</h3>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭确认">
            ×
          </button>
        </div>
        <p>{contract.detail}</p>
        <label>
          <span>{contract.inputLabel}</span>
          <input
            autoFocus
            value={input}
            onChange={(event) => onInput(event.target.value)}
            aria-label={contract.inputLabel}
            autoComplete="off"
          />
        </label>
        <button
          type="button"
          className="f149-maintenance-button is-danger"
          disabled={busy || input !== contract.word}
          onClick={() => onConfirm(contract.action)}
        >
          {contract.button}
        </button>
      </section>
    </div>
  );
}

function MaintenanceSummary({
  recovery,
}: {
  recovery: RecoverySummary | null;
}) {
  return (
    <div className="f149-maintenance-summary">
      <article>
        <span>恢复准备度摘要</span>
        <strong>{recovery?.ready_for_restore ? "已就绪" : "未就绪"}</strong>
        <p>
          {recovery?.latest_recovery_drill?.summary ?? "尚未运行恢复校验。"}
        </p>
      </article>
      <article>
        <span>最近备份</span>
        <strong>
          {formatDateTimeSafe(recovery?.latest_backup?.created_at, "未记录")}
        </strong>
        <p>仅显示净化后的时间与结果，不展示敏感路径。</p>
      </article>
    </div>
  );
}

function MaintenanceActions({
  busy,
  onConfirm,
  onRun,
  update,
}: {
  busy: boolean;
  onConfirm: (confirmation: MaintenanceConfirmation) => void;
  onRun: (action: MaintenanceAction) => void;
  update: UpdateAttemptSummary | null;
}) {
  const dryRunReady = isValidUpdateDryRun(update);
  return (
    <div className="f149-maintenance-actions">
      <article>
        <div>
          <h3>创建备份</h3>
          <p>为当前可恢复内容创建一份新的备份。</p>
        </div>
        <button
          type="button"
          className="f149-maintenance-button"
          disabled={busy}
          onClick={() => onConfirm("backup")}
        >
          创建备份
        </button>
      </article>
      <article>
        <div>
          <h3>导出聊天</h3>
          <p>全部会话与消息（不含密钥与运行日志）</p>
        </div>
        <button
          type="button"
          className="f149-maintenance-button"
          disabled={busy}
          onClick={() => onRun("export")}
        >
          导出聊天
        </button>
      </article>
      <article>
        <div>
          <h3>升级</h3>
          <p>必须先完成有效试运行，才能确认生效。</p>
          <small>
            {dryRunReady
              ? `最近一次有效试运行：${update?.attempt_id}`
              : "最近一次有效试运行：无"}
          </small>
        </div>
        <div className="f149-maintenance-row-actions">
          <button
            type="button"
            className="f149-maintenance-button"
            disabled={busy}
            onClick={() => onRun("update-dry-run")}
          >
            试运行
          </button>
          <button
            type="button"
            className="f149-maintenance-button is-primary"
            disabled={busy || !dryRunReady}
            onClick={() => onConfirm("apply")}
          >
            生效
          </button>
        </div>
      </article>
      <article>
        <div>
          <h3>重启运行时</h3>
          <p>重启期间会短暂不可用。</p>
        </div>
        <button
          type="button"
          className="f149-maintenance-button"
          disabled={busy}
          onClick={() => onConfirm("restart")}
        >
          重启运行时
        </button>
      </article>
      <article>
        <div>
          <h3>校验</h3>
          <p>检查运行时与当前配置是否一致。</p>
        </div>
        <button
          type="button"
          className="f149-maintenance-button"
          disabled={busy}
          onClick={() => onRun("verify")}
        >
          运行校验
        </button>
      </article>
    </div>
  );
}

export default function MaintenanceRecoverySection({
  api = DEFAULT_MAINTENANCE_RECOVERY_API,
  connected,
}: MaintenanceRecoverySectionProps) {
  const {
    closeConfirmation,
    load,
    openConfirmation,
    runAction,
    setConfirmationInput,
    state,
  } = useMaintenanceRecoveryState(api, connected);
  const surfaceState = resolveMaintenanceSurfaceState({
    connected,
    error: state.error,
    loading: state.loading,
  });

  if (surfaceState === "global-auth") {
    return null;
  }

  return (
    <section
      id="settings-group-maintenance"
      className="f149-maintenance"
      data-state-source="maintenance-recovery"
      data-testid="maintenance-recovery-root"
    >
      <header className="f149-maintenance-header">
        <div>
          <span className="f149-maintenance-eyebrow">Advanced</span>
          <h2>高级 · 维护与恢复</h2>
          <p>集中处理备份、聊天导出、升级、重启与运行时校验。</p>
        </div>
        {surfaceState === "ready" ? (
          <span
            className={`f149-maintenance-readiness ${
              state.recovery?.ready_for_restore ? "is-ready" : "is-warning"
            }`}
          >
            {state.recovery?.ready_for_restore ? "已就绪" : "未就绪"}
          </span>
        ) : null}
      </header>

      {surfaceState !== "ready" ? (
        <SurfaceState kind={surfaceState} onRetry={() => void load()} />
      ) : (
        <>
          <MaintenanceSummary recovery={state.recovery} />

          {state.notice ? (
            <div className="f149-maintenance-notice" role="status">
              {state.notice}
            </div>
          ) : null}

          <MaintenanceActions
            busy={state.busyAction !== null}
            onConfirm={openConfirmation}
            onRun={(action) => void runAction(action)}
            update={state.update}
          />
        </>
      )}

      {state.confirmation ? (
        <MaintenanceConfirmationDrawer
          busy={state.busyAction !== null}
          confirmation={state.confirmation}
          input={state.confirmationInput}
          onClose={closeConfirmation}
          onConfirm={(action) => void runAction(action)}
          onInput={setConfirmationInput}
        />
      ) : null}
    </section>
  );
}
