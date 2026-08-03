import { expect, test } from "@playwright/test";
import { L1_TESTIDS } from "./selectors";
import { l1ServerUrl } from "./support";

const ORACLE = "F158_CLAUDE_EARLY_VISUAL_CONTRACT_MISSING";

test.use({ colorScheme: "dark" });

type Box = {
  x: number;
  y: number;
  width: number;
  height: number;
};

type VisualMetrics = {
  shell: Box;
  sidebar: Box;
  main: Box;
  topbar: Box;
  chat: Box;
  runPanel: Box;
  brand: Box;
  brandMark: Box;
  firstNavItem: Box;
  chatBackground: string;
  userMessageBackground: string;
  agentMessageBackground: string;
  agentMessageBorder: string;
};

test("桌面工作台保持 Claude Design 早期紧凑三栏视觉语言", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(l1ServerUrl("loopback"));
  await expect(page.getByTestId(L1_TESTIDS.chatInput), ORACLE).toBeVisible();
  await expect(page.getByTestId(L1_TESTIDS.sessionRunPanel), ORACLE).toBeVisible();

  await expect(page.locator(".wb-sidebar-brand"), ORACLE).toHaveScreenshot(
    "claude-early-sidebar-brand.png",
  );
  await expect(page.locator(".wb-nav > .wb-nav-item").first(), ORACLE).toHaveScreenshot(
    "claude-early-navigation-row.png",
  );
  await expect(page.locator(".wb-chat-form"), ORACLE).toHaveScreenshot(
    "claude-early-composer.png",
  );
  await expect(page.locator(".v2-run-panel-head"), ORACLE).toHaveScreenshot(
    "claude-early-run-panel-head.png",
    {
      // 中文 fallback 字体在 macOS 与 Linux 的抗锯齿不同；419 个差异像素都落在
      // 同一行字形边缘。Playwright 会取 pixels 与 ratio 两者中更严格的上限，
      // 因此这个 280×32 标题同时声明 450 pixels / 5%；结构、颜色、文案与其余
      // 13 个视觉快照继续使用全局 2% 严格门。
      maxDiffPixels: 450,
      maxDiffPixelRatio: 0.05,
    },
  );

  const metrics = await page.evaluate<VisualMetrics>(() => {
    const required = (selector: string): HTMLElement => {
      const element = document.querySelector<HTMLElement>(selector);
      if (!element) {
        throw new Error(`F158_CLAUDE_EARLY_VISUAL_CONTRACT_MISSING: missing ${selector}`);
      }
      return element;
    };
    const box = (selector: string): Box => {
      const rect = required(selector).getBoundingClientRect();
      return {
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
      };
    };
    const style = (selector: string): CSSStyleDeclaration =>
      getComputedStyle(required(selector));
    const messageStyle = (kind: "is-user" | "is-agent") => {
      const existing = document.querySelector<HTMLElement>(`.wb-message-card.${kind}`);
      const element = existing ?? document.createElement("div");
      if (!existing) {
        element.className = `wb-message-card ${kind}`;
        element.style.position = "absolute";
        element.style.visibility = "hidden";
        document.body.append(element);
      }
      const computed = getComputedStyle(element);
      const values = {
        backgroundColor: computed.backgroundColor,
        backgroundImage: computed.backgroundImage,
        borderStyle: computed.borderStyle,
      };
      if (!existing) {
        element.remove();
      }
      return values;
    };
    const userMessageStyle = messageStyle("is-user");
    const agentMessageStyle = messageStyle("is-agent");

    return {
      shell: box(".wb-shell"),
      sidebar: box(".wb-sidebar"),
      main: box(".wb-main"),
      topbar: box(".wb-topbar"),
      chat: box(".wb-chat-panel"),
      runPanel: box(".v2-run-panel"),
      brand: box(".wb-sidebar-brand"),
      brandMark: box(".wb-brand-mark"),
      firstNavItem: box(".wb-nav-item"),
      chatBackground: style(".wb-chat-panel").backgroundColor,
      userMessageBackground: userMessageStyle.backgroundImage,
      agentMessageBackground: agentMessageStyle.backgroundColor,
      agentMessageBorder: agentMessageStyle.borderStyle,
    };
  });

  expect(metrics.shell.x, ORACLE).toBe(0);
  expect(metrics.shell.y, ORACLE).toBe(0);
  expect(metrics.shell.width, ORACLE).toBe(1440);
  expect(metrics.shell.height, ORACLE).toBe(900);

  expect(metrics.sidebar.x, ORACLE).toBe(0);
  expect(metrics.sidebar.y, ORACLE).toBe(0);
  expect(metrics.sidebar.width, ORACLE).toBeGreaterThanOrEqual(244);
  expect(metrics.sidebar.width, ORACLE).toBeLessThanOrEqual(268);
  expect(metrics.main.x - (metrics.sidebar.x + metrics.sidebar.width), ORACLE).toBeLessThanOrEqual(1);

  expect(metrics.brand.height, ORACLE).toBeLessThanOrEqual(86);
  expect(metrics.brandMark.width, ORACLE).toBeLessThanOrEqual(38);
  expect(metrics.firstNavItem.height, ORACLE).toBeLessThanOrEqual(42);
  expect(metrics.topbar.y, ORACLE).toBe(0);
  expect(metrics.topbar.height, ORACLE).toBeLessThanOrEqual(54);

  expect(metrics.runPanel.x - (metrics.chat.x + metrics.chat.width), ORACLE).toBeLessThanOrEqual(1);
  expect(metrics.runPanel.width, ORACLE).toBeGreaterThanOrEqual(296);
  expect(metrics.runPanel.width, ORACLE).toBeLessThanOrEqual(326);
  expect(metrics.chat.y, ORACLE).toBe(metrics.runPanel.y);
  expect(metrics.chat.height, ORACLE).toBe(metrics.runPanel.height);

  expect(metrics.chatBackground, ORACLE).toBe("rgb(16, 18, 23)");
  expect(metrics.userMessageBackground, ORACLE).toContain("linear-gradient");
  expect(metrics.userMessageBackground, ORACLE).toContain("rgb(39, 40, 43)");
  expect(metrics.agentMessageBackground, ORACLE).toBe("rgba(0, 0, 0, 0)");
  expect(metrics.agentMessageBorder, ORACLE).toBe("none");
});
