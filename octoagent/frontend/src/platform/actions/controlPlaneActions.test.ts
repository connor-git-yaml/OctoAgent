import { describe, expect, it } from "vitest";
import {
  buildControlActionRequest,
  resolveActionInvalidationRule,
  shouldRefreshFullSnapshot,
} from "./controlPlaneActions";

describe("controlPlaneActions", () => {
  it("为 project.select 返回 full snapshot invalidation 规则", () => {
    expect(resolveActionInvalidationRule("project.select")).toMatchObject({
      mode: "full-snapshot",
    });
    expect(shouldRefreshFullSnapshot("project.select")).toBe(true);
  });

  it("默认为普通 action 返回 resource ref 刷新规则", () => {
    expect(resolveActionInvalidationRule("wizard.refresh")).toMatchObject({
      mode: "resource-refs",
    });
    expect(shouldRefreshFullSnapshot("wizard.refresh")).toBe(false);
  });

  it("生成标准化 action request envelope", () => {
    const request = buildControlActionRequest("1.0.0", "wizard.refresh", {
      force: true,
    });

    expect(request.contract_version).toBe("1.0.0");
    expect(request.action_id).toBe("wizard.refresh");
    expect(request.surface).toBe("web");
    expect(request.actor.actor_id).toBe("user:web");
    expect(request.params).toEqual({ force: true });
    expect(request.request_id.length).toBeGreaterThan(0);
  });
});
