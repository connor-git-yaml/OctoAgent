import { createElement, type ComponentType } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const ORACLE = "F150_DESKTOP_WEB_SETTINGS_ENTRY_MISSING";
const MODULE_PATH = "./RemoteAccessSettings";

type StatusFixture = {
  state: "unconfigured" | "pending_verification" | "ready" | "fault";
  hostname: string | null;
  owner_email: string | null;
  last_verified_at: string | null;
  reason_code: string | null;
  recovery_action: string | null;
  desktop_web_url: string | null;
  access_logout_url: string | null;
};

type RemoteAccessSettingsProps = {
  loadStatus?: () => Promise<StatusFixture>;
};

type SettingsModule = {
  RemoteAccessSettings: ComponentType<RemoteAccessSettingsProps>;
};

async function loadSettingsModule(): Promise<SettingsModule> {
  try {
    return (await import(/* @vite-ignore */ MODULE_PATH)) as SettingsModule;
  } catch (error) {
    throw new Error(`${ORACLE}: ${String(error)}`);
  }
}

function status(overrides: Partial<StatusFixture> = {}): StatusFixture {
  return {
    state: "ready",
    hostname: "o***.example.com",
    owner_email: "o***@example.com",
    last_verified_at: "2026-07-24T10:30:00Z",
    reason_code: null,
    recovery_action: null,
    desktop_web_url: "https://octo.example.com",
    access_logout_url: "https://octo.example.com/cdn-cgi/access/logout",
    ...overrides,
  };
}

async function renderStatus(payload: StatusFixture) {
  const { RemoteAccessSettings } = await loadSettingsModule();
  const rendered = render(
    createElement(RemoteAccessSettings, {
      loadStatus: async () => payload,
    })
  );
  await waitFor(() => {
    expect(rendered.container.querySelector("[data-status]")).toBeInTheDocument();
  });
  return rendered;
}

describe("RemoteAccessSettings", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each([
    [
      status({
        state: "unconfigured",
        hostname: null,
        owner_email: null,
        last_verified_at: null,
        reason_code: "REMOTE_ACCESS_NOT_CONFIGURED",
        recovery_action: "configure_remote_access",
        desktop_web_url: null,
        access_logout_url: null,
      }),
      "远程访问尚未设置",
    ],
    [
      status({
        state: "pending_verification",
        last_verified_at: null,
        reason_code: "REMOTE_ACCESS_VERIFICATION_PENDING",
        recovery_action: "verify_remote_access",
      }),
      "正在确认远程访问",
    ],
    [status(), "远程访问已就绪"],
    [
      status({
        state: "fault",
        last_verified_at: null,
        reason_code: "REMOTE_ACCESS_ORIGIN_UNAVAILABLE",
        recovery_action: "restart_gateway",
      }),
      "远程访问需要处理",
    ],
  ])("按后端状态展示普通语言：%s", async (payload, label) => {
    const { container } = await renderStatus(payload);

    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(
      container.querySelector('[data-visual-baseline="claude-design-original"]')
    ).toBeInTheDocument();
    expect(container.querySelector('[data-composition="status-card-actions-advanced"]'))
      .toBeInTheDocument();
  });

  it("ready只提供电脑网页与Access登出两个真实动作", async () => {
    await renderStatus(status());

    expect(await screen.findByRole("link", { name: "打开电脑网页" })).toHaveAttribute(
      "href",
      "https://octo.example.com"
    );
    expect(screen.getByRole("link", { name: "退出远程登录" })).toHaveAttribute(
      "href",
      "https://octo.example.com/cdn-cgi/access/logout"
    );
    expect(screen.getByText("o***.example.com")).toBeInTheDocument();
    expect(screen.getByText("o***@example.com")).toBeInTheDocument();
  });

  it("故障恢复使用普通语言，原始reason只进入高级诊断", async () => {
    const { container } = await renderStatus(
      status({
        state: "fault",
        last_verified_at: null,
        reason_code: "REMOTE_ACCESS_ORIGIN_UNAVAILABLE",
        recovery_action: "restart_gateway",
      })
    );

    expect(
      container.querySelector(".remote-access-settings__recovery")
    ).toHaveTextContent("重新启动 Octo 后再检查");
    expect(screen.getByText("高级诊断")).toBeInTheDocument();
    expect(screen.getByText("REMOTE_ACCESS_ORIGIN_UNAVAILABLE")).toBeInTheDocument();
    const ordinary = container.querySelector('[data-content-level="ordinary"]');
    expect(ordinary).not.toHaveTextContent("REMOTE_ACCESS_ORIGIN_UNAVAILABLE");
  });

  it("读取失败提供可聚焦重试且不伪造就绪状态", async () => {
    const loadStatus = vi
      .fn<() => Promise<StatusFixture>>()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(
        status({
          state: "pending_verification",
          last_verified_at: null,
          reason_code: "REMOTE_ACCESS_VERIFICATION_PENDING",
          recovery_action: "verify_remote_access",
        })
      );
    const { RemoteAccessSettings } = await loadSettingsModule();
    render(createElement(RemoteAccessSettings, { loadStatus }));

    expect(await screen.findByText("暂时无法读取远程访问状态")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByText("正在确认远程访问")).toBeInTheDocument();
    expect(loadStatus).toHaveBeenCalledTimes(2);
  });

  it("普通区域不泄漏实现术语或虚构第二套身份产品", async () => {
    const { container } = await renderStatus(status());
    await waitFor(() => expect(screen.getByText("远程访问已就绪")).toBeInTheDocument());
    const ordinary = container.querySelector('[data-content-level="ordinary"]');
    const rendered = ordinary?.textContent?.toLocaleLowerCase() ?? "";

    for (const forbidden of [
      "jwt",
      "service token",
      "provider selector",
      "配对",
      "设备列表",
      "手机浏览器",
      "ios",
    ]) {
      expect(rendered).not.toContain(forbidden);
    }
  });
});
