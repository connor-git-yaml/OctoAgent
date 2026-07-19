import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { StatusBadge } from "../../ui/primitives";
import type { WorkProjectionItem } from "../../types";

/**
 * F148 全局任务浮层（1b）。
 * 读取 WorkbenchLayout 已按 ACTIVE_WORK_STATUSES 过滤好的活跃 works，右下 FAB
 * 触发展开，列出全局进行中的任务。与右栏(1a)同源 delegation.works + 同一状态
 * 词表 → 同任务两处状态一致（交互态④）。点击行跳任务详情。
 */

// 交互态①：无活跃任务不渲染 FAB（避免空按钮），故仅在 activeWorks 非空时出现。

function resolveWorkStatusTone(status: string): string {
  return String(status ?? "").trim().toLowerCase() || "draft";
}

function statusText(status: string): string {
  const normalized = resolveWorkStatusTone(status);
  const map: Record<string, string> = {
    running: "进行中",
    created: "已创建",
    assigned: "已派发",
    escalated: "需关注",
    waiting_approval: "等待确认",
    waiting_input: "等待补充",
  };
  return map[normalized] ?? normalized;
}

export function GlobalTaskOverlay({
  activeWorks,
  resolveAgentName,
}: {
  activeWorks: WorkProjectionItem[];
  resolveAgentName: (agentProfileId: string) => string;
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  if (activeWorks.length === 0) {
    return null;
  }

  return (
    <>
      <button
        type="button"
        className="v2-task-fab"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        aria-label={`进行中的任务 ${activeWorks.length} 项`}
      >
        <span className="octo-bar" aria-hidden="true">
          <i /><i /><i /><i />
        </span>
        <span>进行中</span>
        <span className="v2-task-fab-count">{activeWorks.length}</span>
      </button>

      {open ? (
        <div className="v2-task-overlay" data-testid="global-task-overlay" role="dialog" aria-label="进行中的任务">
          <div className="v2-task-overlay-head">
            <strong>进行中的任务</strong>
            <button
              type="button"
              className="v2-task-overlay-close"
              onClick={() => setOpen(false)}
              aria-label="收起"
            >
              ×
            </button>
          </div>
          <div className="v2-task-overlay-list">
            {activeWorks.map((work) => {
              const ownerProfileId =
                work.session_owner_profile_id || work.agent_profile_id || "";
              const ownerLabel = ownerProfileId ? resolveAgentName(ownerProfileId) : "主 Agent";
              return (
                <button
                  type="button"
                  key={work.work_id}
                  className="v2-task-row"
                  onClick={() => {
                    setOpen(false);
                    if (work.task_id) {
                      navigate(`/tasks/${work.task_id}`);
                    }
                  }}
                >
                  <span className="octo-bar" aria-hidden="true">
                    <i /><i /><i /><i />
                  </span>
                  <span className="v2-task-row-copy">
                    <span className="v2-task-row-title">{work.title || "未命名任务"}</span>
                    <span className="v2-task-row-meta">{ownerLabel}</span>
                  </span>
                  <StatusBadge tone={resolveWorkStatusTone(work.status)}>
                    {statusText(work.status)}
                  </StatusBadge>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </>
  );
}

export default GlobalTaskOverlay;
