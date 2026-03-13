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

function buildSettingsSnapshot() {
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
                backend_mode: { type: "string", enum: ["local_only", "memu"] },
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
          "memory.backend_mode": {
            field_path: "memory.backend_mode",
            section: "memory-basic",
            label: "Memory 模式",
            description: "",
            widget: "select",
            placeholder: "",
            help_text: "",
            sensitive: false,
            multiline: false,
            order: 6,
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
            backend_mode: "local_only",
          },
        },
      },
      project_selector: {
        current_project_id: "project-default",
        current_workspace_id: "workspace-default",
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
      skill_provider_catalog: {
        summary: {
          installed_count: 1,
          custom_count: 0,
          builtin_count: 1,
        },
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
    expect(screen.getByRole("heading", { name: "先把真实模型接起来" })).toBeInTheDocument();
    expect(screen.getByText("先按这 3 步走通一次")).toBeInTheDocument();
    expect(screen.getAllByText("先添加一个 Provider。").length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole("button", { name: "添加 OpenAI" }));

    expect(await screen.findByDisplayValue("OpenAI")).toBeInTheDocument();
    expect(screen.getByDisplayValue("main")).toBeInTheDocument();
    expect(screen.getByDisplayValue("cheap")).toBeInTheDocument();
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
    snapshot.resources.config.current_value.runtime.llm_mode = "litellm";
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

    expect(screen.getByRole("heading", { name: "配置已经够用，先保存再验证" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "先保存当前修改" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "回聊天验证" }).length).toBeGreaterThan(0);
  });

  it("把 Agent 能力管理入口迁到 Agents 页面", async () => {
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

    expect(screen.getByText("Agent 能力管理已移到 Agents")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "打开 Agents > Providers" }).length).toBeGreaterThan(0);
    expect(screen.queryByText("安全与能力")).not.toBeInTheDocument();
  });
});
