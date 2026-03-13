import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import HomePage from "./HomePage";

let mockWorkbench: {
  snapshot: unknown;
  submitAction: ReturnType<typeof vi.fn>;
  busyActionId: string | null;
};

vi.mock("../../components/shell/WorkbenchLayout", () => ({
  useWorkbench: () => mockWorkbench,
}));

function buildSnapshot(options?: {
  setupReady?: boolean;
  diagnosticsStatus?: string;
  llmMode?: string;
  pendingCount?: number;
}) {
  const setupReady = options?.setupReady ?? false;
  const diagnosticsStatus = options?.diagnosticsStatus ?? "degraded";
  const llmMode = options?.llmMode ?? "echo";
  const pendingCount = options?.pendingCount ?? 0;

  return {
    resources: {
      project_selector: {
        current_project_id: "project-default",
        current_workspace_id: "workspace-default",
        fallback_reason: "",
        available_projects: [
          {
            project_id: "project-default",
            name: "Default Project",
          },
        ],
        available_workspaces: [
          {
            workspace_id: "workspace-default",
            project_id: "project-default",
            name: "Primary",
          },
        ],
      },
      wizard: {
        status: "ready",
      },
      diagnostics: {
        overall_status: diagnosticsStatus,
        channel_summary: {},
      },
      sessions: {
        sessions: [],
        operator_summary: {
          total_pending: pendingCount,
          approvals: pendingCount,
          pairing_requests: 0,
        },
      },
      memory: {
        summary: {
          sor_current_count: 3,
          fragment_count: 5,
          proposal_count: 1,
        },
      },
      context_continuity: {
        frames: [],
        sessions: [],
        degraded: {
          is_degraded: false,
        },
      },
      setup_governance: {
        review: {
          ready: setupReady,
          next_actions: setupReady ? [] : ["先完成 Provider 与密钥连接。"],
          blocking_reasons: setupReady ? [] : ["当前还没有接入真实模型。"],
        },
      },
      config: {
        current_value: {
          runtime: {
            llm_mode: llmMode,
          },
        },
      },
      delegation: {
        works: [],
        updated_at: "2026-03-13T16:00:00Z",
      },
    },
  };
}

describe("HomePage", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("未就绪时优先把用户引导到设置与真实模型连接", () => {
    mockWorkbench = {
      snapshot: buildSnapshot({
        setupReady: false,
        diagnosticsStatus: "degraded",
        llmMode: "echo",
      }),
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    expect(screen.getByRole("heading", { name: "先完成基础配置" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "连接真实模型" })).toBeInTheDocument();
    expect(screen.getByText("先完成 Provider 与密钥连接。")).toBeInTheDocument();
  });

  it("已就绪且非 echo 模式时优先引导进入聊天", () => {
    mockWorkbench = {
      snapshot: buildSnapshot({
        setupReady: true,
        diagnosticsStatus: "ready",
        llmMode: "litellm",
      }),
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    expect(screen.getByRole("heading", { name: "可以开始使用" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "进入聊天" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "连接真实模型" })).not.toBeInTheDocument();
  });
});
