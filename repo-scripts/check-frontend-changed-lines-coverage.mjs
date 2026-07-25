#!/usr/bin/env node

import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const requireFromFrontend = createRequire(
  path.resolve(scriptDir, "../octoagent/frontend/package.json"),
);
const ts = requireFromFrontend("typescript");

/**
 * @typedef {{ covered: number, total: number, percent: number, uncovered: string[] }} CoverageResult
 */

/**
 * @param {{ sources: Map<string, string>, changedLines: Map<string, Set<number>>, lcov: string }} inputs
 * @returns {CoverageResult}
 */
export function evaluateChangedLineCoverage(inputs) {
  const hits = parseLcov(inputs.lcov);
  const uncovered = [];
  let covered = 0;
  let total = 0;
  for (const [filePath, changed] of inputs.changedLines) {
    const source = inputs.sources.get(filePath);
    if (source === undefined || excludedPath(filePath)) continue;
    const executable = executableLines(filePath, source);
    for (const line of [...changed].sort((left, right) => left - right)) {
      if (!executable.has(line)) continue;
      total += 1;
      if ((hits.get(filePath)?.get(line) ?? 0) > 0) covered += 1;
      else uncovered.push(`${filePath}:${line}`);
    }
  }
  const percent = total === 0 ? 100 : Number(((covered * 100) / total).toFixed(2));
  return { covered, total, percent, uncovered };
}

function normalizePath(value) {
  const normalized = value.split(path.sep).join("/");
  const marker = "/octoagent/frontend/";
  return normalized.includes(marker) ? normalized.split(marker).at(-1) : normalized;
}

function excludedPath(filePath) {
  return (
    /\.(?:spec|test)\.[cm]?[jt]sx?$/.test(filePath) ||
    filePath.includes("/generated/") ||
    filePath.endsWith(".d.ts")
  );
}

function executableLines(filePath, source) {
  const kind = filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sourceFile = ts.createSourceFile(filePath, source, ts.ScriptTarget.Latest, true, kind);
  const lines = new Set();
  function mark(node) {
    lines.add(sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1);
  }
  function visit(node) {
    if (
      (ts.isVariableDeclaration(node) && node.initializer) ||
      ts.isExpressionStatement(node) ||
      ts.isReturnStatement(node) ||
      ts.isThrowStatement(node) ||
      ts.isIfStatement(node) ||
      ts.isForStatement(node) ||
      ts.isForOfStatement(node) ||
      ts.isForInStatement(node) ||
      ts.isWhileStatement(node) ||
      ts.isDoStatement(node) ||
      ts.isSwitchStatement(node) ||
      ts.isTryStatement(node) ||
      ts.isCallExpression(node) ||
      ts.isNewExpression(node) ||
      ts.isAwaitExpression(node) ||
      ts.isPropertyAssignment(node) ||
      ts.isJsxElement(node) ||
      ts.isJsxSelfClosingElement(node) ||
      ts.isJsxExpression(node)
    ) {
      mark(node);
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return lines;
}

function parseLcov(source) {
  const hits = new Map();
  let current = null;
  for (const line of source.split(/\r?\n/)) {
    if (line.startsWith("SF:")) {
      current = normalizePath(line.slice(3));
      if (!hits.has(current)) hits.set(current, new Map());
    } else if (current && line.startsWith("DA:")) {
      const [lineNumber, count] = line.slice(3).split(",").map(Number);
      hits.get(current).set(lineNumber, count);
    } else if (line === "end_of_record") {
      current = null;
    }
  }
  return hits;
}

function collectSources(sourceRoot) {
  const sources = new Map();
  function visit(current) {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const target = path.join(current, entry.name);
      if (entry.isDirectory()) {
        visit(target);
      } else if (/\.[cm]?[jt]sx?$/.test(entry.name)) {
        const relative = `src/${normalizePath(path.relative(sourceRoot, target))}`;
        sources.set(relative, readFileSync(target, "utf8"));
      }
    }
  }
  visit(sourceRoot);
  return sources;
}

function parseChangedLines(diff) {
  const changed = new Map();
  let current = null;
  for (const line of diff.split(/\r?\n/)) {
    if (line.startsWith("+++ b/octoagent/frontend/")) {
      current = line.slice("+++ b/octoagent/frontend/".length);
    } else if (line.startsWith("@@") && current) {
      const match = /\+(\d+)(?:,(\d+))?/.exec(line);
      if (!match) continue;
      const start = Number(match[1]);
      const count = match[2] === undefined ? 1 : Number(match[2]);
      const lines = changed.get(current) ?? new Set();
      for (let offset = 0; offset < count; offset += 1) lines.add(start + offset);
      changed.set(current, lines);
    }
  }
  return changed;
}

function includeUntrackedLines(repoRoot, sources, changed) {
  const output = execFileSync(
    "git",
    ["ls-files", "--others", "--exclude-standard", "--", "octoagent/frontend/src"],
    { cwd: repoRoot, encoding: "utf8" },
  );
  for (const repoPath of output.split(/\r?\n/).filter(Boolean)) {
    const filePath = repoPath.replace(/^octoagent\/frontend\//, "");
    const source = sources.get(filePath);
    if (source === undefined) continue;
    const lineCount = source === "" ? 0 : source.split(/\r?\n/).length;
    changed.set(
      filePath,
      new Set(Array.from({ length: lineCount }, (_, index) => index + 1)),
    );
  }
}

function parseArgs(argv) {
  const result = { lcov: "", base: "", minPercent: Number.NaN };
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index];
    const value = argv[index + 1];
    if (name === "--lcov") result.lcov = value;
    else if (name === "--base") result.base = value;
    else if (name === "--min-percent") result.minPercent = Number(value);
    else throw new Error(`F149_COVERAGE_ARGUMENT_INVALID: ${name}`);
  }
  if (!result.lcov || !result.base || !Number.isFinite(result.minPercent)) {
    throw new Error("F149_COVERAGE_ARGUMENT_MISSING");
  }
  return result;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const repoRoot = process.cwd();
  const sourceRoot = path.join(repoRoot, "octoagent/frontend/src");
  const diff = execFileSync(
    "git",
    ["diff", "--unified=0", "--no-ext-diff", args.base, "--", "octoagent/frontend/src"],
    { cwd: repoRoot, encoding: "utf8" },
  );
  const sources = collectSources(sourceRoot);
  const changedLines = parseChangedLines(diff);
  includeUntrackedLines(repoRoot, sources, changedLines);
  const result = evaluateChangedLineCoverage({
    sources,
    changedLines,
    lcov: readFileSync(path.resolve(repoRoot, args.lcov), "utf8"),
  });
  console.log(JSON.stringify(result));
  if (result.total > 0 && result.covered * 100 < args.minPercent * result.total) {
    process.exitCode = 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
