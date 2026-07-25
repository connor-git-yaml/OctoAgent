#!/usr/bin/env node

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import ts from "typescript";

/**
 * @typedef {{ code: string, path: string, message: string }} BoundaryViolation
 * @typedef {{ violations: BoundaryViolation[] }} BoundaryResult
 */

const SOURCE_EXTENSIONS = new Set([".js", ".jsx", ".mjs", ".ts", ".tsx"]);
const TRANSPORT_ALLOWLIST = new Set(["api/client.ts"]);
const NON_F149_FETCH_ALLOWLIST = new Set(["components/shell/BuildVersionWatcher.tsx"]);

function normalizePath(value) {
  return value.split(path.sep).join("/");
}

function collectSourceFiles(rootDir) {
  const files = [];
  function visit(current) {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      if (entry.name === "node_modules" || entry.name === "dist") {
        continue;
      }
      const target = path.join(current, entry.name);
      if (entry.isDirectory()) {
        visit(target);
      } else if (SOURCE_EXTENSIONS.has(path.extname(entry.name))) {
        files.push(target);
      }
    }
  }
  visit(rootDir);
  return files.sort();
}

function scriptKind(filePath) {
  if (filePath.endsWith(".tsx")) return ts.ScriptKind.TSX;
  if (filePath.endsWith(".jsx")) return ts.ScriptKind.JSX;
  if (filePath.endsWith(".js") || filePath.endsWith(".mjs")) return ts.ScriptKind.JS;
  return ts.ScriptKind.TS;
}

function isTestPath(relativePath) {
  return (
    relativePath.includes("/test/") ||
    relativePath.includes("/tests/") ||
    /\.(?:spec|test)\.[cm]?[jt]sx?$/.test(relativePath)
  );
}

function moduleSpecifiers(sourceFile) {
  const specifiers = [];
  function visit(node) {
    if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier)
    ) {
      specifiers.push(node.moduleSpecifier.text);
    } else if (
      ts.isCallExpression(node) &&
      node.arguments.length === 1 &&
      ts.isStringLiteral(node.arguments[0]) &&
      (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
        (ts.isIdentifier(node.expression) && node.expression.text === "require"))
    ) {
      specifiers.push(node.arguments[0].text);
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return specifiers;
}

function resolveRelativeImport(fromPath, specifier, knownPaths) {
  if (!specifier.startsWith(".")) return null;
  const base = path.posix.normalize(path.posix.join(path.posix.dirname(fromPath), specifier));
  const candidates = [
    base,
    ...[...SOURCE_EXTENSIONS].map((extension) => `${base}${extension}`),
    ...[...SOURCE_EXTENSIONS].map((extension) => `${base}/index${extension}`),
  ];
  return candidates.find((candidate) => knownPaths.has(candidate)) ?? null;
}

function layer(relativePath) {
  if (relativePath.startsWith("generated/")) return "generated";
  if (relativePath.startsWith("api/")) return "api";
  if (relativePath.startsWith("platform/")) return "platform";
  if (relativePath.startsWith("domains/") || relativePath.startsWith("hooks/")) return "domain";
  if (
    relativePath.startsWith("pages/") ||
    relativePath.startsWith("components/") ||
    relativePath.startsWith("ui/")
  ) {
    return "ui";
  }
  return "other";
}

function importViolations(relativePath, imports) {
  const violations = [];
  for (const target of imports) {
    const fromLayer = layer(relativePath);
    const targetLayer = layer(target);
    if (["ui", "domain"].includes(fromLayer) && targetLayer === "generated") {
      violations.push(["F149_GENERATED_PAGE_IMPORT", "UI/domain不得直接消费generated wire type"]);
    }
    if (
      (fromLayer === "generated" && targetLayer !== "other") ||
      (fromLayer === "api" && ["domain", "ui"].includes(targetLayer))
    ) {
      violations.push(["F149_REVERSE_LAYER_IMPORT", `${fromLayer}反向依赖${targetLayer}`]);
    }
  }
  return violations;
}

function declarationName(node) {
  if (
    (ts.isClassDeclaration(node) || ts.isFunctionDeclaration(node)) &&
    node.name &&
    ts.isIdentifier(node.name)
  ) {
    return node.name.text;
  }
  if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) {
    return node.name.text;
  }
  return "";
}

function containsLegacyIdentifier(node) {
  let found = false;
  function visit(current) {
    if (ts.isIdentifier(current) && /^legacy[A-Z_]/.test(current.text)) {
      found = true;
      return;
    }
    ts.forEachChild(current, visit);
  }
  visit(node);
  return found;
}

function nodeViolations(relativePath, sourceFile) {
  const violations = [];
  const currentLayer = layer(relativePath);
  const allowTransport = TRANSPORT_ALLOWLIST.has(relativePath);
  const allowFetch = allowTransport || NON_F149_FETCH_ALLOWLIST.has(relativePath);
  function visit(node) {
    if (
      !allowFetch &&
      ts.isCallExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.expression.text === "fetch"
    ) {
      violations.push(["F149_DIRECT_FETCH", "网络调用必须经api/client"]);
    }
    if (
      !allowTransport &&
      ts.isPropertyAssignment(node) &&
      ((ts.isIdentifier(node.name) && node.name.text === "Authorization") ||
        (ts.isStringLiteral(node.name) && node.name.text === "Authorization"))
    ) {
      violations.push(["F149_AUTH_HEADER_BYPASS", "认证header只能由api/client构造"]);
    }
    if (
      currentLayer === "ui" &&
      ((ts.isNumericLiteral(node) && node.text === "401") ||
        (ts.isStringLiteral(node) && node.text === "401"))
    ) {
      violations.push(["F149_PAGE_401_OWNER", "401归F150全局Access owner"]);
    }
    const name = declarationName(node);
    if (!allowTransport && /(?:HttpClient|Transport)$/.test(name)) {
      violations.push(["F149_DUPLICATE_TRANSPORT", `检测到第二transport声明${name}`]);
    }
    if (
      currentLayer === "ui" &&
      (/^create[A-Z].*Store$/.test(name) || /store\.[cm]?[jt]sx?$/i.test(relativePath))
    ) {
      violations.push(["F149_DUPLICATE_STORE", "页面不得创建第二业务store"]);
    }
    if (
      ts.isBinaryExpression(node) &&
      node.operatorToken.kind === ts.SyntaxKind.BarBarToken &&
      containsLegacyIdentifier(node.right)
    ) {
      violations.push(["F149_OPTIONAL_FALLBACK", "禁止兼容旧路径的optional fallback"]);
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return violations;
}

function cycleViolations(graph) {
  const violations = [];
  const visiting = new Set();
  const visited = new Set();
  function visit(current, stack) {
    if (visiting.has(current)) {
      const start = stack.indexOf(current);
      violations.push([current, stack.slice(start).concat(current).join(" -> ")]);
      return;
    }
    if (visited.has(current)) return;
    visiting.add(current);
    for (const target of graph.get(current) ?? []) visit(target, [...stack, current]);
    visiting.delete(current);
    visited.add(current);
  }
  for (const current of graph.keys()) visit(current, []);
  return violations;
}

function addViolation(result, seen, code, relativePath, message) {
  const key = `${code}\u0000${relativePath}`;
  if (seen.has(key)) return;
  seen.add(key);
  result.push({ code, path: relativePath, message });
}

/**
 * @param {string} rootDir
 * @returns {BoundaryResult}
 */
export function inspectBoundaries(rootDir) {
  const root = path.resolve(rootDir);
  if (!existsSync(root)) {
    throw new Error(`F149_BOUNDARY_ROOT_MISSING: ${root}`);
  }
  const records = collectSourceFiles(root).map((filePath) => {
    const relativePath = normalizePath(path.relative(root, filePath));
    const source = readFileSync(filePath, "utf8");
    const sourceFile = ts.createSourceFile(
      relativePath,
      source,
      ts.ScriptTarget.Latest,
      true,
      scriptKind(filePath),
    );
    return { relativePath, sourceFile };
  });
  const knownPaths = new Set(records.map((record) => record.relativePath));
  const graph = new Map();
  const result = [];
  const seen = new Set();
  for (const record of records) {
    if (isTestPath(record.relativePath)) continue;
    const imports = moduleSpecifiers(record.sourceFile)
      .map((specifier) => resolveRelativeImport(record.relativePath, specifier, knownPaths))
      .filter((target) => target !== null);
    graph.set(record.relativePath, imports);
    for (const [code, message] of importViolations(record.relativePath, imports)) {
      addViolation(result, seen, code, record.relativePath, message);
    }
    for (const [code, message] of nodeViolations(record.relativePath, record.sourceFile)) {
      addViolation(result, seen, code, record.relativePath, message);
    }
  }
  for (const [relativePath, cycle] of cycleViolations(graph)) {
    addViolation(result, seen, "F149_IMPORT_CYCLE", relativePath, cycle);
  }
  return { violations: result };
}

function main() {
  const rootDir = process.argv[2] ?? path.resolve(process.cwd(), "src");
  const result = inspectBoundaries(rootDir);
  if (result.violations.length > 0) {
    for (const violation of result.violations) {
      console.error(`${violation.code}: ${violation.path}: ${violation.message}`);
    }
    process.exitCode = 1;
    return;
  }
  console.log("F149 boundary check PASS");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
