#!/usr/bin/env node

import { fileURLToPath } from "node:url";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import ts from "typescript";

/**
 * @typedef {{ code: string, path: string, message: string }} GeneratedViolation
 * @typedef {{ generatedDir: string, expectedDir: string, sliceDir: string }} GeneratedInputs
 */

/**
 * @param {GeneratedInputs} inputs
 * @returns {{ violations: GeneratedViolation[] }}
 */
export function inspectGeneratedContracts(inputs) {
  const violations = [];
  const generated = readTree(inputs.generatedDir);
  const expected = readTree(inputs.expectedDir);
  const paths = new Set([...generated.keys(), ...expected.keys()]);
  for (const relativePath of [...paths].sort()) {
    if (!generated.has(relativePath) || !expected.has(relativePath)) {
      addViolation(violations, "F149_GENERATED_DRIFT", relativePath, "generated文件集合不一致");
    } else if (!generated.get(relativePath).equals(expected.get(relativePath))) {
      addViolation(violations, "F149_GENERATED_DRIFT", relativePath, "generated字节不一致");
    }
  }
  inspectTypes(inputs.generatedDir, true, violations);
  inspectTypes(inputs.sliceDir, false, violations);
  return { violations };
}

const TYPE_EXTENSIONS = new Set([".ts", ".tsx"]);

function readTree(rootDir) {
  const files = new Map();
  if (!existsSync(rootDir)) return files;
  function visit(current) {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const target = path.join(current, entry.name);
      if (entry.isDirectory()) visit(target);
      else files.set(path.relative(rootDir, target).split(path.sep).join("/"), readFileSync(target));
    }
  }
  visit(rootDir);
  return files;
}

function addViolation(violations, code, relativePath, message) {
  if (violations.some((item) => item.code === code && item.path === relativePath)) return;
  violations.push({ code, path: relativePath, message });
}

function rawBoundaryPath(relativePath) {
  return /(?:^|[-_/])(raw|metadata|schema(?:-as-data)?)(?:[-_.\/]|$)/i.test(relativePath);
}

function scriptKind(filePath) {
  return filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
}

function inspectTypes(rootDir, generated, violations) {
  for (const [relativePath, bytes] of readTree(rootDir)) {
    if (!TYPE_EXTENSIONS.has(path.extname(relativePath))) continue;
    const sourceFile = ts.createSourceFile(
      relativePath,
      bytes.toString("utf8"),
      ts.ScriptTarget.Latest,
      true,
      scriptKind(relativePath),
    );
    function visit(node) {
      if (node.kind === ts.SyntaxKind.AnyKeyword) {
        addViolation(
          violations,
          "F149_GENERATED_ANY",
          relativePath,
          "F149 generated/contract不得使用any",
        );
      }
      const isJsonValue =
        (ts.isTypeReferenceNode(node) &&
          ts.isIdentifier(node.typeName) &&
          node.typeName.text === "JsonValue") ||
        (ts.isTypeAliasDeclaration(node) && node.name.text === "JsonValue");
      if (
        !generated &&
        !rawBoundaryPath(relativePath) &&
        (node.kind === ts.SyntaxKind.UnknownKeyword || isJsonValue)
      ) {
        addViolation(
          violations,
          "F149_RAW_TYPE_LEAK",
          relativePath,
          "unknown/JsonValue只能停留在命名raw/metadata/schema边界",
        );
      }
      ts.forEachChild(node, visit);
    }
    visit(sourceFile);
  }
}

function main() {
  const frontendRoot = process.cwd();
  const result = inspectGeneratedContracts({
    generatedDir: process.argv[2] ?? path.join(frontendRoot, "src/generated/f149"),
    expectedDir: process.argv[3] ?? path.join(frontendRoot, ".openapi-generated/f149"),
    sliceDir: process.argv[4] ?? path.join(frontendRoot, "src/api/f149"),
  });
  if (result.violations.length > 0) {
    for (const violation of result.violations) {
      console.error(`${violation.code}: ${violation.path}: ${violation.message}`);
    }
    process.exitCode = 1;
    return;
  }
  console.log("F149 generated contract check PASS");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
