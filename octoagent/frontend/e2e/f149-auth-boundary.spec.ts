import { expect, test } from "@playwright/test";
import { L1_TESTIDS } from "./selectors";
import { assertBombNotTripped, l1ServerUrl } from "./support";

const ORACLE = "F149_AUTH_OWNERSHIP_BOUNDARY_MISSING";

test("origin 403留在页面，页面级401交回F150全局Access", async ({ page }) => {
  let status: 401 | 403 = 403;
  const observedStatuses: number[] = [];

  await page.route("**/api/skills", async (route) => {
    observedStatuses.push(status);
    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify({
        detail: {
          code: status === 401 ? "FRONT_DOOR_TOKEN_REQUIRED" : "FORBIDDEN",
          message: status === 401 ? "需要验证访问身份" : "资源权限不足",
          hint: status === 401 ? "请重新验证后继续" : "请联系管理员",
        },
      }),
    });
  });

  await page.goto(`${l1ServerUrl("loopback")}/skills`);
  await expect(
    page.getByText("当前账号没有权限管理技能"),
    ORACLE,
  ).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Workbench Navigation" }),
    ORACLE,
  ).toBeVisible();
  await expect(
    page.getByTestId(L1_TESTIDS.frontdoorTokenInput),
    ORACLE,
  ).toBeHidden();
  await expect(page.getByText(/重新登录/), ORACLE).toHaveCount(0);

  status = 401;
  await page.reload();
  await expect(
    page.getByTestId(L1_TESTIDS.frontdoorTokenInput),
    ORACLE,
  ).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Workbench Navigation" }),
    ORACLE,
  ).toBeHidden();
  await expect(page.getByText("当前账号没有权限管理技能"), ORACLE).toBeHidden();

  expect(observedStatuses, ORACLE).toContain(403);
  expect(observedStatuses, ORACLE).toContain(401);
  assertBombNotTripped("loopback");
});
