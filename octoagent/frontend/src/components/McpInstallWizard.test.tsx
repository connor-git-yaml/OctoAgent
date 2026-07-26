import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import McpInstallWizard from "./McpInstallWizard";

function envelope(actionId: string, data: Record<string, unknown>) {
  return {
    action_id: actionId,
    code: "OK",
    data,
    message: "ok",
    status: "completed",
  };
}

async function reachInstalling(
  submitAction: ReturnType<typeof vi.fn>,
): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  fireEvent.change(screen.getByLabelText("npm 包名"), {
    target: { value: "@octo/calendar-mcp" },
  });
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  submitAction.mockResolvedValueOnce(
    envelope("mcp_provider.install", {
      server_id: "calendar",
      task_id: "task-install-1",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "确认安装" }));
  await act(async () => undefined);
  expect(screen.getByText("正在安装")).toBeInTheDocument();
}

afterEach(() => {
  vi.useRealTimers();
});

describe("McpInstallWizard v2", () => {
  it("直接消费 generated action 结果，不保留手写 InstallResult", async () => {
    const source = await import("./McpInstallWizard?raw");
    expect(source.default).not.toContain("interface InstallResult");
    expect(source.default).toContain(
      'F149ActionResultById["mcp_provider.install_status"]',
    );
  });

  it("状态检查断线必须可见，并保留任务供用户重新检查", async () => {
    vi.useFakeTimers();
    const submitAction = vi.fn();
    render(
      <McpInstallWizard
        open
        onClose={vi.fn()}
        onComplete={vi.fn()}
        submitAction={submitAction}
      />,
    );
    await reachInstalling(submitAction);
    submitAction.mockRejectedValueOnce(new Error("network disconnected"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(screen.getByText("状态检查暂时中断")).toBeInTheDocument();
    expect(screen.queryByText(/network disconnected/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新检查" })).toBeEnabled();
  });

  it("状态确认超时给出恢复动作，不承诺固定分钟或轮询频率", async () => {
    vi.useFakeTimers();
    const submitAction = vi.fn();
    render(
      <McpInstallWizard
        open
        onClose={vi.fn()}
        onComplete={vi.fn()}
        submitAction={submitAction}
      />,
    );
    await reachInstalling(submitAction);
    submitAction.mockResolvedValue(
      envelope("mcp_provider.install_status", {
        error: null,
        progress_message: "仍在准备",
        result: null,
        status: "running",
        task_id: "task-install-1",
      }),
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(310_000);
    });
    expect(screen.getByText("状态确认超时")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新检查" })).toBeEnabled();
    expect(
      screen.queryByText(/\d+\s*分钟|每\s*\d+\s*秒/),
    ).not.toBeInTheDocument();
  });

  it("关闭会清除临时密钥、取消检查并归还焦点", async () => {
    vi.useFakeTimers();
    const submitAction = vi.fn();
    const triggerRef = createRef<HTMLButtonElement>();

    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button ref={triggerRef} type="button" onClick={() => setOpen(true)}>
            安装服务
          </button>
          <McpInstallWizard
            open={open}
            onClose={() => setOpen(false)}
            onComplete={vi.fn()}
            submitAction={submitAction}
            returnFocusRef={triggerRef}
          />
        </>
      );
    }

    vi.useRealTimers();
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "安装服务" }));
    await user.click(screen.getByRole("button", { name: "下一步" }));
    await user.type(screen.getByLabelText("npm 包名"), "@octo/calendar-mcp");
    await user.type(
      screen.getByLabelText("访问密钥（可选）"),
      "temporary-secret",
    );
    await user.click(screen.getByRole("button", { name: "关闭" }));
    await waitFor(() => expect(triggerRef.current).toHaveFocus());

    await user.click(screen.getByRole("button", { name: "安装服务" }));
    await user.click(screen.getByRole("button", { name: "下一步" }));
    expect(screen.getByLabelText("访问密钥（可选）")).toHaveValue("");
  });

  it("成功结果只在普通区显示用户事实，技术字段留在高级区", async () => {
    vi.useFakeTimers();
    const submitAction = vi.fn();
    render(
      <McpInstallWizard
        open
        onClose={vi.fn()}
        onComplete={vi.fn()}
        submitAction={submitAction}
      />,
    );
    await reachInstalling(submitAction);
    submitAction.mockResolvedValueOnce(
      envelope("mcp_provider.install_status", {
        error: null,
        progress_message: "完成",
        result: {
          command: "uvx hidden-command",
          server_id: "calendar",
          tools: [{ description: "列出日程", name: "calendar.list" }],
          tools_count: 1,
          version: "1.0.0",
        },
        status: "completed",
        task_id: "task-install-1",
      }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(screen.getByText("安装成功")).toBeInTheDocument();
    expect(screen.getByText("发现 1 个可用工具")).toBeInTheDocument();
    expect(screen.queryByText(/hidden-command/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "高级 · 安装详情" }));
    expect(
      within(screen.getByRole("region", { name: "安装详情" })).getByText(
        "calendar.list",
      ),
    ).toBeInTheDocument();
  });
});
