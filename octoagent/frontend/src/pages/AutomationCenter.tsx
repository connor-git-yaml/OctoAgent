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

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { fetchAutomationDocument } from "../api/client";
import { resolveResourcePageState } from "../domains/shared/resourcePageState";
import { executeWorkbenchAction } from "../platform/actions/controlPlaneActions";
import type {
  AutomationJobDocument,
  AutomationJobItem,
} from "../types";
import "./AutomationCenter.css";

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
    return "按固定间隔";
  }
  if (kind === "once") {
    const dt = new Date(expr);
    if (!Number.isNaN(dt.getTime())) {
      return `一次：${dt.toLocaleString("zh-CN")}${tzHint}`;
    }
    return "单次计划";
  }
  if (kind === "cron") {
    const parts = expr.trim().split(/\s+/);
    if (parts.length === 5) {
      const [min, hour, dom, mon, dow] = parts;
      const minuteStep = min.match(/^\*\/(\d+)$/);
      if (
        minuteStep &&
        hour === "*" &&
        dom === "*" &&
        mon === "*" &&
        dow === "*"
      ) {
        return `每 ${minuteStep[1]} 分钟${tzHint}`;
      }
      const hourStep = hour.match(/^\*\/(\d+)$/);
      if (
        hourStep &&
        /^\d+$/.test(min) &&
        dom === "*" &&
        mon === "*" &&
        dow === "*"
      ) {
        return `每 ${hourStep[1]} 小时${tzHint}`;
      }
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
    return `按自定义计划${tzHint}`;
  }
  return "自定义计划";
}

const STATUS_LABEL: Record<string, { label: string; cls: string }> = {
  active: { label: "运行中", cls: "is-active" },
  paused: { label: "已暂停", cls: "is-paused" },
  running: { label: "执行中", cls: "is-active" },
  failed: { label: "需要处理", cls: "is-warning" },
  degraded: { label: "需要处理", cls: "is-warning" },
};

function readReminderText(item: AutomationJobItem): string {
  const p = item.job.params as Record<string, unknown> | undefined;
  const msg = p?.message;
  return typeof msg === "string" ? msg : "";
}

function readJobDisplayName(item: AutomationJobItem): string {
  if (item.job.job_id === "system:memory-consolidate") {
    return "定期整理记忆";
  }
  if (item.job.job_id === "system:memory-profile-generate") {
    return "生成用户画像";
  }
  return item.job.name;
}

export default function AutomationCenter() {
  const [doc, setDoc] = useState<AutomationJobDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [conflictJobId, setConflictJobId] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [advancedJobId, setAdvancedJobId] = useState<string | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const advancedTriggerRef = useRef<HTMLButtonElement | null>(null);
  const advancedCloseRef = useRef<HTMLButtonElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetchAutomationDocument();
      setDoc(resp);
      setConflictJobId(null);
    } catch (err) {
      setError(err instanceof Error ? err : new Error("automation load failed"));
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
        if (result.status === "rejected") {
          setConflictJobId(item.job.job_id);
          return;
        }
        flashToast(nextEnabled ? "已恢复" : "已暂停", false);
        await load();
      } catch {
        flashToast("操作未完成，请稍后重试", true);
      } finally {
        setBusyId(null);
      }
    },
    [doc?.contract_version, flashToast, load]
  );

  const jobs = useMemo(
    () =>
      [...(doc?.jobs ?? [])].sort(
        (left, right) => Number(right.job.enabled) - Number(left.job.enabled)
      ),
    [doc?.jobs]
  );
  const pageResolution = resolveResourcePageState({
    loading,
    hasContent: jobs.length > 0,
    connected: true,
    error,
  });
  const pageState =
    pageResolution.owner === "surface"
      ? pageResolution.state.kind
      : "recoverable-error";
  const advancedItem =
    jobs.find((item) => item.job.job_id === advancedJobId) ?? null;

  const openAdvanced = useCallback(
    (item: AutomationJobItem, trigger: HTMLButtonElement) => {
      advancedTriggerRef.current = trigger;
      setAdvancedJobId(item.job.job_id);
    },
    []
  );

  const closeAdvanced = useCallback(() => {
    setAdvancedJobId(null);
    advancedTriggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!advancedJobId) {
      return;
    }
    advancedCloseRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeAdvanced();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [advancedJobId, closeAdvanced]);

  return (
    <main className="f149-automation-page">
      <section className="f149-automation-hero">
        <div className="f149-automation-hero-copy">
          <p className="f149-automation-kicker">AUTOMATION</p>
          <h1>定时任务</h1>
          <p>
            助手会按约定的时间完成提醒和任务。想新建或删除，直接在对话里告诉助手。
          </p>
        </div>
        <div className="f149-automation-hero-count" aria-label={`共 ${jobs.length} 项任务`}>
          <strong>{jobs.length}</strong>
          <span>项计划</span>
        </div>
      </section>

      {toast && (
        <div
          className={`f149-automation-banner ${
            toast.isError ? "is-warning" : "is-success"
          }`}
          role="status"
        >
          {toast.message}
        </div>
      )}

      {pageState === "loading" && (
        <div className="f149-automation-state" aria-live="polite">
          <span className="f149-automation-state-mark" aria-hidden="true" />
          <strong>正在整理定时任务</strong>
          <p>很快就好。</p>
        </div>
      )}

      {pageState === "permission-denied" && (
        <div className="f149-automation-state is-error" role="alert">
          <strong>当前账号没有权限查看定时任务</strong>
          <p>请联系管理员确认这项资源的访问权限。</p>
        </div>
      )}

      {pageState === "recoverable-error" && (
        <div className="f149-automation-state is-error" role="alert">
          <strong>定时任务加载失败</strong>
          <p>连接可能暂时不稳定，请稍后再试。</p>
          <button
            type="button"
            onClick={() => void load()}
          >
            重试
          </button>
        </div>
      )}

      {pageState === "empty" && (
        <div className="f149-automation-state">
          <span className="f149-automation-state-mark is-empty" aria-hidden="true" />
          <strong>还没有定时任务</strong>
          <p>在对话里说一句即可创建，例如“每天早上 8 点提醒我喝水”。</p>
        </div>
      )}

      {pageState === "ready" && (
        <section className="f149-automation-grid" aria-label="定时任务列表">
          {jobs.map((item) => {
            const statusMeta =
              STATUS_LABEL[item.status] ?? { label: item.status, cls: "" };
            const displayName = readJobDisplayName(item);
            const reminder = readReminderText(item);
            const nextRun = item.next_run_at
              ? new Date(item.next_run_at).toLocaleString("zh-CN")
              : "—";
            return (
              <article key={item.job.job_id} className="f149-automation-card">
                <div className="f149-automation-card-header">
                  <span
                    className={`f149-automation-indicator ${statusMeta.cls}`}
                    aria-hidden="true"
                  />
                  <div className="f149-automation-card-title">
                    <strong>{displayName}</strong>
                    <span className="f149-automation-schedule">
                      {humanizeSchedule(
                        item.job.schedule_kind,
                        item.job.schedule_expr,
                        item.job.timezone
                      )}
                    </span>
                  </div>
                  <span
                    className={`f149-automation-status ${statusMeta.cls}`}
                    aria-label={`状态：${statusMeta.label}`}
                  >
                    {statusMeta.label}
                  </span>
                </div>

                {reminder && (
                  <p className="f149-automation-reminder">
                    <span>提醒内容</span>
                    {reminder}
                  </p>
                )}

                <p className="f149-automation-next-run">
                  <span>下次运行</span>
                  <strong>{nextRun}</strong>
                </p>
                {item.degraded_reason && (
                  <p className="f149-automation-card-warning">
                    {item.degraded_reason}
                  </p>
                )}

                {conflictJobId === item.job.job_id && (
                  <div className="f149-automation-conflict" role="alert">
                    <span>已在别处更改</span>
                    <button type="button" onClick={() => void load()}>
                      刷新
                    </button>
                  </div>
                )}

                <div className="f149-automation-card-actions">
                  <button
                    type="button"
                    className="f149-automation-advanced-trigger"
                    onClick={(event) => openAdvanced(item, event.currentTarget)}
                  >
                    高级 · 任务编号与计划原式
                  </button>
                  <button
                    type="button"
                    className="f149-automation-primary-action"
                    disabled={busyId === item.job.job_id}
                    onClick={() => void handleToggle(item)}
                  >
                    {busyId === item.job.job_id
                      ? "处理中…"
                      : item.job.enabled
                        ? "暂停"
                        : "恢复"}
                  </button>
                </div>
              </article>
            );
          })}
        </section>
      )}

      {advancedItem && (
        <div className="f149-automation-sheet-backdrop">
          <section
            className="f149-automation-sheet"
            role="dialog"
            aria-modal="true"
            aria-label={`${readJobDisplayName(advancedItem)}的高级信息`}
          >
            <header>
              <div>
                <p>高级信息</p>
                <h2>{readJobDisplayName(advancedItem)}</h2>
              </div>
              <button
                ref={advancedCloseRef}
                type="button"
                onClick={closeAdvanced}
                aria-label="关闭高级信息"
              >
                关闭
              </button>
            </header>
            <dl>
              <div>
                <dt>任务编号</dt>
                <dd>{advancedItem.job.job_id}</dd>
              </div>
              <div>
                <dt>动作</dt>
                <dd>{advancedItem.job.action_id}</dd>
              </div>
              <div>
                <dt>计划原式</dt>
                <dd>
                  {advancedItem.job.schedule_kind} ·{" "}
                  {advancedItem.job.schedule_expr} ·{" "}
                  {advancedItem.job.timezone}
                </dd>
              </div>
            </dl>
            <p className="f149-automation-sheet-note">
              如需删除或更改计划，请直接在对话里告诉助手。
            </p>
          </section>
        </div>
      )}
    </main>
  );
}
