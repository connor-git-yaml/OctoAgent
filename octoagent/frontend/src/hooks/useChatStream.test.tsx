import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useChatStream } from "./useChatStream";

const frontDoorRequestMock = vi.fn();
const buildFrontDoorSseUrlMock = vi.fn((path: string, _options?: unknown) => path);
const fetchTaskDetailMock = vi.fn();
const executeControlActionMock = vi.fn();

vi.mock("../api/client", () => ({
  frontDoorRequest: (...args: unknown[]) => frontDoorRequestMock(args[0], args[1]),
  buildFrontDoorSseUrl: (path: string, options?: unknown) =>
    buildFrontDoorSseUrlMock(path, options),
  fetchTaskDetail: (...args: unknown[]) => fetchTaskDetailMock(...args),
  executeControlAction: (...args: unknown[]) => executeControlActionMock(...args),
}));

describe("useChatStream", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
  });

  function installFakeEventSource() {
    class FakeEventSource {
      static CLOSED = 2;
      static instances: FakeEventSource[] = [];
      readyState = 1;
      onopen: ((this: EventSource, ev: Event) => void) | null = null;
      onerror:
        | ((this: EventSource, ev: Event) => void)
        | null = null;
      onmessage:
        | ((this: EventSource, ev: MessageEvent) => void)
        | null = null;
      listeners = new Map<string, Array<(ev: MessageEvent) => void>>();

      constructor() {
        FakeEventSource.instances.push(this);
      }

      addEventListener(type: string, listener: (ev: MessageEvent) => void): void {
        const current = this.listeners.get(type) ?? [];
        current.push(listener);
        this.listeners.set(type, current);
      }

      removeEventListener(type: string, listener: (ev: MessageEvent) => void): void {
        const current = this.listeners.get(type) ?? [];
        this.listeners.set(
          type,
          current.filter((item) => item !== listener)
        );
      }

      emit(type: string, payload: unknown): void {
        const event = {
          data: JSON.stringify(payload),
        } as MessageEvent;
        for (const listener of this.listeners.get(type) ?? []) {
          listener(event);
        }
      }

      close(): void {
        this.readyState = FakeEventSource.CLOSED;
      }
    }

    vi.stubGlobal("EventSource", FakeEventSource);
    return FakeEventSource;
  }

  it("可以开始新对话并阻止旧会话被立即恢复", async () => {
    window.sessionStorage.setItem("octoagent.chat.activeTaskId", "task-old");
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-old",
        title: "旧对话",
        status: "SUCCEEDED",
      },
      events: [
        {
          event_id: "evt-user",
          task_id: "task-old",
          task_seq: 1,
          ts: "2026-03-14T10:00:00Z",
          type: "USER_MESSAGE",
          actor: "user",
          payload: { text: "旧问题" },
        },
        {
          event_id: "evt-model",
          task_id: "task-old",
          task_seq: 2,
          ts: "2026-03-14T10:00:01Z",
          type: "MODEL_CALL_COMPLETED",
          actor: "system",
          payload: { response_summary: "旧回答" },
        },
      ],
      artifacts: [],
    });

    const { result } = renderHook(() => useChatStream({ taskIds: ["task-old"] }));

    await waitFor(() => {
      expect(result.current.taskId).toBe("task-old");
      expect(fetchTaskDetailMock).toHaveBeenCalledTimes(1);
    });

    await act(async () => {
      await result.current.resetConversation();
    });

    expect(result.current.taskId).toBeNull();
    expect(result.current.messages).toEqual([]);
    expect(window.sessionStorage.getItem("octoagent.chat.activeTaskId")).toBeNull();
    expect(executeControlActionMock).toHaveBeenCalledWith(
      expect.objectContaining({
        action_id: "session.new",
        params: { task_id: "task-old" },
      })
    );

    await act(async () => {
      await Promise.resolve();
    });

    expect(fetchTaskDetailMock).toHaveBeenCalledTimes(1);
    expect(result.current.messages).toEqual([]);
  });

  it("恢复历史对话时不会因为 restoreTarget 对象重新创建而卡在 restoring", async () => {
    window.sessionStorage.setItem("octoagent.chat.activeTaskId", "task-restore-1");
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-restore-1",
        title: "恢复中的对话",
        status: "SUCCEEDED",
      },
      events: [
        {
          event_id: "evt-user-restore",
          task_id: "task-restore-1",
          task_seq: 1,
          ts: "2026-03-14T10:00:00Z",
          type: "USER_MESSAGE",
          actor: "user",
          payload: { text: "帮我恢复最近聊天" },
        },
        {
          event_id: "evt-agent-restore",
          task_id: "task-restore-1",
          task_seq: 2,
          ts: "2026-03-14T10:00:01Z",
          type: "MODEL_CALL_COMPLETED",
          actor: "system",
          payload: { response_summary: "已经恢复最近一轮对话。" },
        },
      ],
      artifacts: [],
    });

    const { result } = renderHook(() => useChatStream({ taskIds: ["task-restore-1"] }));

    await waitFor(() => {
      expect(result.current.restoring).toBe(false);
      expect(result.current.messages).toHaveLength(2);
    });
    expect(result.current.messages[0]?.content).toBe("帮我恢复最近聊天");
    expect(result.current.messages[1]?.content).toBe("已经恢复最近一轮对话。");
    expect(fetchTaskDetailMock).toHaveBeenCalledTimes(1);
  });

  it("新对话首条消息会携带 session-scoped 起点，包括显式 Agent 会话入口", async () => {
    const FakeEventSource = installFakeEventSource();
    executeControlActionMock.mockResolvedValueOnce({
      data: {
        new_conversation_token: "token-project-alpha",
        project_id: "project-alpha",
        agent_profile_id: "singleton:research",
      },
    });
    frontDoorRequestMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          task_id: "task-chat-project-alpha",
          stream_url: "/api/stream/task/task-chat-project-alpha",
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      )
    );

    const { result } = renderHook(() =>
      useChatStream(null, {
        activeProjectId: "project-default",
        activeWorkspaceId: "workspace-default",
      })
    );

    await act(async () => {
      await result.current.resetConversation();
    });

    await act(async () => {
      await result.current.sendMessage("开始 alpha project 的新对话");
    });

    expect(frontDoorRequestMock).toHaveBeenCalledWith(
      "/api/chat/send",
      expect.objectContaining({
        method: "POST",
        body: expect.any(String),
      })
    );
    const [, request] = frontDoorRequestMock.mock.calls[0]!;
    const payload = JSON.parse(String(request.body));
    expect(payload.new_conversation_token).toBe("token-project-alpha");
    expect(payload.project_id).toBe("project-alpha");
    expect(payload.agent_profile_id).toBe("singleton:research");

    await waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(1);
    });
  });

  it("可见失败事件会覆盖处理中占位消息", async () => {
    const FakeEventSource = installFakeEventSource();
    frontDoorRequestMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          task_id: "task-chat-visible-failure",
          stream_url: "/api/stream/task/task-chat-visible-failure",
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      )
    );

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.sendMessage("帮我查一下深圳天气");
    });

    expect(executeControlActionMock).toHaveBeenCalledWith(
      expect.objectContaining({
        action_id: "session.focus",
        params: { task_id: "task-chat-visible-failure" },
      })
    );

    await waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(1);
    });
    expect(result.current.messages[result.current.messages.length - 1]?.content).toBe(
      "主助手已接手，正在处理这条消息…"
    );

    await act(async () => {
      FakeEventSource.instances[0]?.emit("MODEL_CALL_FAILED", {
        event_id: "evt-visible-failure",
        task_id: "task-chat-visible-failure",
        task_seq: 2,
        ts: "2026-03-09T10:05:10Z",
        type: "MODEL_CALL_FAILED",
        actor: "system",
        payload: {
          error: "backend connection timeout",
        },
        final: true,
      });
    });

    await waitFor(() => {
      expect(result.current.error).toBe(
        "这次卡在当前工具或运行环境上了，不是你不会问。稍后重试，或先检查联网和后台连接。"
      );
    });
    expect(result.current.messages[result.current.messages.length - 1]?.content).toBe(
      "这次卡在当前工具或运行环境上了，不是你不会问。稍后重试，或先检查联网和后台连接。"
    );
    expect(result.current.messages[result.current.messages.length - 1]?.isStreaming).toBe(false);
  });

  it("内部 skill 失败事件不会盖掉最终回复", async () => {
    const FakeEventSource = installFakeEventSource();
    frontDoorRequestMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          task_id: "task-chat-hidden-failure",
          stream_url: "/api/stream/task/task-chat-hidden-failure",
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      )
    );

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.sendMessage("帮我查一下深圳天气");
    });

    await waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(1);
    });

    await act(async () => {
      FakeEventSource.instances[0]?.emit("MODEL_CALL_FAILED", {
        event_id: "evt-hidden-failure",
        task_id: "task-chat-hidden-failure",
        task_seq: 2,
        ts: "2026-03-09T10:05:10Z",
        type: "MODEL_CALL_FAILED",
        actor: "system",
        payload: {
          skill_id: "chat.general.inline",
          error: "temporary upstream failure",
        },
        final: false,
      });
      FakeEventSource.instances[0]?.emit("MODEL_CALL_COMPLETED", {
        event_id: "evt-final-completed",
        task_id: "task-chat-hidden-failure",
        task_seq: 3,
        ts: "2026-03-09T10:05:30Z",
        type: "MODEL_CALL_COMPLETED",
        actor: "system",
        payload: {
          response_summary: "深圳今天晴，约 20 摄氏度。",
        },
        final: true,
      });
    });

    await waitFor(() => {
      expect(result.current.messages[result.current.messages.length - 1]?.content).toBe(
        "深圳今天晴，约 20 摄氏度。"
      );
    });
    expect(result.current.error).toBeNull();
    expect(result.current.messages[result.current.messages.length - 1]?.isStreaming).toBe(false);
  });

  it("恢复历史答复时会过滤误泄漏的 tool transcript 和 JSON", async () => {
    window.sessionStorage.setItem("octoagent.chat.activeTaskId", "task-restore-sanitize");
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-restore-sanitize",
        title: "清洗脏答复",
        status: "SUCCEEDED",
      },
      events: [
        {
          event_id: "evt-user-sanitize",
          task_id: "task-restore-sanitize",
          task_seq: 1,
          ts: "2026-03-15T10:00:00Z",
          type: "USER_MESSAGE",
          actor: "user",
          payload: { text: "帮我总结 README" },
        },
        {
          event_id: "evt-agent-sanitize",
          task_id: "task-restore-sanitize",
          task_seq: 2,
          ts: "2026-03-15T10:00:02Z",
          type: "MODEL_CALL_COMPLETED",
          actor: "system",
          payload: {
            response_summary:
              "先给结论：README 主要在讲个人 AI OS。 to=memory.search\n" +
              '{"query":"README","matches":[]}\n' +
              "最终结论：这是个人 AI OS。",
          },
        },
      ],
      artifacts: [],
    });

    const { result } = renderHook(() => useChatStream({ taskIds: ["task-restore-sanitize"] }));

    await waitFor(() => {
      expect(result.current.restoring).toBe(false);
      expect(result.current.messages).toHaveLength(2);
    });

    const agentMessage = result.current.messages[1]?.content ?? "";
    expect(agentMessage).toContain("先给结论：README 主要在讲个人 AI OS。");
    expect(agentMessage).toContain("最终结论：这是个人 AI OS。");
    expect(agentMessage).not.toContain("to=memory.search");
    expect(agentMessage).not.toContain('"query"');
    expect(agentMessage).not.toContain('"matches"');
  });

  it("SSE 漏事件时收到 final 会兜底拉任务详情填充最终回复", async () => {
    const FakeEventSource = installFakeEventSource();
    frontDoorRequestMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          task_id: "task-close-fallback",
          stream_url: "/api/stream/task/task-close-fallback",
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      )
    );
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-close-fallback",
        title: "兜底场景",
        status: "SUCCEEDED",
      },
      events: [
        {
          event_id: "evt-user-close",
          task_id: "task-close-fallback",
          task_seq: 1,
          ts: "2026-04-01T10:00:00Z",
          type: "USER_MESSAGE",
          actor: "user",
          payload: { text: "查一下深圳天气" },
        },
        {
          event_id: "evt-agent-close",
          task_id: "task-close-fallback",
          task_seq: 2,
          ts: "2026-04-01T10:00:05Z",
          type: "MODEL_CALL_COMPLETED",
          actor: "system",
          payload: {
            response_summary: "抱歉，处理请求时遇到了问题：连续失败。请稍后重试。",
          },
        },
      ],
      artifacts: [],
    });

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.sendMessage("查一下深圳天气");
    });

    await waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(1);
    });

    // 模拟后端只推了一个 STATE_TRANSITION SUCCEEDED（final），
    // 关键的 MODEL_CALL_COMPLETED 在竞态/溢出中漏了
    await act(async () => {
      FakeEventSource.instances[0]?.emit("STATE_TRANSITION", {
        event_id: "evt-final-close",
        task_id: "task-close-fallback",
        task_seq: 3,
        ts: "2026-04-01T10:00:06Z",
        type: "STATE_TRANSITION",
        actor: "system",
        payload: { from_status: "RUNNING", to_status: "SUCCEEDED" },
        final: true,
      });
    });

    // 兜底生效：closeStream 调 fetchTaskDetail 读到最后的 agent 回复替换 placeholder
    await waitFor(() => {
      expect(fetchTaskDetailMock).toHaveBeenCalledWith("task-close-fallback");
      expect(result.current.messages[result.current.messages.length - 1]?.content).toBe(
        "抱歉，处理请求时遇到了问题：连续失败。请稍后重试。"
      );
    });
    expect(result.current.messages[result.current.messages.length - 1]?.isStreaming).toBe(false);
    expect(result.current.streaming).toBe(false);
  });

  it("续聊时 SSE 漏事件且当前轮无新回复不会把上一轮答复贴到 placeholder", async () => {
    const FakeEventSource = installFakeEventSource();
    frontDoorRequestMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          task_id: "task-turn-boundary",
          stream_url: "/api/stream/task/task-turn-boundary",
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      )
    );
    // 续聊：task 已有一轮 user-1/agent-1，当前第二轮 user-2 尚无 agent 输出
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-turn-boundary",
        title: "续聊边界",
        status: "CANCELLED",
      },
      events: [
        {
          event_id: "evt-user-1",
          task_id: "task-turn-boundary",
          task_seq: 1,
          ts: "2026-04-10T10:00:00Z",
          type: "USER_MESSAGE",
          actor: "user",
          payload: { text: "上一轮问题" },
        },
        {
          event_id: "evt-agent-1",
          task_id: "task-turn-boundary",
          task_seq: 2,
          ts: "2026-04-10T10:00:02Z",
          type: "MODEL_CALL_COMPLETED",
          actor: "system",
          payload: {
            response_summary: "这是上一轮的回复。",
          },
        },
        {
          event_id: "evt-user-2",
          task_id: "task-turn-boundary",
          task_seq: 3,
          ts: "2026-04-10T10:05:00Z",
          type: "USER_MESSAGE",
          actor: "user",
          payload: { text: "这一轮新问题" },
        },
      ],
      artifacts: [],
    });

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.sendMessage("这一轮新问题");
    });

    await waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(1);
    });

    // 模拟后端只推了 STATE_TRANSITION→CANCELLED（final），当前轮无 agent 输出
    await act(async () => {
      FakeEventSource.instances[0]?.emit("STATE_TRANSITION", {
        event_id: "evt-cancelled-final",
        task_id: "task-turn-boundary",
        task_seq: 4,
        ts: "2026-04-10T10:05:01Z",
        type: "STATE_TRANSITION",
        actor: "system",
        payload: { from_status: "RUNNING", to_status: "CANCELLED" },
        final: true,
      });
    });

    // 等兜底 fetchTaskDetail 的 promise 落定（microtask flush）
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(fetchTaskDetailMock).toHaveBeenCalledWith("task-turn-boundary");
    });

    // 关键断言：不允许把上一轮"这是上一轮的回复。"贴到当前 placeholder 上
    const lastMessage = result.current.messages[result.current.messages.length - 1];
    expect(lastMessage?.content).not.toBe("这是上一轮的回复。");
    // placeholder 在无当前轮回复时应保留（由上层决定是否改成失败文案）
    expect(lastMessage?.content).toBe("主助手已接手，正在处理这条消息…");
    expect(result.current.streaming).toBe(false);
  });

  it("恢复历史答复时保留正常的 JSON 示例", async () => {
    window.sessionStorage.setItem("octoagent.chat.activeTaskId", "task-restore-json");
    fetchTaskDetailMock.mockResolvedValue({
      task: {
        task_id: "task-restore-json",
        title: "保留 JSON 示例",
        status: "SUCCEEDED",
      },
      events: [
        {
          event_id: "evt-user-json",
          task_id: "task-restore-json",
          task_seq: 1,
          ts: "2026-03-15T10:10:00Z",
          type: "USER_MESSAGE",
          actor: "user",
          payload: { text: "给我一个 JSON 示例" },
        },
        {
          event_id: "evt-agent-json",
          task_id: "task-restore-json",
          task_seq: 2,
          ts: "2026-03-15T10:10:02Z",
          type: "MODEL_CALL_COMPLETED",
          actor: "system",
          payload: {
            response_summary:
              "配置如下：\n```json\n" +
              '{"result":"ok","ids":[1,2,3]}\n' +
              "```\n按这个格式返回即可。",
          },
        },
      ],
      artifacts: [],
    });

    const { result } = renderHook(() => useChatStream({ taskIds: ["task-restore-json"] }));

    await waitFor(() => {
      expect(result.current.restoring).toBe(false);
      expect(result.current.messages).toHaveLength(2);
    });

    const agentMessage = result.current.messages[1]?.content ?? "";
    expect(agentMessage).toContain("配置如下：");
    expect(agentMessage).toContain('"result":"ok"');
    expect(agentMessage).toContain('"ids":[1,2,3]');
    expect(agentMessage).toContain("按这个格式返回即可。");
  });
});
