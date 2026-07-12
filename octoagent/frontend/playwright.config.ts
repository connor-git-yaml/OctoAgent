/**
 * F140 L1 UI E2E——Playwright 配置（spec D4）。
 *
 * 形态：build 一次 frontend dist → gateway 单进程 serve（SPA + API 同源）→
 * Playwright 直打 gateway 端口。不起独立 vite dev server。
 *
 * webServer ×2（loopback 场景① / bearer 场景②），命令统一
 * `uv run --project <octoagent> --no-sync python <launcher>` + 显式 PYTHONPATH
 * 锁——worktree 下防共享 venv editable 指向漂移（假绿），CI 同树无害。
 *
 * 确定性纪律：workers=1 串行；重试只在 CI 开 1 次（trace 保留可查，本地零
 * 重试让 flake 现形）。
 */
import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  L1_BEARER_PORT,
  L1_BEARER_ROOT,
  L1_FD_TOKEN_VALUE,
  L1_LOOPBACK_PORT,
  L1_LOOPBACK_ROOT,
  l1ServerUrl,
} from "./e2e/support";

const FRONTEND_DIR = dirname(fileURLToPath(import.meta.url));
const OCTOAGENT_DIR = join(FRONTEND_DIR, "..");

/** worktree/CI 双态成立的 PYTHONPATH 锁（六 packages src + gateway src）。 */
const PYTHONPATH_LOCK = [
  "packages/core/src",
  "packages/provider/src",
  "packages/protocol/src",
  "packages/tooling/src",
  "packages/skills/src",
  "packages/policy/src",
  "packages/memory/src",
  "apps/gateway/src",
]
  .map((p) => join(OCTOAGENT_DIR, p))
  .join(":");

const LAUNCHER_CMD =
  "uv run --project . --no-sync python apps/gateway/tests/e2e_live/l1_support/serve_l1_gateway.py";

const SHARED_ENV = {
  PYTHONPATH: PYTHONPATH_LOCK,
  PYTHONNOUSERSITE: "1",
};

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./e2e/.l1-runtime/test-results",
  workers: 1,
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: LAUNCHER_CMD,
      cwd: OCTOAGENT_DIR,
      url: l1ServerUrl("loopback"),
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      // CI 保留 stdout（失败诊断进日志）；本地忽略 structlog 请求噪音
      stdout: process.env.CI ? "pipe" : "ignore",
      stderr: "pipe",
      env: {
        ...SHARED_ENV,
        L1_MODE: "loopback",
        L1_PORT: String(L1_LOOPBACK_PORT),
        L1_ROOT: L1_LOOPBACK_ROOT,
      },
    },
    {
      command: LAUNCHER_CMD,
      cwd: OCTOAGENT_DIR,
      url: l1ServerUrl("bearer"),
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      // CI 保留 stdout（失败诊断进日志）；本地忽略 structlog 请求噪音
      stdout: process.env.CI ? "pipe" : "ignore",
      stderr: "pipe",
      env: {
        ...SHARED_ENV,
        L1_MODE: "bearer",
        L1_PORT: String(L1_BEARER_PORT),
        L1_ROOT: L1_BEARER_ROOT,
        L1_FD_TOKEN: L1_FD_TOKEN_VALUE,
      },
    },
  ],
});
