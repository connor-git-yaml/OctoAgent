import { useEffect, useState } from "react";
import {
  fetchRecoverySummary,
  fetchUpdateStatus,
  triggerBackupCreate,
  triggerExportChats,
  triggerRestart,
  triggerUpdateApply,
  triggerUpdateDryRun,
  triggerVerify,
} from "../api/client";
import type { RecoverySummary, UpdateAttemptSummary } from "../types";
import { formatDateTimeSafe } from "../utils/formatTime";

function drillLabel(summary: RecoverySummary | null): string {
  const status = summary?.latest_recovery_drill?.status;
  if (!status) {
    return "尚未验证";
  }
  if (status === "PASSED") {
    return "已通过";
  }
  if (status === "FAILED") {
    return "失败";
  }
  return "尚未验证";
}

function updateLabel(summary: UpdateAttemptSummary | null): string {
  const status = summary?.overall_status;
  if (!status) {
    return "尚未执行";
  }
  if (status === "RUNNING") {
    return "进行中";
  }
  if (status === "SUCCEEDED") {
    return "已完成";
  }
  if (status === "FAILED") {
    return "失败";
  }
  if (status === "ACTION_REQUIRED") {
    return "待处理";
  }
  return "待执行";
}

export default function RecoveryPanel() {
  const [summary, setSummary] = useState<RecoverySummary | null>(null);
  const [updateSummary, setUpdateSummary] = useState<UpdateAttemptSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<
    "backup" | "export" | "update-preview" | "update-apply" | "restart" | "verify" | null
  >(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function load() {
    try {
      const [recovery, update] = await Promise.all([
        fetchRecoverySummary(),
        fetchUpdateStatus().catch(() => ({
          phases: [],
        }) as UpdateAttemptSummary),
      ]);
      setSummary(recovery);
      setUpdateSummary(update);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load recovery summary");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (updateSummary?.overall_status !== "RUNNING") {
      return undefined;
    }

    const timer = window.setInterval(() => {
      void load();
    }, 2000);

    return () => {
      window.clearInterval(timer);
    };
  }, [updateSummary?.overall_status]);

  async function handleBackup() {
    try {
      setBusy("backup");
      const bundle = await triggerBackupCreate("manual");
      setNotice(`已创建备份: ${bundle.output_path}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Backup failed");
    } finally {
      setBusy(null);
    }
  }

  async function handleExport() {
    try {
      setBusy("export");
      const manifest = await triggerExportChats();
      setNotice(`已导出 chats: ${manifest.output_path}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setBusy(null);
    }
  }

  async function handleUpdatePreview() {
    try {
      setBusy("update-preview");
      const latest = await triggerUpdateDryRun();
      setUpdateSummary(latest);
      setNotice("已完成 update dry-run。");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update dry-run failed");
    } finally {
      setBusy(null);
    }
  }

  async function handleUpdateApply() {
    try {
      setBusy("update-apply");
      const latest = await triggerUpdateApply(false);
      setUpdateSummary(latest);
      setNotice("已接受 update 请求，正在后台执行。");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update apply failed");
    } finally {
      setBusy(null);
    }
  }

  async function handleRestart() {
    try {
      setBusy("restart");
      const latest = await triggerRestart();
      setUpdateSummary(latest);
      setNotice("已触发 restart。");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Restart failed");
    } finally {
      setBusy(null);
    }
  }

  async function handleVerify() {
    try {
      setBusy("verify");
      const latest = await triggerVerify();
      setUpdateSummary(latest);
      setNotice("已完成 verify。");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Verify failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card recovery-panel">
      <div className="recovery-header">
        <div>
          <h2>Recovery</h2>
          <p className="muted">最近一次备份与恢复准备度摘要</p>
        </div>
        <span className={`status-badge ${summary?.ready_for_restore ? "SUCCEEDED" : "FAILED"}`}>
          {summary?.ready_for_restore ? "READY" : "NOT READY"}
        </span>
      </div>

      {loading ? <div className="muted">Loading recovery summary...</div> : null}
      {error ? <div className="error-inline">{error}</div> : null}
      {notice ? <div className="notice-inline">{notice}</div> : null}

      <div className="recovery-grid">
        <div className="recovery-item">
          <div className="muted">最近备份</div>
          <strong>{formatDateTimeSafe(summary?.latest_backup?.created_at, "未记录")}</strong>
          <div className="muted">{summary?.latest_backup?.output_path || "尚未创建 backup"}</div>
        </div>
        <div className="recovery-item">
          <div className="muted">恢复演练</div>
          <strong>{drillLabel(summary)}</strong>
          <div className="muted">
            {formatDateTimeSafe(summary?.latest_recovery_drill?.checked_at, "未记录")}
          </div>
        </div>
      </div>

      <div className="recovery-item">
        <div className="muted">摘要</div>
        <div>
          {summary?.latest_recovery_drill?.summary ||
            "尚未执行 restore dry-run，当前不能确认恢复准备度。"}
        </div>
        {summary?.latest_recovery_drill?.failure_reason ? (
          <div className="muted" style={{ marginTop: "var(--space-xs)" }}>
            失败原因: {summary.latest_recovery_drill.failure_reason}
          </div>
        ) : null}
      </div>

      <div className="recovery-grid">
        <div className="recovery-item">
          <div className="muted">最近升级</div>
          <strong>{updateLabel(updateSummary)}</strong>
          <div className="muted">{formatDateTimeSafe(updateSummary?.started_at, "未记录")}</div>
        </div>
        <div className="recovery-item">
          <div className="muted">当前阶段</div>
          <strong>{updateSummary?.current_phase || "未执行"}</strong>
          <div className="muted">
            管理模式: {updateSummary?.management_mode || "unmanaged"}
          </div>
        </div>
      </div>

      <div className="recovery-item">
        <div className="muted">升级摘要</div>
        <div>
          {updateSummary?.failure_report?.message ||
            (updateSummary?.overall_status
              ? `状态: ${updateSummary.overall_status}`
              : "尚未执行 update / restart / verify。")}
        </div>
        {updateSummary?.failure_report?.suggested_actions?.length ? (
          <div className="muted" style={{ marginTop: "var(--space-xs)" }}>
            下一步: {updateSummary.failure_report.suggested_actions.join("；")}
          </div>
        ) : null}
      </div>

      <div className="recovery-actions">
        <button
          type="button"
          className="action-button"
          onClick={() => void handleBackup()}
          disabled={busy !== null}
        >
          {busy === "backup" ? "创建中..." : "创建备份"}
        </button>
        <button
          type="button"
          className="action-button secondary"
          onClick={() => void handleExport()}
          disabled={busy !== null}
        >
          {busy === "export" ? "导出中..." : "导出 Chats"}
        </button>
        <button
          type="button"
          className="action-button secondary"
          onClick={() => void handleUpdatePreview()}
          disabled={busy !== null}
        >
          {busy === "update-preview" ? "预检中..." : "Update Dry Run"}
        </button>
        <button
          type="button"
          className="action-button"
          onClick={() => void handleUpdateApply()}
          disabled={busy !== null}
        >
          {busy === "update-apply" ? "升级中..." : "执行 Update"}
        </button>
        <button
          type="button"
          className="action-button secondary"
          onClick={() => void handleRestart()}
          disabled={busy !== null}
        >
          {busy === "restart" ? "重启中..." : "Restart"}
        </button>
        <button
          type="button"
          className="action-button secondary"
          onClick={() => void handleVerify()}
          disabled={busy !== null}
        >
          {busy === "verify" ? "验证中..." : "Verify"}
        </button>
      </div>
    </section>
  );
}
