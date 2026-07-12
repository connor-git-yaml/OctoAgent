/**
 * F140 AC-1【场景①】：chat 薄输入 → 脚本决策环 → 真工具执行 → 外部断言。
 *
 * UI 只做两件事（spec D5）：输入消息 + 等待回复气泡稳定信号（真 EventSource
 * SSE 渲染——jsdom FakeEventSource 测不到的那一跳）。断言全在 UI 外：
 * - wire：POST /api/chat/send 响应的 task_id（Playwright waitForResponse）
 * - REST 事件链：TOOL_CALL_STARTED/COMPLETED(filesystem.write_text) +
 *   MODEL_CALL_* ≥2 对（决策环真跑 2 轮）
 * - 文件系统：工具真实写盘产物逐字节全等（cc-haha desktop-smoke 范式）
 * - 零真 LLM 自证（AC-3）：回复文本 == 脚本常量（若任何环节落 Echo/真
 *   provider，文本不可能是脚本值；服务器侧另有 gate=deny + 空凭证 + bomb）
 */
import { expect, test } from "@playwright/test";
import { L1_TESTIDS } from "./selectors";
import {
  L1_WRITE_FILE_CONTENT,
  L1_WRITE_FILE_RELPATH,
  L1_WRITE_MARKER,
  L1_WRITE_REPLY,
  assertBombNotTripped,
  eventsOfType,
  l1ServerUrl,
  pollTaskSucceeded,
  readInstanceFile,
  toolCallEvents,
  withFailureMarkerScan,
} from "./support";

test("chat 输入驱动脚本决策环：真工具写盘 + 事件链外部断言", async ({ page }) => {
  await page.goto(l1ServerUrl("loopback"));

  // --- UI 薄输入 ---
  const input = page.getByTestId(L1_TESTIDS.chatInput);
  await expect(input).toBeVisible();
  await input.fill(`请把这条笔记写进文件 ${L1_WRITE_MARKER}`);

  const sendResponsePromise = page.waitForResponse(
    (resp) => resp.url().includes("/api/chat/send") && resp.request().method() === "POST"
  );
  await page.getByTestId(L1_TESTIDS.chatSend).click();

  // wire 级取 task_id（外部通道，不读 DOM）
  const sendResponse = await sendResponsePromise;
  expect(sendResponse.ok()).toBe(true);
  const { task_id: taskId } = (await sendResponse.json()) as { task_id: string };
  expect(taskId).toBeTruthy();

  // --- 稳定信号：assistant 气泡出现脚本回复（真 SSE 渲染路径） ---
  await withFailureMarkerScan(page, async () => {
    await expect(
      page
        .getByTestId(L1_TESTIDS.chatMessageAssistant)
        .filter({ hasText: L1_WRITE_REPLY })
    ).toBeVisible({ timeout: 30_000 });
  });

  // ==== 以下断言全部在 UI 外（node 上下文） ====

  // 1) REST 事件链
  const detail = await pollTaskSucceeded("loopback", taskId);
  expect(
    toolCallEvents(detail, "TOOL_CALL_STARTED", "filesystem.write_text"),
    "决策环前半段：脚本 LLM 决策必须真驱动 broker 派发"
  ).toHaveLength(1);
  expect(
    toolCallEvents(detail, "TOOL_CALL_COMPLETED", "filesystem.write_text"),
    "工具执行必须完成"
  ).toHaveLength(1);
  expect(
    toolCallEvents(detail, "TOOL_CALL_FAILED", "filesystem.write_text"),
    "工具不得失败"
  ).toHaveLength(0);
  // 决策环真跑 2 轮（第 1 轮吐 tool_call，第 2 轮消费 feedback 后 complete）
  expect(
    eventsOfType(detail, "MODEL_CALL_STARTED").length,
    "MODEL_CALL_STARTED 应 ≥2（决策环 2 轮）"
  ).toBeGreaterThanOrEqual(2);
  expect(
    eventsOfType(detail, "MODEL_CALL_COMPLETED").length,
    "MODEL_CALL_COMPLETED 应 ≥2"
  ).toBeGreaterThanOrEqual(2);

  // 2) 文件系统：真实写盘产物逐字节全等（默认 project 工作区）
  const written = readInstanceFile("loopback", [
    "projects",
    "default",
    ...L1_WRITE_FILE_RELPATH.split("/"),
  ]);
  expect(written).toBe(L1_WRITE_FILE_CONTENT);

  // 3) AC-3 终局：零真 LLM 防线未被任何路径击穿（含后台 memory-extraction）
  assertBombNotTripped("loopback");
});

// 【会话连续性约束——实测发现并显式归档】每个 L1 server 的 web 会话由服务端
// 恢复（同一 conversation 跨 page.goto 连续），第二条消息的决策环 prompt 含
// 首条消息文本 → marker 会跨测试泄漏进路由。v0.1 纪律：**每个 server 每 run
// 只承载一条发消息的对话链**（loopback 归本场景、bearer 归场景②）。新增发
// 消息的测试须走「+ 新建对话」UI 流（NewSessionModal，v0.2 deferred）或起
// 独立 server。
