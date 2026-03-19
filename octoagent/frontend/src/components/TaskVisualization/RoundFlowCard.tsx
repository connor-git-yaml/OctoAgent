/**
 * RoundFlowCard -- 单个轮次的流程图卡片
 *
 * 按 Agent 分行展示节点流：每行代表一个 Agent 的执行片段，
 * 行首显示 Agent 名称 + 耗时 + 状态，行内节点带耗时角标和 Artifact 角标。
 *
 * 支持两种布局模式：
 * - 时间轴布局（degraded=false）：节点按时间戳水平定位，Worker 展宽为胶囊条
 * - 降级布局（degraded=true）：保留原有 flex 等宽布局
 */

import { useState, useMemo } from "react";
import type { Round, FlowNode } from "../../utils/roundSplitter";
import { groupByAgent, computeTimelineLayout, MIN_NODE_WIDTH } from "../../utils/roundSplitter";
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

  if (kind === "tool") {
    const parts = label.split(".");
    const name = parts[parts.length - 1].split(" ")[0];
    return truncate(name, 10);
  }

  if (kind === "llm") {
    const name = label.split(" ")[0];
    return truncate(name, 10);
  }

  return truncate(label, 10);
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + "…" : s;
}

/** 格式化毫秒为简短文本 */
function fmtDur(ms: number): string {
  if (ms <= 0) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}

// ─── 泳道状态图标 ───────────────────────────────────────────

function laneStatusIcon(status: string): string {
  switch (status) {
    case "success": return "✓";
    case "error": return "✗";
    case "running": return "…";
    default: return "";
  }
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
/** 泳道高度常量（min-height 52px + gap 约 8px） */
const LANE_HEIGHT = 60;

export default function RoundFlowCard({ round, onNodeClick }: Props) {
  const [expanded, setExpanded] = useState(false);

  const lanes = useMemo(() => groupByAgent(round.nodes), [round.nodes]);

  // 时间轴布局计算
  const layout = useMemo(
    () => computeTimelineLayout(lanes, round.startTime, round.endTime),
    [lanes, round.startTime, round.endTime],
  );

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

      {/* 根据 degraded 标志选择渲染路径 */}
      {layout.degraded ? (
        /* ─── 降级路径：保留原有 flex 等宽布局，不做任何改动 ─── */
        <div className="tv-lanes">
          {lanes.map((lane, laneIdx) => {
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

            const durText = fmtDur(lane.totalDurationMs);
            const statusIco = laneStatusIcon(lane.laneStatus);

            return (
              <div key={`${lane.agent}-${laneIdx}`} className="tv-lane">
                {/* Agent 标签 + 耗时 + 状态 */}
                <div className="tv-lane-label" title={lane.agent}>
                  <span className="tv-lane-label-text">{lane.agent}</span>
                  <span className="tv-lane-label-meta">
                    {statusIco && (
                      <span className={`tv-lane-status tv-lane-status--${lane.laneStatus}`}>
                        {statusIco}
                      </span>
                    )}
                    {durText && <span className="tv-lane-dur">{durText}</span>}
                  </span>
                </div>

                {/* 节点流 */}
                <div className="tv-lane-flow-scroll">
                  <div className="tv-lane-flow">
                    {lane.nodes.map((node, i) => (
                      <div key={node.id} style={{ display: "contents" }}>
                        {i > 0 && <div className="tv-flow-connector" />}

                        <button
                          className={`tv-flow-node ${STATUS_CLASS[node.status] || ""}`}
                          onClick={() => onNodeClick(node)}
                          title={node.label}
                        >
                          {/* 耗时角标（右上角） */}
                          {node.durationMs > 0 && (
                            <span className="tv-flow-node-dur">{fmtDur(node.durationMs)}</span>
                          )}
                          <span className="tv-flow-node-circle">
                            {iconFor(node)}
                          </span>
                          {/* Artifact 角标（右下角） */}
                          {node.artifacts.length > 0 && (
                            <span className="tv-flow-node-artifact" title={`${node.artifacts.length} 个产物`}>
                              📦
                            </span>
                          )}
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
      ) : (
        /* ─── 时间轴路径：节点按时间定位，Worker 展宽为胶囊条 ─── */
        <div className="tv-timeline-container">
          <div className="tv-lanes-scroll">
            {/* 泳道列表 */}
            <div className="tv-lanes">
              {lanes.map((lane, laneIdx) => {
                if (shouldCollapse && laneIdx >= 2 && laneIdx < lanes.length - 1) {
                  if (laneIdx === 2) {
                    return (
                      <div key="collapse" className="tv-lane">
                        <div className="tv-lane-label" />
                        <div className="tv-lane-track" style={{ width: layout.totalWidthPx }}>
                          <button
                            className="tv-flow-collapse-btn"
                            onClick={() => setExpanded(true)}
                            style={{ position: "absolute", left: 0, top: 6 }}
                          >
                            +{lanes.length - 3} 个 Agent 泳道
                          </button>
                        </div>
                      </div>
                    );
                  }
                  return null;
                }

                const durText = fmtDur(lane.totalDurationMs);
                const statusIco = laneStatusIcon(lane.laneStatus);

                return (
                  <div key={`${lane.agent}-${laneIdx}`} className="tv-lane">
                    {/* Agent 标签 + 耗时 + 状态 */}
                    <div className="tv-lane-label" title={lane.agent}>
                      <span className="tv-lane-label-text">{lane.agent}</span>
                      <span className="tv-lane-label-meta">
                        {statusIco && (
                          <span className={`tv-lane-status tv-lane-status--${lane.laneStatus}`}>
                            {statusIco}
                          </span>
                        )}
                        {durText && <span className="tv-lane-dur">{durText}</span>}
                      </span>
                    </div>

                    {/* 时间轴轨道：节点 absolute 定位 */}
                    <div
                      className="tv-lane-track"
                      style={{ width: layout.totalWidthPx }}
                    >
                      {lane.nodes.map((node) => {
                        const nl = layout.nodeLayouts.get(node.id);
                        if (!nl) return null;

                        const isSpan = nl.widthPx > MIN_NODE_WIDTH;

                        return isSpan ? (
                          /* Worker 展宽节点：胶囊条 */
                          <button
                            key={node.id}
                            className={`tv-flow-node tv-flow-node--span ${STATUS_CLASS[node.status] || ""}`}
                            onClick={() => onNodeClick(node)}
                            title={node.label}
                            style={{
                              position: "absolute",
                              left: nl.leftPx,
                              width: nl.widthPx,
                              top: 4,
                            }}
                          >
                            {/* 耗时角标 */}
                            {node.durationMs > 0 && (
                              <span className="tv-flow-node-dur">{fmtDur(node.durationMs)}</span>
                            )}
                            <div className="tv-flow-node-bar">
                              <span className="tv-flow-node-bar-icon">
                                {iconFor(node)}
                              </span>
                              <span className="tv-flow-node-bar-text">
                                {shortLabel(node)}
                              </span>
                            </div>
                            {/* Artifact 角标 */}
                            {node.artifacts.length > 0 && (
                              <span className="tv-flow-node-artifact" title={`${node.artifacts.length} 个产物`}>
                                📦
                              </span>
                            )}
                          </button>
                        ) : (
                          /* 普通节点：保持圆形 */
                          <button
                            key={node.id}
                            className={`tv-flow-node ${STATUS_CLASS[node.status] || ""}`}
                            onClick={() => onNodeClick(node)}
                            title={node.label}
                            style={{
                              position: "absolute",
                              left: nl.leftPx,
                              top: 4,
                            }}
                          >
                            {/* 耗时角标 */}
                            {node.durationMs > 0 && (
                              <span className="tv-flow-node-dur">{fmtDur(node.durationMs)}</span>
                            )}
                            <span className="tv-flow-node-circle">
                              {iconFor(node)}
                            </span>
                            {/* Artifact 角标 */}
                            {node.artifacts.length > 0 && (
                              <span className="tv-flow-node-artifact" title={`${node.artifacts.length} 个产物`}>
                                📦
                              </span>
                            )}
                            <span className="tv-flow-node-text">
                              {shortLabel(node)}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* 跨泳道 SVG 斜线 overlay (T024) */}
            {layout.crossLaneLinks.length > 0 && (
              <svg
                className="tv-cross-lane-svg"
                width={layout.totalWidthPx}
                height={lanes.length * LANE_HEIGHT}
                style={{ marginLeft: `calc(110px + var(--cp-space-3))` }}
              >
                {layout.crossLaneLinks.map((link, i) => {
                  const fromNl = layout.nodeLayouts.get(link.fromNodeId);
                  const toNl = layout.nodeLayouts.get(link.toNodeId);
                  if (!fromNl || !toNl) return null;

                  // 泳道被折叠时跳过
                  if (shouldCollapse) {
                    const isFromCollapsed = link.fromLaneIndex >= 2 && link.fromLaneIndex < lanes.length - 1;
                    const isToCollapsed = link.toLaneIndex >= 2 && link.toLaneIndex < lanes.length - 1;
                    if (isFromCollapsed || isToCollapsed) return null;
                  }

                  // 节点中心坐标
                  const cx1 = fromNl.leftPx + fromNl.widthPx / 2;
                  const cy1 = link.fromLaneIndex * LANE_HEIGHT + LANE_HEIGHT / 2;
                  const cx2 = toNl.leftPx + toNl.widthPx / 2;
                  const cy2 = link.toLaneIndex * LANE_HEIGHT + LANE_HEIGHT / 2;

                  // 线段方向
                  const dx = cx2 - cx1;
                  const dy = cy2 - cy1;
                  const len = Math.sqrt(dx * dx + dy * dy);
                  if (len < 1) return null;

                  // 从中心沿方向找到 box 边界交点（不穿透节点）
                  const NODE_R = 18; // 圆形节点半径 + 2px 留白
                  const hw1 = (fromNl.widthPx > MIN_NODE_WIDTH ? fromNl.widthPx / 2 : NODE_R);
                  const hh1 = NODE_R;
                  const hw2 = (toNl.widthPx > MIN_NODE_WIDTH ? toNl.widthPx / 2 : NODE_R);
                  const hh2 = NODE_R;

                  const t1 = boxEdgeT(dx / len, dy / len, hw1, hh1);
                  const t2 = boxEdgeT(-dx / len, -dy / len, hw2, hh2);

                  const x1 = cx1 + (dx / len) * t1;
                  const y1 = cy1 + (dy / len) * t1;
                  const x2 = cx2 - (dx / len) * t2;
                  const y2 = cy2 - (dy / len) * t2;

                  return (
                    <line
                      key={i}
                      x1={x1}
                      y1={y1}
                      x2={x2}
                      y2={y2}
                      className={
                        link.type === "dispatch"
                          ? "tv-cross-line--dispatch"
                          : "tv-cross-line--return"
                      }
                    />
                  );
                })}
              </svg>
            )}
          </div>
        </div>
      )}

      {/* 底部统计 */}
      <div className="tv-round-stats">
        <span>{round.nodes.length} 个步骤</span>
        <span className="tv-round-stats-agents">
          {new Set(round.nodes.map((n) => n.agent)).size} 个 Agent
        </span>
        {shouldCollapse && (
          <button className="tv-phase-expand-btn" onClick={() => setExpanded(true)}>
            展开全部
          </button>
        )}
        {expanded && round.nodes.length > COLLAPSE_THRESHOLD && (
          <button className="tv-phase-expand-btn" onClick={() => setExpanded(false)}>
            收起
          </button>
        )}
      </div>
    </div>
  );
}

/** 从 box 中心沿单位方向 (ndx,ndy) 到 box 边框的距离 */
function boxEdgeT(ndx: number, ndy: number, hw: number, hh: number): number {
  const tx = ndx !== 0 ? hw / Math.abs(ndx) : Infinity;
  const ty = ndy !== 0 ? hh / Math.abs(ndy) : Infinity;
  return Math.min(tx, ty);
}
