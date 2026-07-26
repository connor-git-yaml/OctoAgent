/**
 * F140 AC-4：L1 data-testid 选择器契约测试（机器校验，防锚点腐烂）。
 *
 * 遍历 e2e/selectors.ts 的 L1_TESTIDS 单一事实源，机械校验每个锚点在
 * src/**.tsx 源码中以 `data-testid="<value>"` 字面出现 ≥1 次。
 * 组件重构删/改锚点 → 本测试先红（vitest 层），不等 Playwright 在 CI 才炸。
 *
 * 刻意用字面 grep 而非渲染断言：锚点可能分布在多个页面/条件分支，渲染全部
 * 场景成本高且脆；契约只保证「锚点存在于源码」，运行期可达性由 L1 场景自证。
 *
 * 位置在 frontend/testing/（src 外）：本测试用 node API（fs/path/process），
 * 而 `tsc -b`（tsconfig include=["src"]）无 node types——放 src 内会破坏
 * `npm run build`。vitest 默认 include 覆盖本目录（esbuild 转译，不走 tsc）。
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { L1_TESTIDS } from "../e2e/selectors";

interface L1ConfigFacts {
  ambientOrHostPath: boolean;
  noSync: boolean;
  pythonNoUserSite: boolean;
  pythonPaths: string[];
  retries: number;
}

const CANONICAL_PYTHON_PATHS = [
  "packages/core/src",
  "packages/provider/src",
  "packages/protocol/src",
  "packages/tooling/src",
  "packages/skills/src",
  "packages/policy/src",
  "packages/memory/src",
  "apps/gateway/src",
] as const;

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

function validateL1ConfigContract(facts: L1ConfigFacts): boolean {
  return (
    !facts.ambientOrHostPath &&
    facts.noSync &&
    facts.pythonNoUserSite &&
    facts.retries === 0 &&
    facts.pythonPaths.length === CANONICAL_PYTHON_PATHS.length &&
    new Set(facts.pythonPaths).size === facts.pythonPaths.length &&
    facts.pythonPaths.every(
      (path, index) => path === CANONICAL_PYTHON_PATHS[index],
    )
  );
}

function actualL1ConfigFacts(source: string): L1ConfigFacts {
  const pathBlock = source.match(
    /const PYTHONPATH_LOCK = \[([\s\S]*?)\]\s*\.map/,
  )?.[1];
  const launcherCommand =
    source.match(/const LAUNCHER_CMD =\s*"([^"]+)"/)?.[1] ?? "";
  const sharedEnvironment =
    source.match(/const SHARED_ENV = \{([\s\S]*?)\n\};/)?.[1] ?? "";
  const pythonPaths = pathBlock
    ? [...pathBlock.matchAll(/"([^"]+)"/g)].map((match) => match[1])
    : [];
  return {
    ambientOrHostPath:
      /process\.env\.PYTHONPATH|\/Users\/|\/home\/|~\//.test(source),
    noSync: /^uv run --project \. --no-sync python /.test(launcherCommand),
    pythonNoUserSite: /PYTHONNOUSERSITE:\s*"1"/.test(sharedEnvironment),
    pythonPaths,
    retries: /retries:\s*0\b/.test(source) ? 0 : 1,
  };
}

function acceptedL1ConfigFacts(): L1ConfigFacts {
  return {
    ambientOrHostPath: false,
    noSync: true,
    pythonNoUserSite: true,
    pythonPaths: [...CANONICAL_PYTHON_PATHS],
    retries: 0,
  };
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

describe("F149 L1 harness 环境契约", () => {
  const accepted = acceptedL1ConfigFacts();
  const seededNegatives: Array<[string, L1ConfigFacts]> = [
    [
      "retired SDK path",
      {
        ...accepted,
        pythonPaths: [...accepted.pythonPaths, "packages/sdk/src"],
      },
    ],
    ...CANONICAL_PYTHON_PATHS.map(
      (missing): [string, L1ConfigFacts] => [
        `missing ${missing}`,
        {
          ...accepted,
          pythonPaths: accepted.pythonPaths.filter((path) => path !== missing),
        },
      ],
    ),
    ["CI retry", { ...accepted, retries: 1 }],
    ["ambient host path", { ...accepted, ambientOrHostPath: true }],
  ];

  it("接受精确七个保留包、Gateway 与确定性运行参数", () => {
    expect(validateL1ConfigContract(accepted)).toBe(true);
  });

  it.each(seededNegatives)("拒绝 %s", (_label, facts) => {
    expect(validateL1ConfigContract(facts)).toBe(false);
  });

  it("actual post-F151 Playwright config 满足同一合同", () => {
    const configSource = readFileSync(
      join(process.cwd(), "playwright.config.ts"),
      "utf-8",
    );
    expect(validateL1ConfigContract(actualL1ConfigFacts(configSource))).toBe(
      true,
    );
  });
});
