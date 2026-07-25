import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type {
  ResourcePageResolution,
  ResourceSurfaceState,
} from "../../domains/shared/resourcePageState";
import ResourceState from "./ResourceState";

const ORACLE = "F149_SHARED_RESOURCE_STATE_MISSING";

function surface(kind: ResourceSurfaceState["kind"]): ResourcePageResolution {
  return {
    owner: "surface",
    state: { kind },
  };
}

describe("F149 shared resource state primitive", () => {
  it("loading 使用可读状态语义，ready 与 global auth 不渲染 surface 占位", () => {
    const loading = render(
      <ResourceState
        resolution={surface("loading")}
        title="正在加载"
        detail="请稍候"
      />,
    );
    expect(
      screen.getByRole("status", { name: "正在加载" }),
      ORACLE,
    ).toHaveTextContent("请稍候");
    loading.unmount();

    const ready = render(
      <ResourceState
        resolution={surface("ready")}
        title="完成"
        detail="不会显示"
      />,
    );
    expect(ready.container, ORACLE).toBeEmptyDOMElement();
    ready.unmount();

    const auth = render(
      <ResourceState
        resolution={{ owner: "global-auth", state: null }}
        title="认证"
        detail="不能由页面显示"
      />,
    );
    expect(auth.container, ORACLE).toBeEmptyDOMElement();
  });

  it.each([
    ["empty", "当前没有内容", "status", false],
    ["permission-denied", "无权查看", "alert", false],
    ["not-found", "找不到内容", "alert", false],
    ["recoverable-error", "暂时无法加载", "alert", true],
    ["disconnected", "连接已断开", "alert", true],
    ["conflict", "内容已发生变化", "alert", true],
  ] as const)("%s 呈现独立且可访问的状态", async (kind, title, role, retry) => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(
      <ResourceState
        resolution={surface(kind)}
        title={title}
        detail="这是当前页面状态的说明。"
        retryLabel="重试"
        onRetry={onRetry}
      />,
    );

    expect(screen.getByRole(role, { name: title }), ORACLE).toHaveTextContent(
      "这是当前页面状态的说明。",
    );
    if (retry) {
      await user.click(screen.getByRole("button", { name: "重试" }));
      expect(onRetry, ORACLE).toHaveBeenCalledOnce();
    } else {
      expect(screen.queryByRole("button"), ORACLE).toBeNull();
    }
  });
});
