#!/usr/bin/env node

import { fileURLToPath } from "node:url";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";

/**
 * @typedef {{ code: string, path: string, message: string }} StyleViolation
 * @typedef {{ palette: number, spotify: number, nonCpToken: number, secondTheme: number }} StyleMetrics
 * @typedef {{ indexMaxLines: number, baseline: StyleMetrics }} StylePolicy
 */

export const DEFAULT_STYLE_POLICY = Object.freeze({
  indexMaxLines: 4476,
  baseline: Object.freeze({
    palette: 336,
    spotify: 6,
    nonCpToken: 59,
    secondTheme: 37,
  }),
});

const SOURCE_EXTENSIONS = new Set([".css", ".js", ".jsx", ".ts", ".tsx"]);

function countMatches(source, pattern) {
  return source.match(pattern)?.length ?? 0;
}

function sourceMetrics(source) {
  return {
    palette: countMatches(source, /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/g),
    spotify: countMatches(source, /spotify/gi),
    nonCpToken: countMatches(
      source,
      /var\(\s*--(?!cp-)[A-Za-z0-9_-]+|^\s*--(?!cp-)[A-Za-z0-9_-]+\s*:/gm,
    ),
    secondTheme: countMatches(
      source,
      /prefers-color-scheme\s*:\s*light|\[data-theme|\.(?:light|light-theme)\b/gi,
    ),
  };
}

function collectSourceFiles(rootDir) {
  const files = [];
  function visit(current) {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      if (entry.name === "node_modules" || entry.name === "dist") continue;
      const target = path.join(current, entry.name);
      if (entry.isDirectory()) {
        visit(target);
      } else if (
        SOURCE_EXTENSIONS.has(path.extname(entry.name)) &&
        !/\.(?:spec|test)\.[cm]?[jt]sx?$/.test(entry.name)
      ) {
        files.push(target);
      }
    }
  }
  visit(rootDir);
  return files.sort();
}

function addViolation(violations, code, relativePath, message) {
  violations.push({ code, path: relativePath, message });
}

/**
 * @param {string} rootDir
 * @param {StylePolicy} [policy]
 * @returns {{ violations: StyleViolation[], metrics: StyleMetrics & { indexLines: number } }}
 */
export function inspectStyleRatchet(rootDir, policy = DEFAULT_STYLE_POLICY) {
  const root = path.resolve(rootDir);
  const indexPath = path.join(root, "index.css");
  if (!existsSync(indexPath)) {
    throw new Error(`F149_INDEX_CSS_MISSING: ${indexPath}`);
  }
  const metrics = {
    indexLines: countMatches(readFileSync(indexPath, "utf8"), /\n/g),
    palette: 0,
    spotify: 0,
    nonCpToken: 0,
    secondTheme: 0,
  };
  for (const filePath of collectSourceFiles(root)) {
    const current = sourceMetrics(readFileSync(filePath, "utf8"));
    for (const key of Object.keys(current)) metrics[key] += current[key];
  }

  const violations = [];
  if (metrics.indexLines > policy.indexMaxLines) {
    addViolation(
      violations,
      "F149_INDEX_CSS_GROWTH",
      "index.css",
      `${metrics.indexLines}>${policy.indexMaxLines}`,
    );
  }
  const checks = [
    ["palette", "F149_HARDCODED_PALETTE_GROWTH"],
    ["spotify", "F149_SPOTIFY_THEME_GROWTH"],
    ["secondTheme", "F149_SECOND_THEME_GROWTH"],
    ["nonCpToken", "F149_NON_CP_TOKEN_GROWTH"],
  ];
  for (const [key, code] of checks) {
    if (metrics[key] > policy.baseline[key]) {
      addViolation(
        violations,
        code,
        ".",
        `${key}=${metrics[key]}>${policy.baseline[key]}`,
      );
    }
  }
  return { violations, metrics };
}

function main() {
  const rootDir = process.argv[2] ?? path.resolve(process.cwd(), "src");
  const result = inspectStyleRatchet(rootDir);
  if (result.violations.length > 0) {
    for (const violation of result.violations) {
      console.error(`${violation.code}: ${violation.path}: ${violation.message}`);
    }
    process.exitCode = 1;
    return;
  }
  console.log(`F149 style ratchet PASS ${JSON.stringify(result.metrics)}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
