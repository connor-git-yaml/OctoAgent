import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OperatorInboxItem, TaskSummary } from "../types";
import TaskList from "./TaskList";

const testMocks = vi.hoisted(() => ({
  fetchTasks: vi.fn(),
  reloadInbox: vi.fn(),
  submitAction: vi.fn(),
  inboxState: {
    inbox: {
      summary: {
        total_pending: 0,
        approvals: 0,
        alerts: 0,
        retryable_failures: 0,
        pairing_requests: 0,
        degraded_sources: [],
        generated_at: "2026-07-26T00:00:00Z",
      },
      items: [] as OperatorInboxItem[],
    },
    loading: false,
    error: null as string | null,
    busyItemId: null as string | null,
    lastResult: null,
  },
}));

vi.mock("../api/client", async () => {
  const actual =
    await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    fetchTasks: testMocks.fetchTasks,
  };
});

vi.mock("../hooks/useOperatorInbox", () => ({
  useOperatorInbox: () => ({
    ...testMocks.inboxState,
    reload: testMocks.reloadInbox,
    submitAction: testMocks.submitAction,
  }),
}));

import { ApiError } from "../api/client";

function task(
  status: TaskSummary["status"],
  overrides: Partial<TaskSummary> = {},
): TaskSummary {
  return {
    task_id: `task-${status.toLowerCase()}`,
    created_at: "2026-07-26T08:00:00Z",
    updated_at: "2026-07-26T09:00:00Z",
    status,
    title: `${status} 示例任务`,
    thread_id: "thread-private",
    scope_id: "scope-private",
    risk_level: "operator_sensitive",
    ...overrides,
  };
}

function inboxItem(
  overrides: Partial<OperatorInboxItem> = {},
): OperatorInboxItem {
  return {
    item_id: "retry:task-failed",
    kind: "retryable_failure",
    state: "pending",
    title: "网页资料抓取",
    summary: "worker_runtime_timeout task_id=task-failed",
    task_id: "task-failed",
    thread_id: "thread-private",
    source_ref: "runtime.worker",
    created_at: "2026-07-26T08:30:00Z",
    expires_at: null,
    pending_age_seconds: 180,
    suggested_actions: [],
    quick_actions: [],
    recent_action_result: null,
    metadata: {},
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <TaskList />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  testMocks.inboxState = {
    inbox: {
      summary: {
        total_pending: 0,
        approvals: 0,
        alerts: 0,
        retryable_failures: 0,
        pairing_requests: 0,
        degraded_sources: [],
        generated_at: "2026-07-26T00:00:00Z",
      },
      items: [],
    },
    loading: false,
    error: null,
    busyItemId: null,
    lastResult: null,
  };
});

describe("TaskList F149 Tasks v2", () => {
  it("把raw status映射成人话，并以Claude Design卡片与筛选组织任务", async () => {
    testMocks.fetchTasks.mockResolvedValue({
      tasks: [
        task("RUNNING", { title: "本周计划报告生成" }),
        task("SUCCEEDED", { title: "会议纪要整理" }),
        task("FAILED", { title: "网页资料抓取" }),
      ],
    });

    const user = userEvent.setup();
    renderPage();

    expect(
      await screen.findByRole("heading", { name: "任务" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("1 项进行中 · 1 项已完成 · 1 项未成功"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("进行中")).toHaveLength(2);
    expect(screen.getAllByText("已完成")).toHaveLength(2);
    expect(screen.getAllByText("未成功")).toHaveLength(2);
    expect(screen.queryByText("RUNNING")).not.toBeInTheDocument();

    const advanced = screen.getAllByText("高级")[0].closest("details");
    expect(advanced).not.toHaveAttribute("open");
    expect(
      within(advanced as HTMLElement).getByText(/status=RUNNING/),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "已完成" }));
    expect(screen.getByText("会议纪要整理")).toBeInTheDocument();
    expect(screen.queryByText("本周计划报告生成")).not.toBeInTheDocument();
  });

  it("loading与empty使用可访问的互斥页面状态", async () => {
    testMocks.fetchTasks.mockImplementation(() => new Promise(() => undefined));
    const pending = renderPage();
    expect(
      screen.getByRole("status", { name: "正在加载任务" }),
    ).toBeInTheDocument();
    pending.unmount();

    testMocks.fetchTasks.mockResolvedValue({ tasks: [] });
    renderPage();
    expect(
      await screen.findByRole("status", { name: "还没有任务" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/0 件待处理事项/)).not.toBeInTheDocument();
  });

  it("可恢复错误提供重试，并在成功后恢复任务卡片", async () => {
    testMocks.fetchTasks
      .mockRejectedValueOnce(new ApiError("temporary", { status: 503 }))
      .mockResolvedValueOnce({
        tasks: [task("QUEUED", { title: "等待开始的任务" })],
      });

    const user = userEvent.setup();
    renderPage();
    expect(
      await screen.findByRole("alert", { name: "任务暂时不可用" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByText("等待开始的任务")).toBeInTheDocument();
    expect(testMocks.fetchTasks).toHaveBeenCalledTimes(2);
  });

  it("origin 403只说明资源权限并联系管理员，不提供重新登录", async () => {
    testMocks.fetchTasks.mockRejectedValue(
      new ApiError("forbidden", { status: 403 }),
    );

    renderPage();
    expect(
      await screen.findByRole("alert", { name: "无法查看任务" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/联系管理员/)).toBeInTheDocument();
    expect(screen.queryByText(/重新登录/)).not.toBeInTheDocument();
  });

  it("非零待处理事项显著可进入，0项时不占首屏", async () => {
    testMocks.fetchTasks.mockResolvedValue({
      tasks: [
        task("FAILED", { task_id: "task-failed", title: "网页资料抓取" }),
      ],
    });
    testMocks.inboxState.inbox = {
      summary: {
        total_pending: 1,
        approvals: 0,
        alerts: 0,
        retryable_failures: 1,
        pairing_requests: 0,
        degraded_sources: [],
        generated_at: "2026-07-26T00:00:00Z",
      },
      items: [inboxItem()],
    };

    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByText("1 件待处理事项")).toBeInTheDocument();
    expect(
      screen.queryByText(/operator|ops|task_id|runtime/i),
    ).not.toBeInTheDocument();

    await user.click(screen.getByText("进入处理"));
    expect(
      screen.getByText("任务“网页资料抓取”这次没有在时限内完成"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "打开对应任务" })).toHaveAttribute(
      "href",
      "/tasks/task-failed",
    );
  });

  it("普通区不出现Recovery、cancel或resume，也不把知识审批混进任务", async () => {
    testMocks.fetchTasks.mockResolvedValue({
      tasks: [task("WAITING_INPUT", { title: "等待你补充信息" })],
    });

    renderPage();
    expect(await screen.findByText("等待你补充信息")).toBeInTheDocument();
    expect(screen.queryByText(/Recovery/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/\bcancel\b|\bresume\b/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/知识候选|长期记忆提议/)).not.toBeInTheDocument();
  });
});
