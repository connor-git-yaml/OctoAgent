/**
 * F145 AC-9【场景③】：审批中心 UI 点接受 → 真 REST accept → 真落盘外部断言。
 *
 * F140 当年 deferred 的审批场景，现在有确定性触发器（F111 候选由 launcher
 * bootstrap 注入，source_hash 与盘上内容对账，见 scenario_brain.provision_
 * approval_center_scenario）。不重跑 discovery——L3 scripted 已全覆盖；本场景
 * 只验「UI 点击 → REST → 落盘 + 候选归零」的接线（spec D5 薄输入原则）。
 *
 * 断言全在 UI 外：
 * - REST：GET /api/behavior/compact/candidates pending 归零
 * - 文件系统：behavior/system/AGENTS.md 逐字节 == 精简版契约常量
 * - 零真 LLM 防线：bomb sentinel 未落盘
 *
 * 一次性消费守卫：本地 reuseExistingServer 下候选被上次运行 accept 掉——此时
 * 验证盘上已是精简版（上次运行的效果仍成立）后 skip；CI 每次 fresh server。
 */
import { expect, test } from "@playwright/test";
import { L1_TESTIDS } from "./selectors";
import {
  L1_COMPACT_COMPACTED_CONTENT,
  L1_COMPACT_FILE_RELPATH,
  assertBombNotTripped,
  fetchCompactPendingCount,
  l1ServerUrl,
  readInstanceFile,
  withFailureMarkerScan,
} from "./support";

test("审批中心点接受：规则精简真落盘 + 候选归零", async ({ page }) => {
  const pending = await fetchCompactPendingCount("loopback");
  if (pending === 0) {
    // 已消费（本地重复 run）：上次 accept 的落盘效果必须仍成立，否则是真失败
    expect(
      readInstanceFile("loopback", L1_COMPACT_FILE_RELPATH),
      "候选缺席时盘上必须已是精简版（否则 provision 或上次 accept 有问题）"
    ).toBe(L1_COMPACT_COMPACTED_CONTENT);
    test.skip(true, "场景③候选已被上次运行消费（重启 L1 server 复跑完整场景）");
    return;
  }

  // --- UI 薄输入：打开审批中心 → 规则精简卡可见 → 点接受 ---
  await page.goto(`${l1ServerUrl("loopback")}/approvals`);
  const card = page.getByTestId(L1_TESTIDS.approvalCompactCard);
  await withFailureMarkerScan(page, async () => {
    await expect(card).toBeVisible({ timeout: 30_000 });
  });
  await page.getByTestId(L1_TESTIDS.approvalCompactAccept).click();

  // ==== 以下断言全部在 UI 外（node 上下文） ====

  // 1) REST：pending 归零（accept 为终态 APPLIED，不再挂列表）
  await expect
    .poll(() => fetchCompactPendingCount("loopback"), { timeout: 15_000 })
    .toBe(0);

  // 2) 文件系统：行为文件真被覆写为精简版（accept 是唯一落盘入口）
  expect(readInstanceFile("loopback", L1_COMPACT_FILE_RELPATH)).toBe(
    L1_COMPACT_COMPACTED_CONTENT
  );

  // 3) 零真 LLM 防线未击穿（审批链路全程不该有任何 provider 解析）
  assertBombNotTripped("loopback");
});
