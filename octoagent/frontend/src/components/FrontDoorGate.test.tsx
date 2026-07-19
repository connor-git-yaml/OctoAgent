import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import FrontDoorGate from "./FrontDoorGate";
import { ApiError, clearFrontDoorToken } from "../api/client";

/**
 * F134：限流 429 的 gate 路由分叉直断言（Opus 增量复核 P3-1）。
 * client.test.ts 已证 isFrontDoorApiError 纳入两个 429 code；本套件补
 * gate 渲染分叉——verify-first 语义下 bearer 版限流必须给 token 输入框
 * （输对即恢复），trusted_proxy 版必须给代理指引（bearer token 对它无用）。
 */

function gateError(code: string, hint?: string): ApiError {
  return new ApiError("认证失败次数过多，请稍后再试。", {
    status: 429,
    code,
    hint,
  });
}

describe("FrontDoorGate 限流 429 路由（F134）", () => {
  afterEach(() => {
    clearFrontDoorToken();
    vi.restoreAllMocks();
  });

  it("bearer 版限流 FRONT_DOOR_RATE_LIMITED → 渲染 token 输入框（可恢复）", () => {
    render(
      <FrontDoorGate
        error={gateError("FRONT_DOOR_RATE_LIMITED", "约 300 秒后可重试")}
        title="需要验证"
        onRetry={() => {}}
      />
    );
    // verify-first 恢复路径：token 输入框在场
    expect(screen.getByTestId("frontdoor-token-input")).toBeInTheDocument();
    expect(screen.getByTestId("frontdoor-submit")).toBeInTheDocument();
    // 后端 hint（含剩余秒数）透传给用户
    expect(screen.getByText(/约 300 秒后可重试/)).toBeInTheDocument();
  });

  it("proxy 版限流 FRONT_DOOR_PROXY_RATE_LIMITED → 不渲染 token 输入框（代理指引）", () => {
    render(
      <FrontDoorGate
        error={gateError("FRONT_DOOR_PROXY_RATE_LIMITED")}
        title="需要验证"
        onRetry={() => {}}
      />
    );
    // bearer token 输入框对 trusted_proxy 无用——不得出现，避免误导
    expect(screen.queryByTestId("frontdoor-token-input")).not.toBeInTheDocument();
  });
});
