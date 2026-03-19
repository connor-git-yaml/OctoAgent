/**
 * RoundFlowCard -- 单个轮次的流程图卡片
 *
 * 按 Agent 分行展示节点流：每行代表一个 Agent 的执行片段，
 * 行首显示 Agent 名称，行内节点按调用顺序排列，点击弹出详情弹框。
 */

import { useState, useMemo } from "react";
import type { Round, FlowNode } from "../../utils/roundSplitter";
import { groupByAgent } from "../../utils/roundSplitter";
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

export default function RoundFlowCard({ round, onNodeClick }: Props) {
  const [expanded, setExpanded] = useState(false);

  const lanes = useMemo(() => groupByAgent(round.nodes), [round.nodes]);

  // 折叠逻辑：基于总节点数
  const shouldCollapse =
    round.nodes.length > COLLAPSE_THRESHOLD && !expanded;

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

      {/* 按 Agent 分行的流程图 */}
      <div className="tv-lanes">
        {lanes.map((lane, laneIdx) => {
          // 折叠模式下只保留前后几个 lane
          if (shouldCollapse && laneIdx >= 2 && laneIdx < lanes.length - 1) {
            if (laneIdx === 2) {
              return (
                <div key="collapse" className="tv-lane">
                  <div className="tv-lane-label" />
                  <div className="tv-lane-flow-scroll">
                    <div className="tv-lane-flow">
                      <button
                        className="tv-flow-collapse-btn"
                        onClick={() => setExpanded(true)}
                      >
                        +{lanes.length - 3} 个 Agent 泳道
                      </button>
                    </div>
                  </div>
                </div>
              );
            }
            return null;
          }

          return (
            <div key={`${lane.agent}-${laneIdx}`} className="tv-lane">
              {/* Agent 名称标签 */}
              <div className="tv-lane-label" title={lane.agent}>
                <span className="tv-lane-label-text">{lane.agent}</span>
              </div>

              {/* 该 Agent 的节点流 */}
              <div className="tv-lane-flow-scroll">
                <div className="tv-lane-flow">
                  {lane.nodes.map((node, i) => (
                    <div key={node.id} style={{ display: "contents" }}>
                      {/* 连线 */}
                      {i > 0 && <div className="tv-flow-connector" />}

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
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* 底部统计 */}
      <div className="tv-round-stats">
        <span>{round.nodes.length} 个步骤</span>
        <span className="tv-round-stats-agents">
          {new Set(round.nodes.map((n) => n.agent)).size} 个 Agent
        </span>
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
