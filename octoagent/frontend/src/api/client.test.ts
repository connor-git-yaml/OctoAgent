import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  buildFrontDoorSseUrl,
  clearFrontDoorToken,
  fetchControlSnapshot,
  fetchTaskDetail,
  getFrontDoorTokenStorageMode,
  isFrontDoorApiError,
  saveFrontDoorToken,
} from "./client";

describe("api client front-door auth", () => {
  afterEach(() => {
    clearFrontDoorToken();
    vi.restoreAllMocks();
  });

  it("在保存 token 后自动注入 Authorization header", async () => {
    saveFrontDoorToken("frontdoor-secret");
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ contract_version: "1.0.0", resources: {}, registry: {} }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );

    await fetchControlSnapshot();

    const [, init] = fetchMock.mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer frontdoor-secret");
  });

  it("调用方自带 init.headers 时 Authorization 不被覆盖（F140 L1 抓出的回归）", async () => {
    // 真 bug 形态：apiRequest 原来 `{ headers, ...init }` 后展开 init，
    // useChatStream 传 headers: {Content-Type} 时整体覆盖掉
    // Authorization → bearer 模式 /api/chat/send 必 401。
    saveFrontDoorToken("frontdoor-secret");
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("{}", { status: 200 }));

    const { frontDoorRequest } = await import("./client");
    await frontDoorRequest("/api/chat/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: "hi" }),
    });

    const [, init] = fetchMock.mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer frontdoor-secret");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ message: "hi" }));
  });

  it("默认只把 token 保存到 sessionStorage", () => {
    saveFrontDoorToken("frontdoor-secret");

    expect(window.sessionStorage.getItem("octoagent.frontdoorToken.session")).toBe(
      "frontdoor-secret"
    );
    expect(window.localStorage.getItem("octoagent.frontdoorToken")).toBeNull();
    expect(getFrontDoorTokenStorageMode()).toBe("session");
  });

  it("显式持久化时写入 localStorage", () => {
    saveFrontDoorToken("frontdoor-secret", { persist: true });

    expect(window.localStorage.getItem("octoagent.frontdoorToken")).toBe(
      "frontdoor-secret"
    );
    expect(window.sessionStorage.getItem("octoagent.frontdoorToken.session")).toBeNull();
    expect(getFrontDoorTokenStorageMode()).toBe("persistent");
  });

  it("SSE URL 在有 token 时追加 access_token 查询参数", () => {
    saveFrontDoorToken("frontdoor-secret");

    expect(buildFrontDoorSseUrl("/api/stream/task/task-1")).toBe(
      "/api/stream/task/task-1?access_token=frontdoor-secret"
    );
  });

  it("非 2xx 时抛出带 code 的 ApiError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: "FRONT_DOOR_TOKEN_REQUIRED",
            message: "当前实例要求 Bearer Token。",
            hint: "请输入 token 后重试。",
          },
        }),
        {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }
      )
    );

    await expect(fetchTaskDetail("task-1")).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      code: "FRONT_DOOR_TOKEN_REQUIRED",
      message: "当前实例要求 Bearer Token。",
      hint: "请输入 token 后重试。",
    } satisfies Partial<ApiError>);
  });

  it("F134：限流 429 FRONT_DOOR_RATE_LIMITED 被识别为 front-door 错误（走 gate 而非通用错误态）", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: "FRONT_DOOR_RATE_LIMITED",
            message: "认证失败次数过多，请稍后再试。",
            hint: "该来源已被暂时限流，约 300 秒后可重试；使用正确凭证的请求不受影响。",
          },
        }),
        {
          status: 429,
          headers: { "Content-Type": "application/json", "Retry-After": "300" },
        }
      )
    );

    let caught: unknown;
    try {
      await fetchTaskDetail("task-1");
    } catch (error) {
      caught = error;
    }
    expect(caught).toMatchObject({
      name: "ApiError",
      status: 429,
      code: "FRONT_DOOR_RATE_LIMITED",
    } satisfies Partial<ApiError>);
    // verify-first 语义：输入正确 token 即恢复 → 必须归 front-door 域渲染 gate
    expect(isFrontDoorApiError(caught)).toBe(true);
  });

  it("F134：trusted_proxy 侧限流 429（PROXY_RATE_LIMITED）同样归 front-door 域", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: "FRONT_DOOR_PROXY_RATE_LIMITED",
            message: "认证失败次数过多，请稍后再试。",
          },
        }),
        { status: 429, headers: { "Content-Type": "application/json" } }
      )
    );

    let caught: unknown;
    try {
      await fetchTaskDetail("task-1");
    } catch (error) {
      caught = error;
    }
    expect(isFrontDoorApiError(caught)).toBe(true);
  });
});
