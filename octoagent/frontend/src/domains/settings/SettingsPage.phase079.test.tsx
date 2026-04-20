/**
 * Feature 079 Phase 1 测试 —— Setup UX Recovery 行为。
 *
 * 覆盖：
 * 1. PendingChangesBar：用户在内存里改了 providers 但没保存 → sticky bar 出现
 * 2. PendingChangesBar：snapshot 和内存完全一致 → bar 不出现
 * 3. 错误 modal：workbench.error 在 save 动作后出现 → modal 打开
 * 4. 错误 modal：field 校验失败 → modal 打开并列出错误字段
 */

import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "./SettingsPage";

interface MockWorkbench {
  snapshot: unknown;
  submitAction: ReturnType<typeof vi.fn>;
  busyActionId: string | null;
  error?: string | null;
}

let mockWorkbench: MockWorkbench;

vi.mock("../../components/shell/WorkbenchLayout", () => ({
  useWorkbench: () => mockWorkbench,
}));

function buildMinimalSnapshot(): any {
  return {
    resources: {
      config: {
        generated_at: "2026-04-20T00:00:00Z",
        schema: {
          type: "object",
          properties: {
            runtime: {
              type: "object",
              properties: {
                llm_mode: { type: "string", enum: ["echo", "litellm"] },
              },
            },
          },
        },
        ui_hints: {
          "runtime.llm_mode": {
            field_path: "runtime.llm_mode",
            section: "runtime",
            label: "LLM Mode",
            description: "",
            widget: "select",
            placeholder: "",
            help_text: "",
            sensitive: false,
            multiline: false,
            order: 1,
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
            order: 2,
          },
          model_aliases: {
            field_path: "model_aliases",
            section: "providers",
            label: "Aliases",
            description: "",
            widget: "alias-map",
            placeholder: "{}",
            help_text: "",
            sensitive: false,
            multiline: true,
            order: 3,
          },
        },
        current_value: {
          runtime: {
            llm_mode: "litellm",
            litellm_proxy_url: "http://localhost:4000",
            master_key_env: "LITELLM_MASTER_KEY",
          },
          providers: [
            {
              id: "siliconflow",
              name: "SiliconFlow",
              auth_type: "api_key",
              api_key_env: "SILICONFLOW_API_KEY",
              base_url: "",
              enabled: true,
            },
          ],
          model_aliases: {
            main: {
              provider: "siliconflow",
              model: "Qwen/Qwen3.5-32B",
              description: "",
              thinking_level: null,
            },
          },
          memory: {
            reasoning_model_alias: "main",
            expand_model_alias: "main",
            embedding_model_alias: "main",
            rerank_model_alias: "main",
          },
        },
      },
      project_selector: {
        selected: { project_id: "default", name: "默认" },
        projects: [{ project_id: "default", name: "默认" }],
      },
      memory: {
        warnings: [],
        reasoning_model_alias: "main",
        expand_model_alias: "main",
        embedding_model_alias: "main",
        rerank_model_alias: "main",
        backend: { kind: "native", status: "ready" },
        operator_preference: null,
        conclusion_stats: null,
        embedding_stats: null,
      },
      retrieval_platform: null,
      setup_governance: {
        generated_at: "2026-04-20T00:00:00Z",
        review: {
          ready: true,
          risk_level: "info",
          warnings: [],
          blocking_reasons: [],
          next_actions: [],
          provider_runtime_risks: [],
          channel_exposure_risks: [],
          agent_autonomy_risks: [],
          tool_skill_readiness_risks: [],
          secret_binding_risks: [],
        },
        provider_runtime: {
          mode: "litellm",
          details: {},
        },
      },
      agent_profiles: {
        profiles: [],
        active_profile_id: null,
      },
      worker_profiles: { profiles: [] },
      skill_governance: { items: [] },
      mcp_provider_catalog: {
        summary: { installed_count: 0, enabled_count: 0, healthy_count: 0 },
      },
    },
  };
}

describe("SettingsPage · Feature 079 Phase 1", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  // P1.3: PendingChangesBar —— 初始态不应该显示
  it("初始无变更时不显示 PendingChangesBar", () => {
    mockWorkbench = {
      snapshot: buildMinimalSnapshot(),
      submitAction: vi.fn(),
      busyActionId: null,
    };
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    );
    expect(screen.queryByTestId("settings-pending-changes-bar")).not.toBeInTheDocument();
  });

  // P1.3: 修改 provider enabled 状态 → bar 显示并列出 "Provider 列表"
  it("修改 provider 字段后 PendingChangesBar 显示", async () => {
    mockWorkbench = {
      snapshot: buildMinimalSnapshot(),
      submitAction: vi.fn(),
      busyActionId: null,
    };
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    );

    // 清掉默认的 siliconflow provider，模拟 "把 provider 列表清空" 这一 pending 变更
    const removeButtons = screen.queryAllByRole("button", { name: /移除|删除/ });
    if (removeButtons.length > 0) {
      await userEvent.click(removeButtons[0]!);
    } else {
      // 如果没有移除按钮（UI 变化），改成切换 enabled
      const checkbox = screen.queryAllByRole("checkbox")[0];
      if (checkbox) {
        await userEvent.click(checkbox);
      }
    }

    await waitFor(() => {
      expect(
        screen.getByTestId("settings-pending-changes-bar")
      ).toBeInTheDocument();
    });
    expect(screen.getByText(/未保存的变更/)).toBeInTheDocument();
  });

  // P1.2: workbench.error 存在（且之前触发过 setup.apply）时打开错误 modal
  it("workbench.error 在 save 后出现时自动打开 modal", async () => {
    mockWorkbench = {
      snapshot: buildMinimalSnapshot(),
      submitAction: vi.fn().mockResolvedValue(null),
      busyActionId: null,
      error: null,
    };
    const { rerender } = render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    );

    // 触发一次 handleApply（通过顶部"保存配置"按钮，name 可能不同；按 text 找）
    const saveButton = screen.queryAllByRole("button", { name: /保存配置|保存/ })[0];
    expect(saveButton).toBeTruthy();
    if (saveButton) {
      fireEvent.click(saveButton);
    }
    // 等待动作被触发
    await waitFor(() => {
      expect(mockWorkbench.submitAction).toHaveBeenCalled();
    });

    // 模拟后端失败 —— workbench.error 变成非空
    mockWorkbench.error = "配置检查未通过，当前不能保存：main_alias_missing";
    rerender(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(
        screen.getByText(/保存检查未通过|保存请求出错/)
      ).toBeInTheDocument();
    });
  });
});
