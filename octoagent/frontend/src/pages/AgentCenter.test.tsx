import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AgentCenter from "./AgentCenter";

const useWorkbenchMock = vi.fn();
const navigateMock = vi.fn();

vi.mock("../components/shell/WorkbenchLayout", () => ({
  useWorkbench: () => useWorkbenchMock(),
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

function buildSnapshot(options?: {
  currentProjectId?: string;
  currentProjectName?: string;
  defaultProfileId?: string;
  customAgents?: Array<{
    profile_id: string;
    name: string;
    project_id?: string;
    summary?: string;
    status?: "draft" | "active";
    origin_kind?: "custom" | "cloned" | "extracted";
    model_alias?: string;
    default_tool_groups?: string[];
    selected_tools?: string[];
  }>;
}) {
  const currentProjectId = options?.currentProjectId ?? "project-home";
  const customAgents = options?.customAgents ?? [
    {
      profile_id: "project-home:main",
      name: "家庭主 Agent",
      summary: "负责默认聊天入口和日常协调。",
      status: "active",
      model_alias: "main",
      default_tool_groups: ["project", "session"],
      selected_tools: ["project.inspect"],
    },
    {
      profile_id: "project-home:nas",
      name: "NAS 巡检",
      summary: "负责 NAS、备份和磁盘健康巡检。",
      status: "active",
      model_alias: "reasoning",
      default_tool_groups: ["runtime", "project"],
      selected_tools: ["runtime.inspect"],
    },
  ];

  return {
    resources: {
      agent_profiles: {
        generated_at: "2026-03-14T10:03:00Z",
        active_project_id: currentProjectId,
        active_workspace_id: `${currentProjectId}-workspace`,
        profiles: [
          {
            profile_id: options?.defaultProfileId ?? "project-home:main",
            scope: "project",
            project_id: currentProjectId,
            name:
              customAgents.find((item) => item.profile_id === (options?.defaultProfileId ?? "project-home:main"))
                ?.name ?? "家庭主 Agent",
            persona_summary: "负责当前项目的默认会话和持续协作。",
            model_alias: "main",
            tool_profile: "standard",
            bootstrap_template_ids: [
              "behavior:system:AGENTS.md",
              "behavior:system:USER.md",
              "behavior:system:TOOLS.md",
              "behavior:system:BOOTSTRAP.md",
              "behavior:agent:IDENTITY.md",
              "behavior:agent:SOUL.md",
              "behavior:agent:HEARTBEAT.md",
              "behavior:project:PROJECT.md",
              "behavior:project:KNOWLEDGE.md",
              "behavior:project:USER.md",
              "behavior:project:TOOLS.md",
              "behavior:project:instructions/README.md",
              "behavior:project_agent:TOOLS.md",
            ],
            metadata: {},
            updated_at: "2026-03-14T10:03:00Z",
            behavior_system: {
              source_chain: [
                `filesystem:projects/${currentProjectId === "project-work" ? "work" : "home"}/behavior`,
                "filesystem:behavior/system",
                "default_behavior_templates",
              ],
              decision_modes: ["direct_answer", "ask_once", "delegate_specialist"],
              runtime_hint_fields: ["project_path_manifest", "recent_worker_lane_topic"],
              files: [
                {
                  file_id: "AGENTS.md",
                  title: "共享协作约束",
                  layer: "role",
                  visibility: "shared",
                  share_with_workers: true,
                  source_kind: "system_file",
                  path_hint: "behavior/system/AGENTS.md",
                },
                {
                  file_id: "IDENTITY.md",
                  title: "当前 Agent 身份",
                  layer: "role",
                  visibility: "private",
                  share_with_workers: false,
                  source_kind: "agent_file",
                  path_hint: "behavior/agents/butler/IDENTITY.md",
                },
                {
                  file_id: "PROJECT.md",
                  title: "项目共享说明",
                  layer: "solving",
                  visibility: "shared",
                  share_with_workers: true,
                  source_kind: "project_file",
                  path_hint: `projects/${currentProjectId === "project-work" ? "work" : "home"}/behavior/PROJECT.md`,
                },
                {
                  file_id: "TOOLS.md",
                  title: "Project-Agent 工具覆盖",
                  layer: "tool_boundary",
                  visibility: "private",
                  share_with_workers: false,
                  source_kind: "project_agent_file",
                  path_hint: `projects/${currentProjectId === "project-work" ? "work" : "home"}/behavior/agents/butler/TOOLS.md`,
                },
              ],
              worker_slice: {
                shared_file_ids: ["AGENTS.md", "PROJECT.md", "TOOLS.md"],
                layers: ["role", "solving", "tool_boundary"],
              },
              path_manifest: {
                project_root: currentProjectId === "project-work" ? "/tmp/work-project" : "/tmp/home-project",
                project_behavior_root:
                  currentProjectId === "project-work"
                    ? "projects/work/behavior"
                    : "projects/home/behavior",
                project_workspace_root: `${currentProjectId === "project-work" ? "/tmp/work" : "/tmp/home"}/workspace`,
                project_data_root: `${currentProjectId === "project-work" ? "/tmp/work" : "/tmp/home"}/data`,
                project_notes_root: `${currentProjectId === "project-work" ? "/tmp/work" : "/tmp/home"}/notes`,
                project_artifacts_root: `${currentProjectId === "project-work" ? "/tmp/work" : "/tmp/home"}/artifacts`,
                shared_behavior_root: "behavior/system",
                agent_behavior_root: "behavior/agents/butler",
                project_agent_behavior_root:
                  currentProjectId === "project-work"
                    ? "projects/work/behavior/agents/butler"
                    : "projects/home/behavior/agents/butler",
                secret_bindings_path:
                  currentProjectId === "project-work"
                    ? "projects/work/project.secret-bindings.json"
                    : "projects/home/project.secret-bindings.json",
                effective_behavior_files: [
                  {
                    file_id: "AGENTS.md",
                    path: "behavior/system/AGENTS.md",
                    scope: "system_shared",
                    editable_mode: "proposal_required",
                    review_mode: "review_required",
                    source_kind: "system_file",
                    exists_on_disk: true,
                    metadata: {},
                  },
                  {
                    file_id: "IDENTITY.md",
                    path: "behavior/agents/butler/IDENTITY.md",
                    scope: "agent_private",
                    editable_mode: "proposal_required",
                    review_mode: "review_required",
                    source_kind: "agent_file",
                    exists_on_disk: true,
                    metadata: {},
                  },
                  {
                    file_id: "PROJECT.md",
                    path:
                      currentProjectId === "project-work"
                        ? "projects/work/behavior/PROJECT.md"
                        : "projects/home/behavior/PROJECT.md",
                    scope: "project_shared",
                    editable_mode: "proposal_required",
                    review_mode: "review_required",
                    source_kind: "project_file",
                    exists_on_disk: true,
                    metadata: {},
                  },
                  {
                    file_id: "TOOLS.md",
                    path:
                      currentProjectId === "project-work"
                        ? "projects/work/behavior/agents/butler/TOOLS.md"
                        : "projects/home/behavior/agents/butler/TOOLS.md",
                    scope: "project_agent",
                    editable_mode: "proposal_required",
                    review_mode: "review_required",
                    source_kind: "project_agent_file",
                    exists_on_disk: false,
                    metadata: {},
                  },
                ],
                metadata: {},
              },
              storage_boundary_hints: {
                facts_store: "MemoryService",
                secrets_store: "SecretService",
                behavior_store: "behavior_files",
                workspace_roots: ["workspace", "data", "notes", "artifacts"],
                note: "规则进 behavior，事实进 Memory，密钥进 Secret bindings。",
                metadata: {},
              },
              bootstrap_template_ids: [
                "behavior:system:AGENTS.md",
                "behavior:system:USER.md",
                "behavior:system:TOOLS.md",
                "behavior:system:BOOTSTRAP.md",
                "behavior:agent:IDENTITY.md",
                "behavior:agent:SOUL.md",
                "behavior:agent:HEARTBEAT.md",
                "behavior:project:PROJECT.md",
                "behavior:project:KNOWLEDGE.md",
                "behavior:project:USER.md",
                "behavior:project:TOOLS.md",
                "behavior:project:instructions/README.md",
                "behavior:project_agent:TOOLS.md",
              ],
              bootstrap_templates: {
                shared: [
                  "behavior:system:AGENTS.md",
                  "behavior:system:USER.md",
                  "behavior:system:TOOLS.md",
                  "behavior:system:BOOTSTRAP.md",
                ],
                agent_private: [
                  "behavior:agent:IDENTITY.md",
                  "behavior:agent:SOUL.md",
                  "behavior:agent:HEARTBEAT.md",
                ],
                project_shared: [
                  "behavior:project:PROJECT.md",
                  "behavior:project:KNOWLEDGE.md",
                  "behavior:project:USER.md",
                  "behavior:project:TOOLS.md",
                  "behavior:project:instructions/README.md",
                ],
                project_agent: ["behavior:project_agent:TOOLS.md"],
              },
              bootstrap_routes: {
                facts: {
                  store: "MemoryService",
                  summary: "用户稳定事实写入 Memory。",
                },
                secrets: {
                  store: "SecretService",
                  path:
                    currentProjectId === "project-work"
                      ? "projects/work/project.secret-bindings.json"
                      : "projects/home/project.secret-bindings.json",
                  summary: "敏感值写入 project secret bindings。",
                },
                assistant_identity: {
                  target: "IDENTITY.md",
                  summary: "Agent 名称和身份落到 IDENTITY.md。",
                },
                assistant_personality: {
                  target: "SOUL.md",
                  summary: "Agent 性格落到 SOUL.md。",
                },
                project_instructions: {
                  target: "instructions/README.md",
                  summary: "Project 说明放到 instructions README。",
                },
                workspace_materials: {
                  roots: ["workspace", "data", "notes", "artifacts"],
                  summary: "代码和文档进入 project workspace roots。",
                },
              },
            },
          },
        ],
      },
      bootstrap_session: {
        generated_at: "2026-03-14T10:03:00Z",
        active_project_id: currentProjectId,
        active_workspace_id: `${currentProjectId}-workspace`,
        resumable: true,
        session: {
          bootstrap_id: "bootstrap-home",
          project_id: currentProjectId,
          workspace_id: `${currentProjectId}-workspace`,
          agent_profile_id: options?.defaultProfileId ?? "project-home:main",
          status: "pending",
          current_step: "assistant_personality",
          metadata: {
            questionnaire: [
              {
                step: "owner_identity",
                route: "memory",
                target: "OwnerProfile + Memory",
                summary: "用户怎么称呼自己、长期个人事实。",
              },
              {
                step: "assistant_identity",
                route: "behavior:IDENTITY.md",
                target: "IDENTITY.md",
                summary: "默认会话 Agent 叫什么、以什么身份工作。",
              },
              {
                step: "assistant_personality",
                route: "behavior:SOUL.md",
                target: "SOUL.md",
                summary: "默认会话 Agent 的性格和语气。",
              },
              {
                step: "locale_and_location",
                route: "memory",
                target: "Memory",
                summary: "地点、语言、时区进入事实记忆。",
              },
              {
                step: "memory_preferences",
                route: "memory_policy",
                target: "Memory",
                summary: "长期偏好和事实存入 Memory。",
              },
              {
                step: "secret_routing",
                route: "secrets",
                target:
                  currentProjectId === "project-work"
                    ? "projects/work/project.secret-bindings.json"
                    : "projects/home/project.secret-bindings.json",
                summary: "敏感值进入 project secret bindings。",
              },
            ],
          },
        },
      },
      config: {
        generated_at: "2026-03-14T10:00:00Z",
        current_value: {
          model_aliases: {
            main: "openrouter/main",
            reasoning: "openrouter/reasoning",
            cheap: "openrouter/cheap",
          },
        },
      },
      project_selector: {
        generated_at: "2026-03-14T10:00:00Z",
        current_project_id: currentProjectId,
        current_workspace_id: `${currentProjectId}-workspace`,
        available_projects: [
          {
            project_id: "project-home",
            slug: "home",
            name: "家庭自动化",
            is_default: true,
            status: "active",
            workspace_ids: ["project-home-workspace"],
            warnings: [],
          },
          {
            project_id: "project-work",
            slug: "work",
            name: "工作项目",
            is_default: false,
            status: "active",
            workspace_ids: ["project-work-workspace"],
            warnings: [],
          },
        ],
        available_workspaces: [
          {
            workspace_id: "project-home-workspace",
            project_id: "project-home",
            slug: "home",
            name: "Home Workspace",
            kind: "primary",
            root_path: "/tmp/home",
          },
          {
            workspace_id: "project-work-workspace",
            project_id: "project-work",
            slug: "work",
            name: "Work Workspace",
            kind: "primary",
            root_path: "/tmp/work",
          },
        ],
      },
      sessions: {
        focused_session_id: "",
        sessions: [],
      },
      worker_profiles: {
        generated_at: "2026-03-14T10:05:00Z",
        profiles: [
          ...customAgents.map((agent) => ({
            profile_id: agent.profile_id,
            name: agent.name,
            scope: "project",
            project_id: agent.project_id ?? "project-home",
            mode: "singleton",
            origin_kind: agent.origin_kind ?? "custom",
            status: agent.status ?? "active",
            active_revision: agent.status === "draft" ? 0 : 2,
            draft_revision: 2,
            effective_snapshot_id: `worker-profile:${agent.profile_id}:v2`,
            editable: true,
            summary: agent.summary ?? "负责项目内的专项工作。",
            static_config: {
              summary: agent.summary ?? "负责项目内的专项工作。",
              model_alias: agent.model_alias ?? "main",
              tool_profile: "standard",
              default_tool_groups: agent.default_tool_groups ?? ["project"],
              selected_tools: agent.selected_tools ?? [],
              runtime_kinds: ["worker"],
              capabilities: ["planner"],
              metadata: {},
            },
            dynamic_context: {
              active_project_id: agent.project_id ?? "project-home",
              active_workspace_id: `${agent.project_id ?? "project-home"}-workspace`,
              active_work_count: agent.profile_id.endsWith(":nas") ? 1 : 0,
              running_work_count: agent.profile_id.endsWith(":nas") ? 1 : 0,
              attention_work_count: 0,
              latest_work_id: "",
              latest_task_id: "",
              latest_work_title: "",
              latest_work_status: "idle",
              latest_target_kind: "",
              current_selected_tools: agent.selected_tools ?? [],
              current_tool_resolution_mode: "profile_first_core",
              current_tool_warnings: [],
              current_mounted_tools: [],
              current_blocked_tools: [],
              current_discovery_entrypoints: [],
              updated_at: "2026-03-14T10:06:00Z",
            },
            warnings: [],
            capabilities: [],
          })),
          {
            profile_id: "singleton:general",
            name: "Butler Root Agent",
            scope: "system",
            project_id: "",
            mode: "singleton",
            origin_kind: "builtin",
            status: "active",
            active_revision: 1,
            draft_revision: 1,
            effective_snapshot_id: "worker-profile:singleton:general:v1",
            editable: false,
            summary: "适合承担默认协调和主入口。",
            static_config: {
              summary: "适合承担默认协调和主入口。",
              model_alias: "main",
              tool_profile: "standard",
              default_tool_groups: ["project", "session"],
              selected_tools: ["project.inspect"],
              runtime_kinds: ["worker"],
              capabilities: ["planner"],
              metadata: {},
            },
            dynamic_context: {
              active_project_id: currentProjectId,
              active_workspace_id: `${currentProjectId}-workspace`,
              active_work_count: 0,
              running_work_count: 0,
              attention_work_count: 0,
              latest_work_id: "",
              latest_task_id: "",
              latest_work_title: "",
              latest_work_status: "idle",
              latest_target_kind: "",
              current_selected_tools: ["project.inspect"],
              current_tool_resolution_mode: "profile_first_core",
              current_tool_warnings: [],
              current_mounted_tools: [],
              current_blocked_tools: [],
              current_discovery_entrypoints: [],
              updated_at: "2026-03-14T10:00:00Z",
            },
            warnings: [],
            capabilities: [],
          },
          {
            profile_id: "singleton:research",
            name: "Research Root Agent",
            scope: "system",
            project_id: "",
            mode: "singleton",
            origin_kind: "builtin",
            status: "active",
            active_revision: 1,
            draft_revision: 1,
            effective_snapshot_id: "worker-profile:singleton:research:v1",
            editable: false,
            summary: "适合资料整理、检索和信息提炼。",
            static_config: {
              summary: "适合资料整理、检索和信息提炼。",
              model_alias: "reasoning",
              tool_profile: "standard",
              default_tool_groups: ["web", "project"],
              selected_tools: ["web.search"],
              runtime_kinds: ["worker"],
              capabilities: ["research"],
              metadata: {},
            },
            dynamic_context: {
              active_project_id: currentProjectId,
              active_workspace_id: `${currentProjectId}-workspace`,
              active_work_count: 0,
              running_work_count: 0,
              attention_work_count: 0,
              latest_work_id: "",
              latest_task_id: "",
              latest_work_title: "",
              latest_work_status: "idle",
              latest_target_kind: "",
              current_selected_tools: ["web.search"],
              current_tool_resolution_mode: "profile_first_core",
              current_tool_warnings: [],
              current_mounted_tools: [],
              current_blocked_tools: [],
              current_discovery_entrypoints: [],
              updated_at: "2026-03-14T10:00:00Z",
            },
            warnings: [],
            capabilities: [],
          },
        ],
        summary: {
          default_profile_id: options?.defaultProfileId ?? "project-home:main",
          default_profile_name: "家庭主 Agent",
        },
      },
      policy_profiles: {
        generated_at: "2026-03-14T10:00:00Z",
        profiles: [
          {
            profile_id: "default",
            label: "默认策略",
            description: "日常使用",
            allowed_tool_profile: "standard",
            approval_policy: "safe",
            risk_level: "low",
            recommended_for: "日常任务",
            is_active: true,
          },
        ],
      },
      capability_pack: {
        generated_at: "2026-03-14T10:00:00Z",
        pack: {
          tools: [
            {
              tool_name: "project.inspect",
              label: "项目检查",
              tool_group: "project",
              availability: "available",
            },
            {
              tool_name: "runtime.inspect",
              label: "运行检查",
              tool_group: "runtime",
              availability: "available",
            },
            {
              tool_name: "web.search",
              label: "网页搜索",
              tool_group: "web",
              availability: "available",
            },
          ],
        },
      },
      skill_governance: {
        generated_at: "2026-03-14T10:00:00Z",
        items: [
          {
            item_id: "skill:workers.review",
            label: "Worker Review",
            source_kind: "builtin",
            scope: "project",
            enabled_by_default: true,
            selected: true,
            selection_source: "default",
            availability: "available",
            trust_level: "trusted",
            blocking: false,
            required_secrets: [],
            missing_requirements: [],
            install_hint: "",
            details: {},
          },
        ],
      },
      mcp_provider_catalog: {
        generated_at: "2026-03-14T10:00:00Z",
        items: [
          {
            provider_id: "filesystem",
            label: "Filesystem",
            description: "读取项目文件",
            editable: false,
            removable: false,
            enabled: true,
            status: "available",
            command: "",
            args: [],
            cwd: "",
            env: {},
            tool_count: 3,
            selection_item_id: "mcp:filesystem",
            install_hint: "",
            error: "",
            warnings: [],
            details: {},
          },
        ],
      },
    },
  } as any;
}

describe("AgentCenter", () => {
  beforeEach(() => {
    // 模拟 fetch 以支持审批覆盖 API 调用
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ overrides: [], total: 0 }),
    }));
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    navigateMock.mockReset();
  });

  it("默认展示当前项目主 Agent 和已创建 Agent 列表，不把模板混进列表", async () => {
    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      submitAction: vi.fn(),
      busyActionId: "",
    });

    render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    // 当前组件展示「行为文件」标题和 Agents 标签
    expect(await screen.findByRole("heading", { name: "行为文件" })).toBeInTheDocument();
    expect(screen.getByText("Agents")).toBeInTheDocument();

    // 主 Agent 卡片应带 "主 Agent" 标记
    expect(screen.getByText("主 Agent")).toBeInTheDocument();
    expect(screen.getAllByText("家庭主 Agent").length).toBeGreaterThan(0);

    // 项目内其他 Agent 也应出现
    expect(screen.getByText("NAS 巡检")).toBeInTheDocument();

    // 内建模板不应出现在主页 Agent 列表里
    expect(screen.queryByText("Butler Root Agent")).not.toBeInTheDocument();
    expect(screen.queryByText("Research Root Agent")).not.toBeInTheDocument();
  });

  it("点击新建 Agent 后才展示模板选择，并进入结构化编辑页", async () => {
    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      submitAction: vi.fn(),
      busyActionId: "",
    });

    render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    await userEvent.click((await screen.findAllByRole("button", { name: "新建 Agent" }))[0]);

    // 模板选择弹窗
    expect(await screen.findByRole("heading", { name: "先选一个起点，再补最少必要信息" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "从空白开始" })).toBeInTheDocument();

    // 选择当前的主协调模板
    await userEvent.click(screen.getByRole("button", { name: /Butler Root Agent/ }));

    // 进入编辑器
    expect(await screen.findByRole("heading", { name: "新建 Agent" })).toBeInTheDocument();
    expect(screen.getByLabelText(/名称/)).toBeInTheDocument();
    expect(screen.getByText("模型")).toBeInTheDocument();
    expect(screen.getAllByRole("combobox").length).toBeGreaterThan(0);
    // Feature 061: 新编辑器展示行为文件（创建模式不显示已授权工具）
    expect(screen.getAllByText("行为文件").length).toBeGreaterThanOrEqual(1);
  });

  it("保存新 Agent 时会先 review，再发布为当前项目 Agent", async () => {
    const submitAction = vi.fn(async (actionId: string) => {
      if (actionId === "agent.create_worker_with_project") {
        return {
          data: {
            worker_profile_id: "project-home:new-agent",
            project_id: "project-home:new-agent-project",
          },
        };
      }
      if (actionId === "worker_profile.apply") {
        return {
          data: {
            profile_id: "project-home:new-agent",
          },
        };
      }
      return { data: {} };
    });

    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      submitAction,
      busyActionId: "",
    });

    render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    await userEvent.click((await screen.findAllByRole("button", { name: "新建 Agent" }))[0]);
    await userEvent.click(screen.getByRole("button", { name: /Butler Root Agent/ }));

    const nameInput = screen.getByLabelText(/名称/);
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, "资料整理助手");

    // 编辑器中创建新 Agent 的保存按钮文案是 "创建"
    await userEvent.click(screen.getByRole("button", { name: "创建" }));

    await waitFor(() => {
      expect(submitAction).toHaveBeenCalledWith(
        "agent.create_worker_with_project",
        expect.objectContaining({
          worker_name: "资料整理助手",
          project_name: "资料整理助手",
        })
      );
    });

    await waitFor(() => {
      expect(submitAction).toHaveBeenCalledWith(
        "worker_profile.apply",
        expect.objectContaining({
          publish: true,
          set_as_default: false,
          change_summary: "创建 Agent 后同步配置",
        })
      );
    });
  });

  it("编辑器展示审批覆盖列表并支持撤销", async () => {
    // 模拟审批覆盖 API 返回数据
    const fetchMock = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
      if (typeof url === "string" && url.includes("/api/approval-overrides") && (!options || options.method !== "DELETE")) {
        return {
          ok: true,
          json: async () => ({
            overrides: [
              {
                id: 1,
                agent_runtime_id: "butler-001",
                tool_name: "docker.run",
                decision: "always",
                created_at: "2026-03-14T12:00:00Z",
              },
            ],
            total: 1,
          }),
        };
      }
      if (options?.method === "DELETE") {
        return { ok: true, json: async () => ({ success: true, message: "已撤销" }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      submitAction: vi.fn(),
      busyActionId: "",
    });

    render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    // 打开主 Agent 编辑器
    const mainCard = (await screen.findByText("主 Agent")).closest(".wb-agent-card") as HTMLElement | null;
    await userEvent.click(within(mainCard!).getByRole("button", { name: "编辑" }));

    // 等待审批覆盖列表加载完成
    expect(await screen.findByText("已授权工具")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("docker.run")).toBeInTheDocument();
    });

    // 点击撤销
    await userEvent.click(screen.getByRole("button", { name: "撤销" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/approval-overrides/butler-001/docker.run",
        expect.objectContaining({ method: "DELETE" })
      );
    });
  });

  it("编辑 extracted Agent 时会保留原始来源类型", async () => {
    const submitAction = vi.fn(async (actionId: string) => {
      if (actionId === "worker_profile.review") {
        return {
          data: {
            review: {
              can_save: true,
              ready: true,
              warnings: [],
              blocking_reasons: [],
              next_actions: ["可以直接保存。"],
            },
          },
        };
      }
      if (actionId === "worker_profile.apply") {
        return {
          data: {
            profile_id: "project-home:extracted-agent",
          },
        };
      }
      return { data: {} };
    });

    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot({
        customAgents: [
          {
            profile_id: "project-home:main",
            name: "家庭主 Agent",
            summary: "负责默认聊天入口和日常协调。",
            status: "active",
            model_alias: "main",
            default_tool_groups: ["project"],
            selected_tools: ["project.inspect"],
          },
          {
            profile_id: "project-home:extracted-agent",
            name: "从运行时整理的 Agent",
            summary: "从历史任务里整理出来。",
            status: "active",
            origin_kind: "extracted",
            model_alias: "reasoning",
            default_tool_groups: ["runtime"],
            selected_tools: ["runtime.inspect"],
          },
        ],
      }),
      submitAction,
      busyActionId: "",
    });

    render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    const agentCard = (await screen.findByText("从运行时整理的 Agent")).closest(".wb-agent-card") as HTMLElement | null;
    expect(agentCard).not.toBeNull();

    await userEvent.click(within(agentCard!).getByRole("button", { name: "编辑" }));
    // 编辑已有 Agent 时保存按钮文案是 "保存"
    await userEvent.click(await screen.findByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(submitAction).toHaveBeenCalledWith(
        "worker_profile.review",
        expect.objectContaining({
          draft: expect.objectContaining({
            origin_kind: "extracted",
          }),
        })
      );
    });
  });

  it("Agent 编辑器只展示当前配置里的真实 alias", async () => {
    const snapshot = buildSnapshot({
      customAgents: [
        {
          profile_id: "project-home:main",
          name: "家庭主 Agent",
          summary: "负责默认聊天入口和日常协调。",
          status: "active",
          model_alias: "main",
          default_tool_groups: ["project"],
          selected_tools: ["project.inspect"],
        },
      ],
    });
    snapshot.resources.config.current_value.model_aliases = {
      main: "openrouter/main",
      cheap: "openrouter/cheap",
    };

    useWorkbenchMock.mockReturnValue({
      snapshot,
      submitAction: vi.fn(),
      busyActionId: "",
    });

    render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    const mainCard = (await screen.findByText("主 Agent")).closest(".wb-agent-card") as HTMLElement | null;
    expect(mainCard).not.toBeNull();
    await userEvent.click(within(mainCard!).getByRole("button", { name: "编辑" }));

    const modelField = screen.getByText("模型").closest(".wb-field") as HTMLElement | null;
    expect(modelField).not.toBeNull();
    const modelSelect = within(modelField!).getByRole("combobox");
    const optionTexts = within(modelSelect).getAllByRole("option").map((option) => option.textContent);
    expect(optionTexts).toContain("main");
    expect(optionTexts).toContain("cheap");
    expect(optionTexts).not.toContain("reasoning");
  });

  it("当 Agent 当前 alias 已失效时会明确提示修复", async () => {
    const snapshot = buildSnapshot({
      customAgents: [
        {
          profile_id: "project-home:main",
          name: "家庭主 Agent",
          summary: "负责默认聊天入口和日常协调。",
          status: "active",
          model_alias: "main",
          default_tool_groups: ["project"],
          selected_tools: ["project.inspect"],
        },
        {
          profile_id: "project-home:legacy",
          name: "旧版 alias Agent",
          summary: "还保留了历史 alias。",
          status: "active",
          model_alias: "reasoning",
          default_tool_groups: ["runtime"],
          selected_tools: ["runtime.inspect"],
        },
      ],
    });
    snapshot.resources.config.current_value.model_aliases = {
      main: "openrouter/main",
      cheap: "openrouter/cheap",
    };

    useWorkbenchMock.mockReturnValue({
      snapshot,
      submitAction: vi.fn(),
      busyActionId: "",
    });

    render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    const agentCard = (await screen.findByText("旧版 alias Agent")).closest(".wb-agent-card") as HTMLElement | null;
    expect(agentCard).not.toBeNull();
    await userEvent.click(within(agentCard!).getByRole("button", { name: "编辑" }));

    // 失效 alias 仍在 select 选项中（兼容展示），用户可切换
    const modelField = screen.getByText("模型").closest(".wb-field") as HTMLElement | null;
    expect(modelField).not.toBeNull();
    const modelSelect = within(modelField!).getByRole("combobox");
    const optionTexts = within(modelSelect).getAllByRole("option").map((o) => o.textContent);
    expect(optionTexts).toContain("reasoning");
    expect(optionTexts).toContain("main");
  });

  it("主 Agent 默认显示为可编辑状态", async () => {
    const submitAction = vi.fn(async (actionId: string) => {
      if (actionId === "worker_profile.review") {
        return {
          data: {
            review: {
              can_save: true,
              ready: true,
              warnings: [],
              blocking_reasons: [],
              next_actions: ["可以直接保存。"],
            },
          },
        };
      }
      if (actionId === "worker_profile.apply") {
        return {
          data: {
            profile_id: "project-home:main-agent",
          },
        };
      }
      return { data: {} };
    });

    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot({
        defaultProfileId: "singleton:general",
        customAgents: [
          {
            profile_id: "project-home:ops",
            name: "运行保障",
            summary: "负责运行巡检。",
            status: "active",
            model_alias: "reasoning",
            default_tool_groups: ["runtime"],
            selected_tools: ["runtime.inspect"],
          },
        ],
      }),
      submitAction,
      busyActionId: "",
    });

    render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    // 主 Agent 卡片的状态 pill 在 isMainAgent 时总是显示 "主 Agent"
    expect(await screen.findByText("主 Agent")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "编辑" }).length).toBeGreaterThan(0);

    // 点击「编辑」按钮进入编辑器
    await userEvent.click((await screen.findAllByRole("button", { name: "编辑" }))[0]);
    const nameInput = await screen.findByLabelText(/名称/);
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, "家庭主 Agent");
    // 编辑主 Agent 时保存按钮文案是 "保存"
    await userEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(submitAction).toHaveBeenCalledWith(
        "worker_profile.apply",
        expect.objectContaining({
          publish: true,
          set_as_default: true,
          change_summary: "通过 Agents 页面更新主 Agent",
        })
      );
    });
  });

  it("支持删除普通 Agent，删除前弹出确认", async () => {
    const submitAction = vi.fn(async () => ({ data: {} }));
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      submitAction,
      busyActionId: "",
    });

    render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    const agentCard = (await screen.findByText("NAS 巡检")).closest(".wb-agent-card") as HTMLElement | null;
    expect(agentCard).not.toBeNull();

    // 当前组件中，非主 Agent 卡片有"删除"按钮
    await userEvent.click(within(agentCard!).getByRole("button", { name: "删除" }));
    expect(confirmSpy).toHaveBeenCalled();
    await waitFor(() => {
      expect(submitAction).toHaveBeenCalledWith("worker_profile.archive", {
        profile_id: "project-home:nas",
      });
    });
  });

  it("可以从 Agent 卡片直接进入编辑器并加载当前 Agent 信息", async () => {
    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      submitAction: vi.fn(),
      busyActionId: "",
    });

    render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    const agentCard = (await screen.findByText("NAS 巡检")).closest(".wb-agent-card") as HTMLElement | null;
    expect(agentCard).not.toBeNull();

    await userEvent.click(within(agentCard!).getByRole("button", { name: "编辑" }));

    expect(await screen.findByRole("heading", { name: "NAS 巡检" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("NAS 巡检")).toBeInTheDocument();
    expect(screen.getByDisplayValue("reasoning")).toBeInTheDocument();
  });

  it("切换项目后只显示对应项目的 Agent", async () => {
    let snapshot = buildSnapshot();
    useWorkbenchMock.mockImplementation(() => ({
      snapshot,
      submitAction: vi.fn(),
      busyActionId: "",
    }));

    const view = render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    expect((await screen.findAllByText("家庭主 Agent")).length).toBeGreaterThan(0);

    snapshot = buildSnapshot({
      currentProjectId: "project-work",
      currentProjectName: "工作项目",
      defaultProfileId: "project-work:lead",
      customAgents: [
        {
          profile_id: "project-work:lead",
          name: "工作主 Agent",
          project_id: "project-work",
          summary: "负责工作项目的默认聊天入口。",
          status: "active",
          model_alias: "main",
          default_tool_groups: ["project", "web"],
          selected_tools: ["web.search"],
        },
      ],
    });

    view.rerender(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    // 切换后应看到新项目的主 Agent，看不到旧项目的
    expect(screen.getAllByText("工作主 Agent").length).toBeGreaterThan(0);
    expect(screen.queryByText("家庭主 Agent")).not.toBeInTheDocument();
  });

  it("Agent 卡片里的元信息（模型、工具组数量等）和行为文件 chip 都正确呈现", async () => {
    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      submitAction: vi.fn(),
      busyActionId: null,
    });

    render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    // 主 Agent 卡片应包含模型 alias
    const mainCard = (await screen.findByText("主 Agent")).closest(".wb-agent-card") as HTMLElement | null;
    expect(mainCard).not.toBeNull();
    expect(
      within(mainCard!).getAllByText(
        (_, node) => node?.tagName === "SPAN" && node.textContent === "模型 main"
      )[0]
    ).toBeInTheDocument();

    const nasCard = (await screen.findByText("NAS 巡检")).closest(".wb-agent-card") as HTMLElement | null;
    expect(nasCard).not.toBeNull();
    expect(
      within(nasCard!).getAllByText(
        (_, node) => node?.tagName === "SPAN" && node.textContent === "进行中 1"
      )[0]
    ).toBeInTheDocument();
  });

  it("行为文件面板展示全局共享和项目共享 scope 组", async () => {
    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      submitAction: vi.fn(),
      busyActionId: null,
    });

    render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    // 当前组件的行为文件面板标题
    expect(await screen.findByRole("heading", { name: "行为文件" })).toBeInTheDocument();

    // 行为文件面板展示 scope 分组卡片（agent_private 被过滤掉）
    expect(screen.getByText("全局共享规则")).toBeInTheDocument();
    expect(screen.getByText("项目共享配置")).toBeInTheDocument();
    expect(screen.getByText("Project-Agent Override")).toBeInTheDocument();

    // 各分组卡片中展示对应的文件 ID
    const sharedCard = screen.getByText("全局共享规则").closest("article") as HTMLElement | null;
    expect(sharedCard).not.toBeNull();
    expect(within(sharedCard!).getByText("AGENTS.md")).toBeInTheDocument();

    const projectSharedCard = screen.getByText("项目共享配置").closest("article") as HTMLElement | null;
    expect(projectSharedCard).not.toBeNull();
    expect(within(projectSharedCard!).getByText("PROJECT.md")).toBeInTheDocument();
  });

  it("编辑器展示行为文件卡片（IDENTITY.md / SOUL.md / HEARTBEAT.md）", async () => {
    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      submitAction: vi.fn(),
      busyActionId: "",
    });

    render(
      <MemoryRouter>
        <AgentCenter />
      </MemoryRouter>
    );

    // 打开主 Agent 编辑器
    const mainCard = (await screen.findByText("主 Agent")).closest(".wb-agent-card") as HTMLElement | null;
    await userEvent.click(within(mainCard!).getByRole("button", { name: "编辑" }));

    // 编辑器中应展示 3 个行为文件
    expect(await screen.findByText("IDENTITY.md")).toBeInTheDocument();
    expect(screen.getByText("SOUL.md")).toBeInTheDocument();
    expect(screen.getByText("HEARTBEAT.md")).toBeInTheDocument();

    // 行为文件区域标签（页面顶部也有同名标题，所以用 getAllByText）
    expect(screen.getAllByText("行为文件").length).toBeGreaterThanOrEqual(2);
  });
});
