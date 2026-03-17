/**
 * RoundFlowCard -- 单个轮次的流程图卡片
 *
 * 水平连线的节点流：每个节点代表一次 LLM 调用 / 工具调用 / 产物等，
 * 按调用顺序排列，点击弹出详情弹框。
 */

import { useState } from "react";
import type { Round, FlowNode } from "../../utils/roundSplitter";
import { formatTime } from "../../utils/formatTime";

// ─── 节点图标 ────────────────────────────────────────────────

const KIND_ICON: Record<string, string> = {
  message: "📩",
  llm: "🧠",
  tool: "⚙️",
  skill: "🎯",
  worker: "👷",
  memory: "🔍",
  artifact: "📦",
  completion: "✅",
  decision: "🎲",
  a2a: "📡",
  approval: "🔐",
  error: "❌",
  other: "⚪",
};

function iconFor(node: FlowNode): string {
  if (node.kind === "completion" && node.status === "error") return "❌";
  return KIND_ICON[node.kind] || "⚪";
}

// ─── 节点标签截断 ────────────────────────────────────────────

function shortLabel(node: FlowNode): string {
  const { kind, label } = node;

  // 工具调用：取最后一段（如 filesystem.read_text → read_text）
  if (kind === "tool") {
    const parts = label.split(".");
    const name = parts[parts.length - 1].split(" ")[0]; // 去掉耗时
    return truncate(name, 10);
  }

  // LLM 调用：取简短模型名
  if (kind === "llm") {
    const name = label.split(" ")[0]; // 去掉耗时
    return truncate(name, 10);
  }

  return truncate(label, 10);
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + "…" : s;
}

// ─── 组件 ────────────────────────────────────────────────────

const STATUS_CLASS: Record<string, string> = {
  success: "tv-flow-node--success",
  error: "tv-flow-node--error",
  running: "tv-flow-node--running",
  neutral: "tv-flow-node--neutral",
};

interface Props {
  round: Round;
  onNodeClick: (node: FlowNode) => void;
}

const COLLAPSE_THRESHOLD = 30;
const VISIBLE_HEAD = 10;
const VISIBLE_TAIL = 5;

export default function RoundFlowCard({ round, onNodeClick }: Props) {
  const [expanded, setExpanded] = useState(false);

  const shouldCollapse =
    round.nodes.length > COLLAPSE_THRESHOLD && !expanded;
  const visibleNodes = shouldCollapse
    ? [
        ...round.nodes.slice(0, VISIBLE_HEAD),
        ...round.nodes.slice(-VISIBLE_TAIL),
      ]
    : round.nodes;
  const hiddenCount = round.nodes.length - VISIBLE_HEAD - VISIBLE_TAIL;

  return (
    <div className="tv-round-card">
      {/* 头部 */}
      <div className="tv-round-header">
        <span className="tv-round-index">#{round.index}</span>
        <span className="tv-round-message">
          {round.triggerMessage || "（无消息内容）"}
        </span>
        <span className="tv-round-time">
          {formatTime(round.startTime)}
          {round.endTime &&
            round.endTime !== round.startTime &&
            ` → ${formatTime(round.endTime)}`}
        </span>
      </div>

      {/* 流程图 */}
      <div className="tv-round-flow-scroll">
        <div className="tv-round-flow">
          {visibleNodes.map((node, i) => {
            const isCollapsePoint = shouldCollapse && i === VISIBLE_HEAD;

            return (
              <div key={node.id} style={{ display: "contents" }}>
                {/* 连线（不是第一个节点、且不是折叠点） */}
                {i > 0 && !isCollapsePoint && (
                  <div className="tv-flow-connector" />
                )}

                {/* 折叠点 */}
                {isCollapsePoint && (
                  <>
                    <div className="tv-flow-connector" />
                    <button
                      className="tv-flow-collapse-btn"
                      onClick={() => setExpanded(true)}
                    >
                      +{hiddenCount}
                    </button>
                    <div className="tv-flow-connector" />
                  </>
                )}

                {/* 节点 */}
                <button
                  className={`tv-flow-node ${STATUS_CLASS[node.status] || ""}`}
                  onClick={() => onNodeClick(node)}
                  title={node.label}
                >
                  <span className="tv-flow-node-circle">
                    {iconFor(node)}
                  </span>
                  <span className="tv-flow-node-text">
                    {shortLabel(node)}
                  </span>
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* 底部统计 */}
      <div className="tv-round-stats">
        <span>{round.nodes.length} 个步骤</span>
        {shouldCollapse && (
          <button
            className="tv-phase-expand-btn"
            onClick={() => setExpanded(true)}
          >
            展开全部
          </button>
        )}
        {expanded && round.nodes.length > COLLAPSE_THRESHOLD && (
          <button
            className="tv-phase-expand-btn"
            onClick={() => setExpanded(false)}
          >
            收起
          </button>
        )}
      </div>
    </div>
  );
}
