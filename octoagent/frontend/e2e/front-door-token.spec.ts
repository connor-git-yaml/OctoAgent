/**
 * F140 AC-2【场景②】：FrontDoorGate token 流程（bearer 模式）——审计判定
 * 「真正 UI-only、无 API 等价」的第一名（F130 手机远程第一屏，此前 100% 手工验）。
 *
 * 覆盖链：SPA 静态可达（不受 guard）→ API 401 → FrontDoorGate 渲染 → 输 token
 * 保存重试 → 界面解锁 → 发消息全链路成功（bearer 下 SSE 走 access_token query
 * 鉴权——真 EventSource 无法带 Authorization header 的那条真实路径）。
 *
 * storage 断言经 page.evaluate 取值、node 侧 expect（spec D5 允许的「读状态」，
 * 正是审计「必须留浏览器」清单的 storage token 项）。
 */
import { expect, test } from "@playwright/test";
import { L1_TESTIDS } from "./selectors";
import {
  L1_FD_TOKEN_VALUE,
  assertBombNotTripped,
  L1_WRITE_FILE_CONTENT,
  L1_WRITE_FILE_RELPATH,
  L1_WRITE_MARKER,
  L1_WRITE_REPLY,
  l1ServerUrl,
  pollTaskSucceeded,
  readInstanceFile,
  toolCallEvents,
  withFailureMarkerScan,
} from "./support";

// session/persistent 两种 storage key（api/client.ts 字面同步）
const SESSION_KEY = "octoagent.frontdoorToken.session";
const PERSISTENT_KEY = "octoagent.frontdoorToken";

test("bearer 模式：gate 渲染 → 输 token 解锁 → 消息全链路（SSE query 鉴权）", async ({
  page,
}) => {
  await page.goto(l1ServerUrl("bearer"));

  // --- gate 渲染（API 401 FRONT_DOOR_TOKEN_REQUIRED → SPA 渲染 FrontDoorGate） ---
  const tokenInput = page.getByTestId(L1_TESTIDS.frontdoorTokenInput);
  await expect(tokenInput).toBeVisible({ timeout: 15_000 });

  // --- 薄输入：填 token（默认不勾选「记住」= session 模式）+ 保存重试 ---
  await tokenInput.fill(L1_FD_TOKEN_VALUE);
  await page.getByTestId(L1_TESTIDS.frontdoorSubmit).click();

  // --- 解锁稳定信号：聊天输入框可见 ---
  await withFailureMarkerScan(page, async () => {
    await expect(page.getByTestId(L1_TESTIDS.chatInput)).toBeVisible({
      timeout: 15_000,
    });
  });

  // --- storage 模式外部断言：默认 session-only，不落 localStorage ---
  const storage = await page.evaluate(
    ([sessionKey, persistentKey]) => ({
      session: window.sessionStorage.getItem(sessionKey),
      persistent: window.localStorage.getItem(persistentKey),
    }),
    [SESSION_KEY, PERSISTENT_KEY] as const
  );
  expect(storage.session, "token 默认保存到 sessionStorage").toBe(L1_FD_TOKEN_VALUE);
  expect(storage.persistent, "未勾选「记住」不得落 localStorage").toBeNull();

  // --- 解锁后发消息：bearer 下全链路（含 SSE access_token query 鉴权） ---
  const input = page.getByTestId(L1_TESTIDS.chatInput);
  await input.fill(`请把这条笔记写进文件 ${L1_WRITE_MARKER}`);
  const sendResponsePromise = page.waitForResponse(
    (resp) => resp.url().includes("/api/chat/send") && resp.request().method() === "POST"
  );
  await page.getByTestId(L1_TESTIDS.chatSend).click();
  const sendResponse = await sendResponsePromise;
  expect(
    sendResponse.ok(),
    `带 token 后 /api/chat/send 应 2xx，实际 ${sendResponse.status()}：${await sendResponse
      .text()
      .catch(() => "<no body>")}`
  ).toBe(true);
  const { task_id: taskId } = (await sendResponse.json()) as { task_id: string };

  await withFailureMarkerScan(page, async () => {
    await expect(
      page
        .getByTestId(L1_TESTIDS.chatMessageAssistant)
        .filter({ hasText: L1_WRITE_REPLY })
    ).toBeVisible({ timeout: 30_000 });
  });

  // ==== UI 外断言（bearer server 实例） ====
  const detail = await pollTaskSucceeded("bearer", taskId);
  expect(
    toolCallEvents(detail, "TOOL_CALL_COMPLETED", "filesystem.write_text")
  ).toHaveLength(1);
  const written = readInstanceFile("bearer", [
    "projects",
    "default",
    ...L1_WRITE_FILE_RELPATH.split("/"),
  ]);
  expect(written).toBe(L1_WRITE_FILE_CONTENT);

  // AC-3 终局：零真 LLM 防线未被任何路径击穿
  assertBombNotTripped("bearer");
});

test("勾选「记住 token」→ 持久化到 localStorage（persistent 模式）", async ({
  page,
}) => {
  await page.goto(l1ServerUrl("bearer"));

  const tokenInput = page.getByTestId(L1_TESTIDS.frontdoorTokenInput);
  await expect(tokenInput).toBeVisible({ timeout: 15_000 });

  await tokenInput.fill(L1_FD_TOKEN_VALUE);
  await page.getByTestId(L1_TESTIDS.frontdoorPersistCheckbox).check();
  await page.getByTestId(L1_TESTIDS.frontdoorSubmit).click();

  await withFailureMarkerScan(page, async () => {
    await expect(page.getByTestId(L1_TESTIDS.chatInput)).toBeVisible({
      timeout: 15_000,
    });
  });

  const storage = await page.evaluate(
    ([sessionKey, persistentKey]) => ({
      session: window.sessionStorage.getItem(sessionKey),
      persistent: window.localStorage.getItem(persistentKey),
    }),
    [SESSION_KEY, PERSISTENT_KEY] as const
  );
  expect(storage.persistent, "勾选后 token 落 localStorage").toBe(L1_FD_TOKEN_VALUE);
  expect(storage.session, "persistent 模式不再占用 session key").toBeNull();

  assertBombNotTripped("bearer");
});
