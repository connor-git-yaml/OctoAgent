import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useChatStream } from "./useChatStream";

const frontDoorRequestMock = vi.fn();
const buildFrontDoorSseUrlMock = vi.fn((path: string) => path);

vi.mock("../api/client", () => ({
  frontDoorRequest: (...args: unknown[]) => frontDoorRequestMock(...args),
  buildFrontDoorSseUrl: (...args: unknown[]) => buildFrontDoorSseUrlMock(...args),
  fetchTaskDetail: vi.fn(),
}));

describe("useChatStream", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
  });

  it("可见失败事件会覆盖处理中占位消息", async () => {
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

    await waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(1);
    });
    expect(result.current.messages.at(-1)?.content).toBe("主助手已接手，正在处理这条消息…");

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
    expect(result.current.messages.at(-1)?.content).toBe(
      "这次卡在当前工具或运行环境上了，不是你不会问。稍后重试，或先检查联网和后台连接。"
    );
    expect(result.current.messages.at(-1)?.isStreaming).toBe(false);
  });
});
