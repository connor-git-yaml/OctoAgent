import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import WorkbenchLayout from "./WorkbenchLayout";

const mockUseWorkbenchData = vi.fn();

vi.mock("../../platform/queries", () => ({
  useWorkbenchData: () => mockUseWorkbenchData(),
}));

describe("WorkbenchLayout", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("普通用户 shell 不再展示累计计数，也不会泄漏 action code", () => {
    mockUseWorkbenchData.mockReturnValue({
      snapshot: {
        generated_at: "2026-03-14T12:05:00Z",
        resources: {
          project_selector: {
            current_project_id: "project-default",
            current_workspace_id: "workspace-default",
            available_projects: [
              {
                project_id: "project-default",
                slug: "default",
                name: "Default Project",
              },
            ],
            available_workspaces: [
              {
                workspace_id: "workspace-default",
                project_id: "project-default",
                slug: "primary",
                name: "Primary Workspace",
              },
            ],
          },
          diagnostics: {
            overall_status: "degraded",
          },
          sessions: {
            operator_summary: {
              total_pending: 2,
            },
            operator_items: [
              {
                item_id: "alert:1",
                kind: "alert",
                state: "pending",
                title: "Perplexity MCP 当前失败",
              },
            ],
          },
          config: {
            current_value: {
              runtime: {
                llm_mode: "litellm",
              },
            },
          },
          delegation: {
            works: [
              {
                work_id: "work-1",
                status: "RUNNING",
              },
            ],
          },
        },
      },
      loading: false,
      error: null,
      authError: null,
      busyActionId: null,
      lastAction: {
        message: "配置检查已完成。",
        code: "SETUP_REVIEW_READY",
        handled_at: "2026-03-14T12:05:00Z",
      },
      refreshSnapshot: vi.fn(),
      refreshResources: vi.fn(),
      submitAction: vi.fn(),
      clearError: vi.fn(),
    });

    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<WorkbenchLayout />}>
            <Route path="/" element={<div>Home Slot</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByText("配置检查已完成。")).toBeInTheDocument();
    expect(screen.queryByText(/\[SETUP_REVIEW_READY\]/)).not.toBeInTheDocument();
    expect(screen.getByText("有 2 项需要处理")).toBeInTheDocument();
    expect(screen.getByText("Perplexity MCP 当前失败")).toBeInTheDocument();
    expect(screen.queryByText(/可见 work/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/记忆记录/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/current records/i)).not.toBeInTheDocument();
  });
});
