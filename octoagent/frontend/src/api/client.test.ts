import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  buildFrontDoorSseUrl,
  clearFrontDoorToken,
  fetchControlSnapshot,
  fetchTaskDetail,
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
});
