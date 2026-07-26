import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AutomationCenter, { humanizeSchedule } from "./AutomationCenter";
import type {
  AutomationJobDocument,
  AutomationJobItem,
} from "../types";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    fetchAutomationDocument: vi.fn(),
  };
});

vi.mock("../platform/actions/controlPlaneActions", () => ({
  executeWorkbenchAction: vi.fn(),
}));

import { ApiError, fetchAutomationDocument } from "../api/client";
import { executeWorkbenchAction } from "../platform/actions/controlPlaneActions";

const fetchMock = vi.mocked(fetchAutomationDocument);
const actionMock = vi.mocked(executeWorkbenchAction);

function makeDoc(overrides: Partial<AutomationJobDocument> = {}): AutomationJobDocument {
  return {
    contract_version: "1.0.0",
    resource_type: "automation_job",
    resource_id: "automation:jobs",
    schema_version: 1,
    generated_at: "2026-07-06T00:00:00Z",
    updated_at: "2026-07-06T00:00:00Z",
    status: "ready",
    degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
    warnings: [],
    capabilities: [],
    refs: {},
    jobs: [],
    run_history_cursor: "",
    ...overrides,
  } as AutomationJobDocument;
}

function makeItem(
  overrides: Partial<AutomationJobItem["job"]> = {},
  itemOverrides: Partial<AutomationJobItem> = {}
): AutomationJobItem {
  return {
    job: {
      job_id: "job-1",
      name: "喝水提醒",
      action_id: "reminder.notify",
      params: { message: "喝水" },
      project_id: "p1",
      schedule_kind: "cron",
      schedule_expr: "0 8 * * *",
      timezone: "UTC",
      enabled: true,
      created_at: "2026-07-06T00:00:00Z",
      updated_at: "2026-07-06T00:00:00Z",
      ...overrides,
    },
    status: overrides.enabled === false ? "paused" : "active",
    next_run_at: null,
    last_run: null,
    supported_actions: [],
    degraded_reason: "",
    ...itemOverrides,
  } as AutomationJobItem;
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AutomationCenter />
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AutomationCenter", () => {
  it("渲染人读任务卡，并把任务编号与计划原式收进按卡片打开的高级区", async () => {
    fetchMock.mockResolvedValue(
      makeDoc({
        jobs: [
          makeItem({
            name: "交周报提醒",
            params: { message: "交周报" },
            schedule_expr: "0 9 * * mon",
            timezone: "Asia/Shanghai",
          }),
        ],
      })
    );

    renderPage();

    await waitFor(() => expect(screen.getByText("交周报提醒")).toBeInTheDocument());
    expect(screen.getByText(/每周一 09:00/)).toBeInTheDocument();
    expect(screen.getByText("提醒内容")).toBeInTheDocument();
    expect(screen.getByText("交周报")).toBeInTheDocument();
    expect(screen.queryByText("job-1")).toBeNull();
    expect(screen.queryByText("reminder.notify")).toBeNull();
    expect(screen.queryByText(/0 9 \* \* mon/)).toBeNull();
    expect(screen.queryByRole("button", { name: /删除/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /立即运行|新建|取消/ })).toBeNull();
    expect(screen.getByRole("button", { name: "暂停" })).toBeInTheDocument();

    const advancedTrigger = screen.getByRole("button", {
      name: "高级 · 任务编号与计划原式",
    });
    await userEvent.click(advancedTrigger);

    expect(
      screen.getByRole("dialog", { name: "交周报提醒的高级信息" })
    ).toBeInTheDocument();
    expect(screen.getByText("job-1")).toBeInTheDocument();
    expect(screen.getByText("reminder.notify")).toBeInTheDocument();
    expect(screen.getByText(/0 9 \* \* mon/)).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(advancedTrigger).toHaveFocus();
  });

  it("空状态提示走对话建任务", async () => {
    fetchMock.mockResolvedValue(makeDoc({ jobs: [] }));
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/还没有定时任务/)).toBeInTheDocument()
    );
  });

  it("点暂停 → 调 automation.pause + 刷新", async () => {
    fetchMock.mockResolvedValue(
      makeDoc({
        jobs: [makeItem()],
      })
    );
    actionMock.mockResolvedValue({
      status: "completed",
      message: "已暂停自动化任务",
    } as never);

    renderPage();
    await waitFor(() => expect(screen.getByText("喝水提醒")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "暂停" }));

    await waitFor(() =>
      expect(actionMock).toHaveBeenCalledWith("1.0.0", "automation.pause", {
        job_id: "job-1",
      })
    );
  });

  it("toggle 返回 rejected → 卡片内展示冲突并由用户刷新", async () => {
    fetchMock.mockResolvedValue(
      makeDoc({
        jobs: [makeItem()],
      })
    );
    actionMock.mockResolvedValue({
      status: "rejected",
      code: "JOB_NOT_FOUND",
      message: "任务不存在",
    } as never);

    renderPage();
    await waitFor(() => expect(screen.getByText("喝水提醒")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "暂停" }));

    await waitFor(() =>
      expect(screen.getByText("已在别处更改")).toBeInTheDocument()
    );
    expect(screen.queryByText("任务不存在")).toBeNull();
    expect(screen.queryByText("已暂停")).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("加载失败展示普通用户文案 + 重试，不泄漏原始错误", async () => {
    fetchMock.mockRejectedValueOnce(new Error("boom"));
    renderPage();
    await waitFor(() =>
      expect(screen.getByText("定时任务加载失败")).toBeInTheDocument()
    );
    expect(screen.queryByText("boom")).toBeNull();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("403 由当前页面显示资源权限不足，不提供重新登录动作", async () => {
    fetchMock.mockRejectedValueOnce(
      new ApiError("forbidden", { status: 403, code: "FORBIDDEN" })
    );
    renderPage();

    await waitFor(() =>
      expect(
        screen.getByText("当前账号没有权限查看定时任务")
      ).toBeInTheDocument()
    );
    expect(screen.queryByText(/重新登录/)).toBeNull();
    expect(screen.queryByRole("button", { name: /登录/ })).toBeNull();
  });

  it("启用任务排在暂停任务前，主界面仍只有暂停或恢复", async () => {
    fetchMock.mockResolvedValue(
      makeDoc({
        jobs: [
          makeItem(
            { job_id: "paused", name: "暂停任务", enabled: false },
            { status: "paused" }
          ),
          makeItem({ job_id: "active", name: "启用任务", enabled: true }),
        ],
      })
    );
    renderPage();

    await waitFor(() => expect(screen.getByText("启用任务")).toBeInTheDocument());
    const cards = screen.getAllByRole("article");
    expect(cards[0]).toHaveTextContent("启用任务");
    expect(cards[1]).toHaveTextContent("暂停任务");
    expect(screen.getAllByRole("button", { name: /暂停|恢复/ })).toHaveLength(2);
  });
});

describe("humanizeSchedule", () => {
  it("每天定点", () => {
    expect(humanizeSchedule("cron", "0 8 * * *", "UTC")).toBe("每天 08:00");
  });
  it("每周命名星期", () => {
    expect(humanizeSchedule("cron", "0 9 * * mon", "Asia/Shanghai")).toBe(
      "每周一 09:00（Asia/Shanghai）"
    );
  });
  it("每月某日", () => {
    expect(humanizeSchedule("cron", "0 10 1 * *", "UTC")).toBe("每月 1 号 10:00");
  });
  it("interval 小时", () => {
    expect(humanizeSchedule("interval", "3600", "UTC")).toBe("每 1 小时");
  });
  it("常见步进计划转换为普通语言，不泄漏 cron 原式", () => {
    expect(humanizeSchedule("cron", "*/5 * * * *", "UTC")).toBe("每 5 分钟");
    expect(humanizeSchedule("cron", "0 */4 * * *", "UTC")).toBe("每 4 小时");
  });
  it("无法识别时只显示自定义计划", () => {
    expect(humanizeSchedule("cron", "1,7 2-9 */3 * mon-fri", "UTC")).toBe(
      "按自定义计划"
    );
  });
});
