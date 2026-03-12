import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import AgentCenter from "./AgentCenter";

const fetchWorkerProfileRevisionsMock = vi.fn();
const useWorkbenchMock = vi.fn();

vi.mock("../api/client", () => ({
  fetchWorkerProfileRevisions: (...args: unknown[]) => fetchWorkerProfileRevisionsMock(...args),
}));

vi.mock("../components/shell/WorkbenchLayout", () => ({
  useWorkbench: () => useWorkbenchMock(),
}));

function buildReviewResult() {
  return {
    mode: "update",
    can_save: true,
    ready: true,
    warnings: [],
    save_errors: [],
    blocking_reasons: [],
    next_actions: ["检查通过，可以保存草稿或直接发布 revision。"],
    profile: {},
    existing_profile: {},
    source_profile: {},
    diff: {
      has_changes: true,
      changed_fields: [
        {
          field: "summary",
          before: "负责家庭基础自动化。",
          after: "负责家庭 NAS 与网络设备巡检。",
        },
      ],
    },
  };
}

function buildSnapshot() {
  return {
    resources: {
      config: {
        current_value: {
          runtime: {
            llm_mode: "litellm",
            litellm_proxy_url: "http://localhost:4000",
          },
          providers: [{ id: "openrouter", enabled: true }],
          model_aliases: {
            main: "openrouter/main",
            reasoning: "openrouter/reasoning",
          },
        },
      },
      project_selector: {
        current_project_id: "project-default",
        current_workspace_id: "workspace-default",
        available_projects: [
          {
            project_id: "project-default",
            name: "Default Project",
          },
        ],
        available_workspaces: [
          {
            workspace_id: "workspace-default",
            project_id: "project-default",
            name: "Primary",
          },
        ],
      },
      setup_governance: {
        generated_at: "2026-03-12T09:00:00Z",
        agent_governance: {
          details: {
            active_agent_profile: {
              profile_id: "owner-profile",
              scope: "project",
              project_id: "project-default",
              name: "OctoAgent",
              persona_summary: "默认 Butler",
              model_alias: "main",
              tool_profile: "standard",
              memory_access_policy: {
                allow_vault: false,
                include_history: true,
              },
              context_budget_policy: {
                memory_recall: {
                  post_filter_mode: "keyword_overlap",
                  rerank_mode: "heuristic",
                  min_keyword_overlap: 1,
                  scope_limit: 4,
                  per_scope_limit: 3,
                  max_hits: 4,
                },
              },
              updated_at: "2026-03-12T09:00:00Z",
            },
          },
        },
        review: {
          ready: true,
          risk_level: "low",
          warnings: [],
          blocking_reasons: [],
          next_actions: [],
          provider_runtime_risks: [],
          channel_exposure_risks: [],
          agent_autonomy_risks: [],
          tool_skill_readiness_risks: [],
          secret_binding_risks: [],
        },
      },
      skill_governance: {
        items: [],
      },
      policy_profiles: {
        profiles: [
          {
            profile_id: "default",
            label: "默认策略",
            description: "常规使用",
            allowed_tool_profile: "standard",
            approval_policy: "safe",
            risk_level: "low",
            recommended_for: "日常任务",
            is_active: true,
          },
        ],
      },
      capability_pack: {
        pack: {
          fallback_toolset: ["project.inspect", "web.search"],
          worker_profiles: [
            {
              worker_type: "general",
              capabilities: ["planner", "handoff"],
              default_model_alias: "main",
              default_tool_profile: "minimal",
              default_tool_groups: ["project", "session"],
              bootstrap_file_ids: [],
              runtime_kinds: ["worker", "subagent"],
              metadata: {},
            },
            {
              worker_type: "ops",
              capabilities: ["runtime", "watchdog"],
              default_model_alias: "reasoning",
              default_tool_profile: "standard",
              default_tool_groups: ["runtime", "project"],
              bootstrap_file_ids: [],
              runtime_kinds: ["worker", "acp_runtime"],
              metadata: {},
            },
          ],
          tools: [
            {
              tool_name: "project.inspect",
              label: "Project Inspect",
              tool_group: "project",
              availability: "available",
            },
            {
              tool_name: "web.search",
              label: "Web Search",
              tool_group: "web",
              availability: "available",
            },
            {
              tool_name: "runtime.inspect",
              label: "Runtime Inspect",
              tool_group: "runtime",
              availability: "available",
            },
          ],
        },
      },
      agent_profiles: {
        profiles: [
          {
            profile_id: "owner-profile",
            scope: "project",
            project_id: "project-default",
            name: "OctoAgent",
            persona_summary: "默认 Butler",
            model_alias: "main",
            tool_profile: "standard",
            memory_access_policy: {
              allow_vault: false,
              include_history: true,
            },
            context_budget_policy: {
              memory_recall: {
                post_filter_mode: "keyword_overlap",
                rerank_mode: "heuristic",
                min_keyword_overlap: 1,
                scope_limit: 4,
                per_scope_limit: 3,
                max_hits: 4,
              },
            },
            updated_at: "2026-03-12T09:00:00Z",
          },
        ],
      },
      worker_profiles: {
        status: "ready",
        generated_at: "2026-03-12T09:05:00Z",
        profiles: [
          {
            profile_id: "project-default:nas-guardian",
            name: "NAS 管家",
            scope: "project",
            project_id: "project-default",
            mode: "singleton",
            origin_kind: "custom",
            status: "active",
            active_revision: 2,
            draft_revision: 2,
            effective_snapshot_id: "worker-profile:project-default:nas-guardian:v2",
            editable: true,
            summary: "负责家庭 NAS 与备份巡检。",
            static_config: {
              base_archetype: "ops",
              summary: "负责家庭 NAS 与备份巡检。",
              model_alias: "reasoning",
              tool_profile: "standard",
              default_tool_groups: ["runtime", "project"],
              selected_tools: ["runtime.inspect"],
              runtime_kinds: ["worker", "acp_runtime"],
              policy_refs: ["default"],
              instruction_overlays: ["先解释风险，再建议操作。"],
              tags: ["nas", "ops"],
              capabilities: ["runtime", "watchdog"],
            },
            dynamic_context: {
              active_project_id: "project-default",
              active_workspace_id: "workspace-default",
              active_work_count: 1,
              running_work_count: 1,
              attention_work_count: 0,
              latest_work_id: "work-nas-1",
              latest_task_id: "task-work-nas-1",
              latest_work_title: "检查 NAS 健康状态",
              latest_work_status: "running",
              latest_target_kind: "worker",
              current_selected_tools: ["runtime.inspect"],
              updated_at: "2026-03-12T09:10:00Z",
            },
            warnings: [],
            capabilities: [
              {
                capability_id: "worker.spawn_from_profile",
                label: "启动 Root Agent",
                action_id: "worker.spawn_from_profile",
                enabled: true,
                support_status: "supported",
                reason: "",
              },
            ],
          },
          {
            profile_id: "singleton:general",
            name: "Butler",
            scope: "system",
            project_id: "",
            mode: "singleton",
            origin_kind: "builtin",
            status: "active",
            active_revision: 1,
            draft_revision: 1,
            effective_snapshot_id: "worker-profile:singleton:general:v1",
            editable: false,
            summary: "系统默认 Starter Template。",
            static_config: {
              base_archetype: "general",
              summary: "系统默认 Starter Template。",
              model_alias: "main",
              tool_profile: "minimal",
              default_tool_groups: ["project", "session"],
              selected_tools: [],
              runtime_kinds: ["worker", "subagent"],
              policy_refs: [],
              instruction_overlays: [],
              tags: ["planner"],
              capabilities: ["planner", "handoff"],
            },
            dynamic_context: {
              active_project_id: "project-default",
              active_workspace_id: "workspace-default",
              active_work_count: 0,
              running_work_count: 0,
              attention_work_count: 0,
              latest_work_id: "",
              latest_task_id: "",
              latest_work_title: "",
              latest_work_status: "idle",
              latest_target_kind: "",
              current_selected_tools: [],
              updated_at: "2026-03-12T09:00:00Z",
            },
            warnings: ["当前还没有运行中的 work。"],
            capabilities: [],
          },
        ],
        summary: {},
      },
      delegation: {
        works: [
          {
            work_id: "work-nas-1",
            task_id: "task-work-nas-1",
            parent_work_id: "",
            title: "检查 NAS 健康状态",
            status: "running",
            target_kind: "worker",
            selected_worker_type: "ops",
            route_reason: "按 Root Agent NAS 管家派发",
            owner_id: "owner",
            selected_tools: ["runtime.inspect"],
            pipeline_run_id: "",
            runtime_id: "runtime.ops",
            project_id: "project-default",
            workspace_id: "workspace-default",
            requested_worker_profile_id: "project-default:nas-guardian",
            requested_worker_profile_version: 2,
            effective_worker_snapshot_id: "worker-profile:project-default:nas-guardian:v2",
            child_work_ids: [],
            child_work_count: 0,
            merge_ready: false,
            runtime_summary: {
              requested_tool_profile: "standard",
              requested_model_alias: "reasoning",
            },
            updated_at: "2026-03-12T09:12:00Z",
            capabilities: [],
          },
        ],
      },
    },
  };
}

describe("AgentCenter", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("worker_profiles 刷新时保留未保存的 Root Agent 草稿", async () => {
    let snapshot = buildSnapshot();
    const submitAction = vi.fn();
    useWorkbenchMock.mockImplementation(() => ({
      snapshot,
      submitAction,
      busyActionId: "",
    }));
    fetchWorkerProfileRevisionsMock.mockResolvedValue({ revisions: [] });

    const view = render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    expect(await screen.findByText("Root Agent Profiles")).toBeInTheDocument();
    const summaryField = screen.getByLabelText("Summary") as HTMLTextAreaElement;
    await userEvent.clear(summaryField);
    await userEvent.type(summaryField, "新的未保存 Root Agent 摘要");

    snapshot = buildSnapshot();
    snapshot.resources.worker_profiles.generated_at = "2026-03-12T09:06:00Z";

    view.rerender(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect((screen.getByLabelText("Summary") as HTMLTextAreaElement).value).toBe(
        "新的未保存 Root Agent 摘要"
      );
    });
  });

  it("支持在 Profile Studio 中检查、发布并按 Root Agent 启动任务", async () => {
    const submitAction = vi.fn(async (actionId: string, payload: Record<string, unknown>) => {
      if (actionId === "worker_profile.review") {
        return { data: { review: buildReviewResult() } };
      }
      if (actionId === "worker_profile.apply") {
        return {
          data: {
            profile_id: "project-default:nas-guardian",
            review: buildReviewResult(),
          },
        };
      }
      if (actionId === "worker.spawn_from_profile") {
        return { data: { work_id: "work-nas-2", objective: payload.objective } };
      }
      return { data: {} };
    });

    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      submitAction,
      busyActionId: "",
    });
    fetchWorkerProfileRevisionsMock.mockResolvedValue({
      revisions: [
        {
          revision_id: "worker-profile:project-default:nas-guardian:v2",
          profile_id: "project-default:nas-guardian",
          revision: 2,
          change_summary: "补充 NAS 巡检策略",
          created_by: "owner",
          created_at: "2026-03-12T09:15:00Z",
          snapshot_payload: {
            profile_id: "project-default:nas-guardian",
          },
        },
      ],
    });

    render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    expect(await screen.findByText("Root Agent Profiles")).toBeInTheDocument();
    expect((await screen.findAllByText("NAS 管家")).length).toBeGreaterThan(0);
    expect(screen.getByText("Runtime Lineage")).toBeInTheDocument();

    await waitFor(() => {
      expect(fetchWorkerProfileRevisionsMock).toHaveBeenCalledWith(
        "project-default:nas-guardian"
      );
    });

    expect(
      (await screen.findAllByText("worker-profile:project-default:nas-guardian:v2")).length
    ).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole("button", { name: "检查草稿" }));
    await waitFor(() => {
      expect(submitAction).toHaveBeenCalledWith(
        "worker_profile.review",
        expect.objectContaining({
          draft: expect.objectContaining({
            profile_id: "project-default:nas-guardian",
            name: "NAS 管家",
          }),
        })
      );
    });
    expect(await screen.findByText("当前草稿可以保存或发布")).toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText("Objective"));
    await userEvent.type(
      screen.getByLabelText("Objective"),
      "检查今晚的家庭备份是否异常，并给出处理建议。"
    );
    await userEvent.click(screen.getByRole("button", { name: "从这个 Root Agent 启动" }));
    await waitFor(() => {
      expect(submitAction).toHaveBeenCalledWith("worker.spawn_from_profile", {
        profile_id: "project-default:nas-guardian",
        objective: "检查今晚的家庭备份是否异常，并给出处理建议。",
      });
    });

    await userEvent.click(screen.getByRole("button", { name: "发布 Revision" }));
    await waitFor(() => {
      expect(submitAction).toHaveBeenCalledWith(
        "worker_profile.apply",
        expect.objectContaining({
          publish: true,
          change_summary: "通过 AgentCenter 发布",
        })
      );
    });
  });

  it("当前选中的 Root Agent revision 变化后会自动重拉历史", async () => {
    let snapshot = buildSnapshot();
    const submitAction = vi.fn();
    useWorkbenchMock.mockImplementation(() => ({
      snapshot,
      submitAction,
      busyActionId: "",
    }));
    fetchWorkerProfileRevisionsMock.mockResolvedValue({ revisions: [] });

    const view = render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    expect(await screen.findByText("Root Agent Profiles")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchWorkerProfileRevisionsMock).toHaveBeenCalledWith(
        "project-default:nas-guardian"
      );
    });

    fetchWorkerProfileRevisionsMock.mockClear();
    snapshot = buildSnapshot();
    snapshot.resources.worker_profiles.generated_at = "2026-03-12T09:18:00Z";
    snapshot.resources.worker_profiles.profiles[0].active_revision = 3;
    snapshot.resources.worker_profiles.profiles[0].draft_revision = 3;
    snapshot.resources.worker_profiles.profiles[0].effective_snapshot_id =
      "worker-profile:project-default:nas-guardian:v3";

    view.rerender(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(fetchWorkerProfileRevisionsMock).toHaveBeenCalledWith(
        "project-default:nas-guardian"
      );
    });
  });
});
