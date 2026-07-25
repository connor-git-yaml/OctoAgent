import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { inspectGeneratedContracts } from "./check-openapi-generated.mjs";

type Fixture = {
  code: string;
  generated: Record<string, string>;
  expected?: Record<string, string>;
  slice?: Record<string, string>;
};

const createdRoots: string[] = [];

async function writeFiles(root: string, files: Record<string, string>): Promise<void> {
  await Promise.all(
    Object.entries(files).map(async ([relativePath, source]) => {
      const target = path.join(root, relativePath);
      await mkdir(path.dirname(target), { recursive: true });
      await writeFile(target, source, "utf8");
    }),
  );
}

async function createFixture(fixture: Omit<Fixture, "code">): Promise<{
  generatedDir: string;
  expectedDir: string;
  sliceDir: string;
}> {
  const root = await mkdtemp(path.join(tmpdir(), "f149-openapi-generated-"));
  createdRoots.push(root);
  const generatedDir = path.join(root, "generated");
  const expectedDir = path.join(root, "expected");
  const sliceDir = path.join(root, "slice");
  await Promise.all([
    writeFiles(generatedDir, fixture.generated),
    writeFiles(expectedDir, fixture.expected ?? fixture.generated),
    writeFiles(sliceDir, fixture.slice ?? {}),
  ]);
  return { generatedDir, expectedDir, sliceDir };
}

const violations: Fixture[] = [
  {
    code: "F149_GENERATED_DRIFT",
    generated: { "rest.d.ts": "export type Task = { id: string };\n" },
    expected: { "rest.d.ts": "export type Task = { id: number };\n" },
  },
  {
    code: "F149_GENERATED_ANY",
    generated: { "rest.d.ts": "export type Task = { payload: any };\n" },
  },
  {
    code: "F149_RAW_TYPE_LEAK",
    generated: { "rest.d.ts": "export type Task = { id: string };\n" },
    slice: { "task.ts": "export type TaskView = { payload: unknown };\n" },
  },
  {
    code: "F149_RAW_TYPE_LEAK",
    generated: { "rest.d.ts": "export type Task = { id: string };\n" },
    slice: { "task.ts": "export type JsonValue = string | number | object;\n" },
  },
];

afterEach(async () => {
  await Promise.all(createdRoots.splice(0).map((root) => rm(root, { recursive: true })));
});

describe("F149 OpenAPI generated contract checker", () => {
  it("rejects deterministic drift, any, and unnamed raw type leakage", async () => {
    const missed: string[] = [];
    for (const fixture of violations) {
      const inputs = await createFixture(fixture);
      const codes = inspectGeneratedContracts(inputs).violations.map((item) => item.code);
      if (!codes.includes(fixture.code)) missed.push(fixture.code);
    }
    expect(missed, `F149_OPENAPI_GENERATED_RULES_MISSING: ${missed.join(",")}`).toEqual([]);
  });

  it("accepts byte-identical generated types and named open boundaries", async () => {
    const inputs = await createFixture({
      generated: { "rest.d.ts": "export type Task = { id: string };\n" },
      slice: {
        "raw-task-boundary.ts": "export type RawTaskBoundary = Record<string, unknown>;\n",
        "metadata.ts": "export type Metadata = Record<string, unknown>;\n",
        "schema-as-data.ts": "export type JsonValue = string | number | object;\n",
      },
    });
    expect(inspectGeneratedContracts(inputs).violations).toEqual([]);
  });
});
