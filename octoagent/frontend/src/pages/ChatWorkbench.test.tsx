import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChatWorkbench from "./ChatWorkbench";

const useWorkbenchMock = vi.fn();
const useChatStreamMock = vi.fn();
const fetchTaskDetailMock = vi.fn();
const fetchTaskExecutionSessionMock = vi.fn();
const fetchApprovalsMock = vi.fn();
const attachExecutionInputMock = vi.fn();

vi.mock("../components/shell/WorkbenchLayout", () => ({
  useWorkbench: () => useWorkbenchMock(),
}));

vi.mock("../hooks/useChatStream", () => ({
  useChatStream: (...args: unknown[]) => useChatStreamMock(...args),
}));

vi.mock("../api/client", () => ({
  fetchTaskDetail: (...args: unknown[]) => fetchTaskDetailMock(...args),
  fetchTaskExecutionSession: (...args: unknown[]) =>
    fetchTaskExecutionSessionMock(...args),
  fetchApprovals: (...args: unknown[]) => fetchApprovalsMock(...args),
  attachExecutionInput: (...args: unknown[]) => attachExecutionInputMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    code?: string;
    hint?: string;

    constructor(message: string, options: { status: number; code?: string; hint?: string }) {
      super(message);
      this.status = options.status;
      this.code = options.code;
      this.hint = options.hint;
    }
  },
}));

function buildSnapshot(): any {
  return {
    resources: {
      sessions: {
        resource_type: "sessions",
        resource_id: "sessions:overview",
        schema_version: 1,
        sessions: [],
        focused_session_id: "",
        focused_thread_id: "",
        new_conversation_token: "",
        new_conversation_project_id: "",
        new_conversation_workspace_id: "",
        new_conversation_agent_profile_id: "",
      },
      project_selector: {
        current_project_id: "project-default",
        current_workspace_id: "workspace-default",
        available_projects: [
          {
            project_id: "project-default",
            slug: "default",
            name: "默认项目",
          },
          {
            project_id: "project-ops",
            slug: "ops",
            name: "运维项目",
          },
        ],
        available_workspaces: [
          {
            workspace_id: "workspace-default",
            project_id: "project-default",
            slug: "primary",
            name: "默认空间",
          },
          {
            workspace_id: "workspace-ops",
            project_id: "project-ops",
            slug: "ops",
            name: "运维空间",
          },
        ],
      },
      worker_profiles: {
        generated_at: "2026-03-14T10:00:00Z",
        summary: {
          default_profile_id: "project-default:nas-guardian",
        },
        profiles: [
          {
            profile_id: "project-default:nas-guardian",
            name: "NAS 管家",
            summary: "默认 Worker 模板。",
            static_config: {
              tool_profile: "standard",
            },
            dynamic_context: {},
          },
          {
            profile_id: "singleton:research",
            name: "Research Root Agent",
            summary: "资料整理与检索。",
            static_config: {
              tool_profile: "standard",
            },
            dynamic_context: {},
          },
        ],
      },
      delegation: {
        resource_type: "delegation_plane",
        resource_id: "delegation:overview",
        schema_version: 1,
        works: [],
      },
      context_continuity: {
        resource_type: "context_continuity",
        resource_id: "context:overview",
        schema_version: 1,
        frames: [],
        a2a_conversations: [],
        a2a_messages: [],
        recall_frames: [],
        degraded: {
          is_degraded: false,
        },
      },
      memory: {
        summary: {
          sor_current_count: 0,
        },
      },
    },
  };
}

describe("ChatWorkbench", () => {
  beforeEach(() => {
    fetchTaskDetailMock.mockResolvedValue(null);
    fetchTaskExecutionSessionMock.mockResolvedValue({ session: null });
    fetchApprovalsMock.mockResolvedValue({ approvals: [], total: 0 });
    attachExecutionInputMock.mockResolvedValue({
      result: {
        task_id: "task-default",
        session_id: "exec-default",
        request_id: "req-default",
        artifact_id: "artifact-default",
        delivered_live: true,
      },
      session: null,
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it("普通 Butler 对话发送消息时不会偷偷继承当前 Agent profile", async () => {
    const sendMessage = vi.fn().mockResolvedValue(undefined);
    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [],
      sendMessage,
      resetConversation: vi.fn(),
      streaming: false,
      restoring: false,
      error: null,
      taskId: null,
    });
    fetchTaskDetailMock.mockResolvedValue(null);

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    await userEvent.type(
      screen.getByPlaceholderText("告诉 OctoAgent 你现在要做什么"),
      "检查今天的备份情况"
    );
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith("检查今天的备份情况");
    });
  });

  it("继续普通会话时不会因为当前 work 是某个 worker 就把后续消息绑过去", async () => {
    const snapshot = buildSnapshot();
    snapshot.resources.worker_profiles.profiles.push({
      profile_id: "project-default:ops-root",
      name: "Ops Root",
      summary: "运维专用 Root Agent。",
      static_config: {
        tool_profile: "ops",
      },
      dynamic_context: {},
    });
    snapshot.resources.sessions.sessions = [
      {
        session_id: "thread-chat-ops",
        thread_id: "thread-chat-ops",
        task_id: "task-chat-ops",
        agent_profile_id: "",
        latest_message_summary: "继续看今天的机器状态",
        execution_summary: {
          work_id: "work-chat-ops",
        },
      },
    ];
    snapshot.resources.delegation.works = [
      {
        work_id: "work-chat-ops",
        task_id: "task-chat-ops",
        parent_work_id: "",
        title: "查看机器状态",
        status: "running",
        target_kind: "worker",
        selected_worker_type: "ops",
        route_reason: "delegation_strategy=follow_active_profile",
        owner_id: "butler.main",
        selected_tools: [],
        pipeline_run_id: "",
        runtime_id: "butler.main",
        project_id: "project-default",
        workspace_id: "workspace-default",
        agent_profile_id: "project-default:ops-root",
        requested_worker_profile_id: "project-default:ops-root",
        requested_worker_profile_version: 1,
        effective_worker_snapshot_id: "worker-snapshot:ops-root:1",
        child_work_ids: [],
        child_work_count: 0,
        merge_ready: false,
        runtime_summary: {},
        updated_at: "2026-03-09T10:03:00Z",
        capabilities: [],
      },
    ];

    const sendMessage = vi.fn().mockResolvedValue(undefined);
    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [{ id: "msg-ops", role: "agent", content: "我继续看一下。" }],
      sendMessage,
      streaming: false,
      restoring: false,
      error: null,
      taskId: "task-chat-ops",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-chat-ops",
        title: "继续看今天的机器状态",
        status: "RUNNING",
      },
      events: [],
      artifacts: [],
    });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    await userEvent.type(
      screen.getByPlaceholderText("告诉 OctoAgent 你现在要做什么"),
      "继续检查今天的错误日志"
    );
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith("继续检查今天的错误日志");
    });
  });

  it("发送后会把协作进度放进正在整理回复卡片里，而不是放在消息上方", async () => {
    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [
        {
          id: "msg-streaming",
          role: "agent",
          content: "主助手已接手，正在处理这条消息…",
          isStreaming: true,
        },
      ],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      resetConversation: vi.fn(),
      streaming: true,
      restoring: false,
      error: null,
      taskId: "task-streaming-1",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-streaming-1",
        title: "深圳今天天气怎么样",
        status: "RUNNING",
      },
      events: [],
      artifacts: [],
    });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    expect(await screen.findByLabelText("内部协作进度")).toBeInTheDocument();
    expect(screen.getByText("主助手")).toBeInTheDocument();
    expect(screen.queryByLabelText("当前处理进度")).not.toBeInTheDocument();
  });

  it("可以显式开始新对话", async () => {
    const resetConversation = vi.fn();
    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [
        {
          id: "msg-existing",
          role: "agent",
          content: "这是上一轮对话。",
          isStreaming: false,
        },
      ],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      resetConversation,
      streaming: false,
      restoring: false,
      error: null,
      taskId: "task-existing-1",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-existing-1",
        title: "旧会话",
        status: "SUCCEEDED",
      },
      events: [],
      artifacts: [],
    });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    await userEvent.click(screen.getByRole("button", { name: "开始新对话" }));

    expect(resetConversation).toHaveBeenCalledTimes(1);
  });

  it("会显示待创建新会话的项目起点", () => {
    const snapshot = buildSnapshot();
    snapshot.resources.sessions.new_conversation_token = "token-new";
    snapshot.resources.sessions.new_conversation_project_id = "project-ops";
    snapshot.resources.sessions.new_conversation_workspace_id = "workspace-ops";
    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      resetConversation: vi.fn(),
      streaming: false,
      restoring: false,
      error: null,
      taskId: null,
    });
    fetchTaskDetailMock.mockResolvedValue(null);

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    expect(screen.getByText("新会话起点已冻结")).toBeInTheDocument();
    expect(screen.getByText("会话项目 运维项目")).toBeInTheDocument();
    expect(screen.getByText("Workspace 运维空间")).toBeInTheDocument();
    expect(
      screen.getByText(/这段新对话会从 运维项目 \/ 运维空间 创建/)
    ).toBeInTheDocument();
  });

  it("显式开启专长 Agent 会话时，会明确提示下一条消息的直接入口", () => {
    const snapshot = buildSnapshot();
    snapshot.resources.sessions.new_conversation_token = "token-research";
    snapshot.resources.sessions.new_conversation_project_id = "project-default";
    snapshot.resources.sessions.new_conversation_workspace_id = "workspace-default";
    snapshot.resources.sessions.new_conversation_agent_profile_id = "singleton:research";
    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      resetConversation: vi.fn(),
      streaming: false,
      restoring: false,
      error: null,
      taskId: null,
    });
    fetchTaskDetailMock.mockResolvedValue(null);

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    expect(screen.getByText("待开启 Research Root Agent 会话")).toBeInTheDocument();
    expect(
      screen.getByText(/下一条消息会直接开启 Research Root Agent 会话/)
    ).toBeInTheDocument();
  });

  it("会明确提示当前会话绑定的项目与顶部选择不同", async () => {
    const snapshot = buildSnapshot();
    snapshot.resources.sessions.sessions = [
      {
        session_id: "session-ops",
        thread_id: "thread-ops",
        task_id: "task-ops",
        parent_task_id: "",
        parent_work_id: "",
        title: "运维排障",
        status: "RUNNING",
        channel: "web",
        requester_id: "owner",
        project_id: "project-ops",
        workspace_id: "workspace-ops",
        agent_profile_id: "",
        runtime_kind: "worker",
        lane: "running",
        latest_message_summary: "继续排查磁盘告警",
        latest_event_at: "2026-03-14T10:00:00Z",
        execution_summary: {},
        capabilities: [],
        detail_refs: {},
      },
    ];
    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [{ id: "msg-ops", role: "agent", content: "正在排查。", isStreaming: false }],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      resetConversation: vi.fn(),
      streaming: false,
      restoring: false,
      error: null,
      taskId: "task-ops",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-ops",
        title: "运维排障",
        status: "RUNNING",
      },
      events: [],
      artifacts: [],
    });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    expect(screen.getByText("会话项目 运维项目")).toBeInTheDocument();
    expect(screen.getByText("顶部选择 默认项目 / 默认空间")).toBeInTheDocument();
    expect(
      screen.getByText(/当前会话继续沿用 运维项目 \/ 运维空间/)
    ).toBeInTheDocument();
  });

  it("恢复状态停留较久时会给出直接开始新对话的出口", async () => {
    vi.useFakeTimers();
    const resetConversation = vi.fn();
    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      resetConversation,
      streaming: false,
      restoring: true,
      error: null,
      taskId: null,
    });
    fetchTaskDetailMock.mockResolvedValue(null);

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    expect(screen.getByText("正在恢复最近对话")).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(1700);
    });

    const resetButton = screen.getByRole("button", { name: "直接开始新对话" });
    expect(resetButton).toHaveClass("wb-button-inline");

    await act(async () => {
      resetButton.click();
    });
    expect(resetConversation).toHaveBeenCalledTimes(1);

    vi.useRealTimers();
  });

  it("Chat 头部的主要操作按钮使用统一的小规格按钮", async () => {
    const resetConversation = vi.fn();
    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [
        {
          id: "msg-existing",
          role: "agent",
          content: "这是上一轮对话。",
          isStreaming: false,
        },
      ],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      resetConversation,
      streaming: false,
      restoring: false,
      error: null,
      taskId: "task-existing-1",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-existing-1",
        title: "旧会话",
        status: "SUCCEEDED",
      },
      events: [],
      artifacts: [],
    });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(fetchTaskDetailMock).toHaveBeenCalledWith("task-existing-1");
    });
    expect(screen.getByRole("button", { name: "开始新对话" })).toHaveClass("wb-button-inline");
    expect(screen.getByRole("link", { name: "打开任务" })).toHaveClass("wb-button-inline");
  });

  it("会在消息区内展示当前参与处理的 Agent，并移除旧侧栏模块", async () => {
    const snapshot = buildSnapshot();
    snapshot.resources.sessions.sessions = [
      {
        session_id: "thread-chat-1",
        thread_id: "thread-chat-1",
        task_id: "task-chat-1",
        latest_message_summary: "请帮我查一下深圳天气",
        execution_summary: {
          work_id: "work-chat-1",
        },
      },
    ];
    snapshot.resources.delegation.works = [
      {
        work_id: "work-chat-1",
        task_id: "task-chat-1",
        parent_work_id: "",
        title: "深圳今天天气怎么样",
        status: "running",
        target_kind: "worker",
        selected_worker_type: "general",
        route_reason: "delegation_strategy=butler_owned_freshness",
        owner_id: "butler.main",
        selected_tools: [],
        pipeline_run_id: "",
        runtime_id: "butler.main",
        project_id: "project-default",
        workspace_id: "workspace-default",
        requested_worker_profile_id: "",
        requested_worker_profile_version: 0,
        effective_worker_snapshot_id: "",
        child_work_ids: [],
        child_work_count: 0,
        merge_ready: false,
        runtime_summary: {
          delegation_strategy: "butler_owned_freshness",
          research_a2a_conversation_id: "a2a-weather-1",
          research_worker_agent_session_id: "agent-session-worker-research-1",
          research_worker_id: "worker.llm.research",
          research_worker_status: "RUNNING",
        },
        updated_at: "2026-03-09T10:03:00Z",
        capabilities: [],
      },
    ];
    snapshot.resources.context_continuity.frames = [
      {
        context_frame_id: "context-1",
        task_id: "task-chat-1",
        session_id: "thread-chat-1",
        project_id: "project-default",
        workspace_id: "workspace-default",
        agent_profile_id: "agent-profile-default",
        recent_summary: "Butler 已经把天气查询转给 Research Worker。",
        memory_hit_count: 1,
        memory_hits: [],
        memory_recall: {},
        budget: {},
        source_refs: [],
        degraded_reason: "",
        created_at: "2026-03-09T10:02:00Z",
      },
    ];
    snapshot.resources.context_continuity.a2a_conversations = [
      {
        a2a_conversation_id: "a2a-weather-1",
        task_id: "task-chat-1-child",
        work_id: "work-chat-1-child",
        project_id: "project-default",
        workspace_id: "workspace-default",
        source_agent_runtime_id: "runtime-butler-default",
        source_agent_session_id: "agent-session-butler-default",
        target_agent_runtime_id: "runtime-worker-research-1",
        target_agent_session_id: "agent-session-worker-research-1",
        source_agent: "agent://butler.main",
        target_agent: "agent://worker.llm.research",
        context_frame_id: "context-1",
        request_message_id: "a2a-message-1",
        latest_message_id: "a2a-message-2",
        latest_message_type: "UPDATE",
        status: "running",
        message_count: 2,
        trace_id: "trace-a2a",
        metadata: {},
        updated_at: "2026-03-09T10:03:30Z",
      },
    ];

    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [{ id: "msg-1", role: "agent", content: "我正在查。" }],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      streaming: false,
      restoring: false,
      error: null,
      taskId: "task-chat-1",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-chat-1",
        title: "深圳今天天气怎么样",
        status: "RUNNING",
      },
      events: [],
      artifacts: [],
    });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    expect(await screen.findByLabelText("内部协作进度")).toBeInTheDocument();
    expect(screen.getByText("主助手")).toBeInTheDocument();
    expect(screen.getByText("Research Worker")).toBeInTheDocument();
    expect(screen.getByText("正在查资料：深圳今天天气怎么样")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "打开任务" })).toHaveAttribute(
      "href",
      "/tasks/task-chat-1"
    );
    expect(screen.queryByText("当前可用工具")).not.toBeInTheDocument();
    expect(screen.queryByText("记忆与上下文")).not.toBeInTheDocument();
    expect(screen.queryByText("当前任务")).not.toBeInTheDocument();
    expect(screen.queryByText("当前 Worker 模板")).not.toBeInTheDocument();
  });

  it("只展示当前 active work 的内部协作链，不混入同一 task 的历史残留 work", async () => {
    const snapshot = buildSnapshot();
    snapshot.resources.sessions.sessions = [
      {
        session_id: "thread-chat-live",
        thread_id: "thread-chat-live",
        task_id: "task-chat-live",
        latest_message_summary: "继续天气查询",
        execution_summary: {
          work_id: "work-chat-current",
        },
      },
    ];
    snapshot.resources.delegation.works = [
      {
        work_id: "work-chat-current",
        task_id: "task-chat-live",
        parent_work_id: "",
        title: "当前这次天气查询",
        status: "running",
        target_kind: "worker",
        selected_worker_type: "research",
        route_reason: "delegation_strategy=butler_owned_freshness",
        owner_id: "butler.main",
        selected_tools: ["web.search"],
        pipeline_run_id: "",
        runtime_id: "butler.main",
        project_id: "project-default",
        workspace_id: "workspace-default",
        requested_worker_profile_id: "",
        requested_worker_profile_version: 0,
        effective_worker_snapshot_id: "",
        child_work_ids: [],
        child_work_count: 0,
        merge_ready: false,
        runtime_summary: {
          research_a2a_conversation_id: "a2a-current",
          research_worker_agent_session_id: "agent-session-worker-current",
          research_worker_id: "worker.llm.research",
          research_worker_status: "RUNNING",
        },
        updated_at: "2026-03-14T12:00:00Z",
        capabilities: [],
      },
      {
        work_id: "work-chat-stale",
        task_id: "task-chat-live",
        parent_work_id: "",
        title: "上一次被中断的天气查询",
        status: "assigned",
        target_kind: "worker",
        selected_worker_type: "research",
        route_reason: "delegation_strategy=butler_owned_freshness",
        owner_id: "butler.main",
        selected_tools: ["web.search"],
        pipeline_run_id: "",
        runtime_id: "butler.main",
        project_id: "project-default",
        workspace_id: "workspace-default",
        requested_worker_profile_id: "",
        requested_worker_profile_version: 0,
        effective_worker_snapshot_id: "",
        child_work_ids: [],
        child_work_count: 0,
        merge_ready: false,
        runtime_summary: {
          research_a2a_conversation_id: "a2a-stale",
          research_worker_agent_session_id: "agent-session-worker-stale",
          research_worker_id: "worker.llm.research",
          research_worker_status: "RUNNING",
        },
        updated_at: "2026-03-14T11:30:00Z",
        capabilities: [],
      },
    ];
    snapshot.resources.context_continuity.a2a_conversations = [
      {
        a2a_conversation_id: "a2a-current",
        task_id: "task-chat-live-child",
        work_id: "work-chat-current",
        project_id: "project-default",
        workspace_id: "workspace-default",
        source_agent_runtime_id: "runtime-butler-default",
        source_agent_session_id: "agent-session-butler-default",
        target_agent_runtime_id: "runtime-worker-research-current",
        target_agent_session_id: "agent-session-worker-current",
        source_agent: "agent://butler.main",
        target_agent: "agent://worker.llm.research",
        context_frame_id: "context-current",
        request_message_id: "a2a-message-current-1",
        latest_message_id: "a2a-message-current-2",
        latest_message_type: "UPDATE",
        status: "running",
        message_count: 2,
        trace_id: "trace-current",
        metadata: {},
        updated_at: "2026-03-14T12:00:20Z",
      },
    ];

    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [{ id: "msg-live", role: "agent", content: "正在继续查。" }],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      streaming: false,
      restoring: false,
      error: null,
      taskId: "task-chat-live",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-chat-live",
        title: "继续天气查询",
        status: "RUNNING",
      },
      events: [],
      artifacts: [],
    });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    expect(await screen.findByLabelText("内部协作进度")).toBeInTheDocument();
    expect(screen.getAllByText("Research Worker")).toHaveLength(1);
    expect(screen.getByText("正在查资料：当前这次天气查询")).toBeInTheDocument();
    expect(screen.queryByText("正在查资料：上一次被中断的天气查询")).not.toBeInTheDocument();
  });

  it("支持 hover 查看主助手委派轨迹和 Worker 工具轨迹", async () => {
    const snapshot = buildSnapshot();
    snapshot.resources.sessions.sessions = [
      {
        session_id: "thread-chat-trace",
        thread_id: "thread-chat-trace",
        task_id: "task-chat-trace",
        latest_message_summary: "继续天气查询",
        execution_summary: {
          work_id: "work-chat-trace",
        },
      },
    ];
    snapshot.resources.delegation.works = [
      {
        work_id: "work-chat-trace",
        task_id: "task-chat-trace",
        parent_work_id: "",
        title: "帮我查深圳今天天气",
        status: "running",
        target_kind: "worker",
        selected_worker_type: "research",
        route_reason: "delegation_strategy=butler_owned_freshness",
        owner_id: "butler.main",
        selected_tools: ["web.search", "web.fetch"],
        pipeline_run_id: "",
        runtime_id: "butler.main",
        project_id: "project-default",
        workspace_id: "workspace-default",
        requested_worker_profile_id: "",
        requested_worker_profile_version: 0,
        effective_worker_snapshot_id: "",
        child_work_ids: [],
        child_work_count: 0,
        merge_ready: false,
        runtime_summary: {
          research_a2a_conversation_id: "a2a-trace",
          research_worker_agent_session_id: "agent-session-worker-trace",
          research_worker_id: "worker.llm.research",
          research_worker_status: "RUNNING",
        },
        updated_at: "2026-03-14T12:10:00Z",
        capabilities: [],
      },
    ];
    snapshot.resources.context_continuity.a2a_conversations = [
      {
        a2a_conversation_id: "a2a-trace",
        task_id: "task-chat-trace-child",
        work_id: "work-chat-trace",
        project_id: "project-default",
        workspace_id: "workspace-default",
        source_agent_runtime_id: "runtime-butler-default",
        source_agent_session_id: "agent-session-butler-default",
        target_agent_runtime_id: "runtime-worker-research-trace",
        target_agent_session_id: "agent-session-worker-trace",
        source_agent: "agent://butler.main",
        target_agent: "agent://worker.llm.research",
        context_frame_id: "context-trace",
        request_message_id: "a2a-message-trace-1",
        latest_message_id: "a2a-message-trace-2",
        latest_message_type: "UPDATE",
        status: "running",
        message_count: 2,
        trace_id: "trace-a2a",
        metadata: {},
        updated_at: "2026-03-14T12:10:20Z",
      },
    ];

    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [
        {
          id: "msg-trace",
          role: "agent",
          content: "正在继续处理。",
          isStreaming: true,
        },
      ],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      resetConversation: vi.fn(),
      streaming: true,
      restoring: false,
      error: null,
      taskId: "task-chat-trace",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-chat-trace",
        title: "继续天气查询",
        status: "RUNNING",
      },
      events: [
        {
          event_id: "event-a2a-received",
          task_seq: 11,
          ts: "2026-03-14T12:10:10Z",
          type: "A2A_MESSAGE_RECEIVED",
          actor: "worker.llm.research",
          payload: {
            work_id: "work-chat-trace",
            message_type: "UPDATE",
          },
        },
        {
          event_id: "event-tool-started",
          task_seq: 12,
          ts: "2026-03-14T12:10:12Z",
          type: "MODEL_CALL_STARTED",
          actor: "worker.llm.research",
          payload: {
            work_id: "work-chat-trace",
          },
        },
        {
          event_id: "event-model-completed",
          task_seq: 13,
          ts: "2026-03-14T12:10:13Z",
          type: "MODEL_CALL_COMPLETED",
          actor: "worker.llm.research",
          payload: {
            work_id: "work-chat-trace",
          },
        },
        {
          event_id: "event-tool-started",
          task_seq: 14,
          ts: "2026-03-14T12:10:14Z",
          type: "TOOL_CALL_STARTED",
          actor: "worker.llm.research",
          payload: {
            work_id: "work-chat-trace",
            tool_name: "web.search",
            args_summary: "q=深圳天气",
          },
        },
        {
          event_id: "event-tool-completed",
          task_seq: 15,
          ts: "2026-03-14T12:10:15Z",
          type: "TOOL_CALL_COMPLETED",
          actor: "worker.llm.research",
          payload: {
            work_id: "work-chat-trace",
            tool_name: "web.search",
            args_summary: "q=深圳天气",
            output_summary: "返回了 5 条候选结果。",
          },
        },
        {
          event_id: "event-worker-returned",
          task_seq: 16,
          ts: "2026-03-14T12:10:16Z",
          type: "WORKER_RETURNED",
          actor: "worker.llm.research",
          payload: {
            work_id: "work-chat-trace",
            status: "SUCCEEDED",
            summary: "已经拿到实时天气结果。",
          },
        },
      ],
      artifacts: [],
    });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    expect(await screen.findByLabelText("内部协作进度")).toBeInTheDocument();

    expect(screen.getByText("委派目标")).toBeInTheDocument();
    expect(screen.getByText("授权工具")).toBeInTheDocument();
    expect(screen.getByText("接手执行")).toBeInTheDocument();
    expect(screen.getByText("模型处理")).toBeInTheDocument();
    expect(screen.getByText("web.search")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看细节" })).not.toBeInTheDocument();

    const dispatchTrigger = screen.getAllByText("委派目标")[0]!;
    await userEvent.hover(dispatchTrigger);
    expect(await screen.findByText("输入")).toBeInTheDocument();
    expect(screen.getByText("Butler 已经把这轮问题改写成内部执行目标。")).toBeInTheDocument();
    expect(
      screen.getByText("核实外部事实，优先使用受治理的 Web 工具，再把可直接回复用户的结论带回主助手。")
    ).toBeInTheDocument();

    await userEvent.unhover(dispatchTrigger);
    const toolTrigger = screen.getAllByText("web.search")[0]!;
    await userEvent.hover(toolTrigger);
    expect(await screen.findByText("输入")).toBeInTheDocument();
    expect(screen.getByText("q=深圳天气")).toBeInTheDocument();
    expect(screen.getByText("返回了 5 条候选结果。")).toBeInTheDocument();
  });

  it("内部 worker 已经完成时不会过早显示已回传，而是明确主助手仍在整理", async () => {
    const snapshot = buildSnapshot();
    snapshot.resources.sessions.sessions = [
      {
        session_id: "thread-chat-result",
        thread_id: "thread-chat-result",
        task_id: "task-chat-result",
        latest_message_summary: "请继续整理天气答复",
        execution_summary: {
          work_id: "work-chat-result",
        },
      },
    ];
    snapshot.resources.delegation.works = [
      {
        work_id: "work-chat-result",
        task_id: "task-chat-result",
        parent_work_id: "",
        title: "深圳今天天气怎么样",
        status: "running",
        target_kind: "worker",
        selected_worker_type: "general",
        route_reason: "delegation_strategy=butler_owned_freshness",
        owner_id: "butler.main",
        selected_tools: [],
        pipeline_run_id: "",
        runtime_id: "butler.main",
        project_id: "project-default",
        workspace_id: "workspace-default",
        requested_worker_profile_id: "",
        requested_worker_profile_version: 0,
        effective_worker_snapshot_id: "",
        child_work_ids: [],
        child_work_count: 0,
        merge_ready: false,
        runtime_summary: {
          research_a2a_conversation_id: "a2a-weather-result",
          research_worker_agent_session_id: "agent-session-worker-research-result",
          research_worker_id: "worker.llm.research",
          research_worker_status: "SUCCEEDED",
        },
        updated_at: "2026-03-09T10:03:00Z",
        capabilities: [],
      },
    ];
    snapshot.resources.context_continuity.a2a_conversations = [
      {
        a2a_conversation_id: "a2a-weather-result",
        task_id: "task-chat-result-child",
        work_id: "work-chat-result-child",
        project_id: "project-default",
        workspace_id: "workspace-default",
        source_agent_runtime_id: "runtime-butler-default",
        source_agent_session_id: "agent-session-butler-default",
        target_agent_runtime_id: "runtime-worker-research-result",
        target_agent_session_id: "agent-session-worker-research-result",
        source_agent: "agent://butler.main",
        target_agent: "agent://worker.llm.research",
        context_frame_id: "context-result",
        request_message_id: "a2a-message-result-1",
        latest_message_id: "a2a-message-result-2",
        latest_message_type: "RESULT",
        status: "completed",
        message_count: 2,
        trace_id: "trace-result",
        metadata: {},
        updated_at: "2026-03-09T10:03:30Z",
      },
    ];

    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [
        {
          id: "msg-result",
          role: "agent",
          content: "主助手还在整理最终回复。",
          isStreaming: true,
        },
      ],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      streaming: true,
      restoring: false,
      error: null,
      taskId: "task-chat-result",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-chat-result",
        title: "深圳今天天气怎么样",
        status: "RUNNING",
      },
      events: [],
      artifacts: [],
    });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    expect(await screen.findByLabelText("内部协作进度")).toBeInTheDocument();
    expect(screen.getByText("内部结果已经拿到，但主助手还在整理、核对并收口最终回复。")).toBeInTheDocument();
    expect(screen.getByText("这轮查询已经完成，但主助手还在整理最终答复。")).toBeInTheDocument();
    expect(screen.queryByText("已回传")).not.toBeInTheDocument();
  });

  it("主助手默认执行器会折叠成主助手直连工具处理，不再渲染成额外 worker", async () => {
    const snapshot = buildSnapshot();
    snapshot.resources.sessions.sessions = [
      {
        session_id: "thread-chat-direct",
        thread_id: "thread-chat-direct",
        task_id: "task-chat-direct",
        latest_message_summary: "读取当前项目 README",
        execution_summary: {
          work_id: "work-chat-direct",
        },
      },
    ];
    snapshot.resources.delegation.works = [
      {
        work_id: "work-chat-direct",
        task_id: "task-chat-direct",
        parent_work_id: "",
        title: "读取当前项目 README",
        status: "running",
        target_kind: "fallback",
        selected_worker_type: "general",
        route_reason: "fallback=single_worker",
        owner_id: "orchestrator",
        selected_tools: ["filesystem.list_dir", "filesystem.read_text", "terminal.exec"],
        pipeline_run_id: "",
        runtime_id: "worker.llm.default",
        project_id: "project-default",
        workspace_id: "workspace-default",
        requested_worker_profile_id: "worker-profile-project-default-default-project-agent",
        requested_worker_profile_version: 1,
        effective_worker_snapshot_id: "worker-snapshot:general:1",
        child_work_ids: [],
        child_work_count: 0,
        merge_ready: false,
        runtime_summary: {},
        updated_at: "2026-03-15T00:56:00Z",
        capabilities: [],
      },
    ];

    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [
        {
          id: "msg-direct",
          role: "agent",
          content: "主助手正在直接处理。",
          isStreaming: true,
        },
      ],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      resetConversation: vi.fn(),
      streaming: true,
      restoring: false,
      error: null,
      taskId: "task-chat-direct",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-chat-direct",
        title: "读取当前项目 README",
        status: "RUNNING",
      },
      events: [
        {
          event_id: "event-model-started",
          task_seq: 10,
          ts: "2026-03-15T00:56:01Z",
          type: "MODEL_CALL_STARTED",
          actor: "worker.llm.default",
          payload: {
            work_id: "work-chat-direct",
          },
        },
        {
          event_id: "event-tool-started",
          task_seq: 11,
          ts: "2026-03-15T00:56:02Z",
          type: "TOOL_CALL_STARTED",
          actor: "tool",
          payload: {
            work_id: "work-chat-direct",
            tool_name: "filesystem.read_text",
            args_summary: "path=app/README.md",
          },
        },
        {
          event_id: "event-tool-completed",
          task_seq: 12,
          ts: "2026-03-15T00:56:03Z",
          type: "TOOL_CALL_COMPLETED",
          actor: "tool",
          payload: {
            work_id: "work-chat-direct",
            tool_name: "filesystem.read_text",
            args_summary: "path=app/README.md",
            output_summary: "读取到了 README 开头段落。",
          },
        },
      ],
      artifacts: [],
    });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    expect(await screen.findByText("主助手正在直接调用工具处理这条消息。")).toBeInTheDocument();
    expect(screen.getByText("处理方式")).toBeInTheDocument();
    expect(screen.getByText("直连工具")).toBeInTheDocument();
    expect(screen.getByText("filesystem.read_text")).toBeInTheDocument();
    expect(screen.queryByText("worker.llm.default")).not.toBeInTheDocument();
    expect(screen.queryByText("Research Worker")).not.toBeInTheDocument();
  });

  it("任务完成后不再展示内部协作运行条", async () => {
    const snapshot = buildSnapshot();
    snapshot.resources.sessions.sessions = [
      {
        session_id: "thread-chat-2",
        thread_id: "thread-chat-2",
        task_id: "task-chat-2",
        latest_message_summary: "请帮我查一下深圳天气",
        execution_summary: {
          work_id: "work-chat-2",
        },
      },
    ];
    snapshot.resources.delegation.works = [
      {
        work_id: "work-chat-2",
        task_id: "task-chat-2",
        parent_work_id: "",
        title: "深圳今天天气怎么样",
        status: "succeeded",
        target_kind: "worker",
        selected_worker_type: "general",
        route_reason: "delegation_strategy=butler_owned_freshness",
        owner_id: "butler.main",
        selected_tools: [],
        pipeline_run_id: "",
        runtime_id: "butler.main",
        project_id: "project-default",
        workspace_id: "workspace-default",
        requested_worker_profile_id: "",
        requested_worker_profile_version: 0,
        effective_worker_snapshot_id: "",
        child_work_ids: [],
        child_work_count: 0,
        merge_ready: false,
        runtime_summary: {
          delegation_strategy: "butler_owned_freshness",
          research_a2a_conversation_id: "a2a-weather-2",
          research_worker_agent_session_id: "agent-session-worker-research-2",
          research_worker_id: "worker.llm.research",
          research_worker_status: "SUCCEEDED",
        },
        updated_at: "2026-03-09T10:05:00Z",
        capabilities: [],
      },
    ];
    snapshot.resources.context_continuity.a2a_conversations = [
      {
        a2a_conversation_id: "a2a-weather-2",
        task_id: "task-chat-2-child",
        work_id: "work-chat-2-child",
        project_id: "project-default",
        workspace_id: "workspace-default",
        source_agent_runtime_id: "runtime-butler-default",
        source_agent_session_id: "agent-session-butler-default",
        target_agent_runtime_id: "runtime-worker-research-2",
        target_agent_session_id: "agent-session-worker-research-2",
        source_agent: "agent://butler.main",
        target_agent: "agent://worker.llm.research",
        context_frame_id: "context-2",
        request_message_id: "a2a-message-1",
        latest_message_id: "a2a-message-3",
        latest_message_type: "RESULT",
        status: "completed",
        message_count: 3,
        trace_id: "trace-a2a",
        metadata: {},
        updated_at: "2026-03-09T10:05:30Z",
      },
    ];

    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [{ id: "msg-2", role: "agent", content: "深圳今天晴。" }],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      streaming: false,
      restoring: false,
      error: null,
      taskId: "task-chat-2",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-chat-2",
        title: "深圳今天天气怎么样",
        status: "SUCCEEDED",
      },
      events: [],
      artifacts: [],
    });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    expect(await screen.findByText("深圳今天晴。")).toBeInTheDocument();
    expect(screen.queryByLabelText("内部协作进度")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "打开任务" })).toBeInTheDocument();
  });

  it("会按 markdown 渲染主消息内容", async () => {
    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [
        {
          id: "msg-markdown",
          role: "agent",
          content: "如果你默认还是 **深圳**，那你今天穿：\n\n- **白天**：长袖衬衫\n- **下装**：长裤",
          isStreaming: false,
        },
      ],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      streaming: false,
      restoring: false,
      error: null,
      taskId: null,
    });
    fetchTaskDetailMock.mockResolvedValue(null);

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    expect(await screen.findByText("深圳")).toBeInTheDocument();
    expect(screen.getByText("白天")).toBeInTheDocument();
    const list = screen.getByRole("list");
    expect(list).toBeInTheDocument();
    expect(list.textContent).toContain("长裤");
  });

  it("当前 task 存在时会刷新会话、工作和上下文资源", async () => {
    const snapshot = buildSnapshot();
    const refreshResources = vi.fn().mockResolvedValue(undefined);
    snapshot.resources.sessions.sessions = [
      {
        session_id: "thread-chat-refresh",
        thread_id: "thread-chat-refresh",
        task_id: "task-chat-refresh",
        title: "刷新中的聊天",
        latest_message_summary: "继续整理上下文",
        status: "RUNNING",
        channel: "web",
        latest_event_at: "2026-03-09T10:09:00Z",
        execution_summary: {
          work_id: "work-chat-refresh",
        },
      },
    ];
    snapshot.resources.context_continuity.frames = [
      {
        context_frame_id: "context-refresh",
        task_id: "task-chat-refresh",
        session_id: "thread-chat-refresh",
        project_id: "project-default",
        workspace_id: "workspace-default",
        agent_profile_id: "agent-profile-default",
        recent_summary: "这是当前 task 的上下文摘要。",
        memory_hit_count: 1,
        memory_hits: [],
        memory_recall: {},
        budget: {},
        source_refs: [],
        degraded_reason: "",
        created_at: "2026-03-09T10:08:00Z",
      },
    ];
    snapshot.resources.delegation.works = [
      {
        work_id: "work-chat-refresh",
        task_id: "task-chat-refresh",
        parent_work_id: "",
        title: "Chat Planner Work",
        status: "running",
        target_kind: "worker",
        selected_worker_type: "general",
        route_reason: "delegation_strategy=butler_owned_freshness",
        owner_id: "butler.main",
        selected_tools: [],
        pipeline_run_id: "",
        runtime_id: "butler.main",
        project_id: "project-default",
        workspace_id: "workspace-default",
        requested_worker_profile_id: "",
        requested_worker_profile_version: 0,
        effective_worker_snapshot_id: "",
        child_work_ids: [],
        child_work_count: 0,
        merge_ready: false,
        runtime_summary: {},
        updated_at: "2026-03-09T10:09:00Z",
        capabilities: [],
      },
    ];

    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources,
    });
    useChatStreamMock.mockReturnValue({
      messages: [{ id: "msg-refresh", role: "agent", content: "已为你整理出一版发布计划。" }],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      streaming: false,
      restoring: false,
      error: null,
      taskId: "task-chat-refresh",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-chat-refresh",
        title: "Chat Task",
        status: "RUNNING",
      },
      events: [],
      artifacts: [],
    });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    expect(await screen.findByText("已为你整理出一版发布计划。")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Chat Task" })).toBeInTheDocument();
    expect(screen.getByText("主助手正在直接处理这条消息。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "打开任务" })).toHaveAttribute(
      "href",
      "/tasks/task-chat-refresh"
    );
    expect(screen.queryByText("当前可用工具")).not.toBeInTheDocument();
    expect(screen.queryByText("记忆与上下文")).not.toBeInTheDocument();
    expect(screen.queryByText("当前任务")).not.toBeInTheDocument();

    await waitFor(() => {
      expect(refreshResources).toHaveBeenCalledWith([
        {
          resource_type: "sessions",
          resource_id: "sessions:overview",
          schema_version: 1,
        },
        {
          resource_type: "delegation_plane",
          resource_id: "delegation:overview",
          schema_version: 1,
        },
        {
          resource_type: "context_continuity",
          resource_id: "context:overview",
          schema_version: 1,
        },
      ]);
    });
  });

  it("当前任务等待审批时，可以直接在聊天里点击批准一次", async () => {
    const snapshot = buildSnapshot();
    snapshot.resources.sessions.sessions = [
      {
        session_id: "session-approve",
        thread_id: "thread-approve",
        task_id: "task-chat-approve",
        title: "审批中的会话",
        latest_message_summary: "README 查询",
      },
    ];
    snapshot.resources.sessions.operator_items = [
      {
        item_id: "approval:approval-1",
        kind: "approval",
        state: "pending",
        title: "terminal.exec 需要审批",
        summary: "这次只读命令需要你确认后继续。",
        task_id: "task-chat-approve",
        thread_id: "thread-approve",
        source_ref: "approval-1",
        created_at: "2026-03-15T10:00:00Z",
        suggested_actions: ["approve_once"],
        quick_actions: [
          { kind: "approve_once", label: "批准一次", style: "primary", enabled: true },
          { kind: "deny", label: "拒绝", style: "secondary", enabled: true },
        ],
        metadata: {},
      },
    ];

    const submitAction = vi.fn().mockResolvedValue({
      message: "这轮确认已经处理。",
      handled_at: "2026-03-15T10:00:03Z",
    });
    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources: vi.fn().mockResolvedValue(undefined),
      submitAction,
      busyActionId: null,
    });
    useChatStreamMock.mockReturnValue({
      messages: [{ id: "msg-approve", role: "user", content: "继续读 README", isStreaming: false }],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      resetConversation: vi.fn(),
      streaming: false,
      restoring: false,
      error: null,
      taskId: "task-chat-approve",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-chat-approve",
        title: "审批中的会话",
        status: "WAITING_APPROVAL",
      },
      events: [],
      artifacts: [],
    });
    fetchTaskExecutionSessionMock.mockResolvedValue({
      session: {
        session_id: "exec-approve",
        task_id: "task-chat-approve",
        state: "RUNNING",
        current_step: "waiting approval",
        can_attach_input: false,
        can_cancel: true,
        metadata: {},
      },
    });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    await userEvent.click(await screen.findByRole("button", { name: "批准一次" }));

    expect(submitAction).toHaveBeenCalledWith("operator.approval.resolve", {
      approval_id: "approval-1",
      mode: "once",
    });
  });

  it("审批快照缺失时，也会回退到当前执行会话里的 pending approval", async () => {
    const snapshot = buildSnapshot();
    snapshot.resources.sessions.sessions = [
      {
        session_id: "session-approve-fallback",
        thread_id: "thread-approve-fallback",
        task_id: "task-chat-approve-fallback",
        title: "审批补发会话",
        latest_message_summary: "README 查询",
      },
    ];

    const submitAction = vi.fn().mockResolvedValue({
      message: "这轮确认已经处理。",
      handled_at: "2026-03-15T10:10:03Z",
    });
    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources: vi.fn().mockResolvedValue(undefined),
      submitAction,
      busyActionId: null,
    });
    useChatStreamMock.mockReturnValue({
      messages: [{ id: "msg-approve-fallback", role: "user", content: "继续读 README", isStreaming: false }],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      resetConversation: vi.fn(),
      streaming: true,
      restoring: false,
      error: null,
      taskId: "task-chat-approve-fallback",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-chat-approve-fallback",
        title: "审批补发会话",
        status: "WAITING_APPROVAL",
      },
      events: [
        {
          event_id: "event-tool-started",
          task_seq: 11,
          ts: "2026-03-15T10:10:00Z",
          type: "TOOL_CALL_STARTED",
          actor: "tool",
          payload: {
            tool_name: "terminal.exec",
            args_summary: "cmd=rg --files README.md",
          },
        },
      ],
      artifacts: [],
    });
    fetchTaskExecutionSessionMock.mockResolvedValue({
      session: {
        session_id: "exec-approve-fallback",
        task_id: "task-chat-approve-fallback",
        state: "WAITING_INPUT",
        current_step: "waiting approval",
        pending_approval_id: "approval-fallback-1",
        requested_input: "请确认是否允许执行只读终端命令",
        can_attach_input: false,
        can_cancel: true,
        metadata: {},
      },
    });
    fetchApprovalsMock.mockResolvedValue({ approvals: [], total: 0 });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    await userEvent.type(
      await screen.findByPlaceholderText("告诉 OctoAgent 你现在要做什么"),
      "/approve"
    );
    await userEvent.click(screen.getByRole("button", { name: "执行命令" }));

    expect(submitAction).toHaveBeenCalledWith("operator.approval.resolve", {
      approval_id: "approval-fallback-1",
      mode: "once",
    });
  });

  it("输入斜杠命令时会给出可筛选的补全列表，并支持键盘补全", async () => {
    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      refreshResources: vi.fn().mockResolvedValue(undefined),
      submitAction: vi.fn().mockResolvedValue(null),
      busyActionId: null,
    });
    useChatStreamMock.mockReturnValue({
      messages: [{ id: "msg-streaming", role: "agent", content: "正在处理。", isStreaming: true }],
      sendMessage: vi.fn().mockResolvedValue(undefined),
      resetConversation: vi.fn(),
      streaming: true,
      restoring: false,
      error: null,
      taskId: "task-command-menu",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-command-menu",
        title: "命令补全会话",
        status: "RUNNING",
      },
      events: [],
      artifacts: [],
    });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    const input = await screen.findByPlaceholderText("告诉 OctoAgent 你现在要做什么");
    await userEvent.type(input, "/a");

    expect(screen.getByRole("listbox", { name: "聊天命令建议" })).toBeInTheDocument();
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(2);
    expect(options.map((item) => item.textContent)).toEqual(
      expect.arrayContaining(["/approve批准一次当前审批", "/approve always总是批准当前审批"])
    );
    expect(screen.queryByRole("option", { name: /^\/deny/ })).not.toBeInTheDocument();

    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(input).toHaveValue("/approve always");
  });

  it("等待输入时会把下一条消息作为 steer 附加到当前执行", async () => {
    const snapshot = buildSnapshot();
    snapshot.resources.sessions.sessions = [
      {
        session_id: "session-steer",
        thread_id: "thread-steer",
        task_id: "task-chat-steer",
        title: "等待补充的会话",
        latest_message_summary: "请补充路径",
      },
    ];

    const submitAction = vi.fn().mockResolvedValue(null);
    const sendMessage = vi.fn().mockResolvedValue(undefined);
    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources: vi.fn().mockResolvedValue(undefined),
      submitAction,
      busyActionId: null,
    });
    useChatStreamMock.mockReturnValue({
      messages: [{ id: "msg-steer", role: "agent", content: "请补充 README 路径。", isStreaming: false }],
      sendMessage,
      resetConversation: vi.fn(),
      streaming: false,
      restoring: false,
      error: null,
      taskId: "task-chat-steer",
    });
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-chat-steer",
        title: "等待补充的会话",
        status: "WAITING_INPUT",
      },
      events: [],
      artifacts: [],
    });
    fetchTaskExecutionSessionMock.mockResolvedValue({
      session: {
        session_id: "exec-steer",
        task_id: "task-chat-steer",
        state: "WAITING_INPUT",
        current_step: "requesting path",
        requested_input: "请告诉我 README 的相对路径",
        can_attach_input: true,
        can_cancel: true,
        metadata: {},
      },
    });
    attachExecutionInputMock.mockResolvedValue({
      result: {
        task_id: "task-chat-steer",
        session_id: "exec-steer",
        request_id: "req-steer",
        artifact_id: "artifact-steer",
        delivered_live: true,
      },
      session: {
        session_id: "exec-steer",
        task_id: "task-chat-steer",
        state: "RUNNING",
        current_step: "running",
        can_attach_input: false,
        can_cancel: true,
        metadata: {},
      },
    });

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    await userEvent.type(
      await screen.findByPlaceholderText("请告诉我 README 的相对路径"),
      "./README.md"
    );
    await userEvent.click(screen.getByRole("button", { name: "继续这轮" }));

    expect(attachExecutionInputMock).toHaveBeenCalledWith("task-chat-steer", {
      text: "./README.md",
      approval_id: undefined,
      actor: "user:web",
    });
    expect(sendMessage).not.toHaveBeenCalled();
  });
});
