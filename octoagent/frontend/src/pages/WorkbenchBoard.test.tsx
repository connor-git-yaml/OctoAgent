import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import WorkbenchBoard from "./WorkbenchBoard";

let mockWorkbench: {
  snapshot: unknown;
  submitAction: ReturnType<typeof vi.fn>;
  busyActionId: string | null;
};

vi.mock("../components/shell/WorkbenchLayout", () => ({
  useWorkbench: () => mockWorkbench,
}));

function buildWork(
  workId: string,
  status: string,
  overrides?: Partial<Record<string, unknown>>
) {
  return {
    work_id: workId,
    task_id: overrides?.task_id ?? `task-${workId}`,
    parent_work_id: overrides?.parent_work_id ?? "",
    title: overrides?.title ?? workId,
    status,
    target_kind: overrides?.target_kind ?? "worker",
    selected_worker_type: overrides?.selected_worker_type ?? "general",
    route_reason: overrides?.route_reason ?? "delegated_by_butler",
    owner_id: overrides?.owner_id ?? "",
    selected_tools: overrides?.selected_tools ?? [],
    pipeline_run_id: "",
    runtime_id: "",
    project_id: "project-default",
    workspace_id: "workspace-default",
    requested_worker_profile_id: "worker-profile-default",
    requested_worker_profile_version: 1,
    effective_worker_snapshot_id: "snapshot-default",
    child_work_ids: overrides?.child_work_ids ?? [],
    child_work_count: overrides?.child_work_count ?? 0,
    merge_ready: false,
    runtime_summary: overrides?.runtime_summary ?? {},
    updated_at: overrides?.updated_at ?? "2026-03-15T11:08:00Z",
    capabilities: overrides?.capabilities ?? [],
  };
}

function buildSnapshot(options?: {
  works?: Array<Record<string, unknown>>;
  operatorItems?: Array<Record<string, unknown>>;
  operatorSummary?: Record<string, unknown>;
}) {
  return {
    resources: {
      delegation: {
        works: options?.works ?? [],
      },
      sessions: {
        operator_summary: {
          total_pending: 0,
          approvals: 0,
          alerts: 0,
          retryable_failures: 0,
          pairing_requests: 0,
          degraded_sources: [],
          generated_at: "2026-03-15T11:08:00Z",
          ...(options?.operatorSummary ?? {}),
        },
        operator_items: options?.operatorItems ?? [],
      },
      context_continuity: {
        frames: [],
        degraded: {
          is_degraded: false,
          reasons: [],
          unavailable_sections: [],
        },
      },
      capability_pack: {
        pack: {
          tools: [],
          worker_profiles: [],
          degraded_reason: "",
        },
      },
    },
  };
}

describe("WorkbenchBoard", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("会把待确认事项翻译成可操作卡片并支持快速动作", async () => {
    const submitAction = vi.fn().mockResolvedValue({});
    mockWorkbench = {
      snapshot: buildSnapshot({
        works: [buildWork("work-background", "running", { title: "Background Work" })],
        operatorSummary: {
          total_pending: 2,
          alerts: 1,
          retryable_failures: 1,
        },
        operatorItems: [
          {
            item_id: "alert:1",
            kind: "alert",
            state: "pending",
            title: "任务 Control Plane Audit 需要关注",
            summary: "drift=no_progress / stalled=75s",
            task_id: "task-alert",
            source_ref: "task-alert",
            created_at: "2026-03-15T11:06:00Z",
            pending_age_seconds: 75,
            suggested_actions: ["ack_alert"],
            quick_actions: [
              {
                kind: "ack_alert",
                label: "知道了",
                style: "secondary",
                enabled: true,
              },
            ],
            metadata: {
              journal_state: "stalled",
            },
          },
          {
            item_id: "retry:1",
            kind: "retryable_failure",
            state: "pending",
            title:
              "任务 请直接读取当前项目 README 的开头，告诉我这个项目一句话是干什么的。不要委托 Worker。 可重试",
            summary: "worker_runtime_timeout:max_exec",
            task_id: "task-retry",
            source_ref: "task-retry",
            created_at: "2026-03-15T11:05:00Z",
            suggested_actions: ["retry_task"],
            quick_actions: [
              {
                kind: "retry_task",
                label: "重试",
                style: "primary",
                enabled: true,
              },
            ],
            metadata: {
              error_type: "timeout",
            },
          },
        ],
      }),
      submitAction,
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <WorkbenchBoard />
      </MemoryRouter>
    );

    expect(screen.getByRole("heading", { name: "有 2 项事情等你确认" })).toBeInTheDocument();
    expect(screen.getByText("“任务 Control Plane Audit 需要关注”停住了")).toBeInTheDocument();
    expect(screen.getByText("“任务 Control Plane Audit 需要关注”已经 1 分 15 秒 没有推进，可能卡住了。")).toBeInTheDocument();
    expect(screen.getByText("这条任务这次没有在时限内完成")).toBeInTheDocument();
    expect(
      screen.getByText("这次尝试已经结束，旧任务不会自己恢复；如果还要继续，需要重新发起一次。")
    ).toBeInTheDocument();
    expect(screen.queryByText("drift=no_progress / stalled=75s")).not.toBeInTheDocument();
    expect(screen.queryByText("worker_runtime_timeout:max_exec")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() =>
      expect(submitAction).toHaveBeenCalledWith("operator.task.retry", { item_id: "retry:1" })
    );

    await userEvent.click(screen.getByRole("button", { name: "知道了" }));
    await waitFor(() =>
      expect(submitAction).toHaveBeenCalledWith("operator.alert.ack", { item_id: "alert:1" })
    );
  });

  it("会把后台未收尾的工作解释成主任务和子分支，而不是聊天还在继续", () => {
    mockWorkbench = {
      snapshot: buildSnapshot({
        works: [
          buildWork("work-root", "running", { title: "Root Work" }),
          buildWork("work-child", "assigned", {
            title: "Child Work",
            parent_work_id: "work-root",
          }),
        ],
      }),
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <WorkbenchBoard />
      </MemoryRouter>
    );

    expect(screen.getByRole("heading", { name: "2 条后台工作还没收尾" })).toBeInTheDocument();
    expect(screen.getByText("后台未收尾")).toBeInTheDocument();
    expect(
      screen.getByText("包含 1 个主任务和 1 个子分支，不等于还有这么多聊天在继续")
    ).toBeInTheDocument();
  });
});
