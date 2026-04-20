import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { execSync } from "node:child_process";

/**
 * Feature 079 Phase 3：前端 build-id 指纹。
 *
 * 每次 `npm run build` 注入一个 `__BUILD_ID__` 常量到客户端代码，同时通过
 * HTML transform 写入 <meta name="app-build-id">；dev 模式固定为 "dev"，避免
 * 开发期触发版本漂移告警。build_id 的组成：
 *   <unix timestamp>-<git short sha>
 * 缺失 git 环境时仅使用 timestamp。
 */
function resolveBuildId(mode: string): string {
  if (mode !== "production") {
    return "dev";
  }
  const timestamp = String(Date.now());
  try {
    const sha = execSync("git rev-parse --short HEAD", {
      stdio: ["ignore", "pipe", "ignore"],
    })
      .toString()
      .trim();
    return sha ? `${timestamp}-${sha}` : timestamp;
  } catch {
    return timestamp;
  }
}

export default defineConfig(({ mode }) => {
  const buildId = resolveBuildId(mode);
  return {
    plugins: [
      react(),
      {
        name: "octoagent-inject-build-id",
        transformIndexHtml(html: string) {
          const meta = `<meta name="app-build-id" content="${buildId}" />`;
          // 插到 </head> 前面，保持 index.html 简洁
          return html.replace("</head>", `    ${meta}\n  </head>`);
        },
      },
    ],
    define: {
      __BUILD_ID__: JSON.stringify(buildId),
    },
    server: {
      proxy: {
        "/api": {
          target: "http://localhost:8000",
          changeOrigin: true,
        },
        "/health": {
          target: "http://localhost:8000",
          changeOrigin: true,
        },
        "/ready": {
          target: "http://localhost:8000",
          changeOrigin: true,
        },
      },
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: "./src/test/setup.ts",
    },
  };
});
