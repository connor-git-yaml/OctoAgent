import { afterEach, describe, expect, it, vi } from "vitest";

const ORACLE = "F150_DESKTOP_WEB_SETTINGS_ENTRY_MISSING";
const MODULE_PATH = "./remote-access";

type RemoteAccessModule = {
  readRemoteAccessStatus: () => Promise<Record<string, unknown>>;
};

async function loadRemoteAccessModule(): Promise<RemoteAccessModule> {
  try {
    return (await import(/* @vite-ignore */ MODULE_PATH)) as RemoteAccessModule;
  } catch (error) {
    throw new Error(`${ORACLE}: ${String(error)}`);
  }
}

function status(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    state: "ready",
    hostname: "o***.example.com",
    owner_email: "o***@example.com",
    last_verified_at: "2026-07-24T10:30:00Z",
    reason_code: null,
    recovery_action: null,
    desktop_web_url: "https://octo.example.com",
    access_logout_url: "https://octo.example.com/cdn-cgi/access/logout",
    ...overrides,
  };
}

describe("remote access API adapter", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("只从唯一control-plane endpoint读取exact projection", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(status()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    const { readRemoteAccessStatus } = await loadRemoteAccessModule();

    await expect(readRemoteAccessStatus()).resolves.toEqual(status());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/control/resources/remote-access");
  });

  it.each([
    status({
      state: "unconfigured",
      hostname: null,
      owner_email: null,
      last_verified_at: null,
      reason_code: "REMOTE_ACCESS_NOT_CONFIGURED",
      recovery_action: "configure_remote_access",
      desktop_web_url: null,
      access_logout_url: null,
    }),
    status({
      state: "pending_verification",
      last_verified_at: null,
      reason_code: "REMOTE_ACCESS_VERIFICATION_PENDING",
      recovery_action: "verify_remote_access",
    }),
    status(),
    status({
      state: "fault",
      last_verified_at: null,
      reason_code: "REMOTE_ACCESS_ORIGIN_UNAVAILABLE",
      recovery_action: "restart_gateway",
    }),
  ])("保留后端四态但不在前端重建状态机", async (payload) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    const { readRemoteAccessStatus } = await loadRemoteAccessModule();

    await expect(readRemoteAccessStatus()).resolves.toEqual(payload);
  });

  it.each([
    status({ unexpected: "drift" }),
    status({ state: "unknown" }),
    status({ desktop_web_url: "http://octo.example.com" }),
    status({ owner_email: { raw: "owner@example.com" } }),
  ])("对schema或类型漂移fail closed", async (payload) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    const { readRemoteAccessStatus } = await loadRemoteAccessModule();

    await expect(readRemoteAccessStatus()).rejects.toThrow("远程访问状态格式无效");
  });
});
