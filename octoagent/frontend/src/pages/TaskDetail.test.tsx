import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { installFakeEventSource } from "../test/fakeEventSource";
import TaskDetail from "./TaskDetail";
import type { Artifact, TaskDetailResponse, TaskEvent } from "../types";

const ORACLE = "F149_TASK_DETAIL_STATE_CONTRACT_MISSING";

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

describe("TaskDetail", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("REJECTED 任务不会建立 SSE 连接", async () => {
    const FakeEventSource = installFakeEventSource();
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
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("子任务终态和旧状态回放不会覆盖当前任务 badge", async () => {
    const FakeEventSource = installFakeEventSource();

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(makeTaskDetailResponse()),
    );

    renderTaskDetail();

    await screen.findByText("Running Task");
    await screen.findByText("运行中");
    await waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(1);
    });

    await act(async () => {
      FakeEventSource.instances[0]?.emit("STATE_TRANSITION", {
        event_id: "evt-child-succeeded",
        task_id: "child-task-1",
        task_seq: 999,
        ts: "2026-03-21T12:00:09Z",
        type: "STATE_TRANSITION",
        actor: "system",
        payload: {
          kind: "state_transition",
          to_status: "SUCCEEDED",
        },
        final: true,
      });
      FakeEventSource.instances[0]?.emit("STATE_TRANSITION", {
        event_id: "evt-stale-own",
        task_id: "task-1",
        task_seq: 4,
        ts: "2026-03-21T12:00:08Z",
        type: "STATE_TRANSITION",
        actor: "system",
        payload: {
          kind: "state_transition",
          to_status: "RUNNING",
        },
        final: false,
      });
    });

    expect(screen.getByText("运行中")).toBeTruthy();
    expect(screen.queryByText("已完成")).toBeNull();
    expect(FakeEventSource.instances[0]?.readyState, ORACLE).not.toBe(
      FakeEventSource.CLOSED,
    );

    await act(async () => {
      FakeEventSource.instances[0]?.emit("STATE_TRANSITION", {
        event_id: "evt-own-waiting",
        task_id: "task-1",
        task_seq: 6,
        ts: "2026-03-21T12:00:10Z",
        type: "STATE_TRANSITION",
        actor: "system",
        payload: {
          kind: "state_transition",
          to_status: "WAITING_APPROVAL",
        },
        final: false,
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
    await waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(1);
    });

    await act(async () => {
      FakeEventSource.instances[0]?.emit("ARTIFACT_CREATED", {
        event_id: "evt-artifact-created",
        task_id: "task-1",
        task_seq: 6,
        ts: "2026-03-21T12:00:06Z",
        type: "ARTIFACT_CREATED",
        actor: "system",
        payload: {
          kind: "artifact_refresh",
          refresh_artifacts: true,
        },
        final: false,
      });
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    await user.click(screen.getByRole("button", { name: "原始数据" }));

    await screen.findByRole("heading", { name: "产出物 (1)" });
    expect(screen.getByText("lane-screenshot.png")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "事件 (2)" })).toBeTruthy();
  });

  it("标题优先使用 session alias，而不是 task.title", async () => {
    installFakeEventSource({ initialReadyState: 2 });
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

  it("unknown/history 事件不进入普通时间线，只在默认收起的高级诊断展示", async () => {
    const user = userEvent.setup();
    const FakeEventSource = installFakeEventSource();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        makeTaskDetailResponse({
          events: [
            ...makeTaskDetailResponse().events,
            makeEvent({
              event_id: "evt-raw-history",
              task_seq: 6,
              type: "MODEL_CALL_COMPLETED",
              payload: {
                password: "F149_SECRET_SENTINEL_DO_NOT_RENDER",
              },
            }),
            Object.assign(
              makeEvent({
                event_id: "evt-extra-top-level",
                task_seq: 7,
                type: "ARTIFACT_CREATED",
                payload: {
                  refresh_artifacts: true,
                },
              }),
              {
                raw_top_level: "F149_TOP_LEVEL_SECRET_DO_NOT_RENDER",
              },
            ),
          ],
        }),
      ),
    );

    renderTaskDetail();
    await screen.findByText("Running Task");
    await waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(1);
    });

    await act(async () => {
      FakeEventSource.instances[0]?.emit("MODEL_CALL_COMPLETED", {
        event_id: "evt-history",
        task_id: "task-1",
        task_seq: 7,
        ts: "2026-03-21T12:00:07Z",
        type: "MODEL_CALL_COMPLETED",
        actor: "system",
        payload: {
          kind: "diagnostic",
          source_type: "MODEL_CALL_COMPLETED",
          diagnostic: {
            summary: "历史记录已净化",
          },
          truncated: false,
        },
        final: false,
      });
    });

    await user.click(screen.getByRole("button", { name: "原始数据" }));
    expect(screen.getByRole("heading", { name: "事件 (2)" }), ORACLE).toBeTruthy();
    expect(screen.queryByText("历史记录已净化"), ORACLE).toBeNull();
    expect(document.body.textContent, ORACLE).not.toContain(
      "F149_SECRET_SENTINEL_DO_NOT_RENDER",
    );
    expect(document.body.textContent, ORACLE).not.toContain(
      "F149_TOP_LEVEL_SECRET_DO_NOT_RENDER",
    );

    await user.click(screen.getByText("高级诊断"));
    expect(
      screen.getByText("高级诊断").parentElement,
      ORACLE,
    ).toHaveTextContent("历史记录已净化");
    expect(document.body.textContent, ORACLE).not.toContain("payload:");
  });

  it("loading、可恢复错误、403 与 404 使用互斥的详情页状态", async () => {
    installFakeEventSource({ initialReadyState: 2 });
    let resolveFetch: ((response: Response) => void) | undefined;
    vi.spyOn(globalThis, "fetch").mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    renderTaskDetail("task-loading");
    expect(screen.getByText("加载任务详情…"), ORACLE).toBeTruthy();

    await act(async () => {
      resolveFetch?.(jsonResponse(makeTaskDetailResponse()));
    });
    await screen.findByText("Running Task");

    vi.restoreAllMocks();
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(
      new Error("temporary upstream failure"),
    );
    renderTaskDetail("task-recoverable");
    expect(
      await screen.findByText("暂时无法加载任务详情"),
      ORACLE,
    ).toBeTruthy();

    vi.restoreAllMocks();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse(
        {
          error: {
            code: "TASK_ACCESS_DENIED",
            message: "origin resource forbidden",
          },
        },
        403,
      ),
    );
    renderTaskDetail("task-forbidden");
    expect(await screen.findByText("无权查看这个任务"), ORACLE).toBeTruthy();
    expect(screen.queryByText("重新登录"), ORACLE).toBeNull();

    vi.restoreAllMocks();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse(
        {
          error: {
            code: "TASK_NOT_FOUND",
            message: "missing",
          },
        },
        404,
      ),
    );
    renderTaskDetail("task-missing");
    expect(await screen.findByText("找不到这个任务"), ORACLE).toBeTruthy();
  });

  it("进行中任务断线时显示可恢复的连接状态", async () => {
    const FakeEventSource = installFakeEventSource();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(makeTaskDetailResponse()),
    );
    renderTaskDetail();

    await screen.findByText("Running Task");
    await waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(1);
    });
    await act(async () => {
      FakeEventSource.instances[0]?.onerror?.call(
        FakeEventSource.instances[0] as unknown as EventSource,
        new Event("error"),
      );
    });

    expect(
      screen.getByRole("status", { name: "连接已断开，正在重试" }),
      ORACLE,
    ).toBeTruthy();
  });
});
