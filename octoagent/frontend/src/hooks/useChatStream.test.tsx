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
});
