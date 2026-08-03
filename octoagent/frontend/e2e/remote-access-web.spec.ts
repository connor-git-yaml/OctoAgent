/**
 * F150 T013：电脑 Web 经 Cloudflare Access 的真实浏览器语义。
 *
 * 本文件内的 HTTP server 只替代 Cloudflare Access edge：它持有测试专用
 * HttpOnly cookie、登录回跳和登出语义，并把已认证请求逐字节转发给现有
 * hermetic L1 Gateway。它不实现 Octo 认证、JWT verifier、业务 API 或 SSE。
 */
import { expect, test, type Page } from "@playwright/test";
import { createServer, request as requestHttp, type IncomingMessage } from "node:http";
import type { AddressInfo } from "node:net";
import { L1_TESTIDS } from "./selectors";
import {
  L1_WRITE_MARKER,
  L1_WRITE_REPLY,
  assertBombNotTripped,
  l1ServerUrl,
  withFailureMarkerScan,
} from "./support";

const CONTRACT_ORACLE = "F150_DESKTOP_WEB_ACCESS_FLOW_MISSING";
const ACCESS_COOKIE = "f150_access_fixture";

type EdgeFixture = {
  url: string;
  close: () => Promise<void>;
  expireSession: () => void;
  failNextDocument: () => void;
};

function html(title: string, body: string): string {
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">`
    + `<title>${title}</title></head><body><main>${body}</main></body></html>`;
}

function requestCookie(request: IncomingMessage): string {
  const header = request.headers.cookie ?? "";
  return header
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${ACCESS_COOKIE}=`))
    ?.slice(ACCESS_COOKIE.length + 1) ?? "";
}

function loginPage(returnTo: string): string {
  const callback = `/cdn-cgi/access/callback?return_to=${encodeURIComponent(returnTo)}`;
  return html(
    "远程访问登录",
    `<h1>验证远程访问身份</h1>`
      + `<p>这是确定性的本地 Access edge 测试页。</p>`
      + `<a href="${callback}">使用测试身份继续</a>`
  );
}

function writeRedirect(response: import("node:http").ServerResponse, location: string) {
  response.writeHead(302, { Location: location, "Cache-Control": "no-store" });
  response.end();
}

function proxyToGateway(
  incoming: IncomingMessage,
  response: import("node:http").ServerResponse
) {
  const target = new URL(incoming.url ?? "/", l1ServerUrl("loopback"));
  const headers = { ...incoming.headers, host: target.host };
  delete headers.cookie;
  const upstream = requestHttp(
    {
      hostname: target.hostname,
      port: target.port,
      path: `${target.pathname}${target.search}`,
      method: incoming.method,
      headers,
    },
    (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    }
  );
  upstream.on("error", (error) => {
    response.writeHead(502, { "Content-Type": "text/plain; charset=utf-8" });
    response.end(`Gateway unavailable: ${error.message}`);
  });
  incoming.pipe(upstream);
}

async function startAccessEdge(): Promise<EdgeFixture> {
  let session = "session-1";
  let failDocument = false;
  const server = createServer((request, response) => {
    const url = new URL(request.url ?? "/", "http://access.test");
    if (url.pathname === "/cdn-cgi/access/login") {
      response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      response.end(loginPage(url.searchParams.get("return_to") ?? "/"));
      return;
    }
    if (url.pathname === "/cdn-cgi/access/callback") {
      response.setHeader(
        "Set-Cookie",
        `${ACCESS_COOKIE}=${session}; HttpOnly; SameSite=Lax; Path=/`
      );
      writeRedirect(response, url.searchParams.get("return_to") ?? "/");
      return;
    }
    if (url.pathname === "/cdn-cgi/access/logout") {
      response.setHeader(
        "Set-Cookie",
        `${ACCESS_COOKIE}=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/`
      );
      writeRedirect(response, "/cdn-cgi/access/login?return_to=%2F");
      return;
    }
    if (requestCookie(request) !== session) {
      writeRedirect(
        response,
        `/cdn-cgi/access/login?return_to=${encodeURIComponent(url.pathname + url.search)}`
      );
      return;
    }
    if (failDocument && request.headers["sec-fetch-dest"] === "document") {
      failDocument = false;
      response.writeHead(503, { "Content-Type": "text/html; charset=utf-8" });
      response.end(
        html(
          "暂时无法连接",
          "<h1>暂时无法连接</h1><p>入口仍受保护，请稍后重试。</p>"
            + "<button type=\"button\" onclick=\"location.reload()\">重新连接</button>"
        )
      );
      return;
    }
    proxyToGateway(request, response);
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const port = (server.address() as AddressInfo).port;
  return {
    url: `http://127.0.0.1:${port}`,
    close: () =>
      new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      }),
    expireSession: () => {
      session = `expired-${session}`;
    },
    failNextDocument: () => {
      failDocument = true;
    },
  };
}

async function authenticate(page: Page) {
  await expect(page.getByRole("heading", { name: "验证远程访问身份" })).toBeVisible();
  await page.getByRole("link", { name: "使用测试身份继续" }).click();
  await withFailureMarkerScan(page, async () => {
    await expect(page.getByTestId(L1_TESTIDS.chatInput)).toBeVisible();
  });
}

async function runContract(run: () => Promise<void>) {
  try {
    await run();
  } catch (error) {
    throw new Error(`${CONTRACT_ORACLE}: ${String(error)}`);
  }
}

test.describe.serial("F150 desktop Web Access", () => {
  let edge: EdgeFixture;

  test.beforeAll(async () => {
    edge = await startAccessEdge();
  });

  test.afterAll(async () => {
    await edge.close();
  });

  test("登录回跳、刷新、SSE、登出、过期重认证与错误恢复", async ({ page }) => {
    await runContract(async () => {
      await page.goto(edge.url);
      await authenticate(page);

      await page.reload();
      await expect(page.getByTestId(L1_TESTIDS.chatInput)).toBeVisible();

      await page
        .getByTestId(L1_TESTIDS.chatInput)
        .fill(`请回复一条远程访问验证消息 ${L1_WRITE_MARKER}`);
      const assistantReplies = page
        .getByTestId(L1_TESTIDS.chatMessageAssistant)
        .filter({ hasText: L1_WRITE_REPLY });
      const assistantReplyCountBefore = await assistantReplies.count();
      await page.getByTestId(L1_TESTIDS.chatSend).click();
      await expect(assistantReplies).toHaveCount(assistantReplyCountBefore + 1, {
        timeout: 30_000,
      });
      await expect(assistantReplies.last()).toBeVisible();

      await page.goto(`${edge.url}/settings`);
      const remoteAccessRegion = page.getByRole("region", {
        name: "从电脑安全访问 Octo",
      });
      await expect(remoteAccessRegion).toBeVisible();
      await expect(
        remoteAccessRegion.getByRole("heading", { name: "远程访问尚未设置" })
      ).toBeVisible();
      await expect(remoteAccessRegion.getByText("本机使用不受影响。")).toBeVisible();
      await expect(remoteAccessRegion.getByText("高级诊断")).toBeVisible();

      const storedOctoCredential = await page.evaluate(() => ({
        session: sessionStorage.getItem("octoagent.frontdoorToken.session"),
        persistent: localStorage.getItem("octoagent.frontdoorToken"),
      }));
      expect(storedOctoCredential).toEqual({ session: null, persistent: null });

      await page.goto(`${edge.url}/cdn-cgi/access/logout`);
      await authenticate(page);

      edge.expireSession();
      await page.reload();
      await authenticate(page);

      edge.failNextDocument();
      await page.reload();
      await expect(page.getByRole("heading", { name: "暂时无法连接" })).toBeVisible();
      await page.getByRole("button", { name: "重新连接" }).click();
      await expect(page.getByTestId(L1_TESTIDS.chatInput)).toBeVisible();

      const body = await page.textContent("body");
      expect(body ?? "").not.toMatch(/Octo\s*(二次)?登录|配对码|浏览器设备|远程 session/i);
      assertBombNotTripped("loopback");
    });
  });

  test("390px 仅验证 Web 窄窗口 overflow 与键盘焦点", async ({ page }) => {
    await runContract(async () => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto(edge.url);
      await authenticate(page);
      await page.goto(`${edge.url}/settings`);

      await expect
        .poll(() =>
          page.evaluate(() => ({
            clientWidth: document.documentElement.clientWidth,
            scrollWidth: document.documentElement.scrollWidth,
          }))
        )
        .toEqual({ clientWidth: 390, scrollWidth: 390 });

      await page.keyboard.press("Tab");
      const focus = await page.evaluate(() => {
        const element = document.activeElement;
        if (!(element instanceof HTMLElement)) {
          return null;
        }
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName,
          width: rect.width,
          left: rect.left,
          right: rect.right,
        };
      });
      expect(focus).not.toBeNull();
      expect(focus?.width ?? 0).toBeGreaterThan(0);
      expect(focus?.left ?? -1).toBeGreaterThanOrEqual(0);
      expect(focus?.right ?? 391).toBeLessThanOrEqual(390);

      const body = await page.textContent("body");
      expect(body ?? "").not.toMatch(/手机产品已交付|iOS 已交付|移动认证已完成/);
      assertBombNotTripped("loopback");
    });
  });
});
