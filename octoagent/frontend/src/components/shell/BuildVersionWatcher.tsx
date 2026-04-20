/**
 * BuildVersionWatcher — Feature 079 Phase 3。
 *
 * 周期性 poll ``/api/ops/frontend-version`` 对比客户端启动时的 __BUILD_ID__。
 * 发现 mismatch 时弹一个温和的 toast "有新版本，点击刷新"，用户主动点击才
 * reload（加时间戳绕缓存）。**不阻断**正在进行的表单/对话。
 *
 * 与 Feature 078/079 其它组件分工：
 * - RouteErrorBoundary：chunk 404 时在 route 级兜底（事后救援）
 * - RootErrorBoundary：shell 级崩溃兜底
 * - BuildVersionWatcher：前置告警（事前提醒）—— 用户还没点到失效 chunk 就知道
 *
 * dev / 测试：当 __BUILD_ID__ === "dev" 时完全 no-op，避免本地开发期反复打
 * 诊断请求或触发 dev-mode 的 HMR 假阳性。
 */

import { useEffect, useState } from "react";

const POLL_INTERVAL_MS = 10 * 60 * 1000; // 10 min
const FIRST_CHECK_DELAY_MS = 30 * 1000; // 30s — 避免和启动首屏请求同时打
const ENDPOINT = "/api/ops/frontend-version";

interface FrontendVersionPayload {
  build_id: string;
  served_at?: string;
}

async function fetchFrontendVersion(): Promise<FrontendVersionPayload | null> {
  try {
    const resp = await fetch(ENDPOINT, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!resp.ok) {
      return null;
    }
    const data = (await resp.json()) as FrontendVersionPayload;
    if (typeof data?.build_id !== "string") {
      return null;
    }
    return data;
  } catch {
    return null;
  }
}

export default function BuildVersionWatcher() {
  const clientBuildId = typeof __BUILD_ID__ === "string" ? __BUILD_ID__ : "dev";
  const [mismatched, setMismatched] = useState(false);
  const [serverBuildId, setServerBuildId] = useState<string>("");

  useEffect(() => {
    // dev 模式完全 no-op
    if (clientBuildId === "dev" || clientBuildId === "unknown") {
      return;
    }

    let cancelled = false;
    let intervalHandle: ReturnType<typeof setInterval> | null = null;

    async function check() {
      const payload = await fetchFrontendVersion();
      if (cancelled || !payload) {
        return;
      }
      // server 返回 "dev" / "unknown" → 不作为漂移信号
      if (payload.build_id === "dev" || payload.build_id === "unknown") {
        return;
      }
      if (payload.build_id !== clientBuildId) {
        setServerBuildId(payload.build_id);
        setMismatched(true);
      }
    }

    const initialTimer = setTimeout(() => {
      void check();
      intervalHandle = setInterval(() => {
        void check();
      }, POLL_INTERVAL_MS);
    }, FIRST_CHECK_DELAY_MS);

    return () => {
      cancelled = true;
      clearTimeout(initialTimer);
      if (intervalHandle) {
        clearInterval(intervalHandle);
      }
    };
  }, [clientBuildId]);

  function handleReload() {
    const url = new URL(window.location.href);
    url.searchParams.set("_cb", String(Date.now()));
    window.location.href = url.toString();
  }

  function handleDismiss() {
    // 只隐藏本次提示；下次 poll 仍会重新告警（build_id 还是没变的话）
    setMismatched(false);
  }

  if (!mismatched) {
    return null;
  }

  return (
    <div
      className="wb-build-version-toast"
      role="status"
      aria-live="polite"
      data-testid="build-version-toast"
    >
      <div className="wb-build-version-toast-main">
        <strong>有新版本可用</strong>
        <span className="wb-muted">
          你当前打开的是旧版本前端（{clientBuildId}），服务器已经在用{" "}
          {serverBuildId}。刷新即可拿到最新界面，不会影响未保存的草稿。
        </span>
      </div>
      <div className="wb-build-version-toast-actions">
        <button
          type="button"
          className="wb-button wb-button-primary"
          onClick={handleReload}
        >
          刷新
        </button>
        <button
          type="button"
          className="wb-button wb-button-secondary"
          onClick={handleDismiss}
          aria-label="稍后再说"
        >
          稍后
        </button>
      </div>
    </div>
  );
}
