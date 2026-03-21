/**
 * NodeDetailModal -- 流程图节点详情弹框
 *
 * 点击流程图节点弹出，按类型展示不同内容：
 * - LLM 调用：模型、token、耗时
 * - 工具调用：工具名、参数、结果
 * - 产物：文件信息 + 图片预览
 * - 通用：事件类型、时间、payload
 */

import { useEffect } from "react";
import type { FlowNode } from "../../utils/roundSplitter";
import type { TaskEvent, ArtifactPart } from "../../types";
import { formatTime } from "../../utils/formatTime";

interface Props {
  node: FlowNode | null;
  onClose: () => void;
}

export default function NodeDetailModal({ node, onClose }: Props) {
  // ESC 关闭
  useEffect(() => {
    if (!node) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [node, onClose]);

  if (!node) return null;

  const event = node.events[0];
  const endEvent = node.events.length > 1 ? node.events[1] : undefined;

  return (
    <div className="tv-modal-overlay" onClick={onClose}>
      <div className="tv-modal" onClick={(e) => e.stopPropagation()}>
        {/* 头部 */}
        <div className="tv-modal-header">
          <h3 className="tv-modal-title">{kindTitle(node.kind)}</h3>
          <span className="tv-modal-event-id" title={event.event_id}>
            {event.event_id}
          </span>
          <button className="tv-modal-close" onClick={onClose}>
            ✕
          </button>
        </div>

        {/* 内容 */}
        <div className="tv-modal-body">
          {/* 基本信息 */}
          <div className="tv-modal-meta">
            <Row label="时间">{formatTime(node.ts)}</Row>
            <Row label="状态">
              <span
                className={`tv-phase-event-badge tv-phase-event-badge--${statusBadge(node.status)}`}
              >
                {statusText(node.status)}
              </span>
            </Row>
            {event.actor && <Row label="Actor">{event.actor}</Row>}
          </div>

          {/* 类型特定内容 */}
          <KindContent node={node} event={event} endEvent={endEvent} />

          {/* 产物图片预览（兼容旧 artifact 字段） */}
          {node.artifact?.parts && (
            <ArtifactPreview parts={node.artifact.parts} />
          )}

          {/* 折叠附带的 Artifacts */}
          {node.artifacts.length > 0 && (
            <Section title={`产物 (${node.artifacts.length})`}>
              {node.artifacts.map((art) => (
                <div key={art.artifact_id} style={{ marginBottom: "8px" }}>
                  <div className="tv-modal-meta">
                    <Row label="名称">{art.name}</Row>
                    <Row label="大小">{formatSize(art.size)}</Row>
                  </div>
                  {art.parts?.some((p) => p.mime?.startsWith("image/") && p.content) && (
                    <ArtifactPreview parts={art.parts} />
                  )}
                  {art.parts?.filter((p) => p.content && !p.mime?.startsWith("image/")).map((p, pi) => (
                    <details key={pi} className="tv-modal-payload-details">
                      <summary>{p.mime || "内容"}</summary>
                      <pre className="tv-modal-payload">{p.content}</pre>
                    </details>
                  ))}
                </div>
              ))}
            </Section>
          )}

          {/* 原始 Payload */}
          <details className="tv-modal-payload-details">
            <summary>原始数据</summary>
            {node.events.map((evt, i) => (
              <div key={evt.event_id}>
                {node.events.length > 1 && (
                  <div className="tv-modal-payload-label">
                    {i === 0 ? "Start" : "End"}: {evt.type}
                  </div>
                )}
                <pre className="tv-modal-payload">
                  {JSON.stringify(evt.payload, null, 2)}
                </pre>
              </div>
            ))}
          </details>
        </div>
      </div>
    </div>
  );
}

// ─── 子组件 ──────────────────────────────────────────────────

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="tv-modal-meta-row">
      <span className="tv-modal-meta-key">{label}</span>
      <span>{children}</span>
    </div>
  );
}

function KindContent({
  node,
  event,
  endEvent,
}: {
  node: FlowNode;
  event: TaskEvent;
  endEvent?: TaskEvent;
}) {
  const p = endEvent?.payload || event.payload;

  switch (node.kind) {
    case "message":
      return (
        <Section title="消息内容">
          <div className="tv-modal-content-text">
            {String(event.payload?.content || event.payload?.text || "—")}
          </div>
        </Section>
      );

    case "llm": {
      const model =
        s(p.model_name) || s(event.payload?.model_alias) || "—";
      const tokens = (p.tokens || p.token_usage || {}) as Record<
        string,
        unknown
      >;
      const duration = Number(p.duration_ms) || 0;
      const toolCalls = (p.tool_calls || []) as Array<{ tool_name?: string; arguments?: unknown }>;
      return (
        <Section title="LLM 调用详情">
          <div className="tv-modal-meta">
            <Row label="模型">{model}</Row>
            {!!(tokens.prompt_tokens || tokens.prompt) && (
              <Row label="Token">
                {num(tokens.prompt_tokens ?? tokens.prompt)} prompt /{" "}
                {num(tokens.completion_tokens ?? tokens.completion)} completion
              </Row>
            )}
            {duration > 0 && <Row label="耗时">{fmtMs(duration)}</Row>}
          </div>
          {toolCalls.length > 0 && (
            <Section title={`工具调用 (${toolCalls.length})`}>
              <div className="tv-modal-tool-calls">
                {toolCalls.map((tc, i) => (
                  <details key={i} className="tv-modal-payload-details">
                    <summary>{tc.tool_name || `tool_call_${i}`}</summary>
                    {tc.arguments != null && (
                      <pre className="tv-modal-payload">
                        {typeof tc.arguments === "string"
                          ? tc.arguments
                          : JSON.stringify(tc.arguments, null, 2)}
                      </pre>
                    )}
                  </details>
                ))}
              </div>
            </Section>
          )}
          {node.status === "error" && !!p.error && (
            <div className="tv-modal-error">{String(p.error)}</div>
          )}
        </Section>
      );
    }

    case "tool": {
      const toolName =
        s(p.tool_name) || s(event.payload?.tool_name) || "—";
      const duration = Number(p.duration_ms) || 0;
      return (
        <Section title="工具调用详情">
          <div className="tv-modal-meta">
            <Row label="工具">{toolName}</Row>
            {duration > 0 && <Row label="耗时">{fmtMs(duration)}</Row>}
          </div>
          {!!event.payload?.arguments && (
            <details className="tv-modal-payload-details">
              <summary>参数</summary>
              <pre className="tv-modal-payload">
                {typeof event.payload.arguments === "string"
                  ? event.payload.arguments
                  : JSON.stringify(event.payload.arguments, null, 2)}
              </pre>
            </details>
          )}
          {p.result != null && (
            <details className="tv-modal-payload-details">
              <summary>结果</summary>
              <pre className="tv-modal-payload">
                {typeof p.result === "string"
                  ? p.result
                  : JSON.stringify(p.result, null, 2)}
              </pre>
            </details>
          )}
          {node.status === "error" && !!p.error && (
            <div className="tv-modal-error">{String(p.error)}</div>
          )}
        </Section>
      );
    }

    case "skill": {
      const skillName =
        s(p.skill_id) || s(event.payload?.skill_id) || "—";
      const duration = Number(p.duration_ms) || 0;
      return (
        <Section title="Skill 执行详情">
          <div className="tv-modal-meta">
            <Row label="Skill">{skillName}</Row>
            {duration > 0 && <Row label="耗时">{fmtMs(duration)}</Row>}
          </div>
          {node.status === "error" && !!p.error && (
            <div className="tv-modal-error">{String(p.error)}</div>
          )}
        </Section>
      );
    }

    case "artifact": {
      const artifact = node.artifact;
      if (!artifact) return null;
      return (
        <Section title="产物信息">
          <div className="tv-modal-meta">
            <Row label="名称">{artifact.name}</Row>
            <Row label="大小">{formatSize(artifact.size)}</Row>
          </div>
        </Section>
      );
    }

    case "completion":
      return (
        <Section title="状态变更">
          <div className="tv-modal-meta">
            <Row label="从">{s(event.payload?.from_status) || "—"}</Row>
            <Row label="到">{s(event.payload?.to_status) || "—"}</Row>
          </div>
        </Section>
      );

    case "worker":
      return (
        <Section title="Worker 信息">
          <div className="tv-modal-meta">
            {!!event.payload?.worker_type && (
              <Row label="类型">{s(event.payload.worker_type)}</Row>
            )}
            {!!event.payload?.worker_id && (
              <Row label="ID">{s(event.payload.worker_id)}</Row>
            )}
          </div>
        </Section>
      );

    case "decision": {
      const routeReason = String(event.payload?.route_reason || "");
      const isDirectExec = routeReason.startsWith("butler_direct_execution:");
      return (
        <Section title={isDirectExec ? "Agent 直接处理" : "调度决策"}>
          <div className="tv-modal-meta">
            {!!event.payload?.decision && (
              <Row label="决策">{s(event.payload.decision)}</Row>
            )}
            {!!routeReason && (
              <Row label="路由原因">{s(routeReason)}</Row>
            )}
            {!!event.payload?.reason && (
              <Row label="原因">{s(event.payload.reason)}</Row>
            )}
          </div>
        </Section>
      );
    }

    case "error":
      return (
        <Section title="错误信息">
          <div className="tv-modal-error">
            {String(event.payload?.message || event.payload?.error || "未知错误")}
          </div>
        </Section>
      );

    default:
      return null;
  }
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="tv-modal-section">
      <div className="tv-modal-section-title">{title}</div>
      {children}
    </div>
  );
}

function ArtifactPreview({ parts }: { parts: ArtifactPart[] }) {
  const imageParts = parts.filter(
    (p) => p.mime?.startsWith("image/") && p.content,
  );
  if (imageParts.length === 0) return null;

  return (
    <Section title="预览">
      <div className="tv-modal-image-grid">
        {imageParts.map((part, i) => {
          const src =
            part.content!.startsWith("data:") ||
            part.content!.startsWith("http")
              ? part.content!
              : `data:${part.mime};base64,${part.content}`;
          return (
            <img
              key={i}
              src={src}
              alt={`产物预览 ${i + 1}`}
              className="tv-modal-image"
            />
          );
        })}
      </div>
    </Section>
  );
}

// ─── 辅助 ────────────────────────────────────────────────────

function kindTitle(kind: string): string {
  const map: Record<string, string> = {
    message: "用户消息",
    llm: "LLM 调用",
    tool: "工具调用",
    skill: "Skill 执行",
    worker: "Worker 派发",
    memory: "记忆检索",
    artifact: "产物",
    completion: "任务完成",
    decision: "调度/路由",
    a2a: "A2A 消息",
    approval: "审批",
    error: "错误",
    other: "事件",
  };
  return map[kind] || kind;
}

function statusBadge(status: string): string {
  if (status === "error") return "danger";
  if (status === "success") return "success";
  return "warning";
}

function statusText(status: string): string {
  const map: Record<string, string> = {
    success: "成功",
    error: "失败",
    running: "进行中",
    neutral: "—",
  };
  return map[status] || status;
}

function s(value: unknown): string {
  if (value == null) return "";
  return String(value).trim();
}

function num(value: unknown): string {
  const n = Number(value || 0);
  return n.toLocaleString();
}

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
