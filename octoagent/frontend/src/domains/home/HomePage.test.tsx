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
  nextActions?: string[];
  blockingReasons?: string[];
  operatorItems?: Array<Record<string, unknown>>;
  sessions?: Array<Record<string, unknown>>;
  channelSummary?: Record<string, unknown>;
  availableProjects?: Array<Record<string, unknown>>;
  availableWorkspaces?: Array<Record<string, unknown>>;
}) {
  const setupReady = options?.setupReady ?? false;
  const diagnosticsStatus = options?.diagnosticsStatus ?? "degraded";
  const llmMode = options?.llmMode ?? "echo";
  const pendingCount = options?.pendingCount ?? 0;
  const nextActions = options?.nextActions ?? (setupReady ? [] : ["先完成 Provider 与密钥连接。"]);
  const blockingReasons =
    options?.blockingReasons ?? (setupReady ? [] : ["当前还没有接入真实模型。"]);
  const channelSummary = options?.channelSummary ?? {};
  const operatorItems = options?.operatorItems ?? [];
  const sessions = options?.sessions ?? [];
  const availableProjects =
    options?.availableProjects ??
    [
      {
        project_id: "project-default",
        name: "Default Project",
      },
    ];
  const availableWorkspaces =
    options?.availableWorkspaces ??
    [
      {
        workspace_id: "workspace-default",
        project_id: "project-default",
        name: "Primary",
      },
    ];

  return {
    resources: {
      project_selector: {
        current_project_id: "project-default",
        current_workspace_id: "workspace-default",
        fallback_reason: "",
        available_projects: availableProjects,
        available_workspaces: availableWorkspaces,
      },
      diagnostics: {
        overall_status: diagnosticsStatus,
        channel_summary: channelSummary,
      },
      sessions: {
        sessions,
        operator_summary: {
          total_pending: pendingCount,
          approvals: 0,
          alerts: pendingCount,
          retryable_failures: 0,
          pairing_requests: 0,
        },
        operator_items: operatorItems,
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
          next_actions: nextActions,
          blocking_reasons: blockingReasons,
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
      },
    },
  };
}

describe("HomePage", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("体验模式下优先要求连接真实模型，而不是展示控制台统计", () => {
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

    // buildPrimaryState 在 echo 模式下返回的标题和按钮文案
    expect(screen.getByRole("heading", { name: "模型未连接" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往设置" })).toBeInTheDocument();
    expect(screen.getByText("当前还没有接入真实模型。")).toBeInTheDocument();
    expect(screen.queryByText("背景记忆")).not.toBeInTheDocument();
    expect(screen.queryByText("当前提醒")).not.toBeInTheDocument();
  });

  it("echo-ready 状态下不会把已通过 review 的提示混进首页主引导", () => {
    mockWorkbench = {
      snapshot: buildSnapshot({
        setupReady: true,
        diagnosticsStatus: "ready",
        llmMode: "echo",
        nextActions: ['检查已通过，可以点击“保存配置”。'],
      }),
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    // echo 模式（setupReady=true）下，buildPrimaryState 仍走 echo 分支
    expect(screen.getByRole("heading", { name: "模型未连接" })).toBeInTheDocument();
    expect(screen.getByText("当前配置已经可以保存，但还没有切到真实模型。")).toBeInTheDocument();
    expect(
      screen.getByText("打开 Settings，连接 Provider 并切到真实模型后，再回来发第一条真实消息。")
    ).toBeInTheDocument();
    expect(screen.queryByText('检查已通过，可以点击“保存配置”。')).not.toBeInTheDocument();
  });

  it("系统可用时会给出可直接开始的用法，而不是继续堆状态卡", () => {
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

    // 系统完全就绪时，buildPrimaryState 返回 "就绪"
    expect(screen.getByRole("heading", { name: "就绪" })).toBeInTheDocument();
    expect(
      screen.getByText("“深圳今天天气怎么样？我今天穿什么比较合适？”")
    ).toBeInTheDocument();
    expect(screen.getByText("Telegram已连接。先用 Web 也不受影响。")).toBeInTheDocument();
    expect(screen.queryByLabelText("切换 Project")).not.toBeInTheDocument();
    expect(screen.queryByText("系统已经替你记住了多少")).not.toBeInTheDocument();
  });

  it("有待处理事项时会把技术态摘要翻译成用户下一步", () => {
    mockWorkbench = {
      snapshot: buildSnapshot({
        setupReady: true,
        diagnosticsStatus: "ready",
        llmMode: "litellm",
        pendingCount: 2,
        operatorItems: [
          {
            item_id: "alert:1",
            kind: "alert",
            state: "pending",
            title: "任务 Control Plane Audit 需要关注",
            summary: "drift=no_progress / stalled=75s",
          },
          {
            item_id: "retry:1",
            kind: "retryable_failure",
            state: "pending",
            title:
              "任务 请直接读取当前项目 README 的开头，告诉我这个项目一句话是干什么的。不要委托 Worker。 可重试",
            summary: "worker_runtime_timeout:max_exec",
          },
        ],
      }),
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    // buildPrimaryState 在有 operatorItems 时返回 "N 项待处理"
    expect(screen.getByRole("heading", { name: "2 项待处理" })).toBeInTheDocument();
    expect(screen.getByText("“任务 Control Plane Audit 需要关注”停住了")).toBeInTheDocument();
    expect(screen.getByText("“任务 Control Plane Audit 需要关注”已经 1 分 15 秒 没有推进，可能卡住了。")).toBeInTheDocument();
    expect(screen.getByText("这条任务这次没有在时限内完成")).toBeInTheDocument();
    expect(
      screen.getByText("这次尝试已经结束，旧任务不会自己恢复；如果还要继续，需要重新发起一次。")
    ).toBeInTheDocument();
    expect(screen.queryByText("drift=no_progress / stalled=75s")).not.toBeInTheDocument();
    expect(screen.queryByText("worker_runtime_timeout:max_exec")).not.toBeInTheDocument();
    expect(screen.queryByText(/审批 0/)).not.toBeInTheDocument();
  });

  it("只有存在多个上下文选项时才显示切换控件", () => {
    mockWorkbench = {
      snapshot: buildSnapshot({
        setupReady: true,
        diagnosticsStatus: "ready",
        llmMode: "litellm",
        availableWorkspaces: [
          {
            workspace_id: "workspace-default",
            project_id: "project-default",
            name: "Primary",
          },
          {
            workspace_id: "workspace-focus",
            project_id: "project-default",
            name: "Focus",
          },
        ],
      }),
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    expect(screen.getByText("切换工作上下文")).toBeInTheDocument();
    expect(screen.getByLabelText("切换 Workspace")).toBeInTheDocument();
  });

  it("最近一次对话会展示用户上次输入，而不是冒充处理结果", () => {
    mockWorkbench = {
      snapshot: buildSnapshot({
        setupReady: true,
        diagnosticsStatus: "ready",
        llmMode: "litellm",
        sessions: [
          {
            session_id: "session-1",
            task_id: "task-1",
            title: "本周计划",
            latest_message_summary: "帮我整理下周最重要的三件事",
            latest_event_at: "2026-03-13T16:00:00Z",
          },
        ],
      }),
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );

    expect(screen.getByText("最近一次对话")).toBeInTheDocument();
    expect(screen.getByText("你上次发的是")).toBeInTheDocument();
    expect(screen.getByText("帮我整理下周最重要的三件事")).toBeInTheDocument();
    expect(screen.queryByText("最近一次处理结果")).not.toBeInTheDocument();
  });
});
