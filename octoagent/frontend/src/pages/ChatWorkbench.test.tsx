import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChatWorkbench from "./ChatWorkbench";

const useWorkbenchMock = vi.fn();
const useChatStreamMock = vi.fn();
const fetchTaskDetailMock = vi.fn();

vi.mock("../components/shell/WorkbenchLayout", () => ({
  useWorkbench: () => useWorkbenchMock(),
}));

vi.mock("../hooks/useChatStream", () => ({
  useChatStream: (...args: unknown[]) => useChatStreamMock(...args),
}));

vi.mock("../api/client", () => ({
  fetchTaskDetail: (...args: unknown[]) => fetchTaskDetailMock(...args),
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
      },
      worker_profiles: {
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
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("发送消息时会带上当前默认 Worker 模板的 profile_id", async () => {
    const sendMessage = vi.fn().mockResolvedValue(undefined);
    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [],
      sendMessage,
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
      expect(sendMessage).toHaveBeenCalledWith("检查今天的备份情况", {
        agentProfileId: "project-default:nas-guardian",
      });
    });
  });

  it("继续当前会话时会沿用当前 work 绑定的 profile_id", async () => {
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
      expect(sendMessage).toHaveBeenCalledWith("继续检查今天的错误日志", {
        agentProfileId: "project-default:ops-root",
      });
    });
  });

  it("发送后会展示简洁的处理中运行条，而不是旧的折叠协作入口", async () => {
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

    expect(await screen.findByLabelText("当前处理进度")).toBeInTheDocument();
    expect(screen.getByText("主助手")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看内部协作进度" })).not.toBeInTheDocument();
  });

  it("会用运行条展示当前参与处理的 Agent，并移除旧侧栏模块", async () => {
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

    expect(await screen.findByLabelText("当前处理进度")).toBeInTheDocument();
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
    expect(screen.queryByLabelText("当前处理进度")).not.toBeInTheDocument();
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
});
