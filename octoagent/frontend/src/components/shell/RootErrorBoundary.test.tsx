import type { JSX } from "react";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RootErrorBoundary from "./RootErrorBoundary";

function ThrowOnRender(): JSX.Element {
  throw new Error("ChunkLoadError: Failed to fetch dynamically imported module");
}

describe("RootErrorBoundary", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("在前端崩溃时显示可恢复的降级页", () => {
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <RootErrorBoundary>
        <ThrowOnRender />
      </RootErrorBoundary>
    );

    expect(
      screen.getByRole("heading", { name: "页面刚更新，请刷新一次" })
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新页面" })).toBeInTheDocument();
    expect(consoleSpy).toHaveBeenCalled();
  });
});
