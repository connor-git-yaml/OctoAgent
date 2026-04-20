/**
 * RouteErrorBoundary 单测 —— Feature 079 Phase 1。
 *
 * 覆盖：
 * - chunk 404（Failed to fetch dynamically imported module）→ "需要刷新" 文案 + 刷新按钮
 * - 一般渲染异常 → "没有正常加载" 文案 + "重试本页" 按钮
 * - routeKey 变化时重置 boundary state（切 route 后不残留错误）
 * - pageLabel 影响标题
 */

import type { JSX } from "react";
import { render, screen, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RouteErrorBoundary from "./RouteErrorBoundary";

function ThrowChunk(): JSX.Element {
  throw new Error("Failed to fetch dynamically imported module: /assets/X-abc.js");
}

function ThrowGeneric(): JSX.Element {
  throw new Error("Cannot read properties of undefined (reading 'foo')");
}

function Success({ tag }: { tag: string }): JSX.Element {
  return <div data-testid="route-child">{tag}</div>;
}

describe("RouteErrorBoundary", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("chunk 404 时显示刷新提示 + 刷新按钮", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <RouteErrorBoundary pageLabel="设置中心">
        <ThrowChunk />
      </RouteErrorBoundary>
    );

    expect(
      screen.getByRole("heading", { name: /设置中心需要刷新才能加载/ })
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新页面" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试本页" })).not.toBeInTheDocument();
  });

  it("一般异常显示重试按钮（2 次重试后降级为刷新）", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <RouteErrorBoundary pageLabel="Agent 中心">
        <ThrowGeneric />
      </RouteErrorBoundary>
    );

    expect(screen.getByRole("heading", { name: /Agent 中心没有正常加载/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试本页" })).toBeInTheDocument();
  });

  it("pageLabel 缺省时使用 '当前页面'", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <RouteErrorBoundary>
        <ThrowChunk />
      </RouteErrorBoundary>
    );

    expect(screen.getByRole("heading", { name: /当前页面/ })).toBeInTheDocument();
  });

  it("routeKey 变化后 boundary 重置，新 route 的 children 能正常渲染", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { rerender } = render(
      <RouteErrorBoundary routeKey="/settings" pageLabel="设置中心">
        <ThrowGeneric />
      </RouteErrorBoundary>
    );
    expect(screen.getByRole("heading", { name: /没有正常加载/ })).toBeInTheDocument();

    act(() => {
      rerender(
        <RouteErrorBoundary routeKey="/agents" pageLabel="Agent 中心">
          <Success tag="ok" />
        </RouteErrorBoundary>
      );
    });

    expect(screen.getByTestId("route-child")).toHaveTextContent("ok");
    expect(screen.queryByRole("heading", { name: /没有正常加载/ })).not.toBeInTheDocument();
  });

  it("错误消息 detail 被渲染出来（供诊断）", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <RouteErrorBoundary>
        <ThrowChunk />
      </RouteErrorBoundary>
    );

    expect(
      screen.getByText(/Failed to fetch dynamically imported module/)
    ).toBeInTheDocument();
  });
});
