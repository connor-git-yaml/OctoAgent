/**
 * PhaseCardList -- 阶段卡片流容器
 *
 * 遍历 phases，仅渲染已到达（status 非 pending）的用户可见阶段。
 */

import type { PhaseState } from "../../types";
import PhaseCard from "./PhaseCard";

interface PhaseCardListProps {
  phases: PhaseState[];
}

export default function PhaseCardList({ phases }: PhaseCardListProps) {
  // 过滤：只渲染用户可见 + 已到达（非 pending）的阶段
  const activePhases = phases.filter(
    (p) => p.config.userVisible && p.status !== "pending",
  );

  if (activePhases.length === 0) {
    return (
      <div style={{ color: "var(--cp-muted)", fontSize: "13px", textAlign: "center", padding: "var(--cp-space-4)" }}>
        等待事件...
      </div>
    );
  }

  return (
    <div className="tv-phase-card-list">
      {activePhases.map((phase) => (
        <PhaseCard key={phase.config.id} phase={phase} />
      ))}
    </div>
  );
}
