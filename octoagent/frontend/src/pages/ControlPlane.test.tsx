import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ControlPlane from "./ControlPlane";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function buildSnapshot(currentProjectId = "project-default") {
  return {
    contract_version: "1.0.0",
    generated_at: "2026-03-08T09:00:00Z",
    registry: {
      contract_version: "1.0.0",
      resource_type: "action_registry",
      resource_id: "actions:registry",
      schema_version: 1,
      generated_at: "2026-03-08T09:00:00Z",
      updated_at: "2026-03-08T09:00:00Z",
      status: "ready",
      degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
      warnings: [],
      capabilities: [],
      refs: {},
      actions: [
        {
          action_id: "project.select",
          label: "切换项目",
          description: "",
          category: "projects",
          supported_surfaces: ["web", "telegram", "system"],
          surface_aliases: { web: ["project.select"], telegram: ["/project select"] },
          support_status_by_surface: { web: "supported", telegram: "supported" },
          params_schema: {},
          result_schema: {},
          risk_hint: "low",
          approval_hint: "none",
          idempotency_hint: "request_id",
          resource_targets: ["project_selector"],
        },
        {
          action_id: "operator.approval.resolve",
          label: "处理审批",
          description: "",
          category: "operator",
          supported_surfaces: ["web", "telegram", "system"],
          surface_aliases: { web: ["operator.approval.resolve"], telegram: ["/approve"] },
          support_status_by_surface: { web: "supported", telegram: "supported" },
          params_schema: {},
          result_schema: {},
          risk_hint: "medium",
          approval_hint: "none",
          idempotency_hint: "request_id",
          resource_targets: ["session_projection"],
        },
      ],
    },
    resources: {
      wizard: {
        contract_version: "1.0.0",
        resource_type: "wizard_session",
        resource_id: "wizard:default",
        schema_version: 1,
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        session_version: 1,
        current_step: "doctor_live",
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
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        schema: {},
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
            litellm_proxy_url: "http://localhost:4000",
          },
        },
        validation_rules: ["Provider ID 必须唯一"],
        bridge_refs: [],
        secret_refs_only: true,
      },
      project_selector: {
        contract_version: "1.0.0",
        resource_type: "project_selector",
        resource_id: "project:selector",
        schema_version: 1,
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        current_project_id: currentProjectId,
        current_workspace_id:
          currentProjectId === "project-ops" ? "workspace-ops" : "workspace-default",
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
          {
            project_id: "project-ops",
            slug: "ops",
            name: "Ops Project",
            is_default: false,
            status: "active",
            workspace_ids: ["workspace-ops"],
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
          {
            workspace_id: "workspace-ops",
            project_id: "project-ops",
            slug: "ops",
            name: "Ops Primary",
            kind: "primary",
            root_path: "/tmp/ops",
          },
        ],
      },
      sessions: {
        contract_version: "1.0.0",
        resource_type: "session_projection",
        resource_id: "sessions:overview",
        schema_version: 1,
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        focused_thread_id: "",
        sessions: [
          {
            session_id: "thread-1",
            thread_id: "thread-1",
            task_id: "task-1",
            title: "网关升级失败",
            status: "FAILED",
            channel: "telegram",
            requester_id: "owner",
            project_id: currentProjectId,
            workspace_id:
              currentProjectId === "project-ops"
                ? "workspace-ops"
                : "workspace-default",
            latest_message_summary: "请检查 update plan",
            latest_event_at: "2026-03-08T09:10:00Z",
            execution_summary: {},
            capabilities: [],
            detail_refs: { task: "/tasks/task-1" },
          },
        ],
        operator_summary: {
          total_pending: 1,
          approvals: 1,
          alerts: 0,
          retryable_failures: 0,
          pairing_requests: 0,
          degraded_sources: [],
          generated_at: "2026-03-08T09:10:00Z",
        },
        operator_items: [
          {
            item_id: "approval:approval-1",
            kind: "approval",
            state: "pending",
            title: "允许执行 runtime verify",
            summary: "需要 owner 批准后继续",
            task_id: "task-1",
            thread_id: "thread-1",
            source_ref: "/tasks/task-1",
            created_at: "2026-03-08T09:10:00Z",
            expires_at: null,
            pending_age_seconds: 12,
            suggested_actions: ["approve_once", "deny"],
            quick_actions: [
              {
                kind: "approve_once",
                label: "批准一次",
                style: "primary",
                enabled: true,
              },
              {
                kind: "deny",
                label: "拒绝",
                style: "danger",
                enabled: true,
              },
            ],
            recent_action_result: null,
            metadata: {
              risk: "medium",
            },
          },
        ],
      },
      automation: {
        contract_version: "1.0.0",
        resource_type: "automation_job",
        resource_id: "automation:jobs",
        schema_version: 1,
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
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
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        overall_status: "ready",
        subsystems: [
          {
            subsystem_id: "runtime",
            label: "Runtime",
            status: "ok",
            summary: "TaskRunner / Execution runtime",
            detail_ref: "/health",
            warnings: [],
          },
        ],
        recent_failures: [],
        runtime_snapshot: {},
        recovery_summary: {},
        update_summary: {},
        channel_summary: {
          telegram: {
            enabled: true,
            mode: "webhook",
            dm_policy: "open",
            group_policy: "open",
            pending_pairings: 0,
            approved_users: 2,
            allowed_groups: ["chat-1"],
          },
        },
        deep_refs: {},
      },
    },
  };
}

function buildEvents() {
  return {
    contract_version: "1.0.0",
    events: [
      {
        event_id: "evt-1",
        contract_version: "1.0.0",
        event_type: "control.action.completed",
        request_id: "req-1",
        correlation_id: "req-1",
        causation_id: "req-1",
        actor: { actor_id: "user:web", actor_label: "Owner" },
        surface: "web",
        occurred_at: "2026-03-08T09:15:00Z",
        payload_summary: "project selected",
        resource_ref: null,
        resource_refs: [],
        target_refs: [],
        metadata: {},
      },
    ],
  };
}

describe("ControlPlane", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("使用 snapshot 渲染正式控制台首页与主导航", async () => {
    const snapshot = buildSnapshot();
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/events")) {
        return Promise.resolve(jsonResponse(buildEvents()));
      }
      return Promise.resolve(jsonResponse({}));
    });

    render(
      <MemoryRouter>
        <ControlPlane />
      </MemoryRouter>
    );

    expect(
      await screen.findByRole("heading", { name: "OctoAgent Control Plane" })
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Dashboard/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Projects/i })).toBeInTheDocument();
    expect(screen.getByText("project-default")).toBeInTheDocument();
    expect(screen.getByText("网关升级失败")).toBeInTheDocument();
    expect(screen.getByText("TaskRunner / Execution runtime")).toBeInTheDocument();
  });

  it("project.select 会提交统一 action 并按 resource_refs 回刷 project selector", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const beforeSnapshot = buildSnapshot("project-default");
    const afterSelector = buildSnapshot("project-ops").resources.project_selector;

    fetchMock.mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(beforeSnapshot));
      }
      if (url.includes("/api/control/events")) {
        return Promise.resolve(jsonResponse(buildEvents()));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
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
              handled_at: "2026-03-08T09:20:00Z",
              audit_event_id: "evt-project-select",
            },
          })
        );
      }
      if (url.includes("/api/control/resources/project-selector")) {
        return Promise.resolve(jsonResponse(afterSelector));
      }
      return Promise.resolve(jsonResponse({}));
    });

    render(
      <MemoryRouter>
        <ControlPlane />
      </MemoryRouter>
    );

    expect(await screen.findByText("project-default")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Projects/i }));
    expect(await screen.findByText("Ops Project")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "切换到 Ops Primary" })
    );

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(
        "已切换当前 project [PROJECT_SELECTED]"
      );
    });
    await waitFor(() => {
      expect(screen.getAllByText("project-ops").length).toBeGreaterThan(0);
    });

    const actionRequest = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).includes("/api/control/actions") &&
        init?.method === "POST"
    );
    expect(actionRequest).toBeTruthy();
    expect(String(actionRequest?.[1]?.body)).toContain('"action_id":"project.select"');
    expect(String(actionRequest?.[1]?.body)).toContain('"project_id":"project-ops"');

    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("/api/control/resources/project-selector")
      )
    ).toBe(true);
  });

  it("Operator 快捷动作会走统一 action 语义并回刷 sessions 资源", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const snapshot = buildSnapshot();
    const nextSessions = {
      ...snapshot.resources.sessions,
      operator_summary: {
        ...snapshot.resources.sessions.operator_summary,
        total_pending: 0,
        approvals: 0,
      },
      operator_items: [],
    };

    fetchMock.mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/events")) {
        return Promise.resolve(jsonResponse(buildEvents()));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            result: {
              contract_version: "1.0.0",
              request_id: "req-approval",
              correlation_id: "req-approval",
              action_id: "operator.approval.resolve",
              status: "completed",
              code: "APPROVAL_RESOLVED",
              message: "审批已处理",
              data: {},
              resource_refs: [
                {
                  resource_type: "session_projection",
                  resource_id: "sessions:overview",
                  schema_version: 1,
                },
              ],
              target_refs: [],
              handled_at: "2026-03-08T09:30:00Z",
              audit_event_id: "evt-approval",
            },
          })
        );
      }
      if (url.includes("/api/control/resources/sessions")) {
        return Promise.resolve(jsonResponse(nextSessions));
      }
      return Promise.resolve(jsonResponse({}));
    });

    render(
      <MemoryRouter>
        <ControlPlane />
      </MemoryRouter>
    );

    await screen.findByText("project-default");
    await userEvent.click(screen.getByRole("button", { name: /Operator/i }));
    await userEvent.click(screen.getByRole("button", { name: "批准一次" }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(
        "审批已处理 [APPROVAL_RESOLVED]"
      );
    });
    await waitFor(() => {
      expect(screen.getByText(/Approvals 0/)).toBeInTheDocument();
    });

    const actionRequest = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).includes("/api/control/actions") &&
        init?.method === "POST"
    );
    expect(String(actionRequest?.[1]?.body)).toContain(
      '"action_id":"operator.approval.resolve"'
    );
    expect(String(actionRequest?.[1]?.body)).toContain('"approval_id":"approval-1"');
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("/api/control/resources/sessions")
      )
    ).toBe(true);
  });
});
