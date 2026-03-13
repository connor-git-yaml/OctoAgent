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
  channelSummary?: Record<string, unknown>;
}) {
  const setupReady = options?.setupReady ?? false;
  const diagnosticsStatus = options?.diagnosticsStatus ?? "degraded";
  const llmMode = options?.llmMode ?? "echo";
  const pendingCount = options?.pendingCount ?? 0;
  const channelSummary = options?.channelSummary ?? {};

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
        channel_summary: channelSummary,
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

    expect(screen.getByRole("heading", { name: "先连接真实模型" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "打开设置" }).length).toBeGreaterThan(0);
    expect(screen.getAllByText("先完成 Provider 与密钥连接。").length).toBeGreaterThan(0);
    expect(screen.getByText("当前还没有启用外部渠道，先用 Web 即可。")).toBeInTheDocument();
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

    expect(screen.getByRole("heading", { name: "现在可以直接开始聊天" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "进入聊天" }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("link", { name: "继续完成设置" })).not.toBeInTheDocument();
  });

  it("会把渠道对象转成用户语言，而不是显示 object 字符串", () => {
    mockWorkbench = {
      snapshot: buildSnapshot({
        setupReady: true,
        diagnosticsStatus: "ready",
        llmMode: "litellm",
        channelSummary: {
          telegram: {
            status: "ready",
          },
        },
      }),
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    expect(screen.getByText("Telegram已连接")).toBeInTheDocument();
    expect(screen.queryByText(/\[object Object\]/)).not.toBeInTheDocument();
  });

  it("不会因为存在渠道对象就误报外部渠道都已可用", () => {
    mockWorkbench = {
      snapshot: buildSnapshot({
        setupReady: true,
        diagnosticsStatus: "ready",
        llmMode: "litellm",
        channelSummary: {
          telegram: {
            enabled: false,
          },
        },
      }),
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    expect(
      screen.getByText("已经看到了外部渠道配置，但连接或授权可能还没走完；先用 Web 也不受影响。")
    ).toBeInTheDocument();
    expect(
      screen.queryByText("常用入口和外部渠道都已经可用，你可以直接开始使用。")
    ).not.toBeInTheDocument();
  });
});
