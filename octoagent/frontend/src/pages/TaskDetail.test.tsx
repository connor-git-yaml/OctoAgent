import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import TaskDetail from "./TaskDetail";
import type { Artifact, SSEEventData, TaskDetailResponse, TaskEvent } from "../types";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeEvent(overrides?: Partial<TaskEvent>): TaskEvent {
  return {
    event_id: "evt-1",
    task_seq: 1,
    ts: "2026-03-21T12:00:00Z",
    type: "TASK_CREATED",
    actor: "system",
    payload: {},
    ...overrides,
  };
}

function makeArtifact(overrides?: Partial<Artifact>): Artifact {
  return {
    artifact_id: "artifact-1",
    name: "lane-screenshot.png",
    size: 128,
    parts: [
      {
        type: "image",
        mime: "image/png",
        content: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB",
      },
    ],
    ...overrides,
  };
}

function makeTaskDetailResponse(
  overrides?: Partial<TaskDetailResponse>,
): TaskDetailResponse {
  return {
    task: {
      task_id: "task-1",
      created_at: "2026-03-21T12:00:00Z",
      updated_at: "2026-03-21T12:00:05Z",
      status: "RUNNING",
      title: "Running Task",
      thread_id: "thread-1",
      scope_id: "scope-1",
      requester: {
        channel: "web",
        sender_id: "owner",
      },
      risk_level: "low",
    },
    events: [
      makeEvent({
        event_id: "evt-running",
        task_seq: 5,
        type: "STATE_TRANSITION",
        payload: {
          from_status: "CREATED",
          to_status: "RUNNING",
        },
      }),
    ],
    artifacts: [],
    ...overrides,
  };
}

function renderTaskDetail(taskId = "task-1"): void {
  render(
    <MemoryRouter initialEntries={[`/tasks/${taskId}`]}>
      <Routes>
        <Route path="/tasks/:taskId" element={<TaskDetail />} />
      </Routes>
    </MemoryRouter>
  );
}

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

    constructor(public readonly url: string) {
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
        current.filter((item) => item !== listener),
      );
    }

    emit(type: string, payload: SSEEventData): void {
      const event = {
        data: JSON.stringify(payload),
      } as MessageEvent;
      for (const listener of this.listeners.get(type) ?? []) {
        listener(event);
      }
      if (type === "message") {
        this.onmessage?.call(this as unknown as EventSource, event);
      }
    }

    close(): void {
      this.readyState = FakeEventSource.CLOSED;
    }
  }

  vi.stubGlobal("EventSource", FakeEventSource);
  return FakeEventSource;
}

describe("TaskDetail", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("REJECTED 任务不会建立 SSE 连接", async () => {
    const eventSourceCalls: string[] = [];

    class FakeEventSource {
      static CLOSED = 2;
      readyState = 1;
      onopen: ((this: EventSource, ev: Event) => void) | null = null;
      onerror:
        | ((this: EventSource, ev: Event) => void)
        | null = null;
      onmessage:
        | ((this: EventSource, ev: MessageEvent) => void)
        | null = null;

      constructor(url: string) {
        eventSourceCalls.push(url);
      }

      addEventListener(): void {}

      removeEventListener(): void {}

      close(): void {
        this.readyState = FakeEventSource.CLOSED;
      }
    }

    vi.stubGlobal("EventSource", FakeEventSource);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        makeTaskDetailResponse({
          task: {
            task_id: "task-rejected",
            created_at: "2026-03-08T10:00:00Z",
            updated_at: "2026-03-08T10:01:00Z",
            status: "REJECTED",
            title: "Rejected Task",
            thread_id: "thread-1",
            scope_id: "scope-1",
            requester: {
              channel: "web",
              sender_id: "owner",
            },
            risk_level: "low",
          },
          events: [],
          artifacts: [],
        }),
      ),
    );

    renderTaskDetail("task-rejected");

    await screen.findByText("Rejected Task");
    expect(eventSourceCalls).toHaveLength(0);
  });

  it("子任务终态和旧状态回放不会覆盖当前任务 badge", async () => {
    const FakeEventSource = installFakeEventSource();

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(makeTaskDetailResponse()),
    );

    renderTaskDetail();

    await screen.findByText("Running Task");
    await screen.findByText("运行中");
    expect(FakeEventSource.instances).toHaveLength(1);

    await act(async () => {
      FakeEventSource.instances[0]?.emit("STATE_TRANSITION", {
        event_id: "evt-child-succeeded",
        task_id: "child-task-1",
        task_seq: 999,
        ts: "2026-03-21T12:00:09Z",
        type: "STATE_TRANSITION",
        actor: "system",
        payload: {
          from_status: "RUNNING",
          to_status: "SUCCEEDED",
        },
      });
      FakeEventSource.instances[0]?.emit("STATE_TRANSITION", {
        event_id: "evt-stale-own",
        task_id: "task-1",
        task_seq: 4,
        ts: "2026-03-21T12:00:08Z",
        type: "STATE_TRANSITION",
        actor: "system",
        payload: {
          from_status: "WAITING_APPROVAL",
          to_status: "RUNNING",
        },
      });
    });

    expect(screen.getByText("运行中")).toBeTruthy();
    expect(screen.queryByText("已完成")).toBeNull();

    await act(async () => {
      FakeEventSource.instances[0]?.emit("STATE_TRANSITION", {
        event_id: "evt-own-waiting",
        task_id: "task-1",
        task_seq: 6,
        ts: "2026-03-21T12:00:10Z",
        type: "STATE_TRANSITION",
        actor: "system",
        payload: {
          from_status: "RUNNING",
          to_status: "WAITING_APPROVAL",
        },
      });
    });

    await screen.findByText("等待审批");
  });

  it("收到 artifact 事件后会自动刷新详情并展示新截图", async () => {
    const user = userEvent.setup();
    const FakeEventSource = installFakeEventSource();
    const fetchMock = vi.spyOn(globalThis, "fetch");

    fetchMock
      .mockResolvedValueOnce(jsonResponse(makeTaskDetailResponse()))
      .mockResolvedValueOnce(jsonResponse(
        makeTaskDetailResponse({
          artifacts: [makeArtifact()],
        }),
      ));

    renderTaskDetail();

    await screen.findByText("Running Task");
    expect(FakeEventSource.instances).toHaveLength(1);

    await act(async () => {
      FakeEventSource.instances[0]?.emit("ARTIFACT_CREATED", {
        event_id: "evt-artifact-created",
        task_id: "task-1",
        task_seq: 6,
        ts: "2026-03-21T12:00:06Z",
        type: "ARTIFACT_CREATED",
        actor: "system",
        payload: {
          artifact_id: "artifact-1",
          name: "lane-screenshot.png",
          size: 128,
          part_count: 1,
        },
      });
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    await user.click(screen.getByRole("button", { name: "Raw Data" }));

    await screen.findByRole("heading", { name: "Artifacts (1)" });
    expect(screen.getByText("lane-screenshot.png")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Events (2)" })).toBeTruthy();
  });

  it("标题优先使用 session alias，而不是 task.title", async () => {
    vi.stubGlobal(
      "EventSource",
      class FakeEventSource {
        static CLOSED = 2;
        readyState = FakeEventSource.CLOSED;
        onopen: ((this: EventSource, ev: Event) => void) | null = null;
        onerror:
          | ((this: EventSource, ev: Event) => void)
          | null = null;
        onmessage:
          | ((this: EventSource, ev: MessageEvent) => void)
          | null = null;

        constructor() {}

        addEventListener(): void {}

        removeEventListener(): void {}

        close(): void {}
      }
    );
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        task: {
          task_id: "task-alias",
          created_at: "2026-03-21T10:00:00Z",
          updated_at: "2026-03-21T10:01:00Z",
          status: "SUCCEEDED",
          title: "请帮我创建安装一下 openrouter-perplexity MCP，下面的配置里面有你可以参考的信息",
          alias: "深圳",
          thread_id: "thread-alias",
          scope_id: "scope-alias",
          requester: {
            channel: "web",
            sender_id: "owner",
          },
          risk_level: "low",
        },
        events: [],
        artifacts: [],
      })
    );

    render(
      <MemoryRouter initialEntries={["/tasks/task-alias"]}>
        <Routes>
          <Route path="/tasks/:taskId" element={<TaskDetail />} />
        </Routes>
      </MemoryRouter>
    );

    expect(await screen.findByRole("heading", { name: "深圳" })).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", {
        name: "请帮我创建安装一下 openrouter-perplexity MCP，下面的配置里面有你可以参考的信息",
      })
    ).not.toBeInTheDocument();
  });
});
