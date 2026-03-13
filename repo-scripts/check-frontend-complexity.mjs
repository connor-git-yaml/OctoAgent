import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");

const explicitLimits = new Map([
  ["octoagent/frontend/src/pages/AgentCenter.tsx", 4800],
  ["octoagent/frontend/src/pages/ControlPlane.tsx", 4100],
  ["octoagent/frontend/src/pages/SettingsCenter.tsx", 1900],
  ["octoagent/frontend/src/index.css", 3300],
]);

const ruleSet = [
  {
    label: "新页面与域模块",
    roots: [
      "octoagent/frontend/src/domains",
      "octoagent/frontend/src/ui",
      "octoagent/frontend/src/pages",
    ],
    include: (filePath) =>
      filePath.endsWith(".ts") ||
      filePath.endsWith(".tsx"),
    exclude: (filePath) =>
      filePath.endsWith(".test.ts") ||
      filePath.endsWith(".test.tsx") ||
      filePath.endsWith("/index.ts"),
    defaultLimit: 1200,
  },
  {
    label: "共享查询与 Hook",
    roots: [
      "octoagent/frontend/src/hooks",
      "octoagent/frontend/src/platform",
    ],
    include: (filePath) =>
      filePath.endsWith(".ts") || filePath.endsWith(".tsx"),
    exclude: (filePath) =>
      filePath.endsWith(".test.ts") ||
      filePath.endsWith(".test.tsx") ||
      filePath.endsWith("/index.ts"),
    defaultLimit: 500,
  },
  {
    label: "样式层",
    roots: [
      "octoagent/frontend/src/styles",
      "octoagent/frontend/src/index.css",
    ],
    include: (filePath) => filePath.endsWith(".css"),
    exclude: () => false,
    defaultLimit: 700,
  },
];

function walk(targetPath) {
  const absolutePath = path.join(repoRoot, targetPath);
  if (!fs.existsSync(absolutePath)) {
    return [];
  }
  const stat = fs.statSync(absolutePath);
  if (stat.isFile()) {
    return [targetPath];
  }
  const results = [];
  for (const entry of fs.readdirSync(absolutePath, { withFileTypes: true })) {
    const nextRelative = path.join(targetPath, entry.name);
    if (entry.isDirectory()) {
      results.push(...walk(nextRelative));
      continue;
    }
    results.push(nextRelative);
  }
  return results;
}

function countLines(filePath) {
  const content = fs.readFileSync(path.join(repoRoot, filePath), "utf8");
  if (!content) {
    return 0;
  }
  return content.split(/\r?\n/).length;
}

const trackedFiles = new Map();
for (const rule of ruleSet) {
  for (const root of rule.roots) {
    for (const filePath of walk(root)) {
      if (!rule.include(filePath) || rule.exclude(filePath)) {
        continue;
      }
      if (!trackedFiles.has(filePath)) {
        trackedFiles.set(filePath, {
          lines: countLines(filePath),
          labels: new Set(),
        });
      }
      trackedFiles.get(filePath).labels.add(rule.label);
    }
  }
}

if (trackedFiles.size === 0) {
  console.error("前端复杂度检查失败：没有匹配到任何文件，请检查 repo root 或规则配置。");
  process.exit(1);
}

const violations = [];
for (const [filePath, metadata] of trackedFiles.entries()) {
  const matchingRule = ruleSet.find((rule) =>
    rule.roots.some((root) => filePath === root || filePath.startsWith(`${root}/`))
  );
  if (!matchingRule) {
    continue;
  }
  const limit = explicitLimits.get(filePath) ?? matchingRule.defaultLimit;
  if (metadata.lines > limit) {
    violations.push({
      filePath,
      lines: metadata.lines,
      limit,
      labels: Array.from(metadata.labels).join(" / "),
    });
  }
}

if (violations.length > 0) {
  console.error("前端复杂度检查失败：以下文件超过当前阶段上限。");
  for (const violation of violations) {
    console.error(
      `- ${violation.filePath}: ${violation.lines} 行，超过上限 ${violation.limit} 行 (${violation.labels})`
    );
  }
  process.exit(1);
}

console.log("前端复杂度检查通过。");
for (const [filePath, metadata] of Array.from(trackedFiles.entries()).sort()) {
  const matchingRule = ruleSet.find((rule) =>
    rule.roots.some((root) => filePath === root || filePath.startsWith(`${root}/`))
  );
  if (!matchingRule) {
    continue;
  }
  const limit = explicitLimits.get(filePath) ?? matchingRule.defaultLimit;
  console.log(`- ${filePath}: ${metadata.lines}/${limit}`);
}
