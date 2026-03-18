import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "./SettingsPage";

let mockWorkbench: {
  snapshot: unknown;
  submitAction: ReturnType<typeof vi.fn>;
  busyActionId: string | null;
};

vi.mock("../../components/shell/WorkbenchLayout", () => ({
  useWorkbench: () => mockWorkbench,
}));

function buildSettingsSnapshot(): any {
  return {
    resources: {
      config: {
        generated_at: "2026-03-13T16:00:00Z",
        schema: {
          type: "object",
          properties: {
            runtime: {
              type: "object",
              properties: {
                llm_mode: { type: "string", enum: ["echo", "litellm"] },
              },
            },
            memory: {
              type: "object",
              properties: {
                reasoning_model_alias: { type: "string" },
                expand_model_alias: { type: "string" },
                embedding_model_alias: { type: "string" },
                rerank_model_alias: { type: "string" },
              },
            },
          },
        },
        ui_hints: {
          "runtime.llm_mode": {
            field_path: "runtime.llm_mode",
            section: "runtime",
            label: "LLM 模式",
            description: "",
            widget: "select",
            placeholder: "",
            help_text: "",
            sensitive: false,
            multiline: false,
            order: 1,
          },
          "runtime.litellm_proxy_url": {
            field_path: "runtime.litellm_proxy_url",
            section: "runtime",
            label: "LiteLLM 地址",
            description: "",
            widget: "text",
            placeholder: "http://localhost:4000",
            help_text: "",
            sensitive: false,
            multiline: false,
            order: 2,
          },
          "runtime.master_key_env": {
            field_path: "runtime.master_key_env",
            section: "runtime",
            label: "Master Key 环境变量",
            description: "",
            widget: "env-ref",
            placeholder: "LITELLM_MASTER_KEY",
            help_text: "",
            sensitive: false,
            multiline: false,
            order: 3,
          },
          providers: {
            field_path: "providers",
            section: "providers",
            label: "Providers",
            description: "",
            widget: "provider-list",
            placeholder: "[]",
            help_text: "",
            sensitive: false,
            multiline: true,
            order: 4,
          },
          model_aliases: {
            field_path: "model_aliases",
            section: "models",
            label: "模型别名",
            description: "",
            widget: "alias-map",
            placeholder: "{}",
            help_text: "",
            sensitive: false,
            multiline: true,
            order: 5,
          },
          "memory.reasoning_model_alias": {
            field_path: "memory.reasoning_model_alias",
            section: "memory-models",
            label: "加工模型别名",
            description: "",
            widget: "text",
            placeholder: "main",
            help_text: "",
            sensitive: false,
            multiline: false,
            order: 7,
          },
          "memory.expand_model_alias": {
            field_path: "memory.expand_model_alias",
            section: "memory-models",
            label: "扩写模型别名",
            description: "",
            widget: "text",
            placeholder: "main",
            help_text: "",
            sensitive: false,
            multiline: false,
            order: 8,
          },
          "memory.embedding_model_alias": {
            field_path: "memory.embedding_model_alias",
            section: "memory-models",
            label: "Embedding 模型别名",
            description: "",
            widget: "text",
            placeholder: "knowledge-embed",
            help_text: "",
            sensitive: false,
            multiline: false,
            order: 9,
          },
          "memory.rerank_model_alias": {
            field_path: "memory.rerank_model_alias",
            section: "memory-models",
            label: "Rerank 模型别名",
            description: "",
            widget: "text",
            placeholder: "memory-rerank",
            help_text: "",
            sensitive: false,
            multiline: false,
            order: 10,
          },
        },
        current_value: {
          runtime: {
            llm_mode: "echo",
            litellm_proxy_url: "http://localhost:4000",
            master_key_env: "LITELLM_MASTER_KEY",
          },
          providers: [],
          model_aliases: {},
          memory: {
            reasoning_model_alias: "",
            expand_model_alias: "",
            embedding_model_alias: "",
            rerank_model_alias: "",
          },
        },
      },
      project_selector: {
        current_project_id: "project-default",
        current_workspace_id: "workspace-default",
        available_projects: [
          {
            project_id: "project-default",
            slug: "default",
            name: "Default Project",
          },
        ],
        available_workspaces: [
          {
            workspace_id: "workspace-default",
            project_id: "project-default",
            slug: "primary",
            name: "Primary Workspace",
          },
        ],
      },
      memory: {
        backend_state: "ready",
        status: "ready",
        backend_id: "memory-local",
        warnings: [],
        summary: {
          sor_current_count: 1,
          fragment_count: 2,
          pending_replay_count: 0,
          vault_ref_count: 0,
        },
      },
      retrieval_platform: {
        resource_type: "retrieval_platform",
        resource_id: "retrieval:platform",
        generated_at: "2026-03-13T16:00:00Z",
        updated_at: "2026-03-13T16:00:00Z",
        status: "ready",
        degraded: {
          is_degraded: false,
          reasons: [],
          unavailable_sections: [],
        },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        profiles: [
          {
            profile_id: "builtin:sqlite-metadata",
            label: "内建默认层（当前先走本地元数据）",
            target: "sqlite-metadata",
            source_kind: "builtin",
            model_alias: "",
            is_builtin: true,
            is_available: true,
            summary: "当前先沿用本地元数据与关键词召回。",
            warnings: [],
          },
        ],
        corpora: [
          {
            corpus_kind: "memory",
            label: "Memory",
            active_generation_id: "gen-memory-active",
            pending_generation_id: "",
            active_profile_id: "builtin:sqlite-metadata",
            active_profile_target: "sqlite-metadata",
            desired_profile_id: "builtin:sqlite-metadata",
            desired_profile_target: "sqlite-metadata",
            state: "ready",
            summary: "当前 embedding 与在线索引保持一致。",
            last_cutover_at: "2026-03-13T16:00:00Z",
            warnings: [],
          },
          {
            corpus_kind: "knowledge_base",
            label: "知识库",
            active_generation_id: "",
            pending_generation_id: "",
            active_profile_id: "",
            active_profile_target: "",
            desired_profile_id: "builtin:sqlite-metadata",
            desired_profile_target: "sqlite-metadata",
            state: "reserved",
            summary: "知识库还没有接入内容；未来导入文档时会复用这里的 embedding profile。",
            last_cutover_at: null,
            warnings: [],
          },
        ],
        generations: [
          {
            generation_id: "gen-memory-active",
            corpus_kind: "memory",
            profile_id: "builtin:sqlite-metadata",
            profile_target: "sqlite-metadata",
            label: "Memory · 内建默认层（当前先走本地元数据）",
            status: "active",
            is_active: true,
            build_job_id: "",
            previous_generation_id: "",
            created_at: "2026-03-13T15:00:00Z",
            updated_at: "2026-03-13T16:00:00Z",
            activated_at: "2026-03-13T16:00:00Z",
            completed_at: "2026-03-13T16:00:00Z",
            rollback_deadline: null,
            warnings: [],
            metadata: {},
          },
        ],
        build_jobs: [],
        summary: {
          active_generation_count: 1,
          pending_generation_count: 0,
          profile_count: 1,
        },
      },
      setup_governance: {
        generated_at: "2026-03-13T16:00:00Z",
        provider_runtime: {
          status: "ready",
          details: {
            litellm_env_names: [],
            runtime_env_names: [],
            openai_oauth_connected: false,
            openai_oauth_profile: "",
          },
        },
        tools_skills: {
          label: "默认能力",
          summary: "当前项目能力范围正常。",
        },
        review: {
          ready: false,
          risk_level: "medium",
          warnings: [],
          blocking_reasons: ["还没有真实模型配置。"],
          next_actions: ["先添加一个 Provider。"],
          provider_runtime_risks: [],
          channel_exposure_risks: [],
          agent_autonomy_risks: [],
          tool_skill_readiness_risks: [],
          secret_binding_risks: [],
        },
      },
      agent_profiles: {
        profiles: [
          {
            profile_id: "agent-profile-default",
            scope: "project",
            project_id: "project-default",
            name: "Default Butler",
            persona_summary: "负责长期协作。",
            model_alias: "main",
            tool_profile: "standard",
            metadata: {},
            updated_at: "2026-03-13T16:00:00Z",
            behavior_system: {
              source_chain: ["filesystem:behavior/projects/default", "default_behavior_templates"],
              decision_modes: [
                "direct_answer",
                "ask_once",
                "delegate_research",
                "delegate_ops",
                "best_effort_answer",
              ],
              runtime_hint_fields: [
                "explicit_web_search_requested",
                "effective_location_hint",
              ],
              files: [
                {
                  file_id: "AGENTS.md",
                  title: "行为总约束",
                  layer: "role",
                  visibility: "shared",
                  share_with_workers: true,
                  source_kind: "project_file",
                  path_hint: "behavior/projects/default/AGENTS.md",
                },
                {
                  file_id: "USER.md",
                  title: "用户默认值",
                  layer: "communication",
                  visibility: "private",
                  share_with_workers: false,
                  source_kind: "default_template",
                  path_hint: "behavior/system/USER.md",
                },
              ],
              worker_slice: {
                shared_file_ids: ["AGENTS.md", "PROJECT.md", "TOOLS.md"],
                layers: ["role", "solving", "tool_boundary"],
              },
            },
          },
        ],
      },
      policy_profiles: {
        generated_at: "2026-03-13T16:00:00Z",
        active_profile_id: "balanced",
        profiles: [
          {
            profile_id: "balanced",
            label: "Balanced",
            description: "",
            allowed_tool_profile: "standard",
            approval_policy: "balanced",
            risk_level: "medium",
            recommended_for: [],
            is_active: true,
          },
        ],
      },
      skill_governance: {
        items: [
          {
            item_id: "skill:review",
            label: "Review",
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
        summary: {
          installed_count: 0,
          enabled_count: 0,
          healthy_count: 0,
        },
      },
    },
  };
}

describe("SettingsPage", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("支持从空状态添加 Provider，并自动生成 main / cheap 别名", async () => {
    mockWorkbench = {
      snapshot: buildSettingsSnapshot(),
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    );

    expect(screen.getByText("还没有 Provider")).toBeInTheDocument();
    expect(screen.getByText("还没有模型别名")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "先连上至少一个模型 Provider" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "模型 Provider 管理" })).toBeInTheDocument();
    expect(screen.getAllByText("先添加一个 Provider。").length).toBeGreaterThan(0);
    expect(screen.queryByText("LiteLLM 代理地址")).not.toBeInTheDocument();
    expect(screen.queryByText("LiteLLM Master Key 值")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "本地记忆引擎" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "添加 OpenAI" }));

    expect(await screen.findByDisplayValue("OpenAI")).toBeInTheDocument();
    expect(screen.getByDisplayValue("main")).toBeInTheDocument();
    expect(screen.getByDisplayValue("cheap")).toBeInTheDocument();
  });

  it("保存时会自动补齐内部 LiteLLM 运行配置", async () => {
    const snapshot = buildSettingsSnapshot();
    const submitAction = vi.fn().mockResolvedValue({
      data: {
        review: snapshot.resources.setup_governance.review,
      },
    });
    mockWorkbench = {
      snapshot,
      submitAction,
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    );

    await userEvent.click(screen.getByRole("button", { name: "添加 OpenAI" }));

    await userEvent.click(screen.getAllByRole("button", { name: "检查配置" })[0]);

    await waitFor(() => expect(submitAction).toHaveBeenCalledWith("setup.review", expect.anything()));

    const draft = submitAction.mock.calls[0][1].draft;
    expect(draft.config.runtime.llm_mode).toBe("litellm");
    expect(draft.config.runtime.litellm_proxy_url).toBe("http://localhost:4000");
    expect(draft.config.runtime.master_key_env).toBe("LITELLM_MASTER_KEY");
    expect(draft.secret_values.LITELLM_MASTER_KEY).toBeTruthy();
  });

  it("retrieval platform 迁移 UI 暂未启用（void retrievalPlatform）", () => {
    const snapshot = buildSettingsSnapshot();
    snapshot.resources.retrieval_platform.corpora[0].pending_generation_id = "gen-memory-next";
    snapshot.resources.retrieval_platform.corpora[0].desired_profile_id = "alias:knowledge-embed";
    snapshot.resources.retrieval_platform.corpora[0].desired_profile_target = "knowledge-embed";
    snapshot.resources.retrieval_platform.corpora[0].state = "migration_pending";
    mockWorkbench = {
      snapshot,
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    );

    // 迁移管理 UI 暂未启用，不应渲染切换按钮或迁移状态
    expect(screen.queryByRole("button", { name: "切换到新索引" })).not.toBeInTheDocument();
    // Memory 区块仍然正常渲染基础状态卡片
    expect(screen.getByRole("heading", { name: "本地记忆引擎" })).toBeInTheDocument();
    expect(screen.getByText("ready")).toBeInTheDocument();
  });

  it("支持触发 OpenAI Auth 连接动作", async () => {
    const submitAction = vi.fn().mockResolvedValue({
      data: {},
    });
    mockWorkbench = {
      snapshot: buildSettingsSnapshot(),
      submitAction,
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    );

    await userEvent.click(screen.getByRole("button", { name: "添加 OpenAI Auth" }));
    await userEvent.click(screen.getByRole("button", { name: "连接 OpenAI Auth" }));

    await waitFor(() =>
      expect(submitAction).toHaveBeenCalledWith("provider.oauth.openai_codex", {
        env_name: "OPENAI_API_KEY",
        profile_name: "openai-codex-default",
      })
    );
  });

  it("review 已就绪时仍优先要求先保存当前修改", () => {
    const snapshot = buildSettingsSnapshot();
    snapshot.resources.config.current_value.providers = [
      {
        id: "openrouter",
        name: "OpenRouter",
        auth_type: "api_key",
        api_key_env: "OPENROUTER_API_KEY",
        enabled: true,
      },
    ] as unknown as never[];
    snapshot.resources.setup_governance.review = {
      ...snapshot.resources.setup_governance.review,
      ready: true,
      blocking_reasons: [],
      next_actions: [],
    };
    mockWorkbench = {
      snapshot,
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    );

    expect(screen.getByRole("heading", { name: "现在已经可以回聊天验证" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存后回聊天验证" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "回聊天验证" }).length).toBeGreaterThan(0);
  });

  it("未连接真实模型时仍把连接真实模型作为首屏主动作", () => {
    const snapshot = buildSettingsSnapshot();
    snapshot.resources.setup_governance.review = {
      ...snapshot.resources.setup_governance.review,
      ready: true,
      blocking_reasons: [],
      next_actions: ['检查已通过，可以点击“保存配置”。'],
    };
    mockWorkbench = {
      snapshot,
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    );

    expect(screen.getByRole("heading", { name: "先连上至少一个模型 Provider" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "连接真实模型" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "保存配置" }).length).toBeGreaterThan(0);
    expect(screen.getByText("可以先保存")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "回聊天验证" })).not.toBeInTheDocument();
  });

  it("Memory 区块展示基础状态卡片而非兼容接入表单", () => {
    const snapshot = buildSettingsSnapshot();
    snapshot.resources.config.current_value.memory.backend_mode = "memu";
    snapshot.resources.config.current_value.memory.bridge_transport = "command";
    snapshot.resources.config.current_value.memory.bridge_command =
      "uv run python scripts/memu_bridge.py";
    snapshot.resources.config.current_value.memory.bridge_command_cwd = "/tmp/memu";
    snapshot.resources.config.current_value.memory.bridge_command_timeout_seconds = 18;
    mockWorkbench = {
      snapshot,
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    );

    // Feature 063 重构后 Memory 区块展示内建引擎卡片
    expect(screen.getByRole("heading", { name: "本地记忆引擎" })).toBeInTheDocument();
    expect(screen.getByText("内建记忆引擎")).toBeInTheDocument();
    expect(screen.getByText("SQLite / Vault")).toBeInTheDocument();
    // 兼容接入表单字段不应在 Memory 区块中渲染
    expect(screen.queryByDisplayValue("uv run python scripts/memu_bridge.py")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("/tmp/memu")).not.toBeInTheDocument();
  });

  it("Memory 区块展示 embedding 和结论统计", () => {
    const snapshot = buildSettingsSnapshot();
    snapshot.resources.config.current_value.providers = [
      {
        id: "openrouter",
        name: "OpenRouter",
        auth_type: "api_key",
        api_key_env: "OPENROUTER_API_KEY",
        enabled: true,
      },
    ] as unknown as never[];
    snapshot.resources.config.current_value.model_aliases = {
      main: {
        provider: "openrouter",
        model: "openrouter/auto",
        description: "主模型",
      },
    } as never;
    mockWorkbench = {
      snapshot,
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    );

    // Memory 区块正常渲染静态状态卡片
    expect(screen.getByRole("heading", { name: "本地记忆引擎" })).toBeInTheDocument();
    expect(screen.getByText("内建 embedding（默认）")).toBeInTheDocument();
    expect(screen.getByText("换模型时会后台重建索引")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument(); // sor_current_count
    expect(screen.getByText(/片段 2/)).toBeInTheDocument(); // fragment_count
  });

  it("Settings 页面不包含 Agent / Behavior 管理入口", () => {
    mockWorkbench = {
      snapshot: buildSettingsSnapshot(),
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    );

    // Settings 页面聚焦 Provider / Memory / 渠道，不包含 Agent 或 Behavior 管理
    expect(screen.queryByText("安全与能力")).not.toBeInTheDocument();
    expect(screen.queryByText(/Behavior/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Agents/ })).not.toBeInTheDocument();
    // 确认核心区块正常渲染
    expect(screen.getByRole("heading", { name: "模型 Provider 管理" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "本地记忆引擎" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "风险检查与保存" })).toBeInTheDocument();
  });

  it("不再在 Settings 页面展示只读 Behavior Files 视图", () => {
    mockWorkbench = {
      snapshot: buildSettingsSnapshot(),
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    );

    // Settings 页面不包含 Behavior Files 相关内容
    expect(screen.queryByText("当前项目默认行为现在来自显式文件与运行时 hints")).not.toBeInTheDocument();
    expect(screen.queryByText("octo behavior ls")).not.toBeInTheDocument();
    expect(screen.queryByText(/Behavior Files/)).not.toBeInTheDocument();
    // 确认 Settings 页面只聚焦 Providers / Memory / 渠道 / 保存检查
    expect(screen.getByRole("heading", { name: "模型 Provider 管理" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "本地记忆引擎" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "风险检查与保存" })).toBeInTheDocument();
  });

  it("不支持 reasoning 的 alias 会在页面和提交草稿里自动清空推理强度", async () => {
    const snapshot = buildSettingsSnapshot();
    snapshot.resources.config.current_value.providers = [
      {
        id: "openrouter",
        name: "OpenRouter",
        auth_type: "api_key",
        api_key_env: "OPENROUTER_API_KEY",
        enabled: true,
      },
    ] as unknown as never[];
    snapshot.resources.config.current_value.model_aliases = {
      cheap: {
        provider: "openrouter",
        model: "qwen/qwen3.5-9b",
        description: "低成本模型",
        thinking_level: "low",
      },
    } as never;
    const submitAction = vi.fn().mockResolvedValue({
      data: {
        review: snapshot.resources.setup_governance.review,
      },
    });
    mockWorkbench = {
      snapshot,
      submitAction,
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    );

    expect(
      screen.getByText("这个 alias 当前不在支持名单里。保存时会自动清空，后端也会忽略 reasoning 参数。")
    ).toBeInTheDocument();

    const reasoningSelect = screen.getByRole("combobox", { name: /推理强度/ });
    expect(reasoningSelect).toBeDisabled();

    await userEvent.click(screen.getAllByRole("button", { name: "检查配置" })[0]);

    await waitFor(() => expect(submitAction).toHaveBeenCalledWith("setup.review", expect.anything()));

    const setupReviewPayload = submitAction.mock.calls[0][1];
    expect(setupReviewPayload.draft.config.model_aliases.cheap.thinking_level).toBeUndefined();
  });
});
