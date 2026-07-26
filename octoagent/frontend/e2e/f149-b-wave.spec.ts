import { expect, test } from "@playwright/test";
import {
  L1_B_WAVE_SKILL_BODY,
  L1_B_WAVE_SKILL_CONTENT,
  L1_B_WAVE_SKILL_NAME,
  assertBombNotTripped,
  fetchSkillDetail,
  l1ServerUrl,
  readInstanceFile,
} from "./support";

const ORACLE = "F149_B_WAVE_BROWSER_CONTRACT_MISSING";

test("Skill文件选择器、模态焦点与安装结果保持一致", async ({ page }) => {
  await page.goto(`${l1ServerUrl("loopback")}/skills`);
  const installTrigger = page.getByRole("button", { name: "安装 Skill" });
  await expect(installTrigger, ORACLE).toBeVisible();
  await installTrigger.click();

  const installDialog = page.getByRole("dialog", { name: "安装 Skill" });
  await expect(installDialog, ORACLE).toBeVisible();
  expect(
    await installDialog.evaluate((element) =>
      element.contains(document.activeElement),
    ),
    ORACLE,
  ).toBe(true);

  const fileInput = installDialog.getByLabel("选择 SKILL.md");
  await fileInput.setInputFiles({
    name: "SKILL.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(L1_B_WAVE_SKILL_CONTENT, "utf-8"),
  });
  await expect(
    installDialog.getByText(L1_B_WAVE_SKILL_NAME, { exact: true }),
    ORACLE,
  ).toBeVisible();

  const installResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/skills") &&
      response.request().method() === "POST",
  );
  await installDialog.getByRole("button", { name: "安装" }).click();
  expect((await installResponse).status(), ORACLE).toBe(201);
  await expect(installDialog, ORACLE).toBeHidden();
  await expect(installTrigger, ORACLE).toBeFocused();

  const skillCard = page
    .getByRole("article")
    .filter({ hasText: L1_B_WAVE_SKILL_NAME });
  await expect(skillCard, ORACLE).toBeVisible();

  const detailTrigger = skillCard.getByRole("button", { name: /查看详情/ });
  await detailTrigger.click();
  const detailDialog = page.getByRole("dialog", {
    name: L1_B_WAVE_SKILL_NAME,
  });
  await expect(detailDialog, ORACLE).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(detailDialog, ORACLE).toBeHidden();
  await expect(detailTrigger, ORACLE).toBeFocused();

  const detail = await fetchSkillDetail("loopback", L1_B_WAVE_SKILL_NAME);
  expect(detail, ORACLE).toMatchObject({
    name: L1_B_WAVE_SKILL_NAME,
    description: "B 波浏览器安装验收",
    source: "user",
    content: L1_B_WAVE_SKILL_BODY,
  });
  expect(
    readInstanceFile("loopback", [
      ".home",
      ".octoagent",
      "skills",
      L1_B_WAVE_SKILL_NAME,
      "SKILL.md",
    ]),
    ORACLE,
  ).toBe(L1_B_WAVE_SKILL_CONTENT);
  assertBombNotTripped("loopback");
});
