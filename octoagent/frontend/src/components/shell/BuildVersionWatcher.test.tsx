/**
 * BuildVersionWatcher —— Feature 079 Phase 3。
 *
 * 覆盖：
 * - 客户端 build_id 是 "dev" 时完全不 poll
 * - 服务器返回不同 build_id → 显示 toast
 * - 服务器返回 "dev" / "unknown" → 不告警
 * - 点击稍后 → 隐藏 toast
 */

import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import BuildVersionWatcher from "./BuildVersionWatcher";

function mockFetchReturning(payload: unknown, ok = true) {
  return vi.fn().mockResolvedValue({
    ok,
    json: () => Promise.resolve(payload),
  });
}

describe("BuildVersionWatcher", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("客户端 build_id === 'dev' 时不 poll 不渲染", () => {
    // Vite 在 dev/test 模式下 __BUILD_ID__ 已经是 "dev"
    const fetchSpy = vi.spyOn(globalThis, "fetch" as never);
    const { container } = render(<BuildVersionWatcher />);
    expect(container.textContent).toBe("");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("客户端非 dev 且 server 返回同版本时不告警", async () => {
    vi.useFakeTimers();
    // 在非 dev 模式下，__BUILD_ID__ 是 build 常量；这里 mock 全局 define
    (globalThis as Record<string, unknown>).__BUILD_ID__ = "build-X";
    const fetchMock = mockFetchReturning({ build_id: "build-X" });
    vi.stubGlobal("fetch", fetchMock);

    render(<BuildVersionWatcher />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(screen.queryByTestId("build-version-toast")).not.toBeInTheDocument();
    (globalThis as Record<string, unknown>).__BUILD_ID__ = "dev";
  });

  it("server 返回不同 build_id 时显示刷新 toast", async () => {
    vi.useFakeTimers();
    (globalThis as Record<string, unknown>).__BUILD_ID__ = "build-client";
    const fetchMock = mockFetchReturning({ build_id: "build-server-new" });
    vi.stubGlobal("fetch", fetchMock);

    render(<BuildVersionWatcher />);
    // 等 FIRST_CHECK_DELAY_MS 定时器 + fetch 的 microtask 一起刷完
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    // fetch 返回后还要再跑一轮 setState flushing
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.queryByTestId("build-version-toast")).toBeInTheDocument();
    expect(screen.getByText(/有新版本可用/)).toBeInTheDocument();
    expect(screen.getByText(/build-client/)).toBeInTheDocument();
    expect(screen.getByText(/build-server-new/)).toBeInTheDocument();
    (globalThis as Record<string, unknown>).__BUILD_ID__ = "dev";
  });

  it("server 返回 'dev' 时即便客户端已 build 也不告警", async () => {
    vi.useFakeTimers();
    (globalThis as Record<string, unknown>).__BUILD_ID__ = "build-client";
    const fetchMock = mockFetchReturning({ build_id: "dev" });
    vi.stubGlobal("fetch", fetchMock);

    render(<BuildVersionWatcher />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(screen.queryByTestId("build-version-toast")).not.toBeInTheDocument();
    (globalThis as Record<string, unknown>).__BUILD_ID__ = "dev";
  });

  it("点击稍后按钮隐藏 toast（下次 poll 可能再弹）", async () => {
    vi.useFakeTimers();
    (globalThis as Record<string, unknown>).__BUILD_ID__ = "build-client";
    vi.stubGlobal("fetch", mockFetchReturning({ build_id: "build-server-new" }));

    render(<BuildVersionWatcher />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.queryByTestId("build-version-toast")).toBeInTheDocument();

    vi.useRealTimers();
    await userEvent.setup({ delay: null }).click(
      screen.getByRole("button", { name: /稍后/ })
    );
    await waitFor(() =>
      expect(screen.queryByTestId("build-version-toast")).not.toBeInTheDocument()
    );
    (globalThis as Record<string, unknown>).__BUILD_ID__ = "dev";
  });
});
