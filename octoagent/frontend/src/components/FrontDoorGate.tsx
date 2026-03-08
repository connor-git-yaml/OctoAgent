import { useMemo, useState, type FormEvent } from "react";
import {
  ApiError,
  clearFrontDoorToken,
  getFrontDoorToken,
  getFrontDoorTokenStorageMode,
  saveFrontDoorToken,
} from "../api/client";

interface FrontDoorGateProps {
  error: ApiError;
  title: string;
  onRetry: () => void | Promise<void>;
}

const TOKEN_CODES = new Set([
  "FRONT_DOOR_TOKEN_REQUIRED",
  "FRONT_DOOR_TOKEN_INVALID",
]);

const TRUSTED_PROXY_CODES = new Set([
  "FRONT_DOOR_TRUSTED_PROXY_REQUIRED",
  "FRONT_DOOR_PROXY_TOKEN_REQUIRED",
  "FRONT_DOOR_PROXY_TOKEN_INVALID",
  "FRONT_DOOR_PROXY_TOKEN_ENV_MISSING",
]);

function resolveGuidance(error: ApiError): string {
  if (TOKEN_CODES.has(error.code ?? "")) {
    return error.hint ?? "请输入 front-door bearer token 后重试。";
  }
  if (TRUSTED_PROXY_CODES.has(error.code ?? "")) {
    return (
      error.hint ??
      "当前实例要求经受信反向代理访问，请检查代理来源 CIDR 与共享 header 配置。"
    );
  }
  if (
    error.code === "FRONT_DOOR_LOOPBACK_ONLY" ||
    error.code === "FRONT_DOOR_LOOPBACK_PROXY_REJECTED"
  ) {
    return (
      error.hint ??
      "当前实例只允许本机直连访问；如果要对外开放，请改成 bearer 或 trusted_proxy 模式。"
    );
  }
  return error.hint ?? "请检查 front-door 配置后重试。";
}

export default function FrontDoorGate({
  error,
  title,
  onRetry,
}: FrontDoorGateProps) {
  const [tokenInput, setTokenInput] = useState(() => getFrontDoorToken());
  const [persistToken, setPersistToken] = useState(
    () => getFrontDoorTokenStorageMode() === "persistent"
  );
  const [submitting, setSubmitting] = useState(false);
  const requiresToken = useMemo(() => TOKEN_CODES.has(error.code ?? ""), [error.code]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!requiresToken) {
      return;
    }
    setSubmitting(true);
    try {
      saveFrontDoorToken(tokenInput, { persist: persistToken });
      await onRetry();
    } finally {
      setSubmitting(false);
    }
  }

  async function handleClear() {
    clearFrontDoorToken();
    setTokenInput("");
    setSubmitting(true);
    try {
      await onRetry();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="control-empty-state">
      <h1>{title}</h1>
      <p>{error.message}</p>
      <p>{resolveGuidance(error)}</p>
      {requiresToken ? (
        <form
          onSubmit={handleSubmit}
          style={{
            display: "grid",
            gap: "12px",
            width: "min(420px, 100%)",
            marginTop: "16px",
          }}
        >
          <input
            type="password"
            value={tokenInput}
            onChange={(event) => setTokenInput(event.target.value)}
            placeholder="输入 front-door bearer token"
            autoComplete="off"
            style={{
              width: "100%",
              padding: "12px 14px",
              borderRadius: "10px",
              border: "1px solid var(--color-border)",
              background: "var(--color-surface)",
              color: "var(--color-text)",
            }}
          />
          <label
            style={{
              display: "flex",
              gap: "10px",
              alignItems: "center",
              fontSize: "14px",
              color: "var(--color-text-secondary)",
            }}
          >
            <input
              type="checkbox"
              checked={persistToken}
              onChange={(event) => setPersistToken(event.target.checked)}
            />
            在此设备记住 token（默认只保存到当前浏览器会话）
          </label>
          <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
            <button
              type="submit"
              disabled={submitting || !tokenInput.trim()}
              style={{
                padding: "10px 14px",
                borderRadius: "999px",
                border: "none",
                background: "var(--cp-primary)",
                color: "white",
                cursor: submitting || !tokenInput.trim() ? "not-allowed" : "pointer",
                opacity: submitting || !tokenInput.trim() ? 0.6 : 1,
              }}
            >
              保存 Token 并重试
            </button>
            <button
              type="button"
              className="ghost-button"
              onClick={() => {
                void handleClear();
              }}
              disabled={submitting}
            >
              清空本地 Token
            </button>
          </div>
        </form>
      ) : (
        <div style={{ display: "flex", gap: "12px", flexWrap: "wrap", marginTop: "16px" }}>
          <button
            type="button"
            onClick={() => {
              void onRetry();
            }}
            style={{
              padding: "10px 14px",
              borderRadius: "999px",
              border: "none",
              background: "var(--cp-primary)",
              color: "white",
              cursor: "pointer",
            }}
          >
            重试
          </button>
          <button
            type="button"
            className="ghost-button"
            onClick={() => {
              void handleClear();
            }}
          >
            清空本地 Token
          </button>
        </div>
      )}
    </div>
  );
}
