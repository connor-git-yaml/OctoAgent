import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import HomePage from "../../domains/home/HomePage";
import WorkbenchLayout from "./WorkbenchLayout";

const mockUseWorkbenchData = vi.fn();

vi.mock("../../platform/queries", () => ({
  useWorkbenchData: () => mockUseWorkbenchData(),
}));

function buildRuntimeProjectionSnapshot(runtimeMode: string, providers: unknown[]) {
  return {
    generated_at: "2026-07-22T00:00:00Z",
    resources: {
      project_selector: {
        current_project_id: "project-default",
        current_workspace_id: "workspace-default",
        available_projects: [
          { project_id: "project-default", slug: "default", name: "Default Project" },
        ],
        available_workspaces: [],
      },
      diagnostics: { overall_status: "ready", channel_summary: {} },
      sessions: { operator_summary: { total_pending: 0 }, operator_items: [], sessions: [] },
      config: { current_value: { runtime: { llm_mode: runtimeMode }, providers } },
      delegation: { works: [] },
      context_continuity: { degraded: { is_degraded: false } },
      setup_governance: {
        review: {
          ready: true,
          next_actions: [],
          blocking_reasons: [],
          warnings: [],
          risk_level: "low",
          provider_runtime_risks: [],
          channel_exposure_risks: [],
          agent_autonomy_risks: [],
          tool_skill_readiness_risks: [],
        },
      },
      worker_profiles: { profiles: [] },
    },
  };
}

function buildWorkbenchState(snapshot: unknown) {
  return {
    snapshot,
    loading: false,
    error: null,
    authError: null,
    busyActionId: null,
    lastAction: null,
    refreshSnapshot: vi.fn(),
    refreshResources: vi.fn(),
    submitAction: vi.fn(),
    clearError: vi.fn(),
  };
}

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
              providers: [{ id: "openrouter", enabled: true }],
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
      <MemoryRouter initialEntries={["/settings"]}>
        <Routes>
          <Route element={<WorkbenchLayout />}>
            <Route path="/settings" element={<div>Settings Slot</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByText("配置检查已完成。")).toBeInTheDocument();
    expect(screen.queryByText(/\[SETUP_REVIEW_READY\]/)).not.toBeInTheDocument();
    expect(screen.getByText("有 2 项需要处理")).toBeInTheDocument();
    expect(screen.getByText("Perplexity MCP 当前失败")).toBeInTheDocument();
    expect(
      screen.queryByText("把对话、设置和运行中的事情放在一个地方，常用入口都在这里。")
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/可见 work/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/记忆记录/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/current records/i)).not.toBeInTheDocument();
  });

  it("sessions 缺少 operator_items 时不会因为读取第一个待处理项而崩溃", () => {
    mockUseWorkbenchData.mockReturnValue({
      snapshot: {
        generated_at: "2026-03-15T00:55:00Z",
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
            overall_status: "ready",
          },
          sessions: {
            operator_summary: {
              total_pending: 1,
            },
          },
          config: {
            current_value: {
              providers: [{ id: "openrouter", enabled: true }],
            },
          },
          delegation: {
            works: [],
          },
        },
      },
      loading: false,
      error: null,
      authError: null,
      busyActionId: null,
      lastAction: null,
      refreshSnapshot: vi.fn(),
      refreshResources: vi.fn(),
      submitAction: vi.fn(),
      clearError: vi.fn(),
    });

    render(
      <MemoryRouter initialEntries={["/chat"]}>
        <Routes>
          <Route element={<WorkbenchLayout />}>
            <Route path="/chat" element={<div>Chat Slot</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByText("有 1 项需要处理")).toBeInTheDocument();
    expect(screen.getByText("先看一下待处理事项，再继续会更稳。")).toBeInTheDocument();
  });

  it("会话列表会区分对话 owner 和当前执行者", () => {
    mockUseWorkbenchData.mockReturnValue({
      snapshot: {
        generated_at: "2026-03-20T01:00:00Z",
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
            overall_status: "ready",
          },
          sessions: {
            operator_summary: {
              total_pending: 0,
            },
            operator_items: [],
            sessions: [
              {
                session_id: "session-finance",
                thread_id: "thread-finance",
                task_id: "task-finance",
                title: "finance",
                alias: "fin",
                status: "RUNNING",
                channel: "web",
                requester_id: "user:web",
                project_id: "project-default",
                workspace_id: "workspace-default",
                agent_profile_id: "worker-profile-finance",
                session_owner_profile_id: "worker-profile-finance",
                turn_executor_kind: "worker",
                delegation_target_profile_id: "worker-profile-finance",
                runtime_kind: "direct_worker",
                latest_message_summary: "继续看 finance",
                latest_event_at: "2026-03-20T01:00:00Z",
                execution_summary: {},
                capabilities: [],
                detail_refs: {},
              },
            ],
          },
          config: {
            current_value: {
              providers: [{ id: "openrouter", enabled: true }],
            },
          },
          delegation: {
            works: [],
          },
          worker_profiles: {
            profiles: [
              { profile_id: "worker-profile-finance", name: "研究员小 A" },
            ],
          },
        },
      },
      loading: false,
      error: null,
      authError: null,
      busyActionId: null,
      lastAction: null,
      refreshSnapshot: vi.fn(),
      refreshResources: vi.fn(),
      submitAction: vi.fn(),
      clearError: vi.fn(),
    });

    render(
      <MemoryRouter initialEntries={["/chat"]}>
        <Routes>
          <Route element={<WorkbenchLayout />}>
            <Route path="/chat" element={<div>Chat Slot</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByRole("button", { name: /^fin\s*研究员小 A$/ })).toBeInTheDocument();
    expect(screen.queryByText(/对话：/)).not.toBeInTheDocument();
    expect(screen.queryByText(/执行：/)).not.toBeInTheDocument();
  });

  it("workbench and home omit retired runtime activation projection", () => {
    const oracle = "Workbench/Home仍投影旧runtime/activation";
    mockUseWorkbenchData.mockReturnValue(
      buildWorkbenchState(buildRuntimeProjectionSnapshot("litellm", []))
    );
    const disconnected = render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<WorkbenchLayout />}>
            <Route path="/" element={<HomePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    );
    expect(document.body.textContent, oracle).toContain("模型未连接");
    expect(document.body.textContent, oracle).toContain("还在体验模式");
    disconnected.unmount();

    mockUseWorkbenchData.mockReturnValue(
      buildWorkbenchState(
        buildRuntimeProjectionSnapshot("echo", [
          { id: "openrouter", enabled: true },
        ])
      )
    );
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<WorkbenchLayout />}>
            <Route path="/" element={<HomePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    );
    expect(document.body.textContent, oracle).toContain("就绪");
    expect(document.body.textContent, oracle).not.toContain("模型未连接");
  });
});
