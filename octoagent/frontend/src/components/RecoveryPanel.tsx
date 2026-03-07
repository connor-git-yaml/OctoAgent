import { useEffect, useState } from "react";
import {
  fetchRecoverySummary,
  triggerBackupCreate,
  triggerExportChats,
} from "../api/client";
import type { RecoverySummary } from "../types";

function formatTime(value: string | null | undefined): string {
  if (!value) {
    return "未记录";
  }
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

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

export default function RecoveryPanel() {
  const [summary, setSummary] = useState<RecoverySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"backup" | "export" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function load() {
    try {
      const data = await fetchRecoverySummary();
      setSummary(data);
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
          <strong>{formatTime(summary?.latest_backup?.created_at)}</strong>
          <div className="muted">{summary?.latest_backup?.output_path || "尚未创建 backup"}</div>
        </div>
        <div className="recovery-item">
          <div className="muted">恢复演练</div>
          <strong>{drillLabel(summary)}</strong>
          <div className="muted">
            {formatTime(summary?.latest_recovery_drill?.checked_at)}
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
      </div>
    </section>
  );
}
