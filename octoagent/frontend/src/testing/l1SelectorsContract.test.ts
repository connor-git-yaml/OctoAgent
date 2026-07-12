/**
 * F140 AC-4：L1 data-testid 选择器契约测试（机器校验，防锚点腐烂）。
 *
 * 遍历 e2e/selectors.ts 的 L1_TESTIDS 单一事实源，机械校验每个锚点在
 * src/**.tsx 源码中以 `data-testid="<value>"` 字面出现 ≥1 次。
 * 组件重构删/改锚点 → 本测试先红（vitest 层），不等 Playwright 在 CI 才炸。
 *
 * 刻意用字面 grep 而非渲染断言：锚点可能分布在多个页面/条件分支，渲染全部
 * 场景成本高且脆；契约只保证「锚点存在于源码」，运行期可达性由 L1 场景自证。
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { L1_TESTIDS } from "../../e2e/selectors";

function collectTsxSources(dir: string, acc: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      if (entry === "node_modules" || entry === "dist") continue;
      collectTsxSources(full, acc);
    } else if (entry.endsWith(".tsx")) {
      acc.push(full);
    }
  }
  return acc;
}

describe("L1 selectors 契约（F140 AC-4）", () => {
  // vitest root = frontend/，src 相对可达
  const srcRoot = join(process.cwd(), "src");
  const sources = collectTsxSources(srcRoot).map((p) => readFileSync(p, "utf-8"));

  it.each(Object.entries(L1_TESTIDS))(
    "锚点 %s=%s 必须在 src/**.tsx 源码字面存在",
    (_key, testid) => {
      const needle = `data-testid="${testid}"`;
      const dynamicNeedle = `"${testid}"`; // 三元表达式形态（MessageBubble）
      const hit = sources.some(
        (text) => text.includes(needle) || text.includes(dynamicNeedle)
      );
      expect(
        hit,
        `data-testid 锚点 "${testid}" 未在任何 src/**.tsx 中出现——` +
          "若重构删除了它，须同步更新 e2e/selectors.ts 与对应 Playwright 场景"
      ).toBe(true);
    }
  );

  it("清单非空且值唯一", () => {
    const values = Object.values(L1_TESTIDS);
    expect(values.length).toBeGreaterThan(0);
    expect(new Set(values).size).toBe(values.length);
  });
});
