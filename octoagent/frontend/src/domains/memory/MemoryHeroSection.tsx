import { Link } from "react-router-dom";
import type { MemoryConsoleDocument } from "../../types";
import { formatDateTime } from "../../workbench/utils";
import type { MemoryGuideItem } from "./shared";
import { formatMemoryMode } from "./shared";

interface MemoryHeroSectionProps {
  memory: MemoryConsoleDocument;
  memoryMode: string;
  bridgeTransport: string;
  heroTone: "success" | "warning" | "danger";
  heroTitle: string;
  heroSummary: string;
  stateLabel: string;
  retrievalLabel: string;
  nextActionTitle: string;
  nextActionSummary: string;
  showNextActionPanel: boolean;
  guideItems: MemoryGuideItem[];
  hasVisibleRecords: boolean;
  hasStoredRecords: boolean;
  hasBacklog: boolean;
  isDegraded: boolean;
  missingSetupItems: string[];
  busyActionId: string | null;
  onResetFilters: () => Promise<void>;
  onFlushMemory: () => Promise<void>;
}

export default function MemoryHeroSection({
  memory,
  memoryMode,
  bridgeTransport,
  heroTone,
  heroTitle,
  heroSummary,
  stateLabel,
  retrievalLabel,
  nextActionTitle,
  nextActionSummary,
  showNextActionPanel,
  guideItems,
  hasVisibleRecords,
  hasStoredRecords,
  hasBacklog,
  isDegraded,
  missingSetupItems,
  busyActionId,
  onResetFilters,
  onFlushMemory,
}: MemoryHeroSectionProps) {
  const retrievalProfile = memory.retrieval_profile;
  const engineLabel = retrievalProfile?.engine_label || formatMemoryMode(memoryMode);
  const transportLabel = retrievalProfile?.transport_label || bridgeTransport || "内建";

  return (
    <>
      <section className="wb-hero wb-hero-memory">
        <div className="wb-hero-copy">
          <p className="wb-kicker">Memory</p>
          <h1>{heroTitle}</h1>
          <p>{heroSummary}</p>
          <div className="wb-chip-row">
            <span className="wb-chip">引擎 {engineLabel}</span>
            <span className={`wb-chip ${heroTone === "success" ? "is-success" : "is-warning"}`}>
              状态 {stateLabel}
            </span>
            <span className="wb-chip">当前检索 {retrievalLabel}</span>
            <span className="wb-chip">接入 {transportLabel}</span>
            <span className="wb-chip">更新时间 {formatDateTime(memory.updated_at)}</span>
          </div>
        </div>

        <div className="wb-hero-insights">
          <article className="wb-hero-metric">
            <p className="wb-card-label">当前结论</p>
            <strong>{memory.summary.sor_current_count}</strong>
            <span>这是已经整理成稳定结论的内容，最适合直接阅读。</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">新增片段</p>
            <strong>{memory.summary.fragment_count}</strong>
            <span>片段代表刚进入系统的新上下文，通常还会继续被整理。</span>
          </article>
          <article className="wb-hero-metric">
            <p className="wb-card-label">待处理内容</p>
            <strong>{memory.summary.pending_replay_count + memory.summary.vault_ref_count}</strong>
            <span>
              待补齐 {memory.summary.pending_replay_count} / 需授权 {memory.summary.vault_ref_count}
            </span>
          </article>
        </div>
      </section>

      <div className="wb-split">
        {showNextActionPanel ? (
          <section className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">下一步</p>
                <h3>{nextActionTitle}</h3>
              </div>
              <span className={`wb-status-pill is-${heroTone}`}>{stateLabel}</span>
            </div>

            <p className="wb-panel-copy">{nextActionSummary}</p>

            <div className="wb-note-stack">
              {guideItems.map((item) => (
                <div key={`${item.title}-${item.summary}`} className="wb-note">
                  <div className="wb-panel-head">
                    <strong>{item.title}</strong>
                    <span
                      className={`wb-status-pill is-${
                        item.state === "optional" ? "draft" : "warning"
                      }`}
                    >
                      {item.state === "optional" ? "可选" : "待处理"}
                    </span>
                  </div>
                  <span>{item.summary}</span>
                </div>
              ))}
            </div>

            <div className="wb-inline-actions wb-inline-actions-wrap">
              {!hasVisibleRecords || !hasStoredRecords ? (
                <Link className="wb-button wb-button-primary" to="/">
                  去 Chat 产生内容
                </Link>
              ) : null}
              {memoryMode === "local_only" || isDegraded || missingSetupItems.length > 0 ? (
                <Link className="wb-button wb-button-secondary" to="/settings#settings-group-memory">
                  打开 Settings &gt; Memory
                </Link>
              ) : null}
              {!hasVisibleRecords && hasStoredRecords ? (
                <button
                  type="button"
                  className="wb-button wb-button-secondary"
                  onClick={() => void onResetFilters()}
                  disabled={busyActionId === "memory.query"}
                >
                  清空筛选后重查
                </button>
              ) : null}
              {hasBacklog ? (
                <button
                  type="button"
                  className="wb-button wb-button-tertiary"
                  onClick={() => void onFlushMemory()}
                  disabled={busyActionId === "memory.flush"}
                >
                  {busyActionId === "memory.flush" ? "整理中..." : "整理最新记忆"}
                </button>
              ) : null}
              {isDegraded ? (
                <Link className="wb-button wb-button-tertiary" to="/advanced">
                  打开 Advanced 诊断
                </Link>
              ) : null}
            </div>
          </section>
        ) : null}

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">为什么这样判断</p>
              <h3>这些信息决定了当前状态</h3>
            </div>
          </div>

          <div className="wb-note-stack">
            <div className="wb-note">
              <strong>当前模式</strong>
              <span>
                {engineLabel}
                {retrievalProfile?.backend_summary
                  ? `：${retrievalProfile.backend_summary}`
                  : memoryMode === "local_only"
                    ? "：基础链路，不需要额外 Memory 服务。"
                    : "：适合需要跨会话检索和更强回放能力的场景。"}
              </span>
            </div>
            <div className="wb-note">
              <strong>当前记忆路径</strong>
              <span>
                {retrievalLabel}
                {memoryMode === "memu" && memory.retrieval_backend && memory.retrieval_backend !== "memu"
                  ? "。增强记忆暂时回退到了本地路径，但基础内容仍然可读。"
                  : "。这是这次页面实际使用的记忆检索路径。"}
              </span>
            </div>
            {retrievalProfile?.bindings?.map((item) => (
              <div key={item.binding_key} className="wb-note">
                <div className="wb-panel-head">
                  <strong>{item.label}</strong>
                  <span
                    className={`wb-status-pill is-${
                      item.status === "configured"
                        ? "success"
                        : item.status === "misconfigured"
                          ? "warning"
                          : "draft"
                    }`}
                  >
                    {item.effective_label}
                  </span>
                </div>
                <span>{item.summary}</span>
                {item.configured_alias && item.configured_alias !== item.effective_target ? (
                  <span>已配置 {item.configured_alias}，当前实际回退到 {item.effective_label}。</span>
                ) : null}
              </div>
            ))}
            <div className="wb-note">
              <strong>内容覆盖范围</strong>
              <span>
                {memory.summary.sor_current_count +
                  memory.summary.fragment_count +
                  memory.summary.vault_ref_count >
                0
                  ? `当前累计 ${
                      memory.summary.sor_current_count +
                      memory.summary.fragment_count +
                      memory.summary.vault_ref_count
                    } 条结论、片段或受保护引用，来自 ${
                      memory.available_scopes.length || memory.summary.scope_count || 0
                    } 个上下文范围。`
                  : "当前还没有形成可读记忆，通常只是还没发生聊天或导入。"}
              </span>
            </div>
            <div className="wb-note">
              <strong>设置入口</strong>
              <span>
                {memoryMode === "local_only"
                  ? "Settings > Memory 现在主要用来绑定加工、扩写、embedding 和 rerank 模型。当前这套实例还在用内建记忆引擎，不需要额外 bridge。"
                  : bridgeTransport === "command"
                    ? "Settings > Memory 仍然可以绑定 retrieval models；当前这套实例额外走了本地 MemU 兼容链路。command / http 这组字段只在旧实例迁移或排障时才需要展开。"
                    : "Settings > Memory 仍然可以绑定 retrieval models；当前这套实例额外走了远端 MemU 兼容链路。command / http 这组字段只在旧实例迁移或排障时才需要展开。"}
              </span>
              <div className="wb-inline-actions">
                <Link
                  className="wb-button wb-button-tertiary wb-button-inline"
                  to="/settings#settings-group-memory"
                >
                  打开 Settings &gt; Memory
                </Link>
              </div>
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
