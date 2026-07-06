import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AutomationCenter, { humanizeSchedule } from "./AutomationCenter";
import type { AutomationJobDocument } from "../types";

vi.mock("../api/client", () => ({
  fetchAutomationDocument: vi.fn(),
}));

vi.mock("../platform/actions/controlPlaneActions", () => ({
  executeWorkbenchAction: vi.fn(),
}));

import { fetchAutomationDocument } from "../api/client";
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
  it("渲染定时任务列表 + 人读化 schedule + 提醒内容", async () => {
    fetchMock.mockResolvedValue(
      makeDoc({
        jobs: [
          {
            job: {
              job_id: "job-1",
              name: "交周报提醒",
              action_id: "reminder.notify",
              params: { message: "交周报" },
              project_id: "p1",
              schedule_kind: "cron",
              schedule_expr: "0 9 * * mon",
              timezone: "Asia/Shanghai",
              enabled: true,
              created_at: "2026-07-06T00:00:00Z",
              updated_at: "2026-07-06T00:00:00Z",
            },
            status: "active",
            next_run_at: null,
            last_run: null,
            supported_actions: [],
            degraded_reason: "",
          },
        ],
      })
    );

    renderPage();

    await waitFor(() => expect(screen.getByText("交周报提醒")).toBeInTheDocument());
    expect(screen.getByText(/每周一 09:00/)).toBeInTheDocument();
    expect(screen.getByText(/提醒内容：交周报/)).toBeInTheDocument();
    // 无删除按钮（Codex P1-3）——只有暂停/恢复
    expect(screen.queryByRole("button", { name: /删除/ })).toBeNull();
    expect(screen.getByRole("button", { name: "暂停" })).toBeInTheDocument();
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
        jobs: [
          {
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
            },
            status: "active",
            next_run_at: null,
            last_run: null,
            supported_actions: [],
            degraded_reason: "",
          },
        ],
      })
    );
    actionMock.mockResolvedValue({} as never);

    renderPage();
    await waitFor(() => expect(screen.getByText("喝水提醒")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "暂停" }));

    await waitFor(() =>
      expect(actionMock).toHaveBeenCalledWith("1.0.0", "automation.pause", {
        job_id: "job-1",
      })
    );
  });

  it("toggle 返回 rejected → 错误提示（不假成功，Codex P2）", async () => {
    fetchMock.mockResolvedValue(
      makeDoc({
        jobs: [
          {
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
            },
            status: "active",
            next_run_at: null,
            last_run: null,
            supported_actions: [],
            degraded_reason: "",
          },
        ],
      })
    );
    // 后端 404/409 时 executeControlAction 仍解析 result（status=rejected）
    actionMock.mockResolvedValue({
      status: "rejected",
      message: "任务不存在",
    } as never);

    renderPage();
    await waitFor(() => expect(screen.getByText("喝水提醒")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "暂停" }));

    await waitFor(() => expect(screen.getByText("任务不存在")).toBeInTheDocument());
    // 不应出现"已暂停"假成功提示
    expect(screen.queryByText("已暂停")).toBeNull();
  });

  it("加载失败展示错误 + 重试", async () => {
    fetchMock.mockRejectedValueOnce(new Error("boom"));
    renderPage();
    await waitFor(() => expect(screen.getByText("boom")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
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
  it("无法识别回退原表达式", () => {
    expect(humanizeSchedule("cron", "*/5 * * * *", "UTC")).toContain("*/5");
  });
});
