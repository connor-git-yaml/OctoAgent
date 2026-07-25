import { describe, expect, it } from "vitest";
import { ApiError } from "../../api/client";
import {
  presentAdvancedValue,
  resolveResourcePageState,
  type ResourcePageInput,
} from "./resourcePageState";

const ORACLE = "F149_SHARED_RESOURCE_STATE_MISSING";

function input(overrides: Partial<ResourcePageInput> = {}): ResourcePageInput {
  return {
    loading: false,
    hasContent: true,
    connected: true,
    error: null,
    ...overrides,
  };
}

describe("F149 shared resource page state", () => {
  it.each([
    ["loading", input({ loading: true }), "loading"],
    ["ready", input(), "ready"],
    ["empty", input({ hasContent: false }), "empty"],
    [
      "recoverable",
      input({ error: new Error("temporary") }),
      "recoverable-error",
    ],
    [
      "forbidden",
      input({ error: new ApiError("forbidden", { status: 403 }) }),
      "permission-denied",
    ],
    [
      "not-found",
      input({ error: new ApiError("missing", { status: 404 }) }),
      "not-found",
    ],
    ["disconnected", input({ connected: false }), "disconnected"],
    [
      "conflict",
      input({ error: new ApiError("conflict", { status: 409 }) }),
      "conflict",
    ],
  ])("%s 只产生一个互斥 surface state", (_label, value, expected) => {
    expect(resolveResourcePageState(value), ORACLE).toEqual({
      owner: "surface",
      state: { kind: expected },
    });
  });

  it("401 只交给全局认证 owner，surface 不产生可渲染状态", () => {
    expect(
      resolveResourcePageState(
        input({ error: new ApiError("auth", { status: 401 }) }),
      ),
      ORACLE,
    ).toEqual({
      owner: "global-auth",
      state: null,
    });
  });

  it("Advanced path/command 默认中间截断，只有净化且获权内容可复制", () => {
    const value = "workspace/projects/octoagent/very/long/path/settings.json";
    const available = presentAdvancedValue({
      value,
      kind: "path",
      sensitivity: "operator_sensitive",
      sanitized: true,
      copyPermitted: true,
      maxDisplayLength: 30,
    });

    expect(available, ORACLE).toEqual({
      kind: "available",
      displayValue: "workspace/proj…/settings.json",
      copyValue: value,
      canCopy: true,
    });

    expect(
      presentAdvancedValue({
        value: "octo task inspect --summary",
        kind: "command",
        sensitivity: "operator_sensitive",
        sanitized: true,
        copyPermitted: false,
      }),
      ORACLE,
    ).toMatchObject({
      kind: "available",
      copyValue: null,
      canCopy: false,
    });
  });

  it("绝对宿主路径、未净化内容与 secret-shaped value 均为不可达展示态", () => {
    const candidates = [
      {
        value: "/Users/alice/.octoagent/config.json",
        kind: "path" as const,
        sensitivity: "operator_sensitive" as const,
        sanitized: true,
        copyPermitted: true,
      },
      {
        value: "octo run --token hidden-value",
        kind: "command" as const,
        sensitivity: "operator_sensitive" as const,
        sanitized: true,
        copyPermitted: true,
      },
      {
        value: "workspace/safe/path",
        kind: "path" as const,
        sensitivity: "operator_sensitive" as const,
        sanitized: false,
        copyPermitted: true,
      },
      {
        value: "workspace/safe/path",
        kind: "path" as const,
        sensitivity: "metadata_only" as const,
        sanitized: true,
        copyPermitted: true,
      },
    ];

    for (const candidate of candidates) {
      const result = presentAdvancedValue(candidate);
      expect(result, ORACLE).toEqual({
        kind: "unavailable",
        displayValue: "不可显示",
        copyValue: null,
        canCopy: false,
      });
      expect(JSON.stringify(result), ORACLE).not.toContain(candidate.value);
    }
  });
});
