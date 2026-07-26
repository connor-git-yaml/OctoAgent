import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { McpProviderItem } from "../types";
import McpProviderCenter from "./McpProviderCenter";

const useWorkbenchMock = vi.fn();

vi.mock("../components/shell/WorkbenchLayout", () => ({
  useWorkbench: () => useWorkbenchMock(),
}));

const PROVIDER: McpProviderItem = {
  provider_id: "private-calendar",
  label: "家庭日历",
  description: "帮助 Agent 读取家庭日程。",
  editable: true,
  removable: true,
  enabled: true,
  status: "available",
  command: "uvx",
  args: ["calendar-mcp", "--stdio"],
  cwd: "/srv/private-calendar",
  env: { CALENDAR_TOKEN: "catalog-secret-value" },
  tool_count: 3,
  selection_item_id: "mcp:private-calendar",
  install_hint: "",
  error: "",
  warnings: [],
  details: { tools: ["calendar.list", "calendar.create"] },
  install_source: "pip",
  install_version: "1.2.0",
  install_path: "/srv/mcp/private-calendar",
  installed_at: "2026-07-21T00:00:00Z",
};

function snapshotWith(items: McpProviderItem[]) {
  return {
    resources: {
      mcp_provider_catalog: {
        generated_at: "2026-07-21T00:00:00Z",
        resource_type: "mcp_provider_catalog",
        resource_id: "mcp-providers:catalog",
        active_project_id: "project-home",
        items,
        summary: {
          enabled_count: items.filter((item) => item.enabled).length,
          healthy_count: items.filter((item) => item.status === "available")
            .length,
        },
      },
    },
  };
}

function workbenchState(overrides: Record<string, unknown> = {}) {
  return {
    snapshot: snapshotWith([PROVIDER]),
    loading: false,
    error: null,
    authError: null,
    busyActionId: null,
    lastAction: null,
    refreshSnapshot: vi.fn(),
    refreshResources: vi.fn(),
    submitAction: vi.fn().mockResolvedValue(null),
    clearError: vi.fn(),
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <McpProviderCenter />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useWorkbenchMock.mockReturnValue(workbenchState());
});

describe("McpProviderCenter v2", () => {
  it("覆盖加载、空态，并始终保留两个可见入口", () => {
    useWorkbenchMock.mockReturnValue(
      workbenchState({ snapshot: null, loading: true }),
    );
    const { rerender } = renderPage();
    expect(screen.getByText("正在加载外部服务")).toBeInTheDocument();

    useWorkbenchMock.mockReturnValue(
      workbenchState({ snapshot: snapshotWith([]), loading: false }),
    );
    rerender(
      <MemoryRouter>
        <McpProviderCenter />
      </MemoryRouter>,
    );
    expect(screen.getByText("还没有连接外部服务")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "安装服务" })).toBeVisible();
    expect(screen.getByRole("button", { name: "手动添加" })).toBeVisible();
  });

  it("列表失败使用普通语言并提供真实重试", async () => {
    const refreshSnapshot = vi.fn().mockResolvedValue(undefined);
    useWorkbenchMock.mockReturnValue(
      workbenchState({
        snapshot: null,
        error: "database registry exploded",
        refreshSnapshot,
      }),
    );
    const user = userEvent.setup();
    renderPage();

    expect(screen.getByText("服务列表加载失败")).toBeInTheDocument();
    expect(
      screen.queryByText(/database registry exploded/),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(refreshSnapshot).toHaveBeenCalledTimes(1);
  });

  it("origin 403 只说明资源权限，不提供重新登录", () => {
    useWorkbenchMock.mockReturnValue(
      workbenchState({
        snapshot: null,
        authError: new ApiError("upstream forbidden", { status: 403 }),
      }),
    );
    renderPage();

    expect(
      screen.getByText("当前账号没有权限管理外部服务"),
    ).toBeInTheDocument();
    expect(screen.queryByText("upstream forbidden")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /重新登录/ }),
    ).not.toBeInTheDocument();
  });

  it("普通卡片隐藏命令与密钥，高级区才显示净化后的技术摘要", async () => {
    useWorkbenchMock.mockReturnValue(
      workbenchState({
        snapshot: snapshotWith([{ ...PROVIDER, description: "uvx" }]),
      }),
    );
    const user = userEvent.setup();
    renderPage();

    const card = screen.getByRole("article", { name: "家庭日历" });
    expect(
      within(card).getByText("已连接到 OctoAgent，可供 Agent 按权限使用。"),
    ).toBeInTheDocument();
    expect(within(card).queryByText(/uvx/)).not.toBeInTheDocument();
    expect(
      within(card).queryByText(/private-calendar/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/catalog-secret-value/)).not.toBeInTheDocument();

    const trigger = within(card).getByRole("button", {
      name: "高级 · 技术信息",
    });
    await user.click(trigger);
    expect(within(card).getByText("calendar.list")).toBeInTheDocument();
    expect(
      within(card).getByText("uvx calendar-mcp --stdio"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/catalog-secret-value/)).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "true");
  });

  it("编辑密钥只允许保留、替换或清空，不回填现有值", async () => {
    const submitAction = vi.fn().mockResolvedValue({
      action_id: "mcp_provider.save",
      code: "OK",
      data: {},
      message: "saved",
      status: "completed",
    });
    useWorkbenchMock.mockReturnValue(workbenchState({ submitAction }));
    const user = userEvent.setup();
    renderPage();
    const editTrigger = screen.getByRole("button", { name: "编辑家庭日历" });
    await user.click(editTrigger);

    const dialog = screen.getByRole("dialog", { name: "编辑家庭日历" });
    expect(within(dialog).getByText(/现有值不可查看/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText("保留现有值")).toBeChecked();
    expect(within(dialog).getByLabelText("替换访问密钥")).not.toBeChecked();
    expect(within(dialog).getByLabelText("清空访问密钥")).not.toBeChecked();
    expect(
      within(dialog).queryByDisplayValue("catalog-secret-value"),
    ).toBeNull();
    expect(screen.queryByText("catalog-secret-value")).not.toBeInTheDocument();

    await user.click(within(dialog).getByLabelText("替换访问密钥"));
    await user.type(
      within(dialog).getByLabelText("新的访问密钥"),
      "replacement-value",
    );
    await user.click(
      within(dialog).getByRole("button", { name: "保存并生效" }),
    );
    expect(submitAction).toHaveBeenCalledWith(
      "mcp_provider.save",
      expect.objectContaining({
        provider: expect.objectContaining({
          env: {
            CALENDAR_TOKEN: {
              mode: "replace",
              value: "replacement-value",
            },
          },
        }),
      }),
    );
    expect(JSON.stringify(submitAction.mock.calls)).not.toContain(
      "catalog-secret-value",
    );
  });

  it("关闭编辑后清除临时密钥并把焦点还给入口", async () => {
    const user = userEvent.setup();
    renderPage();
    const editTrigger = screen.getByRole("button", { name: "编辑家庭日历" });
    await user.click(editTrigger);
    const dialog = screen.getByRole("dialog", { name: "编辑家庭日历" });

    await user.click(within(dialog).getByLabelText("替换访问密钥"));
    await user.type(
      within(dialog).getByLabelText("新的访问密钥"),
      "temporary-secret",
    );
    await user.click(within(dialog).getByRole("button", { name: "关闭" }));
    await waitFor(() => expect(editTrigger).toHaveFocus());

    await user.click(editTrigger);
    expect(
      within(
        screen.getByRole("dialog", { name: "编辑家庭日历" }),
      ).queryByDisplayValue("temporary-secret"),
    ).toBeNull();
  });
});
