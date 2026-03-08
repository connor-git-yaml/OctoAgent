import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import TaskDetail from "./TaskDetail";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
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
      jsonResponse({
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
      })
    );

    render(
      <MemoryRouter initialEntries={["/tasks/task-rejected"]}>
        <Routes>
          <Route path="/tasks/:taskId" element={<TaskDetail />} />
        </Routes>
      </MemoryRouter>
    );

    await screen.findByText("Rejected Task");
    expect(eventSourceCalls).toHaveLength(0);
  });
});
