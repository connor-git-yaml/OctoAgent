import { expect, test, type Locator, type Page } from "@playwright/test";
import { L1_A_WAVE_TITLE, l1ServerUrl } from "./support";

const ORACLE = "F158_WEB_SURFACE_SCENARIO_CONTRACT_MISSING";

test.use({
  colorScheme: "dark",
  viewport: { width: 1440, height: 900 },
});

async function openSurface(
  page: Page,
  path: string,
  root: string,
  heading: string,
): Promise<Locator> {
  await page.goto(`${l1ServerUrl("loopback")}${path}`);
  const surface = page.locator(root);
  await expect(surface, ORACLE).toBeVisible();
  await expect(
    surface.getByRole("heading", { name: heading, level: 1 }),
    ORACLE,
  ).toBeVisible();
  return surface;
}

test("审批中心呈现普通用户可理解的空态与三类待办概览", async ({ page }) => {
  const surface = await openSurface(
    page,
    "/approvals",
    ".f149-approval-page",
    "审批中心",
  );

  await expect(surface, ORACLE).toContainText("暂无待处理提议");
  await expect(surface, ORACLE).toContainText("新记忆");
  await expect(surface, ORACLE).toContainText("记忆整合");
  await expect(surface, ORACLE).toContainText("行为精简");
  await expect(surface, ORACLE).not.toContainText("proposal_id");
});

test("任务列表筛选状态可切换且真实运行任务保持可达", async ({ page }) => {
  const surface = await openSurface(page, "/work", ".f149-task-page", "任务");
  const runningFilter = surface.getByRole("button", { name: "进行中" });
  const allFilter = surface.getByRole("button", { name: "全部" });

  await runningFilter.click();
  await expect(runningFilter, ORACLE).toHaveAttribute("aria-pressed", "true");
  await expect(
    surface.getByRole("article").filter({ hasText: L1_A_WAVE_TITLE }),
    ORACLE,
  ).toBeVisible();

  await allFilter.click();
  await expect(allFilter, ORACLE).toHaveAttribute("aria-pressed", "true");
});

test("定时任务可以暂停并恢复，不把测试状态留在运行实例", async ({ page }) => {
  const surface = await openSurface(
    page,
    "/automation",
    ".f149-automation-page",
    "定时任务",
  );
  const initialPauseCount = await surface
    .getByRole("button", { name: "暂停" })
    .count();
  expect(initialPauseCount, ORACLE).toBeGreaterThan(0);

  await surface.getByRole("button", { name: "暂停" }).first().click();
  const resume = surface.getByRole("button", { name: "恢复" }).first();
  try {
    await expect(resume, ORACLE).toBeVisible();
  } finally {
    if (await resume.isVisible().catch(() => false)) {
      await resume.click();
    }
  }
  await expect(
    surface.getByRole("button", { name: "暂停" }),
    ORACLE,
  ).toHaveCount(initialPauseCount);
});

test("设置页远程访问状态可刷新且不硬编码个人部署域名", async ({ page }) => {
  const surface = await openSurface(
    page,
    "/settings",
    ".f149-settings-page",
    "设置",
  );
  const remoteAccess = surface.locator(".remote-access-settings");

  await expect(remoteAccess, ORACLE).toContainText("从电脑安全访问 Octo");
  await expect(remoteAccess, ORACLE).toContainText("远程访问尚未设置");
  await remoteAccess.getByRole("button", { name: "重新检查" }).click();
  await expect(remoteAccess, ORACLE).toContainText("远程访问尚未设置");
  await expect(remoteAccess, ORACLE).not.toContainText("maojiwang.work");

  const advanced = remoteAccess.locator("details");
  await advanced.locator("summary").click();
  await expect(advanced, ORACLE).toHaveAttribute("open", "");
  await expect(advanced, ORACLE).toContainText("诊断代码");
});

test("智能体创建入口可进入模板选择并安全取消", async ({ page }) => {
  const surface = await openSurface(
    page,
    "/agents",
    ".f149-agent-page",
    "智能体",
  );
  const trigger = surface.getByRole("button", { name: "新建 Agent" });

  await trigger.click();
  const createPanelCopy = page.getByText("先选一个起点", { exact: false });
  await expect(createPanelCopy, ORACLE).toBeVisible();
  const cancel = page.getByRole("button", { name: "先不创建" });
  await expect(cancel, ORACLE).toBeVisible();
  await cancel.click();
  await expect(createPanelCopy, ORACLE).toBeHidden();
});

test("记忆筛选可编辑，高级诊断可关闭并归还焦点", async ({ page }) => {
  const surface = await openSurface(
    page,
    "/memory",
    ".f149-memory-page",
    "还没有记忆内容",
  );
  const query = surface.getByPlaceholder("例如：客户偏好、发布计划、数据库");
  await query.fill("发布计划");
  await expect(query, ORACLE).toHaveValue("发布计划");
  await surface.getByRole("button", { name: "清空筛选" }).click();
  await expect(query, ORACLE).toHaveValue("");

  const advancedTrigger = surface.getByRole("button", {
    name: "高级 · 检索与记录信息",
  });
  await advancedTrigger.click();
  const dialog = page.getByRole("dialog", { name: "检索与记录信息" });
  await expect(dialog, ORACLE).toBeVisible();
  await expect(dialog, ORACLE).toContainText("检索实现");
  await page.keyboard.press("Escape");
  await expect(dialog, ORACLE).toBeHidden();
  await expect(advancedTrigger, ORACLE).toBeFocused();
});

test("文件工作台在任务产物与工作区版本之间保持可逆切换", async ({ page }) => {
  const surface = await openSurface(
    page,
    "/files",
    ".f149-files-page",
    "文件工作台",
  );
  const artifacts = surface.getByRole("button", { name: "任务产物" });
  const workspace = surface.getByRole("button", { name: "工作区版本" });

  await workspace.click();
  await expect(workspace, ORACLE).toHaveAttribute("aria-pressed", "true");
  await expect(surface, ORACLE).toContainText(/工作区版本|正在加载/);
  await artifacts.click();
  await expect(artifacts, ORACLE).toHaveAttribute("aria-pressed", "true");
  await expect(
    surface.getByRole("region", { name: "任务产物" }),
    ORACLE,
  ).toBeVisible();
});

test("技能安装对话框保持焦点闭环且不在取消时写入技能", async ({ page }) => {
  const surface = await openSurface(
    page,
    "/skills",
    ".f149-skills-page",
    "让常用工作，成为可复用的能力。",
  );
  const trigger = surface.getByRole("button", { name: "安装 Skill" });

  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "安装 Skill" });
  await expect(dialog, ORACLE).toBeVisible();
  expect(
    await dialog.evaluate((element) => element.contains(document.activeElement)),
    ORACLE,
  ).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog, ORACLE).toBeHidden();
  await expect(trigger, ORACLE).toBeFocused();
});

test("MCP 手动添加保持高级边界、Escape 关闭与焦点回归", async ({ page }) => {
  const surface = await openSurface(
    page,
    "/mcp",
    ".f149-mcp-page",
    "外部服务",
  );
  const trigger = surface.getByRole("button", { name: "手动添加" });

  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "手动添加服务" });
  await expect(dialog, ORACLE).toBeVisible();
  await expect(dialog, ORACLE).toContainText("访问密钥");
  await page.keyboard.press("Escape");
  await expect(dialog, ORACLE).toBeHidden();
  await expect(trigger, ORACLE).toBeFocused();
});
