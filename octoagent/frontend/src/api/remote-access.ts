import { frontDoorRequest } from "./client";

type RemoteAccessState =
  | "unconfigured"
  | "pending_verification"
  | "ready"
  | "fault";

type RemoteAccessStatus = {
  state: RemoteAccessState;
  hostname: string | null;
  owner_email: string | null;
  last_verified_at: string | null;
  reason_code: string | null;
  recovery_action: string | null;
  desktop_web_url: string | null;
  access_logout_url: string | null;
};

const _STATUS_FIELDS = new Set([
  "state",
  "hostname",
  "owner_email",
  "last_verified_at",
  "reason_code",
  "recovery_action",
  "desktop_web_url",
  "access_logout_url",
]);
const _STATUS_STATES = new Set<RemoteAccessState>([
  "unconfigured",
  "pending_verification",
  "ready",
  "fault",
]);

function _isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function _isHttpsUrl(value: unknown): value is string | null {
  if (value === null) {
    return true;
  }
  if (typeof value !== "string") {
    return false;
  }
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

function _parseRemoteAccessStatus(value: unknown): RemoteAccessStatus {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("远程访问状态格式无效");
  }
  const payload = value as Record<string, unknown>;
  const fields = Object.keys(payload);
  if (
    fields.length !== _STATUS_FIELDS.size ||
    fields.some((field) => !_STATUS_FIELDS.has(field)) ||
    !_STATUS_STATES.has(payload.state as RemoteAccessState) ||
    !_isNullableString(payload.hostname) ||
    !_isNullableString(payload.owner_email) ||
    !_isNullableString(payload.last_verified_at) ||
    !_isNullableString(payload.reason_code) ||
    !_isNullableString(payload.recovery_action) ||
    !_isHttpsUrl(payload.desktop_web_url) ||
    !_isHttpsUrl(payload.access_logout_url)
  ) {
    throw new Error("远程访问状态格式无效");
  }
  return payload as RemoteAccessStatus;
}

export async function readRemoteAccessStatus(): Promise<RemoteAccessStatus> {
  const response = await frontDoorRequest("/api/control/resources/remote-access", {
    method: "GET",
  });
  if (!response.ok) {
    throw new Error("暂时无法读取远程访问状态");
  }
  return _parseRemoteAccessStatus(await response.json());
}
