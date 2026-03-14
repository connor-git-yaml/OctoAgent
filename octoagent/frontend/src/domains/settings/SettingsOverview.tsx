import { Link } from "react-router-dom";
import type {
  ProjectSelectorDocument,
  SetupReviewSummary,
} from "../../types";

interface SettingsOverviewProps {
  usingEchoMode: boolean;
  review: SetupReviewSummary;
  selector: ProjectSelectorDocument;
  providerDraftCount: number;
  activeProvidersCount: number;
  aliasDraftCount: number;
  defaultProviderId: string;
  memoryLabel: string;
  memoryStatus: string;
  onQuickConnect: () => void;
  onReview: () => void;
  onApply: () => void;
  connectBusy: boolean;
  onScrollToSection: (sectionId: string) => void;
}

export default function SettingsOverview({
  usingEchoMode,
  review,
  selector,
  providerDraftCount,
  activeProvidersCount,
  aliasDraftCount,
  defaultProviderId,
  memoryLabel,
  memoryStatus,
  onQuickConnect,
  onReview,
  onApply,
  connectBusy,
  onScrollToSection,
}: SettingsOverviewProps) {
  const reviewBlockingCount = review.blocking_reasons.length;
  const echoReady = usingEchoMode && review.ready;
  const currentProject =
    selector.available_projects.find((item) => item.project_id === selector.current_project_id) ??
    null;
  const currentWorkspace =
    selector.available_workspaces.find(
      (item) => item.workspace_id === selector.current_workspace_id
    ) ?? null;
  const primaryTitle = usingEchoMode
    ? "先连上至少一个模型 Provider"
    : review.ready
      ? "现在已经可以回聊天验证"
      : `还差 ${Math.max(reviewBlockingCount, 1)} 项才能稳定开始`;
  const primarySummary = usingEchoMode
    ? "还没有连接真实模型。先添加一个 Provider，并补 API Key 或 OAuth；没配好前系统会先自动回退。"
    : review.ready
      ? "当前配置已经够用。先保存这次修改，再回聊天页验证 Butler 的真实回复。"
      : "不用一次看完所有配置。先补齐阻塞项，再回来慢慢扩展其他能力。";
  const primaryActionLabel = usingEchoMode
    ? "连接真实模型"
    : review.ready
      ? "保存后回聊天验证"
      : "先检查阻塞项";
  const checklistItems = usingEchoMode
    ? [
        review.next_actions[0] ?? "先添加一个 Provider，并填好密钥或完成 OAuth。",
        echoReady
          ? "现在可以先保存这次配置；保存后系统会自动开始使用真实模型。"
          : "补好 API Key 或完成 OAuth 连接后，保存配置。",
        "回聊天页发第一条真实消息，确认真实模型已经接管回复。",
      ]
    : review.ready
      ? [
          "先保存当前配置，确保这次修改已经生效。",
          "回到聊天页发第一条消息，确认真实模型和 Butler 已经正常响应。",
          "如果这轮验证没问题，再回来补渠道、Memory 或更多能力。",
        ]
      : [
          review.next_actions[0] ?? "先处理 review 里提示的阻塞项。",
          "执行一次配置检查，确认阻塞项和提醒是否已经收口。",
          "保存配置后，回聊天页验证一次真实对话。",
        ];
  const canWaitItems = [
    "渠道与远程入口：现在先用 Web 就够了，Telegram 和其他远程入口可以后面再配。",
    `记忆增强：当前 ${memoryLabel} 已经是可用状态；第一次真实对话不依赖你现在就把它调到最优。`,
    "Agent 能力与 Provider 绑定：后面需要扩展时，再去 Agents > Providers 处理也来得及。",
  ];
  const minimumStatusValue = usingEchoMode ? "未连接真实模型" : "已连接真实模型";
  const minimumStatusHint = usingEchoMode
    ? "没配好前系统会先自动回退。"
    : "当前已经接入真实模型链路。";
  const nextValidationHint = usingEchoMode
    ? "配好后保存配置，再回聊天页发第一条真实消息。"
    : review.ready
      ? "保存后直接回聊天页发第一条消息。"
      : "先在当前页做一次检查，再决定保存。";
  const statusChipLabel = usingEchoMode
    ? echoReady
      ? "可以先保存"
      : `还差 ${reviewBlockingCount} 项`
    : review.ready
      ? "可以回聊天验证"
      : `还差 ${reviewBlockingCount} 项`;

  return (
    <>
      <section
        id="settings-group-overview"
        className="wb-hero wb-settings-hero wb-settings-hero-refined"
      >
        <div className="wb-hero-copy">
          <p className="wb-kicker">Settings</p>
          <h1>{primaryTitle}</h1>
          <p>{primarySummary}</p>
          <div className="wb-chip-row">
            <span className="wb-chip">{minimumStatusValue}</span>
            <span className="wb-chip">
              当前项目默认 {currentProject?.name ?? selector.current_project_id} /{" "}
              {currentWorkspace?.name ?? selector.current_workspace_id}
            </span>
            <span className="wb-chip">Providers / Memory = 平台级</span>
            <span className="wb-chip">Behavior Files = 项目默认</span>
            <span className={`wb-chip ${review.ready ? "is-success" : "is-warning"}`} role="status">
              {statusChipLabel}
            </span>
          </div>
        </div>
        <div className="wb-settings-hero-actions">
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={usingEchoMode ? onQuickConnect : review.ready ? onApply : onQuickConnect}
            disabled={connectBusy}
          >
            {primaryActionLabel}
          </button>
          <button
            type="button"
            className="wb-button wb-button-secondary"
            onClick={onReview}
            disabled={connectBusy}
          >
            检查配置
          </button>
          {usingEchoMode ? (
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={onApply}
              disabled={connectBusy}
            >
              保存配置
            </button>
          ) : review.ready ? (
            <Link className="wb-button wb-button-secondary" to="/chat">
              回聊天验证
            </Link>
          ) : (
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={onApply}
              disabled={connectBusy}
            >
              保存配置
            </button>
          )}
        </div>
      </section>

      <section className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">作用域说明</p>
            <h3>这里改的是平台设置和项目默认，不是某一条会话本身</h3>
          </div>
        </div>
        <div className="wb-note-stack">
          <div className="wb-note">
            <strong>平台级</strong>
            <span>Models & Providers、Memory bridge 会影响整个平台默认运行面。</span>
          </div>
          <div className="wb-note">
            <strong>项目默认</strong>
            <span>
              Behavior Files 会影响当前项目后续新会话的默认行为，但不会反向改写旧会话。
            </span>
          </div>
          <div className="wb-note">
            <strong>已有会话</strong>
            <span>
              已经创建的会话会继续沿用自己的 session-scoped project / workspace 绑定。
            </span>
          </div>
        </div>
      </section>

      <div className="wb-split">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">现在只管这 3 件事</p>
              <h3>先走通第一次真实对话</h3>
            </div>
          </div>
          <div className="wb-note-stack">
            {checklistItems.map((item, index) => (
              <div key={item} className="wb-note">
                <strong>第 {index + 1} 步</strong>
                <span>{item}</span>
              </div>
            ))}
            <div className="wb-note">
              <strong>做完后回哪里验证</strong>
              <span>
                {nextValidationHint} 当前默认模型来源 {defaultProviderId || "未设置"}。
              </span>
            </div>
          </div>
        </section>

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">这些事情现在不用急</p>
              <h3>先用起来，再慢慢扩展</h3>
            </div>
          </div>
          <div className="wb-note-stack">
            {canWaitItems.map((item) => (
              <div key={item} className="wb-note">
                <strong>可以后面再说</strong>
                <span>{item}</span>
              </div>
            ))}
            <div className="wb-note">
              <strong>当前基础状态</strong>
              <span>
                {minimumStatusHint} 当前 Memory 状态是 {memoryStatus}，模型连接会跟着你保存的
                Provider 自动更新。
              </span>
            </div>
          </div>
        </section>
      </div>

      <section className="wb-panel">
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">完整配置还在下面</p>
            <h3>只有你需要继续细配时，再往下看</h3>
          </div>
          <div className="wb-inline-actions wb-inline-actions-wrap">
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={onReview}
              disabled={connectBusy}
            >
              先检查配置
            </button>
            <Link
              className="wb-button wb-button-tertiary"
              to="/agents?view=providers"
            >
              打开 Agents &gt; Providers
            </Link>
            {!usingEchoMode && review.ready ? (
              <Link className="wb-button wb-button-tertiary" to="/chat">
                回聊天验证
              </Link>
            ) : null}
          </div>
        </div>

        <div className="wb-note-stack">
          <div className="wb-note">
            <strong>这页下面还有什么</strong>
            <span>
              Providers、模型别名、Memory 和渠道连接都还在下面；只想先用起来，不必一次看完。
            </span>
          </div>
          <div className="wb-note">
            <strong>哪些内容已经移走了</strong>
            <span>
              Skill / MCP Provider 的安装、当前项目默认启用范围，以及 Butler / Worker 的绑定，
              现在统一去 Agents &gt; Providers 处理。
            </span>
          </div>
        </div>

        <div className="wb-card-grid wb-card-grid-3">
          <article className="wb-card">
            <p className="wb-card-label">模型与别名</p>
            <strong>{activeProvidersCount} 个已启用 Provider</strong>
            <span>当前草稿 {providerDraftCount}</span>
            <span>模型别名 {aliasDraftCount}</span>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">记忆连接</p>
            <strong>{memoryLabel}</strong>
            <span>{memoryStatus}</span>
            <span>Memory 连接和模式切换仍在本页处理。</span>
          </article>
          <article className="wb-card">
            <p className="wb-card-label">Agent 能力入口</p>
            <strong>Agents &gt; Providers</strong>
            <span>这里不再负责 Skills / MCP 的安装和授权。</span>
          </article>
        </div>
      </section>

      <nav className="wb-settings-section-nav" aria-label="Settings sections">
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("overview")}>
          概览
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("models")}>
          Providers
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("aliases")}>
          模型别名
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("channels")}>
          渠道
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("memory")}>
          Memory
        </button>
        <button type="button" className="wb-section-chip" onClick={() => onScrollToSection("review")}>
          保存检查
        </button>
      </nav>
    </>
  );
}
