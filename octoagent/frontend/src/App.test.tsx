import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

type FetchArgs = [RequestInfo | URL, RequestInit | undefined];

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function buildSnapshot(proxyUrl = "http://localhost:4000") {
  return {
    contract_version: "1.0.0",
    generated_at: "2026-03-09T10:00:00Z",
    registry: {
      contract_version: "1.0.0",
      resource_type: "action_registry",
      resource_id: "actions:registry",
      schema_version: 1,
      generated_at: "2026-03-09T10:00:00Z",
      updated_at: "2026-03-09T10:00:00Z",
      status: "ready",
      degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
      warnings: [],
      capabilities: [],
      refs: {},
      actions: [],
    },
    resources: {
      wizard: {
        contract_version: "1.0.0",
        resource_type: "wizard_session",
        resource_id: "wizard:default",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        session_version: 1,
        current_step: "complete",
        resumable: true,
        blocking_reason: "",
        steps: [],
        summary: {},
        next_actions: [],
      },
      config: {
        contract_version: "1.0.0",
        resource_type: "config_schema",
        resource_id: "config:octoagent",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        schema: {
          type: "object",
          properties: {
            runtime: {
              type: "object",
              properties: {
                litellm_proxy_url: { type: "string" },
              },
            },
          },
        },
        ui_hints: {
          "runtime.litellm_proxy_url": {
            field_path: "runtime.litellm_proxy_url",
            section: "runtime",
            label: "LiteLLM Proxy URL",
            description: "",
            widget: "text",
            placeholder: "http://localhost:4000",
            help_text: "",
            sensitive: false,
            multiline: false,
            order: 10,
          },
        },
        current_value: {
          runtime: {
            litellm_proxy_url: proxyUrl,
          },
        },
        validation_rules: [],
        bridge_refs: [],
        secret_refs_only: true,
      },
      project_selector: {
        contract_version: "1.0.0",
        resource_type: "project_selector",
        resource_id: "project:selector",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        current_project_id: "project-default",
        current_workspace_id: "workspace-default",
        default_project_id: "project-default",
        fallback_reason: "",
        switch_allowed: true,
        available_projects: [
          {
            project_id: "project-default",
            slug: "default",
            name: "Default Project",
            is_default: true,
            status: "active",
            workspace_ids: ["workspace-default"],
            warnings: [],
          },
        ],
        available_workspaces: [
          {
            workspace_id: "workspace-default",
            project_id: "project-default",
            slug: "primary",
            name: "Primary",
            kind: "primary",
            root_path: "/tmp/default",
          },
        ],
      },
      sessions: {
        contract_version: "1.0.0",
        resource_type: "session_projection",
        resource_id: "sessions:overview",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        focused_session_id: "",
        focused_thread_id: "",
        sessions: [],
        operator_summary: {
          total_pending: 0,
          approvals: 0,
          alerts: 0,
          retryable_failures: 0,
          pairing_requests: 0,
          degraded_sources: [],
          generated_at: "2026-03-09T10:00:00Z",
        },
        operator_items: [],
      },
      capability_pack: {
        contract_version: "1.0.0",
        resource_type: "capability_pack",
        resource_id: "capability:bundled",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        selected_project_id: "project-default",
        selected_workspace_id: "workspace-default",
        pack: {
          pack_id: "bundled",
          version: "1.0.0",
          skills: [],
          tools: [],
          worker_profiles: [],
          bootstrap_files: [],
          fallback_toolset: [],
          degraded_reason: "",
          generated_at: "2026-03-09T10:00:00Z",
        },
      },
      delegation: {
        contract_version: "1.0.0",
        resource_type: "delegation_plane",
        resource_id: "delegation:overview",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        works: [],
        summary: { by_status: {} },
      },
      pipelines: {
        contract_version: "1.0.0",
        resource_type: "skill_pipeline",
        resource_id: "pipeline:overview",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        runs: [],
        summary: {},
      },
      automation: {
        contract_version: "1.0.0",
        resource_type: "automation_job",
        resource_id: "automation:jobs",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        jobs: [],
        run_history_cursor: "",
      },
      diagnostics: {
        contract_version: "1.0.0",
        resource_type: "diagnostics_summary",
        resource_id: "diagnostics:runtime",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        overall_status: "ready",
        subsystems: [],
        recent_failures: [],
        runtime_snapshot: {},
        recovery_summary: {},
        update_summary: {},
        channel_summary: { telegram_enabled: false },
        deep_refs: {},
      },
      memory: {
        contract_version: "1.0.0",
        resource_type: "memory_console",
        resource_id: "memory:overview",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        filters: {
          project_id: "project-default",
          workspace_id: "workspace-default",
          scope_id: "",
          partition: "default",
          layer: "sor",
          query: "",
          include_history: false,
          include_vault_refs: false,
          limit: 20,
          cursor: "",
        },
        summary: {
          scope_count: 1,
          fragment_count: 2,
          sor_current_count: 3,
          sor_history_count: 0,
          vault_ref_count: 0,
          proposal_count: 1,
        },
        records: [],
        available_scopes: [],
        available_partitions: [],
        available_layers: [],
      },
      imports: {
        contract_version: "1.0.0",
        resource_type: "import_workbench",
        resource_id: "imports:workbench",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        summary: {
          source_count: 0,
          recent_run_count: 0,
          resume_available_count: 0,
          warning_count: 0,
          error_count: 0,
        },
        sources: [],
        recent_runs: [],
        resume_entries: [],
      },
    },
  };
}

function buildWorkspace(
  workspaceId: string,
  projectId: string,
  name: string
) {
  return {
    workspace_id: workspaceId,
    project_id: projectId,
    slug: workspaceId,
    name,
    kind: "primary",
    root_path: `/tmp/${workspaceId}`,
  };
}

function buildSession(taskId: string, workId: string) {
  return {
    session_id: `thread-${taskId}`,
    thread_id: `thread-${taskId}`,
    task_id: taskId,
    parent_task_id: "",
    parent_work_id: "",
    title: `Task ${taskId}`,
    status: "RUNNING",
    channel: "web",
    requester_id: "owner",
    project_id: "project-default",
    workspace_id: "workspace-default",
    runtime_kind: "worker",
    latest_message_summary: "正在处理中",
    latest_event_at: "2026-03-09T10:06:00Z",
    execution_summary: {
      work_id: workId,
    },
    capabilities: [],
    detail_refs: {},
  };
}

function buildWork(
  workId: string,
  status: string,
  options?: {
    title?: string;
    capabilities?: Array<Record<string, unknown>>;
  }
) {
  return {
    work_id: workId,
    task_id: `task-${workId}`,
    parent_work_id: "",
    title: options?.title ?? `Work ${workId}`,
    status,
    target_kind: "worker",
    selected_worker_type: "general",
    route_reason: "planner",
    owner_id: "owner",
    selected_tools: [],
    pipeline_run_id: "",
    runtime_id: "",
    project_id: "project-default",
    workspace_id: "workspace-default",
    child_work_ids: [],
    child_work_count: 0,
    merge_ready: false,
    runtime_summary: {},
    updated_at: "2026-03-09T10:05:00Z",
    capabilities: options?.capabilities ?? [],
  };
}

function buildTaskDetail(taskId: string, title: string) {
  return {
    task: {
      task_id: taskId,
      created_at: "2026-03-09T10:00:00Z",
      updated_at: "2026-03-09T10:05:00Z",
      status: "RUNNING",
      title,
      thread_id: `thread-${taskId}`,
      scope_id: "scope-default",
      requester: {
        channel: "web",
        sender_id: "owner",
      },
      risk_level: "low",
    },
    events: [],
    artifacts: [],
  };
}

describe("App workbench routing", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    window.history.pushState({}, "", "/");
  });

  it("默认根路由进入 Home，而不是旧控制台首页", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(buildSnapshot()));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "已经可以开始" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Settings/ })).toBeInTheDocument();
  });

  it("设置页保存时通过 config.apply 提交并按 resource_refs 回刷", async () => {
    window.history.pushState({}, "", "/settings");

    const nextSnapshot = buildSnapshot("http://localhost:4100");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, _init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(buildSnapshot()));
      }
      if (url.includes("/api/control/actions")) {
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
            result: {
              contract_version: "1.0.0",
              request_id: "req-config-apply",
              correlation_id: "req-config-apply",
              action_id: "config.apply",
              status: "completed",
              code: "CONFIG_APPLIED",
              message: "配置已保存",
              data: {},
              resource_refs: [
                { resource_type: "config_schema", resource_id: "config:octoagent" },
                { resource_type: "diagnostics_summary", resource_id: "diagnostics:runtime" },
              ],
              target_refs: [],
              handled_at: "2026-03-09T10:02:00Z",
            },
          })
        );
      }
      if (url.includes("/api/control/resources/config")) {
        return Promise.resolve(jsonResponse(nextSnapshot.resources.config));
      }
      if (url.includes("/api/control/resources/diagnostics")) {
        return Promise.resolve(jsonResponse(nextSnapshot.resources.diagnostics));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    const input = await screen.findByLabelText("LiteLLM Proxy URL");
    await userEvent.clear(input);
    await userEvent.type(input, "http://localhost:4100");
    await userEvent.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) =>
          String((call as FetchArgs)[0]).includes("/api/control/actions")
        )
      ).toBe(true)
    );

    const actionCall = fetchMock.mock.calls.find((call) =>
      String((call as FetchArgs)[0]).includes("/api/control/actions")
    ) as FetchArgs | undefined;

    expect(String(actionCall?.[1]?.body)).toContain('"action_id":"config.apply"');
    expect(String(actionCall?.[1]?.body)).toContain("http://localhost:4100");
    expect(await screen.findByText(/配置已保存/)).toBeInTheDocument();
  });

  it("project.select 后会全量回刷工作台并同步 Project 摘要", async () => {
    const beforeSnapshot = buildSnapshot();
    beforeSnapshot.resources.project_selector.available_projects.push({
      project_id: "project-ops",
      slug: "ops",
      name: "Ops Project",
      is_default: false,
      status: "active",
      workspace_ids: ["workspace-ops"],
      warnings: [],
    });
    beforeSnapshot.resources.project_selector.available_workspaces.push(
      buildWorkspace("workspace-ops", "project-ops", "Ops Primary")
    );

    const afterSnapshot = buildSnapshot();
    afterSnapshot.resources.project_selector.current_project_id = "project-ops";
    afterSnapshot.resources.project_selector.current_workspace_id = "workspace-ops";
    afterSnapshot.resources.project_selector.available_projects =
      beforeSnapshot.resources.project_selector.available_projects;
    afterSnapshot.resources.project_selector.available_workspaces =
      beforeSnapshot.resources.project_selector.available_workspaces;
    afterSnapshot.resources.sessions.operator_summary = {
      ...afterSnapshot.resources.sessions.operator_summary,
      total_pending: 4,
      approvals: 3,
      pairing_requests: 1,
    };
    afterSnapshot.resources.delegation.works = [
      buildWork("work-ops", "running", { title: "Ops Work" }),
    ] as typeof afterSnapshot.resources.delegation.works;
    afterSnapshot.resources.delegation.summary = { by_status: { running: 1 } };
    afterSnapshot.resources.memory.summary = {
      ...afterSnapshot.resources.memory.summary,
      sor_current_count: 8,
    };

    let snapshotCallCount = 0;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        snapshotCallCount += 1;
        return Promise.resolve(
          jsonResponse(snapshotCallCount === 1 ? beforeSnapshot : afterSnapshot)
        );
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
            result: {
              contract_version: "1.0.0",
              request_id: "req-project-select",
              correlation_id: "req-project-select",
              action_id: "project.select",
              status: "completed",
              code: "PROJECT_SELECTED",
              message: "已切换当前 project",
              data: {
                project_id: "project-ops",
                workspace_id: "workspace-ops",
              },
              resource_refs: [
                {
                  resource_type: "project_selector",
                  resource_id: "project:selector",
                  schema_version: 1,
                },
              ],
              target_refs: [],
              handled_at: "2026-03-09T10:03:00Z",
            },
          })
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    const { container } = render(<App />);

    await screen.findByRole("heading", { name: "已经可以开始" });
    await userEvent.selectOptions(screen.getByLabelText("切换 Project"), "project-ops");
    await userEvent.selectOptions(screen.getByLabelText("切换 Workspace"), "workspace-ops");
    await userEvent.click(screen.getByRole("button", { name: "切换" }));

    const projectPanelLabels = screen.getAllByText("当前 Project");
    const projectPanel =
      projectPanelLabels[projectPanelLabels.length - 1]?.closest("section") ?? null;
    expect(projectPanel).not.toBeNull();
    await waitFor(() =>
      expect(
        within(projectPanel!).getByRole("heading", { name: "Ops Project" })
      ).toBeInTheDocument()
    );

    const summaryGrid = container.querySelector<HTMLElement>(".wb-card-grid.wb-card-grid-4");
    expect(summaryGrid).not.toBeNull();

    const pendingCard = within(summaryGrid!).getByText("待你确认").closest("article");
    const workCard = within(summaryGrid!).getByText("当前工作").closest("article");
    const memoryCard = within(summaryGrid!).getByText("记忆摘要").closest("article");

    expect(pendingCard).not.toBeNull();
    expect(workCard).not.toBeNull();
    expect(memoryCard).not.toBeNull();
    expect(within(pendingCard!).getByText("4")).toBeInTheDocument();
    expect(within(workCard!).getByText("1")).toBeInTheDocument();
    expect(within(memoryCard!).getByText("8")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter((call) =>
        String((call as FetchArgs)[0]).includes("/api/control/snapshot")
      )
    ).toHaveLength(2);
  });

  it("首页允许在同一 Project 内切换 Workspace", async () => {
    const beforeSnapshot = buildSnapshot();
    beforeSnapshot.resources.project_selector.available_workspaces.push(
      buildWorkspace("workspace-analysis", "project-default", "Analysis")
    );

    const afterSnapshot = buildSnapshot();
    afterSnapshot.resources.project_selector.current_workspace_id = "workspace-analysis";
    afterSnapshot.resources.project_selector.available_workspaces =
      beforeSnapshot.resources.project_selector.available_workspaces;

    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        const snapshot =
          fetchMock.mock.calls.filter((call) =>
            String((call as FetchArgs)[0]).includes("/api/control/snapshot")
          ).length === 1
            ? beforeSnapshot
            : afterSnapshot;
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
            result: {
              contract_version: "1.0.0",
              request_id: "req-workspace-select",
              correlation_id: "req-workspace-select",
              action_id: "project.select",
              status: "completed",
              code: "PROJECT_SELECTED",
              message: "已切换当前 project",
              data: {
                project_id: "project-default",
                workspace_id: "workspace-analysis",
              },
              resource_refs: [
                {
                  resource_type: "project_selector",
                  resource_id: "project:selector",
                  schema_version: 1,
                },
              ],
              target_refs: [],
              handled_at: "2026-03-09T10:04:00Z",
            },
          })
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    await screen.findByRole("heading", { name: "已经可以开始" });
    await userEvent.selectOptions(screen.getByLabelText("切换 Workspace"), "workspace-analysis");
    await userEvent.click(screen.getByRole("button", { name: "切换" }));

    const actionCall = fetchMock.mock.calls.find((call) => {
      const [url, init] = call as FetchArgs;
      return String(url).includes("/api/control/actions") && init?.method === "POST";
    }) as FetchArgs | undefined;

    expect(String(actionCall?.[1]?.body)).toContain('"project_id":"project-default"');
    expect(String(actionCall?.[1]?.body)).toContain('"workspace_id":"workspace-analysis"');
    expect(await screen.findByText("workspace-analysis")).toBeInTheDocument();
  });

  it("聊天发送后会回刷 sessions 和 delegation 摘要", async () => {
    window.history.pushState({}, "", "/chat");

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

      addEventListener(): void {}

      removeEventListener(): void {}

      close(): void {
        this.readyState = FakeEventSource.CLOSED;
      }
    }

    vi.stubGlobal("EventSource", FakeEventSource);

    const snapshot = buildSnapshot();
    const nextSessions = {
      ...snapshot.resources.sessions,
      sessions: [buildSession("task-chat-1", "work-chat-1")],
    };
    const nextDelegation = {
      ...snapshot.resources.delegation,
      works: [buildWork("work-chat-1", "running", { title: "Chat Planner Work" })],
      summary: { by_status: { running: 1 } },
    };

    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/chat/send") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            task_id: "task-chat-1",
            stream_url: "/api/stream/task/task-chat-1",
          })
        );
      }
      if (url.includes("/api/tasks/task-chat-1")) {
        return Promise.resolve(jsonResponse(buildTaskDetail("task-chat-1", "Chat Task")));
      }
      if (url.includes("/api/control/resources/sessions")) {
        return Promise.resolve(jsonResponse(nextSessions));
      }
      if (url.includes("/api/control/resources/delegation")) {
        return Promise.resolve(jsonResponse(nextDelegation));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    const input = await screen.findByPlaceholderText("告诉 OctoAgent 你现在要做什么");
    await userEvent.type(input, "帮我整理发布计划");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("Chat Task")).toBeInTheDocument();
    expect(await screen.findByText("Chat Planner Work")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some((call) =>
        String((call as FetchArgs)[0]).includes("/api/control/resources/sessions")
      )
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some((call) =>
        String((call as FetchArgs)[0]).includes("/api/control/resources/delegation")
      )
    ).toBe(true);
  });

  it("Work 看板会覆盖完整状态并在 split 失败时保留草稿", async () => {
    window.history.pushState({}, "", "/work");

    const snapshot = buildSnapshot();
    snapshot.resources.delegation.works = [
      buildWork("work-assigned", "assigned", { title: "Assigned Work" }),
      buildWork("work-escalated", "escalated", { title: "Escalated Work" }),
      buildWork("work-timeout", "timed_out", { title: "Timed Out Work" }),
      buildWork("work-split", "running", {
        title: "Split Work",
        capabilities: [
          {
            capability_id: "work.split",
            label: "拆分 Work",
            action_id: "work.split",
            enabled: true,
            support_status: "supported",
          },
        ],
      }),
    ] as typeof snapshot.resources.delegation.works;

    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse(
            {
              error: {
                code: "WORK_SPLIT_FAILED",
                message: "拆分失败",
              },
            },
            500
          )
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    await screen.findByRole("heading", {
      name: "把 session、work 和 child work 放到一张板上",
    });

    const activeCard = screen.getByText("进行中").closest("article");
    const doneCard = screen.getByText("已结束").closest("article");
    expect(activeCard).not.toBeNull();
    expect(doneCard).not.toBeNull();
    expect(within(activeCard!).getByText("3")).toBeInTheDocument();
    expect(within(doneCard!).getByText("1")).toBeInTheDocument();

    const textarea = await screen.findByLabelText("拆分成子目标");
    await userEvent.type(textarea, "整理依赖\n补测试");
    await userEvent.click(screen.getByRole("button", { name: "创建 child works" }));

    expect(await screen.findByText("拆分失败")).toBeInTheDocument();
    expect(textarea).toHaveValue("整理依赖\n补测试");
  });
});
