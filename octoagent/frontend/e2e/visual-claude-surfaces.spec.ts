import { expect, test, type Page } from "@playwright/test";
import { L1_A_WAVE_TASK_ID, l1ServerUrl } from "./support";

const ORACLE = "F158_CLAUDE_EARLY_SURFACE_VISUAL_CONTRACT_MISSING";

interface SurfaceVisualContract {
  key: string;
  path: string;
  root: string;
  hero: string;
  title: string;
  maxHeroHeight: number;
}

const SURFACES: readonly SurfaceVisualContract[] = [
  {
    key: "approvals",
    path: "/approvals",
    root: ".f149-approval-page",
    hero: ".f149-approval-hero",
    title: ".f149-approval-hero h1",
    maxHeroHeight: 310,
  },
  {
    key: "tasks",
    path: "/work",
    root: ".f149-task-page",
    hero: ".f149-task-hero",
    title: ".f149-task-hero h1",
    maxHeroHeight: 140,
  },
  {
    key: "automation",
    path: "/automation",
    root: ".f149-automation-page",
    hero: ".f149-automation-hero",
    title: ".f149-automation-hero h1",
    maxHeroHeight: 140,
  },
  {
    key: "settings",
    path: "/settings",
    root: ".f149-settings-page",
    hero: ".f149-settings-hero",
    title: ".f149-settings-hero h1",
    maxHeroHeight: 140,
  },
  {
    key: "agents",
    path: "/agents",
    root: ".f149-agent-page",
    hero: ".f149-agent-hero",
    title: ".f149-agent-hero h1",
    maxHeroHeight: 140,
  },
  {
    key: "memory",
    path: "/memory",
    root: ".f149-memory-page",
    hero: ".wb-hero-memory",
    title: ".wb-hero-memory h1",
    maxHeroHeight: 180,
  },
  {
    key: "files",
    path: "/files",
    root: ".f149-files-page",
    hero: ".f149-files-hero",
    title: ".f149-files-hero h1",
    maxHeroHeight: 140,
  },
  {
    key: "skills",
    path: "/skills",
    root: ".f149-skills-page",
    hero: ".f149-skills-hero",
    title: ".f149-skills-hero h1",
    maxHeroHeight: 160,
  },
  {
    key: "mcp",
    path: "/mcp",
    root: ".f149-mcp-page",
    hero: ".f149-mcp-hero",
    title: ".f149-mcp-hero h1",
    maxHeroHeight: 160,
  },
] as const;

test.use({
  colorScheme: "dark",
  viewport: { width: 1440, height: 900 },
});

async function waitForStableSurface(page: Page, root: string): Promise<void> {
  await expect(page.locator(root), ORACLE).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 }).first(), ORACLE).toBeVisible();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addStyleTag({
    content: ".wb-topbar-meta, time, .event-time { visibility: hidden !important; }",
  });
}

test.describe("Claude Design 早期业务页视觉基线", () => {
  for (const surface of SURFACES) {
    test(`${surface.key} 保持紧凑页头与低亮度连续卡片`, async ({ page }) => {
      await page.goto(`${l1ServerUrl("loopback")}${surface.path}`);
      await waitForStableSurface(page, surface.root);

      const metrics = await page.locator(surface.hero).evaluate((hero, titleSelector) => {
        const title = document.querySelector<HTMLElement>(titleSelector);
        if (!title) {
          throw new Error(
            `F158_CLAUDE_EARLY_SURFACE_VISUAL_CONTRACT_MISSING: missing ${titleSelector}`,
          );
        }
        const heroStyle = getComputedStyle(hero);
        const titleStyle = getComputedStyle(title);
        return {
          height: hero.getBoundingClientRect().height,
          paddingTop: Number.parseFloat(heroStyle.paddingTop),
          paddingRight: Number.parseFloat(heroStyle.paddingRight),
          radius: Number.parseFloat(heroStyle.borderTopLeftRadius),
          backgroundImage: heroStyle.backgroundImage,
          titleFontSize: Number.parseFloat(titleStyle.fontSize),
        };
      }, surface.title);

      expect(metrics.titleFontSize, ORACLE).toBeGreaterThanOrEqual(24);
      expect(metrics.titleFontSize, ORACLE).toBeLessThanOrEqual(30);
      expect(metrics.paddingTop, ORACLE).toBeGreaterThanOrEqual(16);
      expect(metrics.paddingTop, ORACLE).toBeLessThanOrEqual(24);
      expect(metrics.paddingRight, ORACLE).toBeLessThanOrEqual(26);
      expect(metrics.radius, ORACLE).toBeLessThanOrEqual(18);
      expect(metrics.height, ORACLE).toBeLessThanOrEqual(surface.maxHeroHeight);
      expect(metrics.backgroundImage, ORACLE).not.toContain("radial-gradient");

      await expect(page.locator(".wb-main"), ORACLE).toHaveScreenshot(
        `claude-early-${surface.key}.png`,
        {
          animations: "disabled",
          caret: "hide",
        },
      );
    });
  }
});

test("真实运行任务详情保持同一紧凑暗色视觉语言", async ({ page }) => {
  await page.goto(`${l1ServerUrl("loopback")}/tasks/${L1_A_WAVE_TASK_ID}`);
  await expect(
    page.getByRole("heading", { name: "A 波实时任务" }),
    ORACLE,
  ).toBeVisible();
  await expect(page.locator(".tv-page"), ORACLE).toBeVisible();
  await page.addStyleTag({
    content: ".wb-topbar-meta, time, .event-time { visibility: hidden !important; }",
  });

  const metrics = await page.locator(".tv-detail-header").evaluate((header) => {
    const title = header.querySelector<HTMLElement>(".tv-detail-title");
    if (!title) {
      throw new Error(
        "F158_CLAUDE_EARLY_SURFACE_VISUAL_CONTRACT_MISSING: missing task title",
      );
    }
    const headerStyle = getComputedStyle(header);
    const titleStyle = getComputedStyle(title);
    return {
      height: header.getBoundingClientRect().height,
      radius: Number.parseFloat(headerStyle.borderTopLeftRadius),
      titleFontSize: Number.parseFloat(titleStyle.fontSize),
    };
  });

  expect(metrics.height, ORACLE).toBeLessThanOrEqual(160);
  expect(metrics.radius, ORACLE).toBeLessThanOrEqual(18);
  expect(metrics.titleFontSize, ORACLE).toBeLessThanOrEqual(32);

  await expect(page.locator(".tv-page"), ORACLE).toHaveScreenshot(
    "claude-early-task-detail.png",
    {
      animations: "disabled",
      caret: "hide",
    },
  );
});
