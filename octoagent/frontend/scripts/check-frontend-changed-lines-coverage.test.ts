import { describe, expect, it } from "vitest";

import { evaluateChangedLineCoverage } from "../../../repo-scripts/check-frontend-changed-lines-coverage.mjs";

function lineSet(...lines: number[]): Set<number> {
  return new Set(lines);
}

describe("F149 frontend changed-lines coverage", () => {
  it("counts only changed authored executable lines", () => {
    const sources = new Map([
      [
        "src/domain/task.ts",
        [
          "import type { Task } from './types';",
          "export interface View { id: string }",
          "export type State = 'idle' | 'ready';",
          "export const label = (task: Task) => {",
          "  return task.id;",
          "};",
        ].join("\n"),
      ],
      ["src/domain/task.test.ts", "expect(true).toBe(true);"],
      ["src/generated/f149/rest.d.ts", "export type Wire = { id: string };"],
      ["src/domain/types.d.ts", "export interface Task { id: string }"],
    ]);
    const changedLines = new Map([
      ["src/domain/task.ts", lineSet(1, 2, 3, 4, 5, 6)],
      ["src/domain/task.test.ts", lineSet(1)],
      ["src/generated/f149/rest.d.ts", lineSet(1)],
      ["src/domain/types.d.ts", lineSet(1)],
    ]);
    const lcov = "SF:src/domain/task.ts\nDA:4,1\nDA:5,0\nend_of_record\n";
    expect(evaluateChangedLineCoverage({ sources, changedLines, lcov })).toMatchObject({
      covered: 1,
      total: 2,
      percent: 50,
      uncovered: ["src/domain/task.ts:5"],
    });
  });

  it("rejects 89.99 percent and accepts exactly 90 percent", () => {
    const source = Array.from({ length: 10_000 }, (_, index) => `run(${index});`).join("\n");
    const sources = new Map([["src/large.ts", source]]);
    const changed = lineSet(...Array.from({ length: 10_000 }, (_, index) => index + 1));
    const changedLines = new Map([["src/large.ts", changed]]);
    const below = [
      "SF:src/large.ts",
      ...Array.from({ length: 10_000 }, (_, index) => `DA:${index + 1},${index < 8999 ? 1 : 0}`),
      "end_of_record",
    ].join("\n");
    const exact = below.replace("DA:9000,0", "DA:9000,1");
    expect(evaluateChangedLineCoverage({ sources, changedLines, lcov: below }).percent).toBe(
      89.99,
    );
    expect(evaluateChangedLineCoverage({ sources, changedLines, lcov: exact }).percent).toBe(90);
  });

  it("counts a new executable file without LCOV as zero coverage", () => {
    const result = evaluateChangedLineCoverage({
      sources: new Map([["src/new-feature.ts", "export const run = () => 1;\n"]]),
      changedLines: new Map([["src/new-feature.ts", lineSet(1)]]),
      lcov: "",
    });
    expect(result).toMatchObject({
      covered: 0,
      total: 1,
      percent: 0,
      uncovered: ["src/new-feature.ts:1"],
    });
  });
});
