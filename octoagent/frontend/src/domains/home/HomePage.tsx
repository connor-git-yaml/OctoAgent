import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useWorkbench } from "../../components/shell/WorkbenchLayout";
import { describeOperatorItemForUser } from "../operator/userFacing";
import { PageIntro } from "../../ui/primitives";
import type { OperatorInboxItem, WorkProjectionItem } from "../../types";
import { getValueAtPath } from "../../workbench/utils";

const ACTIVE_WORK_STATUSES = new Set(["created", "assigned", "running", "escalated"]);
const READY_DIAGNOSTIC_STATUSES = new Set(["ready", "ok", "healthy"]);
const CHANNEL_LABELS: Record<string, string> = {
  telegram: "Telegram",
  web: "Web",
  wechat: "微信",
  wechat_import: "微信导入",
};
const WORKER_LABELS: Record<string, string> = {
  general: "主助手",
  research: "调研",
  ops: "运维",
  dev: "开发",
};

interface HomePrimaryState {
  title: string;
  summary: string;
  primaryActionLabel: string;
  primaryActionTo: string;
  secondaryActionLabel?: string;
  secondaryActionTo?: string;
}

function firstStringValue(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

function firstBooleanValue(record: Record<string, unknown>, keys: string[]): boolean | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "boolean") {
      return value;
    }
  }
  return null;
}

function normalizeWhitespace(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function truncateText(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }
  return `${value.slice(0, maxLength - 1)}…`;
}

function looksTechnicalSummary(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  if (!normalized) {
    return false;
  }
  return [
    "web.search",
    "websearch",
    "mcp.",
    "tool_name:",
    "task_id:",
    "json",
    "response_artifact_ref:",
    "runtime.",
  ].some((pattern) => normalized.includes(pattern));
}

function formatChannelStatusText(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (!normalized) {
    return "状态未记录";
  }
  if (["ready", "ok", "healthy", "enabled", "connected"].includes(normalized)) {
    return "已连接";
  }
  if (["warning", "warn", "degraded", "partial"].includes(normalized)) {
    return "需要检查";
  }
  if (["error", "failed", "unreachable", "offline"].includes(normalized)) {
    return "当前不可用";
  }
  if (["disabled", "off", "none"].includes(normalized)) {
    return "未启用";
  }
  return status;
}

function summarizeChannelEntry(key: string, value: unknown): string {
  const label = CHANNEL_LABELS[key] ?? key;
  if (typeof value === "boolean") {
    return `${label}${value ? "已连接" : "未启用"}`;
  }
  if (typeof value === "string") {
    return `${label}${formatChannelStatusText(value)}`;
  }
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    const status = firstStringValue(record, ["summary", "status", "state", "detail"]);
    if (status) {
      return `${label}${formatChannelStatusText(status)}`;
    }
    const connected = firstBooleanValue(record, [
      "connected",
      "ready",
      "enabled",
      "configured",
    ]);
    if (connected != null) {
      return `${label}${connected ? "已连接" : "待配置"}`;
    }
    return `${label}已配置`;
  }
  return `${label}状态未记录`;
}

function buildRemoteEntrySummary(channelSummaryEntries: Array<[string, unknown]>): string {
  if (channelSummaryEntries.length === 0) {
    return "现在先用 Web 就够了；Telegram 之类的远程入口可以后面再配。";
  }
  const userFacingEntries = channelSummaryEntries
    .map(([key, value]) => summarizeChannelEntry(key, value))
    .slice(0, 2);
  return `${userFacingEntries.join("；")}。先用 Web 也不受影响。`;
}

function buildAvailabilityImpact(options: {
  usingEchoMode: boolean;
  setupReady: boolean;
  diagnosticsStatus: string;
}): string {
  if (options.usingEchoMode) {
    return "现在还能先体验页面和流程，但实时查询和专门角色协作不会稳定工作。";
  }
  if (!options.setupReady) {
    return "当前配置还没完全收口；先补齐阻塞项，再开始会更稳。";
  }
  if (!READY_DIAGNOSTIC_STATUSES.has(options.diagnosticsStatus.trim().toLowerCase())) {
    return "普通聊天还能继续，但联网查询、外部连接或后台能力可能会变慢或失败。";
  }
  return "普通聊天、联网查询和多 Agent 协作都已经可以直接开始。";
}

function buildSessionSummary(summary: string): string {
  const normalized = normalizeWhitespace(summary);
  if (!normalized) {
    return "打开这条记录，就能继续回到上一次对话。";
  }
  if (looksTechnicalSummary(normalized)) {
    return "这条记录包含较多技术细节，打开后再继续查看会更清楚。";
  }
  return truncateText(normalized, 96);
}

function buildEchoModeGuidance(options: {
  setupReady: boolean;
  nextActions: string[];
  blockingReasons: string[];
}): { status: string; nextStep: string } {
  if (options.setupReady) {
    return {
      status: "当前配置已经可以保存，但还没有切到真实模型。",
      nextStep: "打开 Settings，连接 Provider 并切到真实模型后，再回来发第一条真实消息。",
    };
  }
  return {
    status: options.blockingReasons[0] ?? "当前还没有接入真实模型。",
    nextStep: options.nextActions[0] ?? "先到设置里接入一个真实模型。",
  };
}

function formatWorkSummary(work: WorkProjectionItem): string {
  const workerLabel = WORKER_LABELS[work.selected_worker_type] ?? "专门角色";
  if (work.status.toLowerCase() === "running") {
    return `这条后台工作还没收尾，目前主要由 ${workerLabel} 继续处理。`;
  }
  return "这条后台工作还没有完全收尾，可以按需回去继续看。";
}

function buildPrimaryState(options: {
  usingEchoMode: boolean;
  setupReady: boolean;
  diagnosticsStatus: string;
  operatorItems: OperatorInboxItem[];
  activeWorks: WorkProjectionItem[];
}): HomePrimaryState {
  if (options.usingEchoMode) {
    return {
      title: "模型未连接",
      summary: "当前处于体验模式，需要先在 Settings 中配置至少一个 Provider。",
      primaryActionLabel: "前往设置",
      primaryActionTo: "/settings",
      secondaryActionLabel: "体验聊天",
      secondaryActionTo: "/chat",
    };
  }

  if (!options.setupReady) {
    return {
      title: "配置未完成",
      summary: "部分阻塞项需要补齐后才能稳定使用。",
      primaryActionLabel: "前往设置",
      primaryActionTo: "/settings",
    };
  }

  if (options.operatorItems.length > 0) {
    return {
      title: `${options.operatorItems.length} 项待处理`,
      summary: "有需要确认或重试的操作。",
      primaryActionLabel: "查看详情",
      primaryActionTo: "/work",
      secondaryActionLabel: "继续聊天",
      secondaryActionTo: "/chat",
    };
  }

  if (!READY_DIAGNOSTIC_STATUSES.has(options.diagnosticsStatus.trim().toLowerCase())) {
    return {
      title: "部分能力受限",
      summary: "基础对话正常，联网查询或后台任务可能不稳定。",
      primaryActionLabel: "进入聊天",
      primaryActionTo: "/chat",
      secondaryActionLabel: "诊断详情",
      secondaryActionTo: "/advanced",
    };
  }

  if (options.activeWorks.length > 0) {
    return {
      title: `${options.activeWorks.length} 项任务进行中`,
      summary: "可以继续聊天，或查看当前进度。",
      primaryActionLabel: "继续聊天",
      primaryActionTo: "/chat",
      secondaryActionLabel: "查看任务",
      secondaryActionTo: "/work",
    };
  }

  return {
    title: "就绪",
    summary: "模型和主助手已准备好，可以开始对话。",
    primaryActionLabel: "进入聊天",
    primaryActionTo: "/chat",
    secondaryActionLabel: "设置",
    secondaryActionTo: "/settings",
  };
}

export default function HomePage() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const selector = snapshot!.resources.project_selector;
  const diagnostics = snapshot!.resources.diagnostics;
  const sessions = snapshot!.resources.sessions;
  const context = snapshot!.resources.context_continuity;
  const setup = snapshot!.resources.setup_governance;
  const config = snapshot!.resources.config;
  const delegation = snapshot!.resources.delegation;

  const currentProject =
    selector.available_projects.find((item) => item.project_id === selector.current_project_id) ??
    null;
  const [selectedProjectId, setSelectedProjectId] = useState(selector.current_project_id);
  const operatorItems = (sessions.operator_items ?? []).filter((item) => item.state === "pending");
  const activeWorks = delegation.works.filter((item) =>
    ACTIVE_WORK_STATUSES.has(String(item.status).toLowerCase())
  );
  const latestSession = useMemo(
    () =>
      [...sessions.sessions].sort((left, right) =>
        String(right.latest_event_at ?? "").localeCompare(String(left.latest_event_at ?? ""))
      )[0] ?? null,
    [sessions.sessions]
  );
  const runtimeMode =
    String(getValueAtPath(config.current_value, "runtime.llm_mode") ?? "echo")
      .trim()
      .toLowerCase() || "echo";
  const usingEchoMode = runtimeMode === "echo";
  const channelSummaryEntries = Object.entries(diagnostics.channel_summary ?? {}).filter(
    ([, value]) => Boolean(value)
  );
  const primaryState = buildPrimaryState({
    usingEchoMode,
    setupReady: setup.review.ready,
    diagnosticsStatus: diagnostics.overall_status,
    operatorItems,
    activeWorks,
  });
  const echoModeGuidance = buildEchoModeGuidance({
    setupReady: setup.review.ready,
    nextActions: setup.review.next_actions,
    blockingReasons: setup.review.blocking_reasons,
  });
  const showContextSwitcher = selector.available_projects.length > 1;
  const latestSessionTitle = latestSession?.title?.trim() || "还没有最近对话";
  const latestSessionSummary = latestSession?.latest_message_summary?.trim()
    ? buildSessionSummary(latestSession.latest_message_summary)
    : "发一条消息后，这里会显示你最近一次对话。";

  useEffect(() => {
    setSelectedProjectId(selector.current_project_id);
  }, [selector.current_project_id]);

  return (
    <div className="wb-page">
      <PageIntro
        kicker="主页"
        title={primaryState.title}
        summary={primaryState.summary}
        compact
        actions={
          <>
            <Link className="wb-button wb-button-primary" to={primaryState.primaryActionTo}>
              {primaryState.primaryActionLabel}
            </Link>
            {primaryState.secondaryActionLabel && primaryState.secondaryActionTo ? (
              <Link className="wb-button wb-button-secondary" to={primaryState.secondaryActionTo}>
                {primaryState.secondaryActionLabel}
              </Link>
            ) : null}
          </>
        }
      />

      <div className="wb-split">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">现在先做什么</p>
              <h3>
                {usingEchoMode || !setup.review.ready
                  ? "先把这一步补上"
                  : operatorItems.length > 0
                    ? "现在需要你处理的事情"
                    : activeWorks.length > 0
                      ? "当前还有这些事在跑"
                      : "你现在可以直接这样开始"}
              </h3>
            </div>
          </div>

          {usingEchoMode ? (
            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>当前状态</strong>
                <span>{echoModeGuidance.status}</span>
              </div>
              <div className="wb-note">
                <strong>下一步</strong>
                <span>{echoModeGuidance.nextStep}</span>
              </div>
              <Link className="wb-button wb-button-secondary" to="/settings">
                去设置页处理
              </Link>
            </div>
          ) : !setup.review.ready ? (
            <div className="wb-note-stack">
              {(setup.review.next_actions.length > 0
                ? setup.review.next_actions
                : ["先到设置里接入一个真实模型。"]
              )
                .slice(0, 2)
                .map((item) => (
                  <div key={item} className="wb-note">
                    <strong>下一步</strong>
                    <span>{item}</span>
                  </div>
                ))}
              {setup.review.blocking_reasons.slice(0, 2).map((item) => (
                <div key={item} className="wb-note">
                  <strong>为什么现在先做这一步</strong>
                  <span>{item}</span>
                </div>
              ))}
              <Link className="wb-button wb-button-secondary" to="/settings">
                去设置页处理
              </Link>
            </div>
          ) : operatorItems.length > 0 ? (
            <div className="wb-note-stack">
              {operatorItems.slice(0, 3).map((item) => {
                const userFacingItem = describeOperatorItemForUser(item);
                return (
                  <div key={item.item_id} className="wb-note">
                    <strong>{userFacingItem.title}</strong>
                    <span>{userFacingItem.summary}</span>
                    <small>{userFacingItem.nextStep}</small>
                  </div>
                );
              })}
              <Link className="wb-button wb-button-secondary" to="/work">
                去 Work 里处理
              </Link>
            </div>
          ) : activeWorks.length > 0 ? (
            <div className="wb-note-stack">
              {activeWorks.slice(0, 3).map((work) => (
                <div key={work.work_id} className="wb-note">
                  <strong>{work.title}</strong>
                  <span>{formatWorkSummary(work)}</span>
                </div>
              ))}
              <Link className="wb-button wb-button-secondary" to="/work">
                查看当前工作
              </Link>
            </div>
          ) : (
            <div className="wb-note-stack">
              <div className="wb-note">
                <strong>“帮我把今天下午的工作拆成 3 个优先级，并给我执行顺序。”</strong>
                <span>让主助手先帮你把任务收口成一个可执行顺序。</span>
              </div>
              <div className="wb-note">
                <strong>“深圳今天天气怎么样？我今天穿什么比较合适？”</strong>
                <span>用实时查询和内部协作链直接体验联网能力。</span>
              </div>
              <div className="wb-note">
                <strong>“帮我整理这周最重要的 3 件事，并告诉我先做什么。”</strong>
                <span>先从你自己的日常问题开始，比看设置卡片更能感受到系统价值。</span>
              </div>
            </div>
          )}
        </section>

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">如果你现在直接开始</p>
              <h3>你会感受到的影响</h3>
            </div>
          </div>
          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>聊天会不会被挡住</strong>
              <span>
                {usingEchoMode
                  ? "现在还能体验页面和对话流程，但回答不会代表真实能力。"
                  : !setup.review.ready
                    ? "基础对话可能还能继续，但先补齐设置会更稳。"
                    : "你现在可以直接开始对话，不需要先看控制台。"}
              </span>
            </div>
            <div className="wb-note">
              <strong>哪些能力可能受影响</strong>
              <span>
                {buildAvailabilityImpact({
                  usingEchoMode,
                  setupReady: setup.review.ready,
                  diagnosticsStatus: diagnostics.overall_status,
                })}
              </span>
            </div>
            <div className="wb-note">
              <strong>远程入口</strong>
              <span>{buildRemoteEntrySummary(channelSummaryEntries)}</span>
            </div>
            <div className="wb-note">
              <strong>这轮背景会不会断掉</strong>
              <span>
                {context.degraded.is_degraded
                  ? "当前只保留了基础背景摘要，继续聊天没问题，系统会慢慢把更完整的背景补齐。"
                  : "这轮对话背景已经连上了，继续追问时一般不用重复交代。"}
              </span>
            </div>
          </div>
        </section>
      </div>

      <div className="wb-split">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">最近一次对话</p>
              <h3>{latestSessionTitle}</h3>
            </div>
          </div>
          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>你上次发的是</strong>
              <span>{latestSessionSummary}</span>
            </div>
            {latestSession?.task_id ? (
              <Link className="wb-button wb-button-secondary" to={`/tasks/${latestSession.task_id}`}>
                打开这条记录
              </Link>
            ) : (
              <Link className="wb-button wb-button-secondary" to="/chat">
                进入聊天
              </Link>
            )}
          </div>
        </section>

        {showContextSwitcher ? (
          <section className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">切换工作上下文</p>
                <h3>只有你真的有多个上下文时，才需要在这里切换</h3>
              </div>
            </div>
            <p className="wb-panel-copy">
              当前项目 <strong>{currentProject?.name ?? selector.current_project_id}</strong>。
            </p>
            {selector.fallback_reason ? (
              <p className="wb-panel-copy wb-copy-warning">{selector.fallback_reason}</p>
            ) : null}
            <div className="wb-inline-form">
              {selector.available_projects.length > 1 ? (
                <label className="wb-field">
                  <span>切换 Project</span>
                  <select
                    value={selectedProjectId}
                    onChange={(event) => setSelectedProjectId(event.target.value)}
                  >
                    {selector.available_projects.map((project) => (
                      <option key={project.project_id} value={project.project_id}>
                        {project.name}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <button
                type="button"
                className="wb-button wb-button-secondary"
                disabled={
                  busyActionId === "project.select" ||
                  selectedProjectId === selector.current_project_id
                }
                onClick={() =>
                  void submitAction("project.select", {
                    project_id: selectedProjectId,
                  })
                }
              >
                切换
              </button>
            </div>
          </section>
        ) : null}
      </div>
    </div>
  );
}
