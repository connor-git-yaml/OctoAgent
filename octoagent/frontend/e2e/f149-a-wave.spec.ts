import { expect, test } from "@playwright/test";
import {
  L1_A_WAVE_DIAGNOSTIC_EVENT_ID,
  L1_A_WAVE_DIAGNOSTIC_SUMMARY,
  L1_A_WAVE_PRIVATE_VALUE,
  L1_A_WAVE_STATE_EVENT_ID,
  L1_A_WAVE_TASK_ID,
  L1_A_WAVE_TITLE,
  assertBombNotTripped,
  fetchTaskDetail,
  l1ServerUrl,
} from "./support";

const ORACLE = "F149_A_WAVE_BROWSER_CONTRACT_MISSING";

test("任务列表到详情保持真实 SSE、Advanced 焦点与净化剪贴板合同", async ({
  context,
  page,
}) => {
  const origin = l1ServerUrl("loopback");
  await context.grantPermissions(["clipboard-read", "clipboard-write"], {
    origin,
  });

  await page.goto(`${origin}/work`);
  const taskCard = page
    .getByRole("article")
    .filter({ hasText: L1_A_WAVE_TITLE });
  await expect(taskCard, ORACLE).toBeVisible();
  await taskCard.getByRole("link", { name: "打开" }).click();

  await expect(page, ORACLE).toHaveURL(
    new RegExp(`/tasks/${L1_A_WAVE_TASK_ID}$`),
  );
  await expect(
    page.getByRole("heading", { name: L1_A_WAVE_TITLE }),
    ORACLE,
  ).toBeVisible();
  await expect(
    page.getByRole("status", { name: "已连接" }),
    ORACLE,
  ).toBeVisible();

  // 普通详情不得直接暴露完整内部 ID 或 synthetic private value。
  await expect(
    page.getByText(L1_A_WAVE_TASK_ID, { exact: true }),
    ORACLE,
  ).toBeHidden();
  await expect(page.locator("body"), ORACLE).not.toContainText(
    L1_A_WAVE_PRIVATE_VALUE,
  );

  const copyButton = page.getByRole("button", {
    name: /Debug|复制诊断信息/,
  });
  await copyButton.click();
  const clipboardText = await page.evaluate(() =>
    navigator.clipboard.readText(),
  );
  expect(clipboardText, ORACLE).toContain("轮次");
  expect(clipboardText, ORACLE).not.toContain(L1_A_WAVE_TASK_ID);
  expect(clipboardText, ORACLE).not.toContain(L1_A_WAVE_STATE_EVENT_ID);
  expect(clipboardText, ORACLE).not.toContain(L1_A_WAVE_PRIVATE_VALUE);

  await page.getByRole("button", { name: "原始数据" }).click();
  const advanced = page.getByText("高级诊断", { exact: true });
  await expect(advanced, ORACLE).toBeVisible();
  await advanced.click();
  await expect(
    page.getByText(L1_A_WAVE_DIAGNOSTIC_SUMMARY, { exact: false }),
    ORACLE,
  ).toBeVisible();
  await expect(advanced.locator(".."), ORACLE).toContainText("[REDACTED]");
  await advanced.press("Escape");
  await expect(advanced.locator(".."), ORACLE).not.toHaveAttribute("open", "");
  await expect(advanced, ORACLE).toBeFocused();

  const streamPattern = "**/api/stream/task/**";
  await page.route(streamPattern, (route) => route.abort("connectionfailed"));
  await page.reload();
  await expect(
    page.getByRole("status", { name: "连接已断开，正在重试" }),
    ORACLE,
  ).toBeVisible();
  await page.unroute(streamPattern);
  await expect(
    page.getByRole("status", { name: "已连接" }),
    ORACLE,
  ).toBeVisible();

  // UI 外 oracle：REST 原始事件链必须仍是同一个真实 Task。
  const detail = await fetchTaskDetail("loopback", L1_A_WAVE_TASK_ID);
  expect(detail.task.status, ORACLE).toBe("RUNNING");
  expect(
    detail.events.map((event) => [event.event_id, event.task_seq, event.type]),
    ORACLE,
  ).toEqual([
    [L1_A_WAVE_STATE_EVENT_ID, 1, "STATE_TRANSITION"],
    [L1_A_WAVE_DIAGNOSTIC_EVENT_ID, 2, "MODEL_CALL_COMPLETED"],
  ]);
  assertBombNotTripped("loopback");
});
