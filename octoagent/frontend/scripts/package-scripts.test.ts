import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const frontendRoot = path.resolve(import.meta.dirname, "..");
const packageJson = JSON.parse(
  readFileSync(path.join(frontendRoot, "package.json"), "utf8"),
) as {
  scripts?: Record<string, string>;
  devDependencies?: Record<string, string>;
};
const packageLock = JSON.parse(
  readFileSync(path.join(frontendRoot, "package-lock.json"), "utf8"),
) as {
  packages?: Record<
    string,
    { version?: string; devDependencies?: Record<string, string> }
  >;
};
const viteConfig = readFileSync(path.join(frontendRoot, "vite.config.ts"), "utf8");

function assertOpenApiCommand(command: string | undefined): void {
  expect(command).toContain("PYTHONNOUSERSITE=1");
  for (const packageName of [
    "core",
    "provider",
    "protocol",
    "tooling",
    "skills",
    "policy",
    "memory",
  ]) {
    expect(command).toContain(`packages/${packageName}/src`);
  }
  expect(command).toContain("apps/gateway/src");
  expect(command).not.toContain("packages/sdk/src");
  expect(command).toContain("uv run --project . --no-sync python");
  expect(command).toContain("../repo-scripts/export-f149-contracts.py");
  expect(command).toContain("openapi-typescript");
  for (const artifact of ["rest", "actions", "task-sse"]) {
    expect(command).toContain(`${artifact}.openapi.json`);
    expect(command).toContain(`${artifact}.d.ts`);
  }
  expect(command).not.toMatch(/generated.*(?:fetch|client)/i);
}

describe("F149 frontend package Gate", () => {
  it("locks the types-only generator and matching coverage provider", () => {
    expect(packageJson.devDependencies?.["openapi-typescript"]).toBe("7.13.0");
    expect(packageJson.devDependencies?.["@vitest/coverage-v8"]).toBe("2.1.9");
    expect(packageLock.packages?.[""]?.devDependencies?.["openapi-typescript"]).toBe(
      "7.13.0",
    );
    expect(packageLock.packages?.[""]?.devDependencies?.["@vitest/coverage-v8"]).toBe(
      "2.1.9",
    );
    expect(packageLock.packages?.["node_modules/openapi-typescript"]?.version).toBe("7.13.0");
    expect(packageLock.packages?.["node_modules/@vitest/coverage-v8"]?.version).toBe("2.1.9");
  });

  it("defines complete post-SDK OpenAPI and coverage aliases", () => {
    assertOpenApiCommand(packageJson.scripts?.["openapi:generate"]);
    assertOpenApiCommand(packageJson.scripts?.["openapi:check"]);
    expect(packageJson.scripts?.["openapi:check"]).toContain(
      "scripts/check-openapi-generated.mjs",
    );
    expect(packageJson.scripts?.["test:coverage"]).toBe("vitest run --coverage");
  });

  it("configures v8 LCOV coverage without tests or generated DTOs", () => {
    expect(viteConfig).toContain('provider: "v8"');
    expect(viteConfig).toContain('reporter: ["text", "lcov"]');
    expect(viteConfig).toContain("src/generated/**");
    expect(viteConfig).toContain("**/*.test.*");
    expect(viteConfig).toContain("**/*.spec.*");
  });
});
