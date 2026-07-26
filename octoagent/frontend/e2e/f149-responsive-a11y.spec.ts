/**
 * F149 十个 Web surface 的 390px 窄窗口几何与可访问性合同。
 *
 * 这里验证的是桌面 Web 在窄浏览器窗口下仍可用，不代表手机产品或 iOS 验收。
 * 断言只覆盖浏览器独有事实：页面级横向溢出、landmark/标题、键盘焦点和
 * reduced-motion；业务状态矩阵继续由各 surface 的 L4 测试负责。
 */
import { expect, test, type Locator, type Page } from "@playwright/test";
import { l1ServerUrl, withFailureMarkerScan } from "./support";

interface Surface {
  name: string;
  path: string;
}

const SURFACES: readonly Surface[] = [
  { name: "Approvals", path: "/approvals" },
  { name: "Tasks", path: "/work" },
  { name: "Task Detail", path: "/tasks/f149-l1-missing" },
  { name: "Automation", path: "/automation" },
  { name: "Settings", path: "/settings" },
  { name: "Agents", path: "/agents" },
  { name: "Memory", path: "/memory" },
  { name: "Files", path: "/files" },
  { name: "Skills", path: "/skills" },
  { name: "MCP", path: "/mcp" },
] as const;

const INTERACTIVE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "summary",
].join(",");

test.use({ viewport: { width: 390, height: 844 } });

async function waitForSurface(page: Page): Promise<Locator> {
  const main = page.getByRole("main").first();
  await withFailureMarkerScan(page, async () => {
    await expect(main).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  });
  return main;
}

async function expectKeyboardReachableOperation(
  page: Page,
  main: Locator,
): Promise<void> {
  const operations = main.locator(INTERACTIVE_SELECTOR);
  const hasVisibleMainOperation = await operations.evaluateAll((elements) =>
    elements.some((element) => {
      const style = getComputedStyle(element);
      return (
        style.display !== "none" &&
        style.visibility !== "hidden" &&
        element.getClientRects().length > 0
      );
    }),
  );
  const operationScope = hasVisibleMainOperation
    ? main
    : page.locator("body");

  await page.locator("body").focus();
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await page.keyboard.press("Tab");
    const reached = await operationScope.evaluate(
      (element, selector) =>
        element.contains(document.activeElement) &&
        document.activeElement?.matches(selector) === true,
      INTERACTIVE_SELECTOR,
    );
    if (reached) {
      const focused = page.locator(":focus");
      await expect(focused).toBeVisible();
      await expect(focused).toHaveAccessibleName(/\S/);
      return;
    }
  }
  throw new Error(
    hasVisibleMainOperation
      ? "F149 窄窗口页面的可见操作无法通过键盘 Tab 到达"
      : "F149 只读空状态缺少可通过键盘 Tab 到达的全局操作",
  );
}

async function expectReducedMotion(page: Page, main: Locator): Promise<void> {
  expect(
    await page.evaluate(
      () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    ),
  ).toBe(true);

  const animatedElements = await main.locator("*").evaluateAll((elements) =>
    elements
      .filter((element) => {
        const style = getComputedStyle(element);
        return (
          style.animationName !== "none" &&
          style.animationDuration
            .split(",")
            .some((duration) => Number.parseFloat(duration) > 0)
        );
      })
      .map((element) => ({
        className: element.className,
        tagName: element.tagName,
      })),
  );
  expect(animatedElements).toEqual([]);
}

test.describe("F149 Web 390px 窄窗口 geometry/a11y sweep", () => {
  for (const surface of SURFACES) {
    test(`${surface.name} 无页面级溢出且键盘与减弱动态可用`, async ({
      page,
    }) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.goto(`${l1ServerUrl("loopback")}${surface.path}`);

      const main = await waitForSurface(page);
      const geometry = await page.evaluate(() => ({
        clientWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
      }));
      expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth);

      await expectKeyboardReachableOperation(page, main);
      await expectReducedMotion(page, main);
    });
  }
});
