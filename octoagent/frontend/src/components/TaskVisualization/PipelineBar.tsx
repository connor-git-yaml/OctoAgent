/**
 * PipelineBar -- 四节点进度条
 *
 * 渲染 4 个用户可见阶段（排除 system）的圆形节点 + 连线。
 * 节点状态：done（实心+勾号）、active（呼吸动画）、error（实心+叉号）、pending（空心）。
 * 连线：已走过实线、未到达虚线。
 */

import type { PhaseState, PhaseStatus } from "../../types";

interface PipelineBarProps {
  phases: PhaseState[];
}

/** 阶段图标（展示在圆圈内，done/active/error 的图标由 CSS 伪元素实现） */
const PHASE_STEP_NUMBER: Record<string, string> = {
  received: "1",
  thinking: "2",
  executing: "3",
  completed: "4",
};

/** 判断连线状态：左边节点已 done -> 实线，否则虚线 */
function getLineStatus(leftStatus: PhaseStatus): "done" | "pending" {
  return leftStatus === "done" ? "done" : "pending";
}

export default function PipelineBar({ phases }: PipelineBarProps) {
  // 只渲染用户可见阶段
  const visiblePhases = phases.filter((p) => p.config.userVisible);

  return (
    <div className="tv-pipeline-bar">
      {visiblePhases.map((phase, index) => (
        <div key={phase.config.id} className="tv-pipeline-step">
          {/* 节点 */}
          <div className="tv-pipeline-node">
            <div className={`tv-pipeline-circle tv-pipeline-circle--${phase.status}`}>
              {/* pending 状态显示序号，其他状态由 CSS ::after 显示勾号/动画/叉号 */}
              {phase.status === "pending" && (
                <span>{PHASE_STEP_NUMBER[phase.config.id]}</span>
              )}
            </div>
            <span className={`tv-pipeline-label tv-pipeline-label--${phase.status}`}>
              {phase.config.label}
            </span>
          </div>

          {/* 连线（最后一个节点后面不加） */}
          {index < visiblePhases.length - 1 && (
            <div
              className={`tv-pipeline-line tv-pipeline-line--${getLineStatus(phase.status)}`}
            />
          )}
        </div>
      ))}
    </div>
  );
}
