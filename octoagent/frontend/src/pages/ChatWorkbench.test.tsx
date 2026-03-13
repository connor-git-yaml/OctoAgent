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
            dynamic_context: {
              current_tool_resolution_mode: "profile_first_core",
              current_mounted_tools: [],
              current_blocked_tools: [],
              current_discovery_entrypoints: ["workers.review"],
            },
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

  it("会在侧栏展示当前 Butler 到 Worker 的内部协作链", async () => {
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
        latest_message_id: "a2a-message-3",
        latest_message_type: "RESULT",
        status: "completed",
        message_count: 3,
        trace_id: "trace-a2a",
        metadata: {},
        updated_at: "2026-03-09T10:03:30Z",
      },
    ];
    snapshot.resources.context_continuity.a2a_messages = [
      {
        a2a_message_id: "a2a-message-1",
        a2a_conversation_id: "a2a-weather-1",
        message_seq: 1,
        task_id: "task-chat-1-child",
        work_id: "work-chat-1-child",
        message_type: "TASK",
        direction: "outbound",
        protocol_message_id: "dispatch-1",
        source_agent_runtime_id: "runtime-butler-default",
        source_agent_session_id: "agent-session-butler-default",
        target_agent_runtime_id: "runtime-worker-research-1",
        target_agent_session_id: "agent-session-worker-research-1",
        from_agent: "agent://butler.main",
        to_agent: "agent://worker.llm.research",
        idempotency_key: "a2a-message-1",
        payload: {},
        trace: {},
        metadata: {},
        created_at: "2026-03-09T10:03:00Z",
      },
      {
        a2a_message_id: "a2a-message-3",
        a2a_conversation_id: "a2a-weather-1",
        message_seq: 3,
        task_id: "task-chat-1-child",
        work_id: "work-chat-1-child",
        message_type: "RESULT",
        direction: "inbound",
        protocol_message_id: "dispatch-1-result",
        source_agent_runtime_id: "runtime-worker-research-1",
        source_agent_session_id: "agent-session-worker-research-1",
        target_agent_runtime_id: "runtime-butler-default",
        target_agent_session_id: "agent-session-butler-default",
        from_agent: "agent://worker.llm.research",
        to_agent: "agent://butler.main",
        idempotency_key: "a2a-message-3",
        payload: {},
        trace: {},
        metadata: {},
        created_at: "2026-03-09T10:03:30Z",
      },
    ];
    snapshot.resources.context_continuity.recall_frames = [
      {
        recall_frame_id: "recall-worker-1",
        agent_runtime_id: "runtime-worker-research-1",
        agent_session_id: "agent-session-worker-research-1",
        context_frame_id: "context-1",
        task_id: "task-chat-1-child",
        project_id: "project-default",
        workspace_id: "workspace-default",
        query: "深圳今天天气怎么样",
        recent_summary: "Research Worker 已整理天气证据。",
        memory_namespace_ids: ["memory/project-default"],
        memory_hit_count: 2,
        degraded_reason: "",
        created_at: "2026-03-09T10:03:25Z",
      },
    ];

    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [{ id: "msg-1", role: "assistant", content: "深圳今天晴。" }],
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

    expect(await screen.findByText("OctoAgent 已拆给专门角色继续处理")).toBeInTheDocument();
    expect(screen.getByText("主助手 -> Research Worker")).toBeInTheDocument();
    expect(screen.getByText("3 条 / 最新 结果回传")).toBeInTheDocument();
    expect(screen.getByText("结果回传 · #3")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "打开 Advanced 诊断" })).toBeInTheDocument();
  });

  it("当前会话的 A2A 不在全局快照里时仍会展示最小内部协作态", async () => {
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
        a2a_conversation_id: "",
        butler_agent_session_id: "",
        worker_agent_session_id: "",
        a2a_message_count: 0,
        runtime_summary: {
          delegation_strategy: "butler_owned_freshness",
          research_a2a_conversation_id: "a2a-weather-missing-from-snapshot",
          research_butler_agent_session_id: "agent-session-butler-default",
          research_worker_agent_session_id: "agent-session-worker-research-2",
          research_worker_id: "worker.llm.research",
          research_a2a_message_count: 2,
          research_worker_status: "SUCCEEDED",
        },
        updated_at: "2026-03-09T10:05:00Z",
        capabilities: [],
      },
    ];
    snapshot.resources.context_continuity.recall_frames = [
      {
        recall_frame_id: "recall-worker-2",
        agent_runtime_id: "runtime-worker-research-2",
        agent_session_id: "agent-session-worker-research-2",
        context_frame_id: "context-2",
        task_id: "task-chat-2-child",
        project_id: "project-default",
        workspace_id: "workspace-default",
        query: "深圳今天天气怎么样",
        recent_summary: "Research Worker 已整理天气证据。",
        memory_namespace_ids: ["memory/project-default"],
        memory_hit_count: 1,
        degraded_reason: "",
        created_at: "2026-03-09T10:05:20Z",
      },
    ];

    useWorkbenchMock.mockReturnValue({
      snapshot,
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [{ id: "msg-2", role: "assistant", content: "深圳今天晴。" }],
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

    expect(await screen.findByText("OctoAgent 已拆给专门角色继续处理")).toBeInTheDocument();
    expect(screen.getByText("主助手 -> Research Worker")).toBeInTheDocument();
    expect(screen.getByText("2 条 / 最新 结果回传")).toBeInTheDocument();
    expect(screen.getByText(/当前只显示协作摘要/)).toBeInTheDocument();
  });
});
