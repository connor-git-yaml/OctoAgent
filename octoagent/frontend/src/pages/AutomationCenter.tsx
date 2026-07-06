/**
 * AutomationCenter 页面（F132 cron 自助工具 — 用户面）
 *
 * 面向普通用户的「定时任务」管理：
 * - 列出所有定时任务（名称 / 人读化时间 / 下次运行 / 状态）
 * - 行内开关：暂停/恢复（automation.pause / automation.resume，可逆操作）
 * - **无删除按钮**：删除是破坏性操作，走对话让助手删除（cron.delete 经 ApprovalGate
 *   Two-Phase 治理，Codex P1-3）。页面对每行给一句提示。
 * - 创建同样走对话（H1 mediated，避免 UI 重造自然语言理解）。
 *
 * 技术字段（job_id / action_id / cron 原始表达式）收在 Advanced 折叠区，主视图只给人读信息。
 * 所有请求经 src/api/client 的内部 apiFetch（front-door 鉴权）。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchAutomationDocument } from "../api/client";
import { executeWorkbenchAction } from "../platform/actions/controlPlaneActions";
import type {
  AutomationJobDocument,
  AutomationJobItem,
} from "../types";

interface ToastState {
  message: string;
  isError: boolean;
}

const WEEKDAY_CN: Record<string, string> = {
  mon: "周一",
  tue: "周二",
  wed: "周三",
  thu: "周四",
  fri: "周五",
  sat: "周六",
  sun: "周日",
  "0": "周一",
  "1": "周二",
  "2": "周三",
  "3": "周四",
  "4": "周五",
  "5": "周六",
  "6": "周日",
};

/**
 * 人读化 schedule（近似，面向非技术用户）。无法识别时回退原表达式。
 * cron 5 字段「分 时 日 月 星期」——仅覆盖最常见的每天/每周/每月定点场景。
 */
export function humanizeSchedule(
  kind: string,
  expr: string,
  timezone: string
): string {
  const tzHint = timezone && timezone !== "UTC" ? `（${timezone}）` : "";
  if (kind === "interval") {
    const secs = Number(expr);
    if (Number.isFinite(secs) && secs > 0) {
      if (secs % 3600 === 0) return `每 ${secs / 3600} 小时`;
      if (secs % 60 === 0) return `每 ${secs / 60} 分钟`;
      return `每 ${secs} 秒`;
    }
    return expr;
  }
  if (kind === "once") {
    const dt = new Date(expr);
    if (!Number.isNaN(dt.getTime())) {
      return `一次：${dt.toLocaleString("zh-CN")}${tzHint}`;
    }
    return `一次：${expr}`;
  }
  if (kind === "cron") {
    const parts = expr.trim().split(/\s+/);
    if (parts.length === 5) {
      const [min, hour, dom, mon, dow] = parts;
      const timeStr =
        /^\d+$/.test(min) && /^\d+$/.test(hour)
          ? `${hour.padStart(2, "0")}:${min.padStart(2, "0")}`
          : "";
      if (timeStr) {
        // 每天
        if (dom === "*" && mon === "*" && dow === "*") {
          return `每天 ${timeStr}${tzHint}`;
        }
        // 每周某天
        if (dom === "*" && mon === "*" && dow !== "*") {
          const days = dow
            .split(/[,-]/)
            .map((d) => WEEKDAY_CN[d.trim().toLowerCase()] ?? d)
            .join("、");
          return `每${days} ${timeStr}${tzHint}`;
        }
        // 每月某日
        if (/^\d+$/.test(dom) && mon === "*" && dow === "*") {
          return `每月 ${dom} 号 ${timeStr}${tzHint}`;
        }
      }
    }
    return `${expr}${tzHint}`;
  }
  return expr;
}

const STATUS_LABEL: Record<string, { label: string; cls: string }> = {
  active: { label: "运行中", cls: "is-ok" },
  paused: { label: "已暂停", cls: "" },
  running: { label: "执行中", cls: "is-ok" },
  failed: { label: "失败", cls: "is-warning" },
  degraded: { label: "异常", cls: "is-warning" },
};

function readReminderText(item: AutomationJobItem): string {
  const p = item.job.params as Record<string, unknown> | undefined;
  const msg = p?.message;
  return typeof msg === "string" ? msg : "";
}

export default function AutomationCenter() {
  const [doc, setDoc] = useState<AutomationJobDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetchAutomationDocument();
      setDoc(resp);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败，请重试");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const flashToast = useCallback((message: string, isError: boolean) => {
    setToast({ message, isError });
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = setTimeout(() => setToast(null), 3200);
  }, []);

  useEffect(
    () => () => {
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    },
    []
  );

  const handleToggle = useCallback(
    async (item: AutomationJobItem) => {
      const nextEnabled = !item.job.enabled;
      const actionId = nextEnabled ? "automation.resume" : "automation.pause";
      setBusyId(item.job.job_id);
      try {
        const result = await executeWorkbenchAction(
          doc?.contract_version,
          actionId,
          { job_id: item.job.job_id }
        );
        // executeControlAction 在 4xx（404/409，如任务已被别处删除）时仍会解析 result，
        // 故必须查 result.status——rejected 走错误提示而非假成功（Codex P2）。
        if (result.status === "rejected") {
          flashToast(result.message || "操作未生效，请刷新重试", true);
        } else {
          flashToast(nextEnabled ? "已恢复" : "已暂停", false);
        }
        await load();
      } catch (err) {
        flashToast(
          err instanceof Error ? err.message : "操作失败，请重试",
          true
        );
      } finally {
        setBusyId(null);
      }
    },
    [doc?.contract_version, flashToast, load]
  );

  const jobs = doc?.jobs ?? [];

  return (
    <div className="wb-page">
      <section className="wb-hero wb-hero-compact">
        <div className="wb-hero-copy">
          <p className="wb-kicker">定时任务</p>
          <h1>定时任务</h1>
          <p>
            这里是助手帮你安排的定时提醒和任务。想新建或删除，直接在对话里告诉助手
            （例如「每周一早上9点提醒我交周报」）。
          </p>
        </div>
      </section>

      {toast && (
        <div
          className={`wb-inline-banner ${toast.isError ? "is-warning" : "is-ok"}`}
          role="status"
        >
          {toast.message}
        </div>
      )}

      {loading && <div className="wb-empty-state">加载中…</div>}

      {!loading && error && (
        <div className="wb-inline-banner is-warning" role="alert">
          {error}
          <button
            type="button"
            className="wb-button wb-button-tertiary"
            onClick={() => void load()}
            style={{ marginLeft: "var(--space-sm)" }}
          >
            重试
          </button>
        </div>
      )}

      {!loading && !error && jobs.length === 0 && (
        <div className="wb-empty-state">
          还没有定时任务。在对话里让助手帮你建一个吧，比如「每天早上8点提醒我喝水」。
        </div>
      )}

      {!loading && !error && jobs.length > 0 && (
        <section className="wb-card-grid">
          {jobs.map((item) => {
            const statusMeta =
              STATUS_LABEL[item.status] ?? { label: item.status, cls: "" };
            const reminder = readReminderText(item);
            const nextRun = item.next_run_at
              ? new Date(item.next_run_at).toLocaleString("zh-CN")
              : "—";
            return (
              <article key={item.job.job_id} className="wb-card">
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-start",
                    gap: "var(--space-sm)",
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <strong style={{ display: "block" }}>{item.job.name}</strong>
                    <span className="wb-chip" style={{ marginTop: "4px" }}>
                      {humanizeSchedule(
                        item.job.schedule_kind,
                        item.job.schedule_expr,
                        item.job.timezone
                      )}
                    </span>
                  </div>
                  <span
                    className={`wb-status-pill ${statusMeta.cls}`}
                    aria-label={`状态：${statusMeta.label}`}
                  >
                    {statusMeta.label}
                  </span>
                </div>

                {reminder && (
                  <p style={{ marginTop: "var(--space-sm)" }}>提醒内容：{reminder}</p>
                )}

                <p className="wb-card-label" style={{ marginTop: "var(--space-sm)" }}>
                  下次运行：{nextRun}
                </p>
                {item.degraded_reason && (
                  <p className="wb-inline-banner is-warning">
                    {item.degraded_reason}
                  </p>
                )}

                <div
                  style={{
                    display: "flex",
                    gap: "var(--space-sm)",
                    marginTop: "var(--space-md)",
                    alignItems: "center",
                  }}
                >
                  <button
                    type="button"
                    className="wb-button wb-button-secondary"
                    disabled={busyId === item.job.job_id}
                    onClick={() => void handleToggle(item)}
                  >
                    {item.job.enabled ? "暂停" : "恢复"}
                  </button>
                  <span className="wb-card-label">
                    如需删除，在对话中让助手删除。
                  </span>
                </div>

                {showAdvanced && (
                  <dl className="wb-advanced-block" style={{ marginTop: "var(--space-sm)" }}>
                    <dt>任务 ID</dt>
                    <dd>{item.job.job_id}</dd>
                    <dt>动作</dt>
                    <dd>{item.job.action_id}</dd>
                    <dt>表达式</dt>
                    <dd>
                      {item.job.schedule_kind} · {item.job.schedule_expr} ·{" "}
                      {item.job.timezone}
                    </dd>
                  </dl>
                )}
              </article>
            );
          })}
        </section>
      )}

      {!loading && jobs.length > 0 && (
        <button
          type="button"
          className="wb-button wb-button-tertiary"
          onClick={() => setShowAdvanced((v) => !v)}
          style={{ marginTop: "var(--space-md)" }}
        >
          {showAdvanced ? "隐藏技术信息" : "显示技术信息"}
        </button>
      )}
    </div>
  );
}
